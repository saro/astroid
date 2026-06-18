"""Port of src/modes/thread_index/query_loader.cc.

Streams ThreadSummary objects from a background thread into a
Gio.ListStore in batches; thread-changed signals arriving while loading
are deferred and replayed when done.
"""

from __future__ import annotations

import queue
import threading

import notmuch2
from gi.repository import GLib, GObject

from ...db import Db, ThreadSummary
from ...log import log

CHUNK = 100

_SORT_BY_NAME = {
    "newest": notmuch2.Database.SORT.NEWEST_FIRST,
    "oldest": notmuch2.Database.SORT.OLDEST_FIRST,
    "message_id": notmuch2.Database.SORT.MESSAGE_ID,
    "unsorted": notmuch2.Database.SORT.UNSORTED,
}


def sort_from_name(name: str) -> notmuch2.Database.SORT:
    return _SORT_BY_NAME.get((name or "newest").strip().lower(),
                             notmuch2.Database.SORT.NEWEST_FIRST)


class ThreadItem(GObject.Object):
    """GObject wrapper so summaries can live in a Gio.ListStore."""

    def __init__(self, summary: ThreadSummary):
        super().__init__()
        self.summary = summary


class QueryLoader(GObject.Object):
    __gsignals__ = {
        "stats-ready": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "first-thread-ready": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "done": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, store, dispatch=GLib.idle_add,
                 sort: notmuch2.Database.SORT
                 = notmuch2.Database.SORT.NEWEST_FIRST):
        super().__init__()
        self.store = store          # Gio.ListStore of ThreadItem
        self._dispatch = dispatch
        self.sort = sort

        self.query = ""
        self.total_messages = 0
        self.unread_messages = 0

        self.loading = False
        self._run = False
        self._thread: threading.Thread | None = None
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        self._deferred_changed: list[str] = []
        self._lock = threading.Lock()

    def start(self, query: str) -> None:
        if self.loading:
            self.stop()
        self.query = query
        self.loading = True
        self._run = True
        self._thread = threading.Thread(target=self._loader, name="query_loader",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._run = False
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None
        self.loading = False

    def refine_query(self, query: str) -> None:
        self.store.remove_all()
        self.start(query)

    # -- background --------------------------------------------------------------

    def _loader(self) -> None:
        try:
            with Db(Db.READ_ONLY) as db:
                self.total_messages = db.count_messages(self.query)
                self.unread_messages = db.count_messages(
                    f"({self.query}) AND tag:unread")
                self._dispatch(self._emit_stats)

                first = True
                batch: list[ThreadSummary] = []
                for t in db.threads(self.query, sort=self.sort):
                    if not self._run:
                        break
                    batch.append(ThreadSummary.from_notmuch(t))
                    del t

                    if len(batch) >= CHUNK:
                        self._queue.put(batch)
                        batch = []
                        self._dispatch(self._drain)
                        if first:
                            first = False
                            self._dispatch(self._emit_first)

                if batch:
                    self._queue.put(batch)
                    self._dispatch(self._drain)
                    if first:
                        self._dispatch(self._emit_first)
        except Exception as e:
            log.error("ql: loader failed: %s", e)
        finally:
            self._dispatch(self._finish)

    # -- GUI thread -----------------------------------------------------------------

    def _drain(self) -> bool:
        items = []
        while True:
            try:
                batch = self._queue.get_nowait()
            except queue.Empty:
                break
            items.extend(ThreadItem(s) for s in batch)
        if items:
            self.store.splice(self.store.get_n_items(), 0, items)
        return False

    def _emit_stats(self) -> bool:
        self.emit("stats-ready")
        return False

    def _emit_first(self) -> bool:
        self.emit("first-thread-ready")
        return False

    def _finish(self) -> bool:
        self._drain()
        self.loading = False

        with self._lock:
            deferred = self._deferred_changed[:]
            self._deferred_changed.clear()
        if deferred:
            log.debug("ql: processing %s deferred thread changes", len(deferred))
            with Db(Db.READ_ONLY) as db:
                for tid in deferred:
                    self.on_thread_changed(db, tid)

        self.emit("done")
        return False

    # -- updates ----------------------------------------------------------------

    def _find_index(self, thread_id: str) -> int:
        for i in range(self.store.get_n_items()):
            if self.store.get_item(i).summary.thread_id == thread_id:
                return i
        return -1

    def on_thread_changed(self, db: Db, thread_id: str) -> None:
        """Refresh, add or remove one thread (port of on_thread_changed)."""
        if self.loading:
            with self._lock:
                self._deferred_changed.append(thread_id)
            return

        i = self._find_index(thread_id)

        # does the thread match the query?
        in_query = db.count_threads(
            f"({self.query}) AND thread:{thread_id}") > 0

        if i >= 0 and not in_query:
            self.store.remove(i)
        elif i >= 0:
            item = self.store.get_item(i)
            item.summary.refresh(db)
            self.store.items_changed(i, 1, 1) if False else None
            # trigger row re-bind by replace
            self.store.splice(i, 1, [ThreadItem(item.summary)])
        elif in_query:
            def grab(t):
                return ThreadSummary.from_notmuch(t) if t is not None else None
            ts = db.on_thread(thread_id, grab)
            if ts is not None:
                pos = self.store.get_n_items()
                if self.sort == notmuch2.Database.SORT.OLDEST_FIRST:
                    # oldest-first: insert after every existing row whose
                    # newest_date is older than ours
                    for j in range(self.store.get_n_items()):
                        if (self.store.get_item(j).summary.newest_date
                                >= ts.newest_date):
                            pos = j
                            break
                else:
                    # newest-first (default): insert before every row older
                    for j in range(self.store.get_n_items()):
                        if (self.store.get_item(j).summary.newest_date
                                <= ts.newest_date):
                            pos = j
                            break
                self.store.splice(pos, 0, [ThreadItem(ts)])

        # update stats
        try:
            self.total_messages = db.count_messages(self.query)
            self.unread_messages = db.count_messages(
                f"({self.query}) AND tag:unread")
            self.emit("stats-ready")
        except Exception:
            pass
