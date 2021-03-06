#!/usr/bin/env python
# vim:fileencoding=utf-8
# License: GPL v3 Copyright: 2018, Kovid Goyal <kovid at kovidgoyal.net>

import re
from gettext import gettext as _

from kitty.fast_data_types import truncate_point_for_length, wcswidth

from .collect import data_for_path, lines_for_path, path_name_map
from .config import formats
from .git import even_up_sides


class HunkRef:

    __slots__ = ('hunk_num', 'line_num')

    def __init__(self, hunk_num, line_num):
        self.hunk_num = hunk_num
        self.line_num = line_num


class Reference:

    __slots__ = ('path', 'extra')

    def __init__(self, path, extra=None):
        self.path = path
        self.extra = extra


class Line:

    __slots__ = ('text', 'ref')

    def __init__(self, text, ref):
        self.text = text
        self.ref = ref


def yield_lines_from(iterator, reference):
    for text in iterator:
        yield Line(text, reference)


def human_readable(size, sep=' '):
    """ Convert a size in bytes into a human readable form """
    divisor, suffix = 1, "B"
    for i, candidate in enumerate(('B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB')):
        if size < (1 << ((i + 1) * 10)):
            divisor, suffix = (1 << (i * 10)), candidate
            break
    size = str(float(size)/divisor)
    if size.find(".") > -1:
        size = size[:size.find(".")+2]
    if size.endswith('.0'):
        size = size[:-2]
    return size + sep + suffix


sanitize_pat = re.compile('[\x00-\x1f\x7f\x80-\x9f]')


def sanitize_sub(m):
    return '<{:x}>'.format(ord(m.group()[0]))


def sanitize(text):
    return sanitize_pat.sub(sanitize_sub, text)


def fit_in(text, count):
    p = truncate_point_for_length(text, count)
    if p >= len(text):
        return text
    if count > 1:
        p = truncate_point_for_length(text, count - 1)
    return text[:p] + '…'


def fill_in(text, sz):
    w = wcswidth(text)
    if w < sz:
        text += ' ' * (sz - w)
    return text


def place_in(text, sz):
    return fill_in(fit_in(text, sz), sz)


def format_func(which):
    def formatted(text):
        fmt = formats[which]
        return '\x1b[' + fmt + 'm' + text + '\x1b[0m'
    formatted.__name__ = which + '_format'
    return formatted


text_format = format_func('text')
title_format = format_func('title')
margin_format = format_func('margin')
added_format = format_func('added')
removed_format = format_func('removed')
removed_margin_format = format_func('removed_margin')
added_margin_format = format_func('added_margin')
filler_format = format_func('filler')


def title_lines(left_path, right_path, args, columns, margin_size):
    name = fit_in(sanitize(path_name_map[left_path]), columns - 2 * margin_size)
    yield title_format(place_in(' ' + name, columns))
    yield title_format('━' * columns)
    yield title_format(' ' * columns)


def binary_lines(path, other_path, columns, margin_size):
    template = _('Binary file: {}')

    def fl(path):
        text = template.format(human_readable(len(data_for_path(path))))
        text = place_in(text, columns // 2 - margin_size)
        return margin_format(' ' * margin_size) + text_format(text)

    return fl(path) + fl(other_path)


def split_to_size(line, width):
    while line:
        p = truncate_point_for_length(line, width)
        yield line[:p]
        line = line[p:]


margin_bg_map = {'filler': filler_format, 'remove': removed_margin_format, 'add': added_margin_format, 'context': margin_format}
text_bg_map = {'filler': filler_format, 'remove': removed_format, 'add': added_format, 'context': text_format}


def render_diff_line(number, text, ltype, margin_size, available_cols):
    margin = margin_bg_map[ltype](place_in(str(number or ''), margin_size))
    content = text_bg_map[ltype](fill_in(text or '', available_cols))
    return margin + content


def render_diff_pair(left_line_number, left, left_is_change, right_line_number, right, right_is_change, is_first, margin_size, available_cols):
    ltype = 'filler' if left_line_number is None else ('remove' if left_is_change else 'context')
    rtype = 'filler' if right_line_number is None else ('add' if right_is_change else 'context')
    return (
            render_diff_line(left_line_number if is_first else None, left, ltype, margin_size, available_cols) +
            render_diff_line(right_line_number if is_first else None, right, rtype, margin_size, available_cols)
    )


def lines_for_diff(left_path, right_path, hunks, args, columns, margin_size):
    available_cols = columns // 2 - margin_size
    left_lines, right_lines = map(lines_for_path, (left_path, right_path))

    for hunk_num, hunk in enumerate(hunks):
        for line_num, (left, right) in enumerate(zip(hunk.left_lines, hunk.right_lines)):
            left_line_number, left_is_change = left
            right_line_number, right_is_change = right
            if left_line_number is None:
                left_wrapped_lines = []
            else:
                left_wrapped_lines = list(split_to_size(left_lines[left_line_number], available_cols))
            if right_line_number is None:
                right_wrapped_lines = []
            else:
                right_wrapped_lines = list(split_to_size(right_lines[right_line_number], available_cols))
            even_up_sides(left_wrapped_lines, right_wrapped_lines, '')
            for i, (left, right) in enumerate(zip(left_wrapped_lines, right_wrapped_lines)):
                yield Line(
                    render_diff_pair(
                        left_line_number, left, left_is_change, right_line_number, right,
                        right_is_change, i == 0, margin_size, available_cols
                    ),
                    Reference(left_path, HunkRef(hunk_num, line_num))
                )


def render_diff(collection, diff_map, args, columns):
    largest_line_number = 0
    for path, item_type, other_path in collection:
        if item_type == 'diff':
            patch = diff_map.get(path)
            if patch is not None:
                largest_line_number = max(largest_line_number, patch.largest_line_number)

    margin_size = max(3, len(str(largest_line_number)) + 1)

    for path, item_type, other_path in collection:
        item_ref = Reference(path)
        if item_type == 'diff':
            yield from yield_lines_from(title_lines(path, other_path, args, columns, margin_size), item_ref)
            is_binary = isinstance(data_for_path(path), bytes)
            if is_binary:
                yield from yield_lines_from(binary_lines(path, other_path, columns, margin_size), item_ref)
            else:
                yield from lines_for_diff(path, other_path, diff_map[path], args, columns, margin_size)
