"""Port of src/config.cc.

Reads the same ~/.config/astroid/config JSON file as the C++ implementation
(CONFIG_VERSION 11). Differences are deliberate and minimal:

* we never write the user's config file back (the C++ version rewrites it
  when defaults were merged in); `--new-config` still writes a full default
  file, in boost-ptree style (all leaves as strings) so the C++ binary can
  read it too.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import CONFIG_VERSION
from .log import log
from .ptree_json import PTree, PTreeBadPath, read_ini
from .utils.misc import expand
from .utils.resource import Resource


@dataclass
class StandardPaths:
    home: Path = field(default_factory=Path)
    config_dir: Path = field(default_factory=Path)
    config_file: Path = field(default_factory=Path)
    data_dir: Path = field(default_factory=Path)
    cache_dir: Path = field(default_factory=Path)
    runtime_dir: Path = field(default_factory=Path)
    socket_dir: Path = field(default_factory=Path)
    searches_file: Path = field(default_factory=Path)
    save_dir: Path = field(default_factory=Path)
    attach_dir: Path = field(default_factory=Path)


class Config:
    def __init__(self, fname: str | None = None, test: bool = False,
                 no_load: bool = False):
        self.test = test
        self.config = PTree()
        self.notmuch_config = PTree()
        self.has_notmuch_config = False

        self.std_paths = StandardPaths()
        self.run_paths = StandardPaths()
        self._load_dirs()

        if fname:
            self.std_paths.config_file = Path(fname)
            log.info("cf: loading config: %s", fname)
        else:
            self.std_paths.config_file = self.std_paths.config_dir / "config"

        if not no_load:
            self.load_config()

    # -- paths (port of Config::load_dirs) ---------------------------------

    def _load_dirs(self) -> None:
        sp = self.std_paths

        if self.test:
            sp.home = Path.cwd() / "tests" / "test_home"
            sp.config_dir = sp.home
        else:
            home = os.environ.get("HOME")
            if not home:
                log.error("cf: HOME environment variable not set.")
                raise SystemExit(1)
            sp.home = Path(home)

            config_home = os.environ.get("XDG_CONFIG_HOME")
            sp.config_dir = (Path(config_home) / "astroid" if config_home
                             else sp.home / ".config" / "astroid")

        sp.searches_file = sp.config_dir / "searches"

        data = os.environ.get("XDG_DATA_HOME")
        sp.data_dir = (Path(data) / "astroid" if data
                       else sp.home / ".local" / "share" / "astroid")

        cache = os.environ.get("XDG_CACHE_HOME")
        sp.cache_dir = (Path(cache) / "astroid" if cache
                        else sp.home / ".cache" / "astroid")

        sp.socket_dir = sp.cache_dir / "socket"

        runtime = os.environ.get("XDG_RUNTIME_HOME")
        sp.runtime_dir = (Path(runtime) / "astroid" if runtime
                          else sp.cache_dir)

        sp.save_dir = sp.home
        sp.attach_dir = sp.home

    # -- defaults (verbatim port of Config::setup_default_config) ----------

    def setup_default_initial_config(self, conf: PTree,
                                     accounts: bool = True,
                                     startup: bool = True) -> None:
        if accounts:
            conf.put("accounts.charlie.name", "Charlie Root")
            conf.put("accounts.charlie.email", "root@localhost")
            conf.put("accounts.charlie.gpgkey", "")
            conf.put("accounts.charlie.always_gpg_sign", False)
            conf.put("accounts.charlie.sendmail", "msmtp -i -t")
            conf.put("accounts.charlie.default", True)
            conf.put("accounts.charlie.save_sent", False)
            conf.put("accounts.charlie.save_sent_to", "/home/root/Mail/sent/cur/")
            conf.put("accounts.charlie.additional_sent_tags", "")
            conf.put("accounts.charlie.save_drafts_to", "/home/root/Mail/drafts/")
            conf.put("accounts.charlie.signature_separate", False)
            conf.put("accounts.charlie.signature_file", "")
            conf.put("accounts.charlie.signature_file_markdown", "")
            conf.put("accounts.charlie.signature_default_on", True)
            conf.put("accounts.charlie.signature_attach", False)
            conf.put("accounts.charlie.select_query", "")

        if startup:
            conf.put("startup.queries.inbox", "tag:inbox")

    def setup_default_config(self, initial: bool) -> PTree:
        d = PTree()
        d.put("astroid.config.version", CONFIG_VERSION)

        nm_cfg = os.environ.get("NOTMUCH_CONFIG",
                                str(self.std_paths.home / ".notmuch-config"))
        d.put("astroid.notmuch_config", nm_cfg)

        d.put("astroid.debug.dryrun_sending", False)
        d.put("astroid.hints.level", 0)
        d.put("astroid.log.syslog", False)
        d.put("astroid.log.stdout", True)
        d.put("astroid.log.level", "info")

        if initial:
            self.setup_default_initial_config(d)

        d.put("terminal.height", 10)
        d.put("terminal.font_description", "default")

        d.put("thread_index.page_jump_rows", 6)
        d.put("thread_index.sort_order", "newest")

        d.put("general.time.clock_format", "local")
        d.put("general.time.same_year", "%b %-e")
        d.put("general.time.diff_year", "%x")
        d.put("general.tagbar_move", "tag")

        d.put("thread_index.cell.font_description", "default")
        d.put("thread_index.cell.line_spacing", 2)
        d.put("thread_index.cell.date_length", 10)
        d.put("thread_index.cell.message_count_length", 4)
        d.put("thread_index.cell.authors_length", 20)
        d.put("thread_index.cell.show_left_icons", True)
        d.put("thread_index.cell.subject_color", "#807d74")
        d.put("thread_index.cell.subject_color_selected", "#000000")
        d.put("thread_index.cell.background_color_selected", "")
        d.put("thread_index.cell.background_color_marked", "#fff584")
        d.put("thread_index.cell.background_color_marked_selected", "#bcb559")
        d.put("thread_index.cell.tags_length", 80)
        d.put("thread_index.cell.tags_upper_color", "#e5e5e5")
        d.put("thread_index.cell.tags_lower_color", "#333333")
        d.put("thread_index.cell.tags_alpha", "0.5")
        d.put("thread_index.cell.hidden_tags", "attachment,flagged,unread")

        # external-editor variant: the embedded (XEmbed) editor does not
        # exist in the GTK4 implementation
        d.put("editor.cmd",
              "gvim -f -c 'set ft=mail' '+set fileencoding=utf-8' "
              "'+set enc=utf-8' '+set ff=unix' '+set fo+=w' %1")
        d.put("editor.external_editor", True)
        d.put("editor.charset", "utf-8")
        d.put("editor.save_draft_on_force_quit", True)
        d.put("editor.attachment_words", "attach")
        d.put("editor.attachment_directory", "~")
        d.put("editor.markdown_processor", "cmark")
        d.put("editor.markdown_on", False)

        d.put("mail.reply.quote_processor", "w3m -dump -T text/html")
        d.put("mail.reply.quote_line", "Excerpts from %1's message of %2:")
        d.put("mail.reply.mailinglist_reply_to_sender", True)
        d.put("mail.forward.quote_line", "Forwarding %1's message of %2:")
        d.put("mail.forward.disposition", "inline")
        d.put("mail.sent_tags", "sent")
        d.put("mail.message_id_fqdn", "")
        d.put("mail.message_id_user", "")
        d.put("mail.user_agent", "default")
        d.put("mail.send_delay", 2)
        d.put("mail.close_on_success", False)
        d.put("mail.format_flowed", False)

        d.put("poll.interval", 60)
        d.put("poll.always_full_refresh", False)

        d.put("attachment.external_open_cmd", "xdg-open")

        d.put("thread_view.open_html_part_external", False)
        d.put("thread_view.preferred_type", "plain")
        d.put("thread_view.preferred_html_only", False)
        d.put("thread_view.allow_remote_when_encrypted", False)
        d.put("thread_view.open_external_link", "xdg-open")
        d.put("thread_view.default_save_directory", "~")
        d.put("thread_view.indent_messages", False)
        d.put("thread_view.gravatar.enable", True)
        d.put("thread_view.mark_unread_delay", 0.5)
        d.put("thread_view.expand_flagged", True)

        d.put("crypto.gpg.path", "gpg2")
        d.put("crypto.gpg.always_trust", True)
        d.put("crypto.gpg.enabled", True)

        d.put("saved_searches.show_on_startup", False)
        d.put("saved_searches.save_history", True)
        d.put("saved_searches.history_lines_to_show", 15)
        d.put("saved_searches.history_lines", 1000)

        return d

    # -- loading (port of Config::load_config) -----------------------------

    def load_config(self) -> None:
        sp = self.std_paths

        if self.test:
            log.info("cf: test config, loading defaults.")
            self.config = self.setup_default_config(True)
            self.config.put("poll.interval", 0)
            self.config.put("accounts.charlie.gpgkey", "gaute@astroidmail.bar")
            self.config.put("mail.send_delay", 0)
            nm = os.environ.get("NOTMUCH_CONFIG")
            if nm and Path(nm).is_file():
                self.notmuch_config = read_ini(nm)
                self.has_notmuch_config = True
            self._post_load()
            return

        log.info("cf: loading: %s", sp.config_file)

        sp.config_dir = sp.config_file.parent.absolute()
        Resource.config_dir = sp.config_dir

        for d in (sp.config_dir, sp.runtime_dir):
            if not d.is_dir():
                log.warning("cf: making dir: %s", d)
                d.mkdir(parents=True, exist_ok=True)

        if not sp.config_file.is_file():
            log.warning("cf: no config, using defaults.")
            self.config = self.setup_default_config(True)
        else:
            self.config = self.setup_default_config(False)
            try:
                new_config = PTree.read(sp.config_file)
            except ValueError as e:
                log.error("cf: failed to parse config: %s", e)
                raise SystemExit(1)
            self.config.merge(new_config)
            self.check_config()

        self._post_load()

    def _post_load(self) -> None:
        sp = self.std_paths

        sp.save_dir = expand(self.config.get_str("thread_view.default_save_directory"))
        self.run_paths.save_dir = sp.save_dir

        sp.attach_dir = expand(self.config.get_str("editor.attachment_directory"))
        self.run_paths.attach_dir = sp.attach_dir

        if not self.test:
            nm_env = os.environ.get("NOTMUCH_CONFIG")
            nm_path = expand(nm_env if nm_env
                             else self.config.get_str("astroid.notmuch_config"))
            if nm_path.is_file():
                self.notmuch_config = read_ini(nm_path)
                self.has_notmuch_config = True
            else:
                self.has_notmuch_config = False

    def check_config(self) -> None:
        """Port of Config::check_config minus the config-file rewrite."""
        try:
            version = self.config.get_int("astroid.config.version")
        except (PTreeBadPath, ValueError):
            version = 0

        startup = self.config.get_child_optional("startup")
        hasstartup = startup is not None and "queries" in startup.data

        if hasstartup and len(startup.get_child("queries")) == 0 \
                and not self.config.get_bool("saved_searches.show_on_startup"):
            log.info("cf: no startup queries, forcing show saved_searches on startup.")
            self.config.put("saved_searches.show_on_startup", True)

        accounts = self.config.get_child_optional("accounts")
        hasaccounts = accounts is not None and len(accounts) > 0

        if not hasaccounts or not hasstartup:
            log.warning("cf: missing accounts or startup.queries: using defaults")
            self.setup_default_initial_config(self.config,
                                              accounts=not hasaccounts,
                                              startup=not hasstartup)

        if version < CONFIG_VERSION:
            log.error("cf: the config file is an old version (%s), "
                      "the current version is: %s", version, CONFIG_VERSION)

    # -- --new-config -------------------------------------------------------

    def write_new_config(self) -> None:
        sp = self.std_paths
        if sp.config_file.exists():
            log.error("cf: config file already exists: %s", sp.config_file)
            raise SystemExit(1)
        sp.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.setup_default_config(True).write(sp.config_file)
        log.info("cf: wrote default config to: %s", sp.config_file)
