"""Port of src/utils/date_utils.cc (pretty + verbose dates)."""

from __future__ import annotations

import time as _time
from datetime import datetime


def pretty_date(t: int, same_year_fmt: str = "%b %-e",
                diff_year_fmt: str = "%x", clock_format: str = "local") -> str:
    if t <= 0:
        return ""
    now = datetime.now()
    d = datetime.fromtimestamp(t)

    if d.date() == now.date():
        if clock_format == "24h":
            return d.strftime("%H:%M")
        if clock_format == "12h":
            return d.strftime("%I:%M %p")
        return d.strftime("%X")

    if d.year == now.year:
        return d.strftime(same_year_fmt)
    return d.strftime(diff_year_fmt)


def pretty_verbose_date(t: int, include_short: bool = False) -> str:
    if t <= 0:
        return ""
    d = datetime.fromtimestamp(t)
    full = d.strftime("%c")
    if include_short:
        return full
    return full
