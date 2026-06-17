"""Port of src/modes/saved_searches.cc (model + persistence).

The file lives at ``$XDG_CONFIG_HOME/astroid/searches`` and is shared
verbatim with the C++ implementation. It is a boost::property_tree JSON
dump, which crucially allows **duplicate keys** in the same object —
that's how the C++ stores the history list (every entry has key
``none``). Standard ``json.loads`` would collapse those; we use an
``object_pairs_hook`` to preserve every entry.

Format::

    {
      "saved":   { "<name>": "<query>", ... },     # may contain "none"
      "history": { "none": "<query>", ... }        # always many "none"s
    }

When we add to ``saved`` we use a unique name when one is given (the
common case for ``s`` in the GUI), and fall back to ``none`` to match
C++ when no name is supplied. History entries always use ``none``.
"""

from __future__ import annotations

import json
from pathlib import Path

from .log import log

PREFIX_SECTIONS = ("saved", "history")


def _parse_pairs(pairs):
    """object_pairs_hook that keeps duplicate-key entries as a list of pairs
    under a private '_dups' attribute, while preserving the canonical dict
    for normal access. Implemented as a tiny wrapper class."""
    return _OrderedMultiObject(pairs)


class _OrderedMultiObject(list):
    """A list-of-pairs that quacks like a dict for simple lookups."""

    def __init__(self, pairs):
        super().__init__(pairs)

    def get(self, key, default=None):
        for k, v in self:
            if k == key:
                return v
        return default


def load_searches(path: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """Read the searches file.

    Returns ``(saved, history)`` where ``saved`` is a list of
    ``(name, query)`` tuples (preserving duplicate names — the C++ stores
    them as duplicate ``none`` keys) and ``history`` is a list of query
    strings (most-recent first, matching what the GUI displays).
    """
    if not path.is_file():
        return [], []

    try:
        text = path.read_text(encoding="utf-8")
        root = json.loads(text, object_pairs_hook=_parse_pairs)
    except (OSError, ValueError) as e:
        log.warning("searches: could not load %s: %s", path, e)
        return [], []

    saved: list[tuple[str, str]] = []
    history: list[str] = []

    if isinstance(root, _OrderedMultiObject):
        for sect, value in root:
            if sect == "saved" and isinstance(value, _OrderedMultiObject):
                for name, q in value:
                    if isinstance(q, str):
                        saved.append((name, q))
            elif sect == "history" and isinstance(value, _OrderedMultiObject):
                for _name, q in value:
                    if isinstance(q, str):
                        history.append(q)

    return saved, history


def write_searches(path: Path, saved: list[tuple[str, str]],
                   history: list[str]) -> None:
    """Write the searches file with the same shape boost::property_tree
    produces: every leaf is a quoted JSON string, history entries all
    share key ``"none"``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    out = ["{"]

    def fmt(name: str, q: str, last: bool) -> str:
        n = json.dumps(name)
        v = json.dumps(q)
        return f"        {n}: {v}" + ("" if last else ",")

    out.append('    "saved": {')
    if saved:
        lines = [fmt(n, q, i == len(saved) - 1) for i, (n, q) in enumerate(saved)]
        out.extend(lines)
    out.append("    },")

    out.append('    "history": {')
    if history:
        lines = [fmt("none", q, i == len(history) - 1)
                 for i, q in enumerate(history)]
        out.extend(lines)
    out.append("    }")

    out.append("}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


# -- model ----------------------------------------------------------------

class SavedSearchesStore:
    """Aggregates the saved + history lists, with history cap & persistence."""

    def __init__(self, config, path: Path | None = None):
        self.config = config
        self.path = path or config.std_paths.searches_file
        self.saved: list[tuple[str, str]] = []
        self.history: list[str] = []

    def load(self) -> None:
        self.saved, self.history = load_searches(self.path)

    def save(self) -> None:
        cfg = self.config.config
        if not cfg.get_bool("saved_searches.save_history"):
            # only persist the saved-list; drop history on disk
            write_searches(self.path, self.saved, [])
            return

        maxh = cfg.get_int("saved_searches.history_lines")
        history = self.history[:maxh] if maxh > 0 else list(self.history)
        write_searches(self.path, self.saved, history)

    # -- mutations ---------------------------------------------------------

    def add_saved(self, query: str, name: str = "") -> None:
        # to match C++ (s.add ("saved.none", q)) when no name provided
        key = name or "none"
        self.saved.append((key, query))

    def remove_saved(self, query: str) -> bool:
        for i, (_n, q) in enumerate(self.saved):
            if q == query:
                del self.saved[i]
                return True
        return False

    def push_history(self, query: str) -> None:
        # most-recent-first; dedupe (move-to-front)
        try:
            self.history.remove(query)
        except ValueError:
            pass
        self.history.insert(0, query)

    def clear_history(self) -> None:
        self.history = []
