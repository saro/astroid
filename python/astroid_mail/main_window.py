"""Port of src/main_window.cc — Notebook of Mode tabs, key dispatch,
command bar, yes/no prompt."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from .command_bar import CommandBar  # noqa: E402
from .keybindings import Keybindings  # noqa: E402
from .log import log  # noqa: E402


class MainWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Astroid")
        self.app = app
        self.set_default_size(1200, 800)

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(self.box)

        self.command = CommandBar(self)
        self.box.append(self.command)

        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        self.notebook.set_vexpand(True)
        self.box.append(self.notebook)

        # poll spinner in the top-right of the tab bar (spins while poll.sh
        # is running), mirroring the C++ Notebook action widget.
        self.poll_spinner = Gtk.Spinner()
        self.poll_spinner.set_margin_start(4)
        self.poll_spinner.set_margin_end(6)
        self.poll_spinner.set_tooltip_text("Polling for new mail…")
        self.poll_spinner.set_visible(False)
        self.notebook.set_action_widget(self.poll_spinner, Gtk.PackType.END)
        if getattr(self.app, "poll", None) is not None:
            self.app.poll.connect("poll-state", self._on_poll_state)

        # yes/no prompt
        self.rev_yes_no = Gtk.Revealer()
        prompt = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        prompt.set_margin_top(3)
        prompt.set_margin_bottom(3)
        self.label_yes_no = Gtk.Label()
        prompt.append(self.label_yes_no)
        prompt.append(Gtk.Label(label="[y/n]"))
        self.rev_yes_no.set_child(prompt)
        self.box.append(self.rev_yes_no)
        self._yes_no_waiting = False
        self._yes_no_closure = None

        # multi-key chord echo
        self.rev_multi = Gtk.Revealer()
        self.label_multi = Gtk.Label()
        self.rev_multi.set_child(self.label_multi)
        self.box.append(self.rev_multi)
        self._multi_waiting = False
        self._multi_keybindings = None

        # window-level keys
        self.keys = Keybindings(title="Main window")
        self._register_keys()

        controller = Gtk.EventControllerKey()
        # CAPTURE: the controller fires *before* child widgets, so the WebKit
        # webview can't swallow keys like 'x'/'y' that drive our commands.
        # We still defer to the focused widget for plain (no-modifier)
        # printable keys when a Gtk.Editable or Gtk.TextView has focus.
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(controller)

        self.notebook.connect("switch-page", self._on_switch_page)

    # -- modes -------------------------------------------------------------------

    def add_mode(self, mode) -> int:
        tab = Gtk.Label(label=mode.get_label() or "?")
        mode.tab_widget = tab
        page = self.notebook.append_page(mode, tab)
        self.notebook.set_current_page(page)
        mode.grab_modal()
        self.set_title(f"{mode.get_label()} - Astroid")
        return page

    def update_tab_label(self, mode, label: str) -> None:
        tab = getattr(mode, "tab_widget", None)
        if tab is not None:
            tab.set_text(label)
        if self.current_mode() is mode:
            self.set_title(f"{label} - Astroid")

    def current_mode(self):
        page = self.notebook.get_current_page()
        if page < 0:
            return None
        return self.notebook.get_nth_page(page)

    def _on_poll_state(self, _poll, polling: bool) -> None:
        if polling:
            self.poll_spinner.set_visible(True)
            self.poll_spinner.start()
        else:
            self.poll_spinner.stop()
            self.poll_spinner.set_visible(False)

    def close_page(self, force: bool = False) -> None:
        mode = self.current_mode()
        if mode is None:
            return
        if mode.invincible and not force:
            return
        mode.pre_close()
        self.notebook.remove_page(self.notebook.get_current_page())
        if self.notebook.get_n_pages() == 0:
            self.close()
        else:
            m = self.current_mode()
            if m:
                m.grab_modal()

    def _on_switch_page(self, notebook, page, num) -> None:
        GLib.idle_add(lambda: (page.grab_modal(), False)[1])
        self.set_title(f"{page.get_label()} - Astroid")

    def grab_active(self) -> None:
        m = self.current_mode()
        if m is not None:
            m.grab_modal()

    # -- prompts ----------------------------------------------------------------------

    def ask_yes_no(self, question: str, closure) -> None:
        log.info("mw: ask yes/no: %s", question)
        self._yes_no_waiting = True
        self._yes_no_closure = closure
        self.label_yes_no.set_text(question)
        self.rev_yes_no.set_reveal_child(True)

    def answer_yes_no(self, yes: bool) -> None:
        self.rev_yes_no.set_reveal_child(False)
        closure = self._yes_no_closure
        self._yes_no_waiting = False
        self._yes_no_closure = None
        if closure is not None:
            closure(yes)

    def enable_multi_key(self, keybindings: Keybindings) -> None:
        self._multi_waiting = True
        self._multi_keybindings = keybindings
        self.label_multi.set_markup(keybindings.short_help())
        self.rev_multi.set_reveal_child(True)

    def disable_multi_key(self) -> None:
        self._multi_waiting = False
        self._multi_keybindings = None
        self.rev_multi.set_reveal_child(False)

    def enable_command(self, mode: str, initial: str, callback) -> None:
        self.command.enable_command(mode, "", initial, callback)

    # -- key dispatch ------------------------------------------------------------------

    def _on_key_pressed(self, controller, keyval, keycode, state) -> bool:
        # yes/no prompt has priority
        if self._yes_no_waiting:
            if keyval in (Gdk.KEY_y, Gdk.KEY_Y):
                self.answer_yes_no(True)
                return True
            if keyval in (Gdk.KEY_n, Gdk.KEY_N, Gdk.KEY_Escape):
                self.answer_yes_no(False)
                return True
            return True

        # multi-key chord
        if self._multi_waiting:
            kb = self._multi_keybindings
            self.disable_multi_key()
            if keyval != Gdk.KEY_Escape and kb is not None:
                return bool(kb.handle(keyval, state))
            return True

        # the command bar (search / tag entry) has its own controller and
        # needs free text input; let it handle keys while it is open.
        if self.command.get_search_mode():
            return False

        # Keybindings always win (same model as the C++ MainWindow::
        # on_key_press): try the active mode's keys, then the window keys.
        # A key that is bound is consumed here and never reaches the focused
        # widget (webview / header entry); only *unbound* keys fall through
        # so they can be typed. This is why x / D / y work regardless of
        # which compose widget holds focus.
        mode = self.current_mode()
        if mode is not None and mode.get_keys().handle(keyval, state):
            return True

        if self.keys.handle(keyval, state):
            return True

        # unbound: let the focused widget (e.g. a header entry) handle it
        return False

    # -- window keys -------------------------------------------------------------------

    def _register_keys(self) -> None:
        k = self.keys

        k.register_key("q", "main_window.quit_ask",
                       "Quit astroid", self._key_quit_ask)
        k.register_key("Q", "main_window.quit",
                       "Quit astroid (without asking)", self._key_quit)

        k.register_key("l", "main_window.next_page", "Next page",
                       lambda _k: self._switch_page(1))
        k.register_key("b", "main_window.previous_page", "Previous page",
                       lambda _k: self._switch_page(-1))

        k.register_key("x", "main_window.close_page", "Close mode (or window)",
                       lambda _k: (self.close_page(), True)[1])

        k.register_key("F", "main_window.search", "Search",
                       lambda _k: (self.enable_search(), True)[1],
                       aliases=["o"])

        k.register_key("c", "main_window.compose", "Compose new message",
                       self._key_compose)
        k.register_key("z", "main_window.show_log", "Show log view",
                       self._key_show_log, aliases=["L"])

        k.register_key("P", "main_window.poll", "Poll for new mail",
                       lambda _k: (self.app.poll.poll(), True)[1])

        k.register_key("C-c", "main_window.cancel_poll",
                       "Cancel the running poll script",
                       lambda _k: (self.app.poll.cancel_poll(), True)[1])

        k.register_key("?", "main_window.show_help", "Show help",
                       self._key_help)

        k.register_key("u", "main_window.undo", "Undo last action",
                       lambda _k: (self.app.actions.undo(), True)[1])

        for i in range(1, 9):
            k.register_key(f"M-{i}", f"main_window.jump_to_page_{i}",
                           f"Jump to page {i}",
                           lambda _k, n=i: self._jump_page(n))

    def enable_search(self) -> None:
        self.command.enable_command(CommandBar.MODE_SEARCH, "Search:", "", None)

    def _key_quit_ask(self, _k) -> bool:
        self.ask_yes_no("Really quit?",
                        lambda yes: self.app.quit() if yes else None)
        return True

    def _key_quit(self, _k) -> bool:
        self.app.quit()
        return True

    def _key_compose(self, _k) -> bool:
        from .modes.edit_message import EditMessage
        self.add_mode(EditMessage(self))
        return True

    def _key_show_log(self, _k) -> bool:
        from .modes.log_view import LogView
        self.add_mode(LogView(self))
        return True

    def _key_help(self, _k) -> bool:
        from .modes.help_mode import HelpMode
        self.add_mode(HelpMode(self, self.current_mode()))
        return True

    def _switch_page(self, delta: int) -> bool:
        n = self.notebook.get_n_pages()
        if n == 0:
            return True
        cur = self.notebook.get_current_page()
        self.notebook.set_current_page((cur + delta) % n)
        return True

    def _jump_page(self, num: int) -> bool:
        if num <= self.notebook.get_n_pages():
            self.notebook.set_current_page(num - 1)
        return True
