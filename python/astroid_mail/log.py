"""Logging setup matching the C++ boost::log configuration.

Format: [HH:MM:SS.ffffff] [thread] [level] message  (stdout)
Levels follow astroid.log.level: trace, debug, info, warning, error, fatal.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys

TRACE = 5
logging.addLevelName(TRACE, "trace")

_LEVELS = {
    "trace": TRACE,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "fatal": logging.CRITICAL,
}

log = logging.getLogger("astroid")


class _Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.levelname = record.levelname.lower()
        return super().format(record)


def setup(level: str = "info", stdout: bool = True, syslog: bool = False,
          disable: bool = False) -> None:
    log.handlers.clear()
    log.setLevel(_LEVELS.get(level, logging.INFO))

    if disable:
        log.addHandler(logging.NullHandler())
        return

    fmt = _Formatter(
        "[%(asctime)s] [%(threadName)s] [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S")

    if stdout:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(fmt)
        log.addHandler(h)

    if syslog:
        try:
            sh = logging.handlers.SysLogHandler(address="/dev/log")
            sh.setFormatter(logging.Formatter("astroid: [%(levelname)s] %(message)s"))
            log.addHandler(sh)
        except OSError:
            pass
