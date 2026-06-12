"""boost::property_tree-compatible JSON access.

The C++ implementation stores its configuration and the saved-searches file
as JSON written by boost::property_tree, which has two quirks we must
preserve so both implementations can share the same files:

* every leaf is written as a JSON string ("true", "2", "0.5"), and
* values are addressed by dotted paths ("astroid.config.version").

PTree wraps a nested dict and reproduces the path API plus boost's
string<->type coercions. Writing serializes all leaves back to strings.
"""

from __future__ import annotations

import json
from typing import Any, Iterator


class PTreeBadPath(KeyError):
    pass


class PTree:
    def __init__(self, data: dict | None = None):
        self.data: dict = data if data is not None else {}

    # -- path helpers -----------------------------------------------------

    @staticmethod
    def _split(path: str) -> list[str]:
        return path.split(".") if path else []

    def _walk(self, path: str, create: bool) -> tuple[dict, str]:
        """Return (parent dict, final key) for path."""
        parts = self._split(path)
        if not parts:
            raise PTreeBadPath("empty path")
        node = self.data
        for p in parts[:-1]:
            nxt = node.get(p)
            if not isinstance(nxt, dict):
                if not create:
                    raise PTreeBadPath(path)
                nxt = {}
                node[p] = nxt
            node = nxt
        return node, parts[-1]

    # -- get/put ----------------------------------------------------------

    def put(self, path: str, value: Any) -> None:
        node, key = self._walk(path, create=True)
        node[key] = value

    def get(self, path: str, default: Any = ...) -> Any:
        try:
            node, key = self._walk(path, create=False)
            if key not in node:
                raise PTreeBadPath(path)
            v = node[key]
            if isinstance(v, dict):
                raise PTreeBadPath(path)
            return v
        except PTreeBadPath:
            if default is ...:
                raise
            return default

    def get_str(self, path: str, default: Any = ...) -> str:
        v = self.get(path, default)
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)

    def get_bool(self, path: str, default: Any = ...) -> bool:
        v = self.get(path, default)
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in ("true", "1"):
            return True
        if s in ("false", "0"):
            return False
        raise ValueError(f"{path}: not a boolean: {v!r}")

    def get_int(self, path: str, default: Any = ...) -> int:
        v = self.get(path, default)
        if isinstance(v, bool):
            return int(v)
        return int(str(v).strip())

    def get_float(self, path: str, default: Any = ...) -> float:
        v = self.get(path, default)
        return float(str(v).strip())

    # -- children ---------------------------------------------------------

    def get_child(self, path: str) -> "PTree":
        node, key = self._walk(path, create=False)
        v = node.get(key)
        if not isinstance(v, dict):
            raise PTreeBadPath(path)
        return PTree(v)

    def get_child_optional(self, path: str) -> "PTree | None":
        try:
            return self.get_child(path)
        except PTreeBadPath:
            return None

    def put_child(self, path: str, child: "PTree") -> None:
        node, key = self._walk(path, create=True)
        node[key] = child.data

    def items(self) -> Iterator[tuple[str, Any]]:
        return iter(self.data.items())

    def __contains__(self, path: str) -> bool:
        try:
            node, key = self._walk(path, create=False)
            return key in node
        except PTreeBadPath:
            return False

    def __len__(self) -> int:
        return len(self.data)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, PTree) and self.data == other.data

    # -- merge (port of Config::merge_ptree) -------------------------------

    def merge(self, other: "PTree") -> None:
        """Overlay other on top of self, leaf by leaf (boost traversal)."""

        def rec(dst: dict, src: dict) -> None:
            for k, v in src.items():
                if isinstance(v, dict):
                    cur = dst.get(k)
                    if not isinstance(cur, dict):
                        cur = {}
                        dst[k] = cur
                    rec(cur, v)
                else:
                    dst[k] = v

        rec(self.data, other.data)

    # -- io -----------------------------------------------------------------

    @staticmethod
    def _stringify(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: PTree._stringify(v) for k, v in node.items()}
        if isinstance(node, bool):
            return "true" if node else "false"
        if isinstance(node, float):
            # match boost lexical_cast-ish output for common cases
            s = repr(node)
            return s
        return str(node)

    def dumps(self) -> str:
        return json.dumps(self._stringify(self.data), indent=4) + "\n"

    def write(self, path) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.dumps())

    @classmethod
    def loads(cls, s: str) -> "PTree":
        return cls(json.loads(s))

    @classmethod
    def read(cls, path) -> "PTree":
        with open(path, encoding="utf-8") as f:
            return cls.loads(f.read())


def read_ini(path) -> PTree:
    """Minimal INI reader matching boost::property_tree::read_ini usage
    for the notmuch config ([section] key=value, ';'/'#' comments)."""
    tree = PTree()
    section = ""
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip()
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                key = f"{section}.{k.strip()}" if section else k.strip()
                tree.put(key, v.strip())
    return tree
