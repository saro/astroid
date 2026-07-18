#!/usr/bin/env python3
"""Real-keypress scroll test for the THREAD VIEW: open a long thread,
press space / S-space / j via xdotool, read window.scrollY from the page.

Run: xvfb-run -a dbus-run-session -- python3 devel/tv_scroll_keys.py
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

LONG_BODY = "\n".join(f"line {i}: lorem ipsum dolor sit amet" for i in range(400))


def setup_env(tmp: Path):
    maildir = tmp / "mail"
    maildir.mkdir()
    # one long thread so the view has to scroll
    for n in range(3):
        (maildir / f"long{n}.eml").write_text(
            f"""From: Long Sender <long@example.com>
To: root@localhost
Subject: long thread
Message-Id: <long-{n}@example.com>
{'References: <long-0@example.com>' if n else ''}
Date: Thu, 0{n+1} Jan 2026 10:00:00 +0000

{LONG_BODY}
""")
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
        "accounts": {"t": {"name": "C", "email": "root@localhost",
                           "sendmail": "false", "default": "true"}},
        "startup": {"queries": {"inbox": "tag:inbox"}},
        "poll": {"interval": "0"},
    }))
    return config_dir / "config"


def xdo(*args):
    subprocess.run(["xdotool", *args], check=False, capture_output=True)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="astroid-tvscroll-"))
    config_file = setup_env(tmp)

    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib
    from astroid_mail.app import Astroid

    app = Astroid()
    results = {"errors": [], "scroll": []}

    def scroll_y(tv, cb):
        def done(webview, result):
            try:
                v = webview.evaluate_javascript_finish(result)
                cb(float(v.to_string()))
            except Exception as e:
                results["errors"].append(f"scrollY read: {e}")
                cb(-1.0)
        tv.webview.evaluate_javascript(
            "String(window.scrollY)", -1, None, None, None, done)

    def stage_open_thread():
        win = app.get_active_window()
        ti = win.current_mode()
        if ti is None or ti.store.get_n_items() == 0:
            GLib.timeout_add(400, stage_open_thread)
            return False
        ti.selection.set_selected(0)
        ti.open_thread()
        GLib.timeout_add(3000, stage_focus_check)
        return False

    def stage_focus_check():
        win = app.get_active_window()
        tv = win.current_mode()
        results["focus"] = type(win.get_focus()).__name__
        print(f"real-tv: focus = {results['focus']}", flush=True)
        xdo("search", "--name", "Astroid", "windowfocus", "--sync")
        scroll_y(tv, lambda y: (results["scroll"].append(("start", y)),
                                GLib.timeout_add(200, stage_space))[1] and False)
        return False

    def stage_space():
        xdo("key", "--clearmodifiers", "space")
        GLib.timeout_add(900, stage_after_space)
        return False

    def stage_after_space():
        win = app.get_active_window()
        tv = win.current_mode()
        scroll_y(tv, lambda y: (results["scroll"].append(("after_space", y)),
                                GLib.timeout_add(200, stage_sspace))[1] and False)
        return False

    def stage_sspace():
        xdo("key", "shift+space")
        GLib.timeout_add(900, stage_after_sspace)
        return False

    def stage_after_sspace():
        win = app.get_active_window()
        tv = win.current_mode()
        scroll_y(tv, lambda y: (results["scroll"].append(("after_sspace", y)),
                                GLib.timeout_add(200, stage_j))[1] and False)
        return False

    def stage_j():
        xdo("key", "--clearmodifiers", "j")
        xdo("key", "--clearmodifiers", "j")
        xdo("key", "--clearmodifiers", "j")
        GLib.timeout_add(900, stage_after_j)
        return False

    def stage_after_j():
        win = app.get_active_window()
        tv = win.current_mode()
        scroll_y(tv, lambda y: (results["scroll"].append(("after_j", y)),
                                GLib.timeout_add(200, finish))[1] and False)
        return False

    def finish():
        app.quit()
        return False

    def on_window(*_):
        GLib.timeout_add(2500, stage_open_thread)

    app.connect("window-added", on_window)
    GLib.timeout_add_seconds(45, lambda: (app.quit(), False)[1])

    app.run(["astroid", "--config", str(config_file), "--no-auto-poll"])

    print()
    d = dict(results["scroll"])
    print(f"  focus:        {results.get('focus')}")
    for k, v in results["scroll"]:
        print(f"  scrollY {k:14s} {v}")
    ok = (d.get("after_space", 0) > d.get("start", 0)
          and d.get("after_sspace", 1e9) < d.get("after_space", 0)
          and not results["errors"])
    print(f"  errors: {results['errors'] or 'none'}")
    print()
    print("TV-SCROLL PASSED" if ok else "TV-SCROLL FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
