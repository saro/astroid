"""Ports of src/actions/tag_action.cc, toggle_action.cc, difftag_action.cc.

Taggables are ThreadSummary or MessageSummary objects from db.py.
"""

from __future__ import annotations

from ..db import (Db, MessageSummary, ThreadSummary,
                  message_add_tag, message_remove_tag,
                  thread_add_tag, thread_remove_tag)
from ..log import log
from .action import Action


def _add_tag(db: Db, item, tag: str) -> bool:
    if isinstance(item, ThreadSummary):
        ok = thread_add_tag(db, item.thread_id, tag)
    else:
        ok = message_add_tag(db, item.mid, tag)
    if ok and tag not in item.tags:
        item.tags.append(tag)
        item.tags.sort()
    return ok


def _remove_tag(db: Db, item, tag: str) -> bool:
    if isinstance(item, ThreadSummary):
        ok = thread_remove_tag(db, item.thread_id, tag)
    else:
        ok = message_remove_tag(db, item.mid, tag)
    if ok and tag in item.tags:
        item.tags.remove(tag)
    return ok


def _emit_updated(manager, db: Db, item) -> None:
    if manager is None:
        return
    if isinstance(item, ThreadSummary):
        manager.emit_thread_updated(db, item.thread_id)
    else:
        manager.emit_message_updated(db, item.mid)


class TagAction(Action):
    def __init__(self, taggables, add=None, remove=None):
        super().__init__()
        if isinstance(taggables, (ThreadSummary, MessageSummary)):
            taggables = [taggables]
        self.taggables = list(taggables)
        self.add = list(add or [])
        self.remove = list(remove or [])

    def undoable(self) -> bool:
        return True

    def doit(self, db) -> bool:
        res = True
        for tagged in self.taggables:
            log.info("tag_action: %s", tagged)
            for t in self.add:
                res &= _add_tag(db, tagged, t)
            for t in self.remove:
                res &= _remove_tag(db, tagged, t)
        return res

    def undo(self, db) -> bool:
        log.info("tag_action: undo.")
        self.add, self.remove = self.remove, self.add
        return self.doit(db)

    def emit(self, db) -> None:
        for t in self.taggables:
            _emit_updated(self.manager, db, t)


class ToggleAction(TagAction):
    def __init__(self, taggables, toggle_tag: str):
        super().__init__(taggables)
        self.toggle_tag = toggle_tag

    def doit(self, db) -> bool:
        res = True
        for tagged in self.taggables:
            log.debug("toggle_action: %s", tagged)
            if self.toggle_tag in tagged.tags:
                res &= _remove_tag(db, tagged, self.toggle_tag)
            else:
                res &= _add_tag(db, tagged, self.toggle_tag)
        return res

    # undo of a toggle is toggling again: TagAction.undo swaps the (empty)
    # add/remove lists and calls doit, which re-derives from current tags


class SpamAction(ToggleAction):
    def __init__(self, taggables):
        super().__init__(taggables, "spam")


class MuteAction(ToggleAction):
    def __init__(self, taggables):
        super().__init__(taggables, "muted")


class DiffTagAction(TagAction):
    """Apply a '+tag -tag' diff specification to multiple items."""

    @classmethod
    def create(cls, taggables, diff_str: str) -> "DiffTagAction | None":
        log.debug("difftag: parsing: %s", diff_str)

        if "," in diff_str:
            log.error("difftag: ',' not allowed, use ' ' to separate tags")
            return None

        add: list[str] = []
        remove: list[str] = []
        for t in (x.strip() for x in diff_str.split(" ")):
            if not t:
                continue
            if t.startswith("-"):
                remove.append(t[1:])
            elif t.startswith("+"):
                add.append(t[1:])
            else:
                add.append(t)

        if not add and not remove:
            log.debug("difftag: nothing to do.")
            return None

        return cls(taggables, add, remove)

    def __init__(self, taggables, add, remove):
        super().__init__(taggables)
        add = sorted(add)
        remove = sorted(remove)

        # per-item: only remove tags it has, only add tags it lacks
        self.taggable_actions = []
        for t in self.taggables:
            ta_remove = [x for x in remove if x in t.tags]
            ta_add = [x for x in add if x not in t.tags]
            if ta_add or ta_remove:
                self.taggable_actions.append((t, ta_add, ta_remove))

    def doit(self, db) -> bool:
        res = True
        for tagged, add, remove in self.taggable_actions:
            for t in add:
                res &= _add_tag(db, tagged, t)
            for t in remove:
                res &= _remove_tag(db, tagged, t)
        return res

    def undo(self, db) -> bool:
        log.info("difftag: undo")
        self.taggable_actions = [(t, rem, add)
                                 for t, add, rem in self.taggable_actions]
        return self.doit(db)

    def emit(self, db) -> None:
        for t, _a, _r in self.taggable_actions:
            _emit_updated(self.manager, db, t)
