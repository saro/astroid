"""Port of src/poll.cc.

Runs ~/.config/astroid/poll.sh, logs its output, tracks the notmuch
lastmod revision around the poll and triggers a partial refresh
(thread-updated per changed thread) or a full refresh afterwards.
"""

from __future__ import annotations

import subprocess
import threading
import time

from gi.repository import GLib, GObject

from .db import Db
from .log import log

POLL_SCRIPT = "poll.sh"


class Poll(GObject.Object):
    DEFAULT_POLL_INTERVAL = 60

    __gsignals__ = {
        "poll-state": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self, config, actions, auto_polling_enabled: bool = True,
                 dispatch=GLib.idle_add, enable_timer: bool = True):
        super().__init__()
        log.info("poll: setting up.")

        self.config = config
        self.actions = actions
        self._dispatch = dispatch

        self.poll_state = False
        self._dopoll = threading.Lock()
        self.external_polling = False
        self.before_poll_revision = 0
        self.last_poll = 0.0

        self.poll_interval = config.config.get_int("poll.interval")
        self.full_refresh = config.config.get_bool("poll.always_full_refresh")
        log.debug("poll: interval: %s", self.poll_interval)

        self.auto_polling_enabled = auto_polling_enabled
        if self.poll_interval <= 0:
            self.auto_polling_enabled = False

        if enable_timer:
            GLib.timeout_add_seconds(1, self._periodic_polling)

        if self.auto_polling_enabled:
            self.poll()
        else:
            log.info("poll: periodic polling disabled.")

    # -- periodic ------------------------------------------------------------

    def _periodic_polling(self) -> bool:
        if self.auto_polling_enabled and not self.poll_state:
            if time.monotonic() - self.last_poll >= self.poll_interval:
                log.info("poll: periodic poll..")
                self.poll()
        return True  # keep timer

    def toggle_auto_polling(self) -> None:
        self.auto_polling_enabled = not self.auto_polling_enabled
        log.info("poll: auto polling: %s", self.auto_polling_enabled)

    # -- polling -------------------------------------------------------------

    def poll(self) -> bool:
        if not self._dopoll.acquire(blocking=False):
            log.warning("poll: already in progress.")
            return False

        script = self.config.std_paths.config_dir / POLL_SCRIPT
        if not script.is_file():
            log.warning("poll: poll script does not exist: %s", script)
            self._dopoll.release()
            return False

        self._set_poll_state(True)
        try:
            with Db(Db.READ_ONLY) as db:
                self.before_poll_revision = db.get_revision()
        except Exception as e:
            log.error("poll: could not read db revision: %s", e)
            self.before_poll_revision = 0

        t = threading.Thread(target=self._run_poll_script, args=(script,),
                             name="poll", daemon=True)
        t.start()
        return True

    def _run_poll_script(self, script) -> None:
        t0 = time.monotonic()
        try:
            p = subprocess.Popen([str(script)], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        except OSError as e:
            log.error("poll: exception while running poll script: %s", e)
            self._dispatch(self._poll_done, 1)
            return

        stdout, stderr = p.communicate()
        for line in stdout.splitlines():
            if line.strip():
                log.debug("poll script: %s", line.strip())
        for line in stderr.splitlines():
            if line.strip():
                log.warning("poll script: %s", line.strip())

        elapsed = time.monotonic() - t0
        log.info("poll: done (time: %.2f s) (status: %s)", elapsed, p.returncode)
        if p.returncode != 0:
            log.error("poll: poll script did not exit successfully.")

        self._dispatch(self._poll_done, p.returncode)

    def _poll_done(self, _status) -> bool:
        """GUI thread: signal refresh."""
        self.last_poll = time.monotonic()
        self._set_poll_state(False)

        if self.full_refresh:
            self.refresh_full()
        else:
            self.refresh_threads()

        self._dopoll.release()
        return False

    # -- refresh -------------------------------------------------------------

    def refresh_full(self) -> None:
        log.info("poll: requesting full refresh..")
        self.actions.emit_refreshed()

    def refresh_threads(self) -> None:
        """Update all threads changed since before_poll_revision."""
        try:
            with Db(Db.READ_ONLY) as db:
                revnow = db.get_revision()
                log.debug("poll: refreshing.. revision after poll: %s", revnow)

                if revnow <= self.before_poll_revision:
                    return

                query = f"lastmod:{self.before_poll_revision}..{revnow}"
                total = db.count_threads(query, exclude=False)
                log.info("poll: %s threads changed, updating..", total)

                if total > 0:
                    tids = [t.threadid
                            for t in db.threads(query, exclude=False)]
                    for tid in tids:
                        self.actions.emit_thread_updated(db, tid)
        except Exception as e:
            log.error("poll: refresh failed: %s", e)

    # -- external polling (CLI integration) -----------------------------------

    def start_polling(self) -> None:
        if not self._dopoll.acquire(blocking=False):
            log.error("poll: polling already in progress.")
            return
        self.external_polling = True
        self._set_poll_state(True)
        try:
            with Db(Db.READ_ONLY) as db:
                self.before_poll_revision = db.get_revision()
        except Exception:
            self.before_poll_revision = 0

    def stop_polling(self) -> None:
        if not self.external_polling:
            return
        self.external_polling = False
        self.last_poll = time.monotonic()
        self._set_poll_state(False)
        self.refresh_threads()
        self._dopoll.release()

    def refresh(self, before: int) -> None:
        if self.external_polling:
            log.error("poll: external polling in progress, --refresh should "
                      "not be used in combination with --start-polling or "
                      "--stop-polling")
            return
        if self._dopoll.acquire(blocking=False):
            log.info("poll: refreshing threads since: %s", before)
            self.before_poll_revision = before
            if before == 0:
                self.refresh_full()
            else:
                self.refresh_threads()
            self._dopoll.release()
        else:
            log.error("poll: polling already in progress, cannot refresh.")

    # -- state -------------------------------------------------------------------

    def _set_poll_state(self, state: bool) -> None:
        if state != self.poll_state:
            self.poll_state = state
            log.info("poll: state: %s", state)
            self.emit("poll-state", state)
