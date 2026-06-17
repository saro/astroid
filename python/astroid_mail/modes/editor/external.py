"""Port of src/modes/editor/external.cc.

Launches ``editor.cmd`` with ``%1`` substituted by the tmpfile path and
watches the tmpfile for changes; each save fires a callback so the
compose preview can be rebuilt. Child exit ends edit mode.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gio, GObject  # noqa: E402

from ...log import log


class ExternalEditor(GObject.Object):
    __gsignals__ = {
        "edited": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "stopped": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, editor_cmd: str, tmpfile: Path):
        super().__init__()
        self.editor_cmd = editor_cmd
        self.tmpfile = Path(tmpfile)
        self.editor_started = False
        self._monitor: Gio.FileMonitor | None = None
        self._pid: int = 0
        self._child_watch_id: int = 0

    # -- launch ---------------------------------------------------------------

    def start(self) -> bool:
        if self.editor_started:
            return False

        cmd = self.editor_cmd.replace("%1", str(self.tmpfile))
        try:
            ok, argv = GLib.shell_parse_argv(cmd)
        except GLib.Error as e:
            log.error("editor: cannot parse editor.cmd: %s", e)
            return False
        if not ok:
            return False

        log.debug("editor: launching: %s", cmd)
        try:
            flags = (GLib.SpawnFlags.SEARCH_PATH
                     | GLib.SpawnFlags.DO_NOT_REAP_CHILD)
            ok, pid = GLib.spawn_async(
                None, argv, None, flags, None, None)
        except GLib.Error as e:
            log.error("editor: spawn failed: %s", e)
            return False
        if not ok:
            return False

        self._pid = pid
        self.editor_started = True

        # file monitor
        gfile = Gio.File.new_for_path(str(self.tmpfile))
        self._monitor = gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
        self._monitor.connect("changed", self._on_file_changed)

        # child watch -> on_stop
        self._child_watch_id = GLib.child_watch_add(
            GLib.PRIORITY_DEFAULT, self._pid, self._on_child_exit)
        return True

    # -- callbacks ------------------------------------------------------------

    def _on_file_changed(self, monitor, file, other_file, event_type) -> None:
        if event_type in (Gio.FileMonitorEvent.CHANGES_DONE_HINT,
                          Gio.FileMonitorEvent.CREATED):
            if self.tmpfile.is_file():
                log.debug("editor: tmpfile changed, emitting 'edited'")
                self.emit("edited")

    def _on_child_exit(self, pid: int, status: int) -> None:
        log.debug("editor: child exited (status %s)", status)
        if status != 0:
            log.error("editor: did not exit successfully.")
        try:
            GLib.spawn_close_pid(pid)
        except Exception:
            pass
        self.editor_started = False
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None
        self.emit("stopped")

    def cancel(self) -> None:
        if self._monitor is not None:
            self._monitor.cancel()
            self._monitor = None
        self.editor_started = False
