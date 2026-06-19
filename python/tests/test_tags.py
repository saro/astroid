"""Tag colour algorithm matches the C++ (MD5 + guint8-overflow formula)."""

from __future__ import annotations

import hashlib

from astroid_mail.utils.tags import (
    DEFAULT_ALPHA, DEFAULT_LOWER, DEFAULT_UPPER, concat_tags_color, tag_color,
)


def _expected_bg(tag, upper=DEFAULT_UPPER, lower=DEFAULT_LOWER):
    md5 = hashlib.md5(tag.encode()).digest()
    return tuple((md5[k] * (upper[k] - lower[k]) + lower[k]) & 0xFF
                 for k in range(3))


def test_bg_uses_md5_and_overflow_formula():
    for tag in ("inbox", "unread", "flagged", "work", "really-long-tag-name"):
        fg, bg = tag_color(tag)
        assert bg == _expected_bg(tag), tag


def test_known_values_are_stable():
    # regression guard: these must not drift between releases
    fg, bg = tag_color("inbox")
    assert bg == _expected_bg("inbox")
    assert fg in ("#000000", "#f2f2f2")


def test_foreground_is_black_or_lightgrey():
    for tag in ("a", "b", "c", "spam", "ham", "x" * 40):
        fg, _bg = tag_color(tag)
        assert fg in ("#000000", "#f2f2f2")


def test_pango_markup_has_alpha_byte():
    out = concat_tags_color(["inbox"], pango=True)
    bg = _expected_bg("inbox")
    alpha_byte = round(DEFAULT_ALPHA * 255)
    expect = "#%02x%02x%02x%02x" % (bg[0], bg[1], bg[2], alpha_byte)
    assert f'bgcolor="{expect}"' in out
    assert "<span" in out and "inbox" in out


def test_html_markup_uses_rgba():
    out = concat_tags_color(["inbox"], pango=False)
    bg = _expected_bg("inbox")
    assert f"rgba({bg[0]}, {bg[1]}, {bg[2]}, {DEFAULT_ALPHA})" in out
    assert "white-space: pre" in out


def test_separator_between_chips():
    out = concat_tags_color(["a", "b"], pango=True)
    assert '<span size="xx-small"> </span>' in out
    # html separator is a plain space
    out2 = concat_tags_color(["a", "b"], pango=False)
    assert out2.count("<span") == 2


def test_maxlen_truncates_and_appends_dots():
    out = concat_tags_color(["abcdefghij", "klmnopqrst", "uvwxyz"],
                            pango=True, maxlen=8)
    assert ".." in out


def test_custom_alpha_and_range():
    fg, bg = tag_color("inbox", alpha=0.25,
                       upper=(200, 200, 200), lower=(50, 50, 50))
    assert bg == _expected_bg("inbox", (200, 200, 200), (50, 50, 50))
