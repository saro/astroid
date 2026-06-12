"""Port of src/actions/action_manager.cc.

A single worker thread executes queued Actions (each opening its own
read-only or read-write Db), keeps an undo stack, and signals the GUI
thread (via GLib.idle_add) where completed actions emit their signals:

    thread-updated (thread_id)   full thread refresh
    thread-changed (thread_id)   thread metadata only
    message-updated (mid)        message tags/flags
    refreshed ()                 poll done, reload everything
"""

from __future__ import annotations

import threading
from collections import deque

from gi.repository import GLib, GObject

from ..db import Db
from ..log import log
from .action import Action


class ActionManager(GObject.Object):
    __gsignals__ = {
        "thread-updated": (GObject.SignalFlags.RUN_FIRST, None, (object, str)),
        "thread-changed": (GObject.SignalFlags.RUN_FIRST, None, (object, str)),
        "message-updated": (GObject.SignalFlags.RUN_FIRST, None, (object, str)),
        "refreshed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "poll-state": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self, dispatch=GLib.idle_add):
        super().__init__()
        log.info("global actions: set up.")

        # dispatch schedules a callable on the GUI thread; injectable for tests
        self._dispatch = dispatch

        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)

        self._actions: deque[Action] = deque()
        self._doneactions: deque[Action] = deque()
        self._toemit: deque[Action] = deque()
        self._emit_lock = threading.Lock()

        self._emit_enabled = True
        self._run = True
        self._worker = threading.Thread(target=self._action_worker,
                                        name="actions", daemon=True)
        self._worker.start()

    # -- queueing -------------------------------------------------------------

    def doit(self, action: Action, undoable: bool | None = None) -> None:
        if undoable is not None:
            action.skip_undo = not undoable
        with self._cv:
            self._actions.append(action)
            self._cv.notify()

    def undo(self) -> None:
        log.info("actions: undo")
        with self._cv:
            if self._actions:
                log.info("actions: action still in queue, removing..")
                a = self._actions[-1]
                if not a.in_undo and not a.skip_undo:
                    self._actions.pop()
                return

        if not self._doneactions:
            log.debug("actions: no more actions to undo.")
            return

        a = self._doneactions.pop()
        a.in_undo = True
        self.doit(a)

    # -- worker -------------------------------------------------------------

    def _action_worker(self) -> None:
        while True:
            with self._cv:
                self._cv.wait_for(lambda: self._actions or not self._run)
                if not self._run and not self._actions:
                    return

            while True:
                with self._cv:
                    if not self._actions:
                        break
                    a = self._actions.popleft()

                a.manager = self

                db = None
                try:
                    if a.need_db:
                        mode = Db.READ_WRITE if a.need_db_rw else Db.READ_ONLY
                        db = Db(mode)
                    elif a.need_db_rw:
                        Db._acquire_rw()
                    else:
                        Db._acquire_ro()

                    try:
                        if not a.in_undo:
                            a.doit(db)
                        else:
                            a.undo(db)
                    finally:
                        if db is not None:
                            db.close()
                        elif a.need_db_rw:
                            Db._release_rw()
                        else:
                            Db._release_ro()
                except Exception as e:
                    log.error("actions: action failed: %s", e)
                    continue

                if not a.in_undo and a.undoable() and not a.skip_undo:
                    self._doneactions.append(a)

                if self._emit_enabled:
                    with self._emit_lock:
                        self._toemit.append(a)

            self._dispatch(self._emitter)

    def _emitter(self) -> bool:
        """Runs on the GUI thread: emit signals for completed actions."""
        if not self._emit_enabled:
            return False
        while True:
            with self._emit_lock:
                if not self._toemit:
                    break
                a = self._toemit.popleft()

            with Db(Db.READ_ONLY) as db:
                try:
                    a.emit(db)
                except Exception as e:
                    log.error("actions: emit failed: %s", e)
        return False  # one-shot idle callback

    def close(self) -> None:
        if not self._run:
            return
        log.debug("actions: cleaning up remaining actions..")
        self._emit_enabled = False
        with self._cv:
            self._run = False
            self._cv.notify()
        self._worker.join(timeout=30)

    # -- signal emission (runs on GUI thread) -----------------------------------

    def emit_thread_updated(self, db, thread_id: str) -> None:
        log.info("actions: emitted updated and changed signal for thread: %s",
                 thread_id)
        self.emit("thread-updated", db, thread_id)
        self.emit("thread-changed", db, thread_id)

    def emit_thread_changed(self, db, thread_id: str) -> None:
        log.info("actions: emitted changed signal for thread: %s", thread_id)
        self.emit("thread-changed", db, thread_id)

    def emit_message_updated(self, db, mid: str) -> None:
        log.info("actions: emitted updated signal for message: %s", mid)
        self.emit("message-updated", db, mid)

        def get_tid(m):
            return m.threadid if m is not None else None

        tid = db.on_message(mid, get_tid)
        if tid:
            self.emit_thread_changed(db, tid)

    def emit_refreshed(self) -> None:
        log.info("actions: emitted refreshed signal.")
        self.emit("refreshed")
