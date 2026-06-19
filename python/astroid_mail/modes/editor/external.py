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
        self._proc: Gio.Subprocess | None = None

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
        if not ok or not argv:
            return False

        log.debug("editor: launching: %s", cmd)
        try:
            # Gio.Subprocess gives a clean spawn + async wait (the bare
            # GLib.spawn_async binding is finicky about argument count).
            self._proc = Gio.Subprocess.new(argv, Gio.SubprocessFlags.NONE)
        except GLib.Error as e:
            log.error("editor: spawn failed: %s", e)
            return False

        self.editor_started = True

        # file monitor on the tmpfile -> live preview
        gfile = Gio.File.new_for_path(str(self.tmpfile))
        self._monitor = gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
        self._monitor.connect("changed", self._on_file_changed)

        # wait for the editor to exit
        self._proc.wait_async(None, self._on_wait_done)
        return True

    # -- callbacks ------------------------------------------------------------

    def _on_file_changed(self, monitor, file, other_file, event_type) -> None:
        if event_type in (Gio.FileMonitorEvent.CHANGES_DONE_HINT,
                          Gio.FileMonitorEvent.CREATED):
            if self.tmpfile.is_file():
                log.debug("editor: tmpfile changed, emitting 'edited'")
                self.emit("edited")

    def _on_wait_done(self, proc, result) -> None:
        try:
            proc.wait_finish(result)
            if not proc.get_successful():
                log.error("editor: did not exit successfully.")
        except GLib.Error as e:
            log.error("editor: wait failed: %s", e)
        log.debug("editor: child exited")
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
