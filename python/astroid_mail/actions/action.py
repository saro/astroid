"""Port of src/actions/action.hh."""

from __future__ import annotations


class Action:
    def __init__(self):
        self.need_db = True
        self.need_db_rw = True
        self.in_undo = False
        self.skip_undo = False
        # set by ActionManager before doit so emit() can reach the signals
        self.manager = None

    def undoable(self) -> bool:
        return False

    def doit(self, db) -> bool:
        raise NotImplementedError

    def undo(self, db) -> bool:
        raise NotImplementedError

    def emit(self, db) -> None:
        raise NotImplementedError
