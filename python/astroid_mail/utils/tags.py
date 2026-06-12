"""Port of the tag colouring in src/utils/vector_utils.cc.

Tags get a deterministic background colour from a hash of the tag name,
blended against the canvas colour with the configured alpha.
"""

from __future__ import annotations

import hashlib
import html


def tag_color(tag: str, canvas: tuple[int, int, int] = (255, 255, 255),
              alpha: float = 0.5,
              upper: tuple[int, int, int] = (229, 229, 229),
              lower: tuple[int, int, int] = (51, 51, 51)) -> tuple[str, str]:
    """Return (foreground, background) hex colours for tag (port of
    VectorUtils::get_tag_color_rgba)."""
    d = hashlib.sha1(tag.encode()).digest()
    base = (d[0], d[1], d[2])

    # clamp to the upper/lower range
    rgb = tuple(min(u, max(l, c)) for c, u, l in zip(base, upper, lower))

    # blend on canvas with alpha
    bg = tuple(int(alpha * c + (1 - alpha) * k)
               for c, k in zip(rgb, canvas))

    # foreground: black or white depending on luminance
    lum = 0.2126 * bg[0] + 0.7152 * bg[1] + 0.0722 * bg[2]
    fg = "#000000" if lum > 128 else "#ffffff"

    return fg, "#%02x%02x%02x" % bg


def concat_tags(tags: list[str]) -> str:
    return ", ".join(tags)


def concat_tags_color(tags: list[str], pango: bool = True,
                      maxlen: int = 0,
                      canvas: tuple[int, int, int] = (255, 255, 255)) -> str:
    """Markup string of coloured tags (pango for the row widget, html-ish
    span for the thread view)."""
    out = []
    length = 0
    for t in tags:
        if maxlen > 0 and length >= maxlen:
            out.append("..")
            break
        fg, bg = tag_color(t, canvas)
        esc = html.escape(t)
        if pango:
            out.append(f'<span bgcolor="{bg}" color="{fg}"> {esc} </span>')
        else:
            out.append(f'<span style="background-color: {bg}; color: {fg}; '
                       f'white-space: pre;"> {esc} </span>')
        length += len(t) + 1
    return " ".join(out)
