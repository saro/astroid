"""Tag colouring — exact port of src/utils/utils.cc:get_tag_color_rgba and
src/utils/vector_utils.cc:concat_tags_color.

The hue comes from the MD5 digest of the tag name (C++ uses
``Crypto::get_md5_digest_b``). Each channel is mapped through the
``tags_upper_color`` / ``tags_lower_color`` range with the *exact* C++
integer arithmetic — which overflows a guint8 — so colours line up with
the original byte-for-byte::

    bg[k] = (md5[k] * (upper[k] - lower[k]) + lower[k]) & 0xff

The chip is drawn at ``tags_alpha`` over the row/email background; the
foreground is black or near-white depending on the blended luminance
(the C++ luminance has a quirk: the blue term blends against
``canvas[0]`` rather than ``canvas[2]`` — replicated here so the
black/white choice matches).
"""

from __future__ import annotations

import hashlib
import html

# defaults from src/config.cc (thread_index.cell.*), truncated to 8-bit
# exactly as the C++ casts Pango::Color::get_red() (guint16) to guint8.
DEFAULT_UPPER = (0xE5, 0xE5, 0xE5)   # #e5e5e5
DEFAULT_LOWER = (0x33, 0x33, 0x33)   # #333333
DEFAULT_ALPHA = 0.5
WHITE = (255, 255, 255)


def _bg_raw(tag: str, upper, lower) -> tuple[int, int, int]:
    md5 = hashlib.md5(tag.encode("utf-8")).digest()
    return tuple((md5[k] * (upper[k] - lower[k]) + lower[k]) & 0xFF
                 for k in range(3))


def _foreground(bg, canvas, alpha: float) -> str:
    # C++ luminance: note the third term uses canvas[0] (a quirk in the
    # original, kept so the fg choice matches exactly).
    lum = ((bg[0] * alpha + (1 - alpha) * canvas[0]) * 0.2126
           + (bg[1] * alpha + (1 - alpha) * canvas[1]) * 0.7152
           + (bg[2] * alpha + (1 - alpha) * canvas[0]) * 0.0722) / 255.0
    return "#000000" if lum > 0.5 else "#f2f2f2"


def tag_color(tag: str, canvas: tuple[int, int, int] = WHITE,
              alpha: float = DEFAULT_ALPHA,
              upper: tuple[int, int, int] = DEFAULT_UPPER,
              lower: tuple[int, int, int] = DEFAULT_LOWER):
    """Return ``(fg_hex, bg_rgb, fg_for_canvas)``.

    * ``fg_hex`` — "#000000" or "#f2f2f2"
    * ``bg_rgb`` — raw (r, g, b) tuple (0-255) before alpha blending
    """
    bg = _bg_raw(tag, upper, lower)
    fg = _foreground(bg, canvas, alpha)
    return fg, bg


def concat_tags(tags: list[str]) -> str:
    return " ".join(tags)


def concat_tags_color(tags: list[str], pango: bool = True,
                      maxlen: int = 0,
                      canvas: tuple[int, int, int] = WHITE,
                      alpha: float = DEFAULT_ALPHA,
                      upper: tuple[int, int, int] = DEFAULT_UPPER,
                      lower: tuple[int, int, int] = DEFAULT_LOWER) -> str:
    """Markup string of coloured tag chips (port of
    VectorUtils::concat_tags_color).

    For pango (thread index) the chip is ``<span bgcolor="#RRGGBBAA"
    color="fg"> tag </span>`` — Pango honours the 8-digit alpha. For HTML
    (thread view) it is an ``rgba()`` background so the browser blends it
    over the white email background, matching the C++ output.
    """
    out: list[str] = []
    first = True
    length = 0
    broken = False
    alpha_byte = round(alpha * 255)

    for t in tags:
        if not first:
            out.append('<span size="xx-small"> </span>' if pango else " ")
        else:
            first = False

        fg, bg = tag_color(t, canvas, alpha, upper, lower)

        if maxlen > 0:
            broken = True
            if length >= maxlen:
                break
            broken = False
            if length + len(t) + 2 > maxlen:
                t = t[:max(0, maxlen - length - 2)] + ".."
            length += len(t) + 2

        esc = html.escape(t)
        if pango:
            bg_hex = "#%02x%02x%02x%02x" % (bg[0], bg[1], bg[2], alpha_byte)
            out.append(f'<span bgcolor="{bg_hex}" color="{fg}"> {esc} </span>')
        else:
            out.append(
                f'<span style="background-color: '
                f'rgba({bg[0]}, {bg[1]}, {bg[2]}, {alpha}); '
                f'color: {fg} !important; white-space: pre;"> {esc} </span>')

    if broken:
        out.append("..")

    return "".join(out)
