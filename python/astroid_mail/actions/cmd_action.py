"""Port of src/actions/cmdaction.cc — shell hooks from run() keybindings."""

from __future__ import annotations

from ..log import log
from ..utils.cmd import Cmd
from .action import Action


class CmdAction(Action):
    def __init__(self, cmd: Cmd, thread_id: str = "", mid: str = ""):
        super().__init__()
        self.cmd = cmd
        self.thread_id = thread_id
        self.mid = mid
        self.need_db = False
        self.need_db_rw = True   # hook may modify the db: hold the write lock
        self.successful = False

    def undoable(self) -> bool:
        return self.cmd.undoable()

    def doit(self, db) -> bool:
        self.successful = self.cmd.run()
        return self.successful

    def undo(self, db) -> bool:
        return self.cmd.undo()

    def emit(self, db) -> None:
        if not self.successful or self.manager is None:
            return
        if self.thread_id:
            # also causes all messages in the thread to be updated
            self.manager.emit_thread_updated(db, self.thread_id)
            return
        if self.mid:
            self.manager.emit_message_updated(db, self.mid)
        log.debug("cmdaction: emitted")
