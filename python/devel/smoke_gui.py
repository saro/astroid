#!/usr/bin/env python3
"""GUI smoke test: boot the app against a temp notmuch DB, open the inbox,
open the first thread in the thread view, exercise navigation keys, quit.

Run:  xvfb-run -a dbus-run-session -- python3 devel/smoke_gui.py
"""

import json
import os
import shutil
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
    (config_dir / "config").write_text(json.dumps({
        "astroid": {"config": {"version": "11"},
                    "log": {"stdout": "true", "level": "debug"}},
        "accounts": {"test": {"name": "Charlie Root",
                              "email": "root@localhost",
                              "sendmail": "false", "default": "true"}},
        "startup": {"queries": {"inbox": "tag:inbox"}},
        "poll": {"interval": "0"},
    }))
    return config_dir / "config"


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="astroid-smoke-"))
    config_file = setup_env(tmp)

    from gi.repository import GLib, Gdk
    from astroid_mail.app import Astroid

    app = Astroid()
    results = {"threads": 0, "tv_ready": False, "errors": []}

    def stage_check_index():
        win = app.get_active_window()
        if win is None:
            results["errors"].append("no window")
            app.quit()
            return False

        mode = win.current_mode()
        n = mode.store.get_n_items() if mode is not None else 0
        print(f"smoke: index check: mode={type(mode).__name__} n={n}",
              flush=True)
        if n == 0:
            results.setdefault("retries", 0)
            results["retries"] += 1
            if results["retries"] > 40:
                results["errors"].append("threads never loaded")
                app.quit()
                return False
            # still loading; retry
            GLib.timeout_add(300, stage_check_index)
            return False

        results["threads"] = n
        print(f"smoke: thread index loaded {n} threads", flush=True)

        # open the first thread
        mode.selection.set_selected(0)
        mode.open_thread()
        GLib.timeout_add(3000, stage_check_tv)
        return False

    def stage_check_tv():
        win = app.get_active_window()
        tv = win.current_mode()
        from astroid_mail.modes.thread_view.thread_view import ThreadView
        if not isinstance(tv, ThreadView):
            results["errors"].append("thread view did not open")
            app.quit()
            return False

        results["tv_ready"] = tv.ready
        print(f"smoke: thread view ready: {tv.ready}", flush=True)

        # drive some navigation through the page client
        tv.page_client.navigate("down", "visual_element")
        tv.page_client.navigate("down", "message")
        tv.page_client.navigate("up", "extreme")

        GLib.timeout_add(1500, finish)
        return False

    def finish():
        app.quit()
        return False

    def on_activated(*_):
        GLib.timeout_add(1500, stage_check_index)

    app.connect("window-added", on_activated)

    def watchdog():
        results["errors"].append("watchdog timeout")
        app.quit()
        return False
    GLib.timeout_add_seconds(45, watchdog)

    rc = app.run(["astroid", "--config", str(config_file), "--no-auto-poll"])

    ok = (results["threads"] > 0 and results["tv_ready"]
          and not results["errors"])
    print()
    print(f"  threads loaded     {results['threads']}")
    print(f"  thread view ready  {results['tv_ready']}")
    print(f"  errors             {results['errors'] or 'none'}")
    print()
    print("SMOKE PASSED" if ok else "SMOKE FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
