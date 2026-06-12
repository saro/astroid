"""Poll: run a dummy poll.sh that delivers new mail, assert partial refresh."""

import os
import shutil
import stat
import time

import pytest

notmuch2 = pytest.importorskip("notmuch2")

from astroid_mail.config import Config  # noqa: E402
from astroid_mail.db import Db  # noqa: E402
from astroid_mail.actions import ActionManager  # noqa: E402
from astroid_mail.poll import Poll  # noqa: E402

CPP_MAIL = None  # set in fixture


@pytest.fixture()
def env(notmuch_db, config_env, monkeypatch, tmp_path):
    maildir, nm_config = notmuch_db
    monkeypatch.setenv("NOTMUCH_CONFIG", str(nm_config))
    cfg = Config()
    Db.init(cfg)
    yield cfg, maildir, nm_config
    Db.path_db = None
    Db.excluded_tags = []


def make_poll_script(cfg, maildir, nm_config, new_eml: str) -> None:
    """poll.sh that drops a new message into the maildir and runs notmuch new."""
    script = cfg.std_paths.config_dir / "poll.sh"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(f"""#!/bin/sh
cat > "{maildir}/polled.eml" <<'EOF'
{new_eml}
EOF
NOTMUCH_CONFIG="{nm_config}" notmuch new
echo polled ok
""")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)


NEW_MAIL = """From: Polly <polly@example.com>
To: root@localhost
Subject: brand new mail
Message-Id: <polled-1@example.com>
Date: Thu, 01 Jan 2026 10:00:00 +0000

hi there
"""


def test_poll_partial_refresh(env):
    cfg, maildir, nm_config = env

    pending = []
    am = ActionManager(dispatch=lambda fn, *a: pending.append((fn, a)))
    updated = []
    am.connect("thread-updated", lambda _am, db, tid: updated.append(tid))

    make_poll_script(cfg, maildir, nm_config, NEW_MAIL)

    poll = Poll(cfg, am, auto_polling_enabled=False,
                dispatch=lambda fn, *a: pending.append((fn, a)),
                enable_timer=False)

    assert poll.poll()

    # wait for the poll thread to dispatch completion
    t0 = time.time()
    while not pending and time.time() - t0 < 15:
        time.sleep(0.05)
    assert pending, "poll did not complete"

    for fn, a in list(pending):
        pending.remove((fn, a))
        fn(*a)

    # the new message's thread got a thread-updated signal
    assert len(updated) >= 1
    with Db(Db.READ_ONLY) as db:
        tids = [t.threadid for t in db.threads("id:polled-1@example.com")]
    assert tids and tids[0] in updated

    assert poll.poll_state is False
    am.close()


def test_poll_missing_script(env):
    cfg, maildir, nm_config = env
    am = ActionManager(dispatch=lambda fn, *a: None)
    poll = Poll(cfg, am, auto_polling_enabled=False,
                dispatch=lambda fn, *a: None, enable_timer=False)
    assert poll.poll() is False  # no poll.sh
    assert poll.poll_state is False
    am.close()
