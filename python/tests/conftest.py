"""Test fixtures.

Builds a real notmuch database from the C++ test corpus
(../../tests/mail/test_mail) in a temp dir, the same way
tests/run_test.sh does for the C++ suite.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PYTHON_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = PYTHON_DIR.parent
CPP_TESTS = REPO_ROOT / "tests"

sys.path.insert(0, str(PYTHON_DIR))


@pytest.fixture()
def notmuch_db(tmp_path, monkeypatch):
    """A populated notmuch database. Returns (db_path, notmuch_config_path)."""
    if shutil.which("notmuch") is None:
        pytest.skip("notmuch binary not available")

    maildir = tmp_path / "test_mail"
    maildir.mkdir()

    src_mail = CPP_TESTS / "mail" / "test_mail"
    for eml in src_mail.glob("*.eml"):
        shutil.copy(eml, maildir)

    nm_config = tmp_path / "notmuch-config"
    template = (CPP_TESTS / "mail" / "test_config.template").read_text()
    nm_config.write_text(template.replace("path=", f"path={maildir}", 1))

    monkeypatch.setenv("NOTMUCH_CONFIG", str(nm_config))
    subprocess.run(["notmuch", "new"], check=True, capture_output=True,
                   env={"NOTMUCH_CONFIG": str(nm_config),
                        "HOME": str(tmp_path),
                        "PATH": "/usr/bin:/bin"})

    return maildir, nm_config


@pytest.fixture()
def config_env(tmp_path, monkeypatch):
    """Isolated XDG environment for Config tests."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("XDG_RUNTIME_HOME", raising=False)
    monkeypatch.delenv("NOTMUCH_CONFIG", raising=False)
    monkeypatch.delenv("ASTROID_DIR", raising=False)
    return home
