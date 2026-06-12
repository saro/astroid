import json
import re
from pathlib import Path

from astroid_mail import CONFIG_VERSION
from astroid_mail.config import Config

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CPP_CONFIG_CC = REPO_ROOT / "src" / "config.cc"


def test_defaults_match_cpp_table(config_env):
    """Compare every default key/value against the table in src/config.cc."""
    cfg = Config(no_load=True)
    defaults = cfg.setup_default_config(True)

    src = CPP_CONFIG_CC.read_text()
    # capture default_config.put / conf.put ("key", value) pairs
    puts = re.findall(
        r'(?:default_config|conf)\.put\s*\(\s*"([^"]+)"\s*,\s*(.+?)\);',
        src)

    assert len(puts) > 60

    skip = {
        # computed at runtime / build-flag dependent — checked separately
        "astroid.config.version",
        "astroid.notmuch_config",
        "astroid.log.level",
        "editor.cmd",
        "editor.external_editor",
        "accounts.charlie.save_sent_to",
        "accounts.charlie.save_drafts_to",
        "poll.interval",
    }

    for key, raw in puts:
        if key in skip:
            assert key in defaults, f"missing key: {key}"
            continue

        raw = raw.strip()
        ours = defaults.get_str(key)

        if raw == "true":
            assert ours == "true", key
        elif raw == "false":
            assert ours == "false", key
        elif m := re.fullmatch(r'"(.*)"', raw, re.S):
            expect = m.group(1).replace('\\"', '"').replace("\\\\", "\\")
            assert ours == expect, f"{key}: {ours!r} != {expect!r}"
        elif re.fullmatch(r"-?\d+", raw):
            assert defaults.get_int(key) == int(raw), key
        elif re.fullmatch(r"-?\.?\d*\.?\d+", raw):
            assert defaults.get_float(key) == float(raw), key
        # else: expression (e.g. Poll::DEFAULT_POLL_INTERVAL) — skip value check
        assert key in defaults

    assert defaults.get_int("astroid.config.version") == CONFIG_VERSION
    assert defaults.get_int("poll.interval") == 60


def test_loads_cpp_style_config(config_env, tmp_path):
    """A config written boost-style (all-string leaves) must load and merge."""
    conf_file = tmp_path / "config"
    user = {
        "astroid": {"config": {"version": "11"}},
        "accounts": {
            "me": {
                "name": "Test User",
                "email": "me@example.com",
                "sendmail": "msmtp -t",
                "default": "true",
            }
        },
        "startup": {"queries": {"inbox": "tag:inbox AND tag:unread"}},
        "poll": {"interval": "120"},
    }
    conf_file.write_text(json.dumps(user))

    cfg = Config(fname=str(conf_file))

    # user values take precedence
    assert cfg.config.get_str("accounts.me.email") == "me@example.com"
    assert cfg.config.get_bool("accounts.me.default") is True
    assert cfg.config.get_int("poll.interval") == 120
    assert cfg.config.get_str("startup.queries.inbox") == "tag:inbox AND tag:unread"
    # defaults fill the gaps
    assert cfg.config.get_str("mail.sent_tags") == "sent"
    assert cfg.config.get_float("thread_view.mark_unread_delay") == 0.5
    # no default charlie account injected when accounts exist
    accounts = cfg.config.get_child("accounts")
    assert "charlie" not in dict(accounts.items())


def test_user_file_never_rewritten(config_env, tmp_path):
    conf_file = tmp_path / "config"
    original = json.dumps({"poll": {"interval": "33"}})
    conf_file.write_text(original)

    Config(fname=str(conf_file))

    assert conf_file.read_text() == original


def test_missing_config_uses_defaults(config_env):
    cfg = Config()
    assert cfg.config.get_int("astroid.config.version") == CONFIG_VERSION
    # initial config: example account + startup query present
    assert cfg.config.get_str("accounts.charlie.email") == "root@localhost"
    assert cfg.config.get_str("startup.queries.inbox") == "tag:inbox"
    # paths derived from HOME
    assert str(cfg.std_paths.config_dir).endswith(".config/astroid")


def test_empty_startup_queries_forces_saved_searches(config_env, tmp_path):
    conf_file = tmp_path / "config"
    conf_file.write_text(json.dumps({
        "accounts": {"me": {"email": "x@y.z"}},
        "startup": {"queries": {}},
    }))
    cfg = Config(fname=str(conf_file))
    assert cfg.config.get_bool("saved_searches.show_on_startup") is True


def test_new_config_writes_boost_readable_file(config_env, tmp_path):
    conf_file = tmp_path / "newdir" / "config"
    cfg = Config(fname=str(conf_file), no_load=True)
    cfg.write_new_config()

    data = json.loads(conf_file.read_text())

    def leaves(node):
        for v in node.values():
            if isinstance(v, dict):
                yield from leaves(v)
            else:
                yield v

    assert all(isinstance(v, str) for v in leaves(data))
    assert data["astroid"]["config"]["version"] == str(CONFIG_VERSION)
