#!/usr/bin/env python3
"""Real-keypress test: sends actual X11 key events with xdotool and checks
they reach the app. This is the truth test for the 'x does not close the
compose window' bug — no handler shortcuts.

Run: xvfb-run -a dbus-run-session -- python3 devel/real_keys.py
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


def setup_env(tmp: Path):
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
    drafts = tmp / "drafts"
    drafts.mkdir()
    (config_dir / "config").write_text(json.dumps({
        "astroid": {"config": {"version": "11"},
                    "log": {"stdout": "true", "level": "debug"}},
        "accounts": {"test": {"name": "Charlie Root",
                              "email": "root@localhost",
                              "sendmail": "false", "default": "true",
                              "save_drafts_to": str(drafts)}},
        "startup": {"queries": {"inbox": "tag:inbox"}},
        "mail": {"send_delay": "0"},
        "poll": {"interval": "0"},
    }))
    return config_dir / "config"


def xdo(*args):
    subprocess.run(["xdotool", *args], check=False, capture_output=True)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="astroid-realkeys-"))
    config_file = setup_env(tmp)

    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import GLib, Gtk
    from astroid_mail.app import Astroid

    app = Astroid()
    results = {"errors": []}

    def stage_compose():
        win = app.get_active_window()
        if win is None:
            results["errors"].append("no window")
            app.quit()
            return False
        # make sure the X server sees our window as active
        xdo("search", "--name", "Astroid", "windowactivate", "--sync")
        win._key_compose(None)
        results["pages_after_compose"] = win.notebook.get_n_pages()
        print(f"real: compose open, pages={results['pages_after_compose']}",
              flush=True)
        print(f"real: focus widget = {type(win.get_focus()).__name__}",
              flush=True)
        GLib.timeout_add(1200, stage_press_x)
        return False

    def stage_press_x():
        # REAL key event through the X server
        xdo("search", "--name", "Astroid", "windowfocus", "--sync")
        xdo("key", "--clearmodifiers", "x")
        print("real: pressed x via xdotool", flush=True)
        GLib.timeout_add(1500, stage_check_closed)
        return False

    def stage_check_closed():
        win = app.get_active_window()
        results["pages_after_x"] = win.notebook.get_n_pages()
        results["closed_by_x"] = (results["pages_after_x"]
                                  == results["pages_after_compose"] - 1)
        print(f"real: pages now {results['pages_after_x']} "
              f"(closed={results['closed_by_x']})", flush=True)

        # also test j in the thread index (real key): selection should move
        ti = win.current_mode()
        results["sel_before_j"] = ti.selection.get_selected()
        xdo("key", "--clearmodifiers", "j")
        GLib.timeout_add(800, stage_check_j)
        return False

    def stage_check_j():
        win = app.get_active_window()
        ti = win.current_mode()
        results["sel_after_j"] = ti.selection.get_selected()
        results["j_moved"] = results["sel_after_j"] != results["sel_before_j"]
        print(f"real: selection {results['sel_before_j']} -> "
              f"{results['sel_after_j']} (j_moved={results['j_moved']})",
              flush=True)

        # space pages down, shift+space pages up (real key events)
        results["sel_before_space"] = ti.selection.get_selected()
        xdo("key", "--clearmodifiers", "space")
        GLib.timeout_add(800, stage_check_space)
        return False

    def stage_check_space():
        win = app.get_active_window()
        ti = win.current_mode()
        after_space = ti.selection.get_selected()
        results["space_moved_down"] = after_space > results["sel_before_space"]
        print(f"real: space {results['sel_before_space']} -> {after_space} "
              f"(down={results['space_moved_down']})", flush=True)

        results["sel_before_sspace"] = after_space
        xdo("key", "shift+space")
        GLib.timeout_add(800, stage_check_shift_space)
        return False

    def stage_check_shift_space():
        win = app.get_active_window()
        ti = win.current_mode()
        after = ti.selection.get_selected()
        results["sspace_moved_up"] = after < results["sel_before_sspace"]
        print(f"real: shift+space {results['sel_before_sspace']} -> {after} "
              f"(up={results['sspace_moved_up']})", flush=True)
        app.quit()
        return False

    def on_window(*_):
        GLib.timeout_add(2500, stage_compose)

    app.connect("window-added", on_window)
    GLib.timeout_add_seconds(40, lambda: (app.quit(), False)[1])

    app.run(["astroid", "--config", str(config_file), "--no-auto-poll"])

    ok = results.get("closed_by_x") and results.get("j_moved") \
        and results.get("space_moved_down") and results.get("sspace_moved_up") \
        and not results["errors"]
    print()
    print(f"  space pages down (real key)  {results.get('space_moved_down')}")
    print(f"  S-space pages up (real key)  {results.get('sspace_moved_up')}")
    print(f"  x closes compose (real key)  {results.get('closed_by_x')}")
    print(f"  j moves selection (real key) {results.get('j_moved')}")
    print(f"  errors                       {results['errors'] or 'none'}")
    print()
    print("REAL-KEYS PASSED" if ok else "REAL-KEYS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
