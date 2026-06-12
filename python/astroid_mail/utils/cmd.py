"""Port of src/utils/cmd.cc — shell hooks and pipe helper."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from ..log import log


class Cmd:
    # set by Config at startup, used for the hooks:: prefix
    config_dir: Path | None = None

    def __init__(self, cmd: str = "", undo_cmd: str = "", prefix: str = ""):
        self.prefix = f"{prefix}: " if prefix else ""
        self.cmd = self._substitute(cmd)
        self.undo_cmd = self._substitute(undo_cmd)

    @classmethod
    def _substitute(cls, cmd: str) -> str:
        if "hooks::" in cmd and cls.config_dir is not None:
            cmd = cmd.replace("hooks::", str(Path(cls.config_dir) / "hooks") + "/")
        return cmd

    def undoable(self) -> bool:
        return bool(self.undo_cmd)

    def run(self) -> bool:
        return self._execute(self.cmd)

    def undo(self) -> bool:
        if not self.undoable():
            log.error("cmd: tried to undo non-undoable command: %s", self.cmd)
            return False
        return self._execute(self.undo_cmd)

    def _execute(self, c: str) -> bool:
        log.info("cmd: running: %s", c)
        try:
            p = subprocess.run(c, shell=True, capture_output=True, text=True)
        except OSError as e:
            log.error("cmd: %sfailed to execute: '%s': %s", self.prefix, c, e)
            return False

        for line in p.stdout.splitlines():
            if line.strip():
                log.debug("cmd: %s%s", self.prefix, line.strip())
        for line in p.stderr.splitlines():
            if line.strip():
                log.error("cmd: %s%s", self.prefix, line.strip())

        return p.returncode == 0

    @staticmethod
    def pipe(cmd: str, stdin: str) -> tuple[bool, str, str]:
        """Run cmd, feed stdin, return (ok, stdout, stderr).
        Used for quote_processor (w3m) and markdown_processor (cmark)."""
        log.info("cmd: running: %s", cmd)
        try:
            p = subprocess.run(shlex.split(cmd), input=stdin,
                               capture_output=True, text=True)
        except OSError as e:
            log.error("cmd: failed to execute: '%s': %s", cmd, e)
            return False, "", str(e)

        if p.stderr:
            log.error("cmd: %s", p.stderr.strip())
        return True, p.stdout, p.stderr
