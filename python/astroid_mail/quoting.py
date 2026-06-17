"""Shared quoting helpers for reply/forward.

quote_line substitution is intentionally done via Glib.DateTime.format so
strftime-style ``%``-escapes in ``mail.reply.quote_line`` keep working
(see src/modes/reply_message.cc:53-54). %% pairs from the user's body /
sender name are preserved by escaping them before formatting, matching
the C++ ``boost::replace_all_copy (..., "%", "%%")``.
"""

from __future__ import annotations

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402


def format_quote_line(template: str, author: str, pretty_date: str,
                      timestamp: int) -> str:
    """Compose-then-format the quote line.

    Order matches C++:
      1. ``ustring::compose(template, author', date')`` where author/date
         are first escaped ``%`` → ``%%``.
      2. ``Glib.DateTime.format(result_at_timestamp)``.
    """
    safe_author = author.replace("%", "%%")
    safe_date = pretty_date.replace("%", "%%")
    # ustring::compose substitutes %1 / %2 ... in order.
    composed = (template
                .replace("%1", safe_author)
                .replace("%2", safe_date))

    dt = GLib.DateTime.new_from_unix_local(int(timestamp))
    formatted = dt.format(composed)
    return formatted if formatted is not None else composed


def prefix_quote(text: str) -> str:
    """Line-by-line ``>`` prefix; add a space when the line doesn't already
    start with ``>`` (matches src/modes/reply_message.cc:60-69 verbatim).

    Preserves the trailing-newline of the input.
    """
    if not text:
        return ""

    # split keeping a flag for whether the original ended with newline
    ends_nl = text.endswith("\n")
    lines = text.split("\n")
    if ends_nl:
        # split() above yields an empty trailing entry; drop it because the
        # quoting loop ends with a final "\n" we add explicitly
        lines = lines[:-1]

    out = []
    for line in lines:
        if line.startswith(">"):
            out.append(">" + line)
        else:
            out.append("> " + line)
    return "\n".join(out) + ("\n" if ends_nl else "")
