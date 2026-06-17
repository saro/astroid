"""Port of src/actions/onmessage.cc.

These actions run on the ActionManager worker thread: they touch the
database (notmuch ``add``, message-level tag changes) then emit the
corresponding ``message-updated`` / ``thread-updated`` signals on the
GUI thread.
"""

from __future__ import annotations

from pathlib import Path

from ..db import Db, message_add_tag
from ..log import log
from .action import Action


class OnMessageAction(Action):
    """Generic 'open this message and call ``fn(db, msg)`'.

    ``fn`` runs with the live notmuch2 message handle and must not let
    it escape. The action also emits ``message-updated`` and (if
    ``thread_id`` is set) ``thread-updated`` on completion.
    """

    def __init__(self, mid: str, thread_id: str = "", fn=None):
        super().__init__()
        self.mid = mid
        self.thread_id = thread_id
        self.fn = fn
        self.need_db_rw = True

    def undoable(self) -> bool:
        return False

    def doit(self, db) -> bool:
        if self.fn is None:
            return True
        return bool(db.on_message(self.mid, lambda m: self.fn(db, m)))

    def emit(self, db) -> None:
        if self.manager is None:
            return
        self.manager.emit_message_updated(db, self.mid)
        if self.thread_id:
            self.manager.emit_thread_updated(db, self.thread_id)


class AddSentMessage(Action):
    """Index a freshly-saved sent message with the sent tags.

    Also adds ``replied`` to ``in_reply_to`` when that mid is provided
    (replies); ForwardMessage queues a sibling that adds ``passed``
    instead — see ``AddPassedTag`` below.
    """

    REPLIED_TAG = "replied"

    def __init__(self, fname: str | Path, extra_tags=None,
                 in_reply_to: str = "", source_tag: str = REPLIED_TAG):
        super().__init__()
        self.fname = str(fname)
        self.extra_tags = list(extra_tags or [])
        self.in_reply_to = in_reply_to
        self.source_tag = source_tag
        self.need_db_rw = True

    def undoable(self) -> bool:
        return False

    def doit(self, db) -> bool:
        try:
            msg, _ = db.nm_db.add(self.fname, sync_flags=True)
        except Exception as e:
            log.error("actions: AddSentMessage: notmuch add failed: %s", e)
            return False

        with msg.frozen():
            for t in Db.sent_tags:
                msg.tags.add(t)
            for t in self.extra_tags:
                if t:
                    msg.tags.add(t)
            msg.tags.discard("unread")
        if Db.maildir_synchronize_flags:
            msg.tags.to_maildir_flags()
        del msg

        if self.in_reply_to and self.source_tag:
            message_add_tag(db, self.in_reply_to, self.source_tag)
        return True

    def emit(self, db) -> None:
        if self.manager is None:
            return
        if self.in_reply_to:
            self.manager.emit_message_updated(db, self.in_reply_to)


class AddDraftMessage(Action):
    """Index a freshly-saved draft. Applies ``Db.draft_tags`` and clears
    ``unread`` (the user just wrote it, so it isn't unread)."""

    def __init__(self, fname: str | Path):
        super().__init__()
        self.fname = str(fname)
        self.need_db_rw = True

    def undoable(self) -> bool:
        return False

    def doit(self, db) -> bool:
        try:
            msg, _ = db.nm_db.add(self.fname, sync_flags=True)
        except Exception as e:
            log.error("actions: AddDraftMessage: notmuch add failed: %s", e)
            return False

        with msg.frozen():
            for t in Db.draft_tags:
                msg.tags.add(t)
            msg.tags.discard("unread")
        if Db.maildir_synchronize_flags:
            msg.tags.to_maildir_flags()
        del msg
        return True

    def emit(self, db) -> None:
        pass


class RemoveMessage(Action):
    """Delete the file backing a message and remove it from notmuch."""

    def __init__(self, fname: str | Path):
        super().__init__()
        self.fname = Path(fname)
        self.need_db_rw = True

    def undoable(self) -> bool:
        return False

    def doit(self, db) -> bool:
        try:
            if self.fname.is_file():
                self.fname.unlink()
        except OSError as e:
            log.warning("actions: RemoveMessage: unlink: %s", e)

        try:
            db.nm_db.remove(str(self.fname))
        except Exception as e:
            log.error("actions: RemoveMessage: notmuch remove failed: %s", e)
            return False
        return True

    def emit(self, db) -> None:
        pass
