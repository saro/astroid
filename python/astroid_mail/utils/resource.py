"""Port of src/utils/resource.cc — locate ui/data files.

Search order for a resource like "ui/thread-view.html":
1. user config dir + full relative path (~/.config/astroid/ui/thread-view.html)
   — only when the resource is user-configurable
2. $ASTROID_DIR + relative path (development: point at a source checkout)
3. relative to the current directory (development convenience)
4. installed prefix share/astroid/
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


class Resource:
    # set by Config at startup
    config_dir: Path | None = None

    @staticmethod
    def _prefix_candidates() -> list[Path]:
        cands = [Path(sys.prefix) / "share" / "astroid"]
        for p in ("/usr/local/share/astroid", "/usr/share/astroid"):
            if Path(p) not in cands:
                cands.append(Path(p))
        return cands

    @classmethod
    def find(cls, user_configurable: bool, rel: str | Path) -> Path:
        rel = Path(rel)

        if user_configurable and cls.config_dir is not None:
            cand = cls.config_dir / rel
            if cand.is_file():
                return cand

        astroid_dir = os.environ.get("ASTROID_DIR")
        if astroid_dir:
            cand = Path(astroid_dir) / rel
            if cand.is_file():
                return cand

        cand = Path.cwd() / rel
        if cand.is_file():
            return cand

        for prefix in cls._prefix_candidates():
            cand = prefix / rel
            if cand.is_file():
                return cand

        raise FileNotFoundError(f"resource not found: {rel}")
