"""Port of src/modes/thread_index/thread_index.cc + list_view.cc.

Gtk.ListView over a Gio.ListStore of ThreadItem; thread_index.* keybinding
names preserved.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from ...actions import DiffTagAction, TagAction, ToggleAction  # noqa: E402
from ...db import Db, ThreadSummary  # noqa: E402
from ...log import log  # noqa: E402
from ..mode import Mode  # noqa: E402
from .query_loader import QueryLoader, ThreadItem  # noqa: E402
from .row_widget import RowConfig, ThreadRow  # noqa: E402


class ThreadIndex(Mode):
    def __init__(self, main_window, query: str, name: str = ""):
        super().__init__(main_window)
        app = main_window.app
        self.app = app
        self.query = query
        self.name = name

        self.page_jump_rows = app.config.config.get_int(
            "thread_index.page_jump_rows")

        self.set_label(name or query)

        self.store = Gio.ListStore(item_type=ThreadItem)
        self.selection = Gtk.SingleSelection(model=self.store)

        self.row_config = RowConfig(app.config)

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._on_setup)
        factory.connect("bind", self._on_bind)

        self.list_view = Gtk.ListView(model=self.selection, factory=factory)
        self.list_view.set_vexpand(True)
        self.list_view.connect("activate", self._on_activate)

        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_child(self.list_view)
        self.scroll.set_vexpand(True)
        self.append(self.scroll)

        self.loader = QueryLoader(self.store)
        self.loader.connect("stats-ready", self._on_stats)
        self.loader.connect("done", lambda *_: log.info("ti: loaded %s threads",
                                                        self.store.get_n_items()))

        app.actions.connect("thread-changed", self._on_thread_changed)
        app.actions.connect("refreshed", self._on_refreshed)

        self.register_keys()
        self.loader.start(query)

    # -- factory ----------------------------------------------------------------

    def _on_setup(self, factory, item) -> None:
        item.set_child(ThreadRow(self.row_config))

    def _on_bind(self, factory, item) -> None:
        row: ThreadRow = item.get_child()
        ti: ThreadItem = item.get_item()
        row.bind(ti.summary)

    # -- signals ------------------------------------------------------------------

    def _on_stats(self, *_args) -> None:
        q = self.name or self.query
        self.set_label(f"{q} ({self.loader.unread_messages}/"
                       f"{self.loader.total_messages})")

    def _on_thread_changed(self, _am, db, thread_id: str) -> None:
        self.loader.on_thread_changed(db, thread_id)

    def _on_refreshed(self, *_args) -> None:
        self.refresh()

    def refresh(self) -> None:
        self.store.remove_all()
        self.loader.start(self.query)

    # -- selection ------------------------------------------------------------------

    def current_thread(self) -> ThreadSummary | None:
        item = self.selection.get_selected_item()
        return item.summary if item is not None else None

    def _move_cursor(self, delta: int) -> None:
        n = self.store.get_n_items()
        if n == 0:
            return
        pos = self.selection.get_selected()
        if pos == Gtk.INVALID_LIST_POSITION:
            pos = 0
        else:
            pos = max(0, min(n - 1, pos + delta))
        self.selection.set_selected(pos)
        self.list_view.scroll_to(pos, Gtk.ListScrollFlags.NONE, None)

    def _on_activate(self, listview, position) -> None:
        self.open_thread()

    def open_thread(self, new_tab: bool = True) -> bool:
        ts = self.current_thread()
        if ts is None:
            return True
        from ..thread_view.thread_view import ThreadView
        tv = ThreadView(self.main_window)
        self.main_window.add_mode(tv)
        tv.load_thread(ts)
        return True

    # -- keys ---------------------------------------------------------------------

    def register_keys(self) -> None:
        keys = self.keys
        keys.title = "Thread Index"

        keys.register_key("j", "thread_index.next_thread", "Next thread",
                          lambda k: (self._move_cursor(1), True)[1],
                          aliases=["Down"])
        keys.register_key("k", "thread_index.previous_thread", "Previous thread",
                          lambda k: (self._move_cursor(-1), True)[1],
                          aliases=["Up"])
        keys.register_key("J", "thread_index.page_down", "Page down",
                          lambda k: (self._move_cursor(self.page_jump_rows), True)[1],
                          aliases=["Page_Down"])
        keys.register_key("K", "thread_index.page_up", "Page up",
                          lambda k: (self._move_cursor(-self.page_jump_rows), True)[1],
                          aliases=["Page_Up"])
        keys.register_key("1", "thread_index.scroll_home", "Scroll to first thread",
                          lambda k: (self._select_abs(0), True)[1],
                          aliases=["Home"])
        keys.register_key("0", "thread_index.scroll_end", "Scroll to last thread",
                          lambda k: (self._select_abs(-1), True)[1],
                          aliases=["End"])

        keys.register_key("Return", "thread_index.open_thread",
                          "Open thread",
                          lambda k: self.open_thread(),
                          aliases=["KP_Enter"])

        keys.register_key("a", "thread_index.archive",
                          "Toggle 'inbox' tag on thread",
                          lambda k: self._toggle_tag("inbox"))
        keys.register_key("*", "thread_index.flag",
                          "Toggle 'flagged' tag on thread",
                          lambda k: self._toggle_tag("flagged"))
        keys.register_key("N", "thread_index.toggle_unread",
                          "Toggle 'unread' tag on thread",
                          lambda k: self._toggle_tag("unread"))
        keys.register_key("#", "thread_index.trash",
                          "Toggle 'trash' tag on thread",
                          lambda k: self._toggle_tag("trash"))
        keys.register_key("S", "thread_index.toggle_spam",
                          "Toggle 'spam' tag on thread",
                          lambda k: self._toggle_tag("spam"))

        keys.register_key("+", "thread_index.tag",
                          "Edit tags on thread", self._key_tag)

        keys.register_key("$", "thread_index.refresh", "Refresh query",
                          lambda k: (self.refresh(), True)[1])

        keys.register_key("u", "thread_index.undo", "Undo last action",
                          lambda k: (self.app.actions.undo(), True)[1])

        keys.register_run("thread_index.run", self._run_hook)

    def _select_abs(self, pos: int) -> None:
        n = self.store.get_n_items()
        if n == 0:
            return
        if pos < 0:
            pos = n - 1
        self.selection.set_selected(pos)
        self.list_view.scroll_to(pos, Gtk.ListScrollFlags.NONE, None)

    def _toggle_tag(self, tag: str) -> bool:
        ts = self.current_thread()
        if ts is not None:
            self.app.actions.doit(ToggleAction(ts, tag))
        return True

    def _key_tag(self, k) -> bool:
        ts = self.current_thread()
        if ts is None:
            return True
        tag_str = " ".join(f"+{t}" for t in ts.tags)

        def on_done(text: str):
            # entered: full new tag list -> diff against current
            new_tags = [t.lstrip("+") for t in text.split() if t]
            add = [t for t in new_tags if t not in ts.tags]
            remove = [t for t in ts.tags if t not in new_tags]
            if add or remove:
                self.app.actions.doit(TagAction(ts, add=add, remove=remove))

        self.main_window.enable_command("tag",
                                        " ".join(ts.tags), on_done)
        return True

    def _run_hook(self, k, cmd: str, undo_cmd: str) -> bool:
        ts = self.current_thread()
        if ts is None:
            return True
        from ...actions import CmdAction
        from ...utils.cmd import Cmd
        c = cmd.replace("%1", ts.thread_id)
        u = undo_cmd.replace("%1", ts.thread_id) if undo_cmd else ""
        self.app.actions.doit(CmdAction(Cmd(c, u), thread_id=ts.thread_id))
        return True
