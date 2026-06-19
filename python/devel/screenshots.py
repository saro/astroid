#!/usr/bin/env python3
"""Capture screenshots of the Python astroid GUI under xvfb.

Run:
    xvfb-run -a -s "-screen 0 1280x800x24" dbus-run-session -- \
        python3 devel/screenshots.py /tmp/astroid_shots
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

PYTHON_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PYTHON_DIR.parent
sys.path.insert(0, str(PYTHON_DIR))

os.environ.setdefault("GTK_A11Y", "none")
os.environ.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
os.environ.setdefault("ASTROID_DIR", str(REPO_ROOT))


def setup_env(tmp: Path) -> Path:
    maildir = tmp / "mail"
    maildir.mkdir()
    for eml in (REPO_ROOT / "tests" / "mail" / "test_mail").glob("*.eml"):
        shutil.copy(eml, maildir)

    nm_config = tmp / "notmuch-config"
    template = (REPO_ROOT / "tests" / "mail" / "test_config.template").read_text()
    nm_config.write_text(template.replace("path=", f"path={maildir}", 1))

    env = dict(os.environ, NOTMUCH_CONFIG=str(nm_config), HOME=str(tmp))
    subprocess.run(["notmuch", "new"], check=True, env=env, capture_output=True)
    os.environ["NOTMUCH_CONFIG"] = str(nm_config)

    config_dir = tmp / "astroid"
    config_dir.mkdir()
    sent_dir = tmp / "sent_cur"; sent_dir.mkdir()
    drafts_dir = tmp / "drafts_cur"; drafts_dir.mkdir()
    (config_dir / "config").write_text(json.dumps({
        "astroid": {"config": {"version": "11"},
                    "log": {"stdout": "true", "level": "debug"}},
        "accounts": {"test": {"name": "Charlie Root",
                              "email": "root@localhost",
                              "sendmail": "false",
                              "default": "true",
                              "save_drafts_to": str(drafts_dir),
                              "save_sent_to": str(sent_dir)}},
        "startup": {"queries": {"inbox": "tag:inbox"}},
        "mail": {"send_delay": "0"},
        "poll": {"interval": "0"},
    }))
    return config_dir / "config"


def shoot(out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    # import from ImageMagick captures the X root window
    subprocess.run(["import", "-window", "root", str(out)], check=False)
    print(f"shot: {out}", flush=True)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: screenshots.py OUTDIR", file=sys.stderr)
        return 2
    outdir = Path(sys.argv[1])
    outdir.mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix="astroid-shots-"))
    config_file = setup_env(tmp)

    from gi.repository import GLib
    from astroid_mail.app import Astroid

    app = Astroid()
    state = {"step": 0}

    def step_index():
        win = app.get_active_window()
        if win is None:
            GLib.timeout_add(400, step_index)
            return False
        mode = win.current_mode()
        n = mode.store.get_n_items() if mode is not None else 0
        if n == 0:
            state.setdefault("retries", 0)
            state["retries"] += 1
            if state["retries"] > 30:
                print("threads never loaded", flush=True)
                app.quit()
                return False
            GLib.timeout_add(300, step_index)
            return False
        # ensure a row is selected so the screenshot shows selection
        mode.selection.set_selected(0)
        shoot(outdir / "01_thread_index.png")
        # open the thread
        mode.open_thread()
        GLib.timeout_add(2500, step_thread_view)
        return False

    def step_thread_view():
        win = app.get_active_window()
        tv = win.current_mode()
        from astroid_mail.modes.thread_view.thread_view import ThreadView
        if not isinstance(tv, ThreadView):
            print("thread view not open", flush=True)
            app.quit()
            return False
        shoot(outdir / "02_thread_view.png")
        # open reply
        if tv.mthread and tv.mthread.messages:
            tv.focused_message = tv.mthread.messages[-1]
        tv._key_reply(None)
        GLib.timeout_add(1500, step_edit_message)
        return False

    def step_edit_message():
        shoot(outdir / "03_edit_message_reply.png")
        # close edit message, go back to thread view, open raw view
        win = app.get_active_window()
        win.close_page(force=True)
        GLib.timeout_add(500, step_raw_view)
        return False

    def step_raw_view():
        win = app.get_active_window()
        tv = win.current_mode()
        if tv.mthread and tv.mthread.messages:
            tv.focused_message = tv.mthread.messages[-1]
        tv._key_raw(None)
        GLib.timeout_add(800, step_raw_shot)
        return False

    def step_raw_shot():
        shoot(outdir / "04_raw_view.png")
        win = app.get_active_window()
        win.close_page(force=True)
        # open log view
        GLib.timeout_add(300, step_log_view)
        return False

    def step_log_view():
        win = app.get_active_window()
        # simulate the L key
        from astroid_mail.modes.log_view import LogView
        win.add_mode(LogView(win))
        # emit a couple of varied log lines so the view isn't empty
        from astroid_mail.log import log as l
        l.info("hello from screenshot helper")
        l.warning("yellow line for visibility")
        l.error("red line: simulated error")
        GLib.timeout_add(700, step_log_shot)
        return False

    def step_log_shot():
        shoot(outdir / "05_log_view.png")
        GLib.timeout_add(300, step_spinner)
        return False

    def step_spinner():
        # write a slow poll.sh, kick a poll, and screenshot the index while
        # the spinner is running in the top-right of the tab bar.
        import stat as _stat
        win = app.get_active_window()
        # back to a thread-index tab
        win.notebook.set_current_page(0)
        cfg_dir = app.config.std_paths.config_dir
        script = cfg_dir / "poll.sh"
        script.write_text("#!/bin/sh\nsleep 3\necho done\n")
        script.chmod(script.stat().st_mode | _stat.S_IEXEC)
        app.poll.poll()
        GLib.timeout_add(600, step_spinner_shot)
        return False

    def step_spinner_shot():
        shoot(outdir / "06_poll_spinner.png")
        app.poll.cancel_poll()
        GLib.timeout_add(300, finish)
        return False

    def finish():
        app.quit()
        return False

    def on_activated(*_):
        GLib.timeout_add(1500, step_index)

    app.connect("window-added", on_activated)
    GLib.timeout_add_seconds(60, lambda: (app.quit(), False)[1])  # watchdog
    rc = app.run(["astroid", "--config", str(config_file), "--no-auto-poll"])
    print("done.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
