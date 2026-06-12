"""Ports of src/utils/utils.cc helpers needed across the app."""

from __future__ import annotations

import os
import random
import re
import string
from pathlib import Path


def expand(p: str | Path) -> Path:
    """Port of Utils::expand: ~ and $VAR expansion."""
    return Path(os.path.expandvars(os.path.expanduser(str(p))))


def format_size(n: int) -> str:
    """Port of Utils::format_size (same thresholds/format as glib)."""
    for unit, factor in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("kB", 1024)):
        if n >= factor:
            return f"{n / factor:.1f} {unit}"
    return f"{n} bytes" if n != 1 else "1 byte"


_fname_bad = re.compile(r"[^0-9A-Za-z.\-]")


def safe_fname(fname: str) -> str:
    """Port of Utils::safe_fname: keep alnum/dot/dash, the rest -> '-'."""
    return _fname_bad.sub("-", fname)


def random_alphanumeric(length: int) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))
