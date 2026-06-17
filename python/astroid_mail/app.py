"""Port of src/astroid.cc + src/main.cc.

Gtk.Application "org.astroid" with single-instance command-line handling.
"""

from __future__ import annotations

import argparse
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from . import __version__  # noqa: E402
from .log import log, setup as log_setup  # noqa: E402


class Astroid(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.astroid",
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.config = None
        self.accounts = None
        self.actions = None
        self.poll = None
        self._started = False

    # -- cli ---------------------------------------------------------------------

    @staticmethod
    def _parser() -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(prog="astroid", add_help=True)
        p.add_argument("-c", "--config", help="config file, default: "
                       "$XDG_CONFIG_HOME/astroid/config")
        p.add_argument("--new-config", action="store_true",
                       help="make new default config, then exit")
        p.add_argument("-m", "--mailto", action="append", default=[],
                       help="compose mail with mailto url or address")
        p.add_argument("--no-auto-poll", action="store_true",
                       help="do not poll automatically")
        p.add_argument("--start-polling", action="store_true")
        p.add_argument("--stop-polling", action="store_true")
        p.add_argument("--refresh", type=int, metavar="LASTMOD")
        p.add_argument("--log-level", default=None)
        p.add_argument("--log-stdout", action="store_true")
        p.add_argument("--disable-log", action="store_true")
        return p

    def do_command_line(self, cmdline) -> int:
        args = cmdline.get_arguments()[1:]
        try:
            opts = self._parser().parse_args(args)
        except SystemExit:
            return 1

        if not self._started:
            # primary instance startup
            from .config import Config
            cfg_kwargs = {}
            if opts.config:
                cfg_kwargs["fname"] = opts.config
            if opts.new_config:
                cfg = Config(no_load=True, **cfg_kwargs)
                cfg.write_new_config()
                return 0

            self.config = Config(**cfg_kwargs)

            level = opts.log_level or self.config.config.get_str("astroid.log.level")
            log_setup(level=level,
                      stdout=opts.log_stdout or
                      self.config.config.get_bool("astroid.log.stdout"),
                      syslog=self.config.config.get_bool("astroid.log.syslog"),
                      disable=opts.disable_log)

            log.info("astroid (python) %s", __version__)
            self._startup(no_auto_poll=opts.no_auto_poll)
            self._started = True

            self.open_new_window()

            for m in opts.mailto:
                self.open_mailto(m)
            return 0

        # secondary invocation forwarded to the primary instance
        if opts.start_polling:
            self.poll.start_polling()
        elif opts.stop_polling:
            self.poll.stop_polling()
        elif opts.refresh is not None:
            self.poll.refresh(opts.refresh)
        elif opts.mailto:
            for m in opts.mailto:
                self.open_mailto(m)
        else:
            self.open_new_window()
        return 0

    # -- startup -------------------------------------------------------------------

    def _startup(self, no_auto_poll: bool = False) -> None:
        from .account import AccountManager
        from .actions import ActionManager
        from .db import Db
        from .keybindings import Keybindings
        from .poll import Poll
        from .utils.cmd import Cmd
        from .utils.resource import Resource

        Resource.config_dir = self.config.std_paths.config_dir
        Cmd.config_dir = self.config.std_paths.config_dir

        Keybindings.init(self.config.std_paths.config_dir)

        Db.init(self.config)
        with Db(Db.READ_ONLY) as db:
            db.load_tags()

        self.accounts = AccountManager(self.config)
        self.actions = ActionManager()
        self.poll = Poll(self.config, self.actions,
                         auto_polling_enabled=not no_auto_poll)

    def open_new_window(self) -> None:
        from .main_window import MainWindow
        from .modes.thread_index.thread_index import ThreadIndex

        w = MainWindow(self)

        queries = self.config.config.get_child_optional("startup.queries")
        if queries is not None:
            for name, q in queries.items():
                if isinstance(q, dict):
                    continue
                ti = ThreadIndex(w, str(q), name=name)
                ti.invincible = True
                w.add_mode(ti)

        w.present()

    def open_mailto(self, uri: str) -> None:
        from .modes.edit_message import EditMessage, parse_mailto
        log.info("astroid: mailto: %s", uri)
        win = self.get_active_window()
        if win is None:
            self.open_new_window()
            win = self.get_active_window()
        if win is None:
            log.error("astroid: no window for mailto")
            return
        try:
            fields = parse_mailto(uri)
            em = EditMessage(win, **fields)
            win.add_mode(em)
        except Exception as e:
            log.error("astroid: mailto open failed: %s", e)

    def do_shutdown(self) -> None:
        if self.actions is not None:
            self.actions.close()
        Gtk.Application.do_shutdown(self)


def main() -> int:
    app = Astroid()
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
