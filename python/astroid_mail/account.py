"""Port of src/account_manager.cc (read path subset)."""

from __future__ import annotations

from pathlib import Path

from .log import log
from .utils.misc import expand


class Account:
    def __init__(self, id_: str, tree: dict, config_dir: Path):
        self.id = id_
        g = lambda k, d="": str(tree.get(k, d))  # noqa: E731
        gb = lambda k, d=False: str(tree.get(k, d)).lower() in ("true", "1")  # noqa: E731

        self.name = g("name")
        self.email = g("email")
        self.sendmail = g("sendmail")
        self.default = gb("default")
        self.save_sent = gb("save_sent")
        self.save_sent_to = expand(g("save_sent_to")) if g("save_sent_to") else None
        self.additional_sent_tags = [t.strip() for t in
                                     g("additional_sent_tags").split(",")
                                     if t.strip()]
        self.save_drafts_to = expand(g("save_drafts_to")) if g("save_drafts_to") else None
        self.gpgkey = g("gpgkey")
        self.always_gpg_sign = gb("always_gpg_sign")
        self.select_query = g("select_query")

        sig = g("signature_file")
        self.signature_file = None
        if sig:
            p = Path(sig)
            self.signature_file = p if p.is_absolute() else config_dir / p
        self.signature_separate = gb("signature_separate")
        self.signature_default_on = gb("signature_default_on", True)
        self.signature_attach = gb("signature_attach")

    def full_address(self) -> str:
        return f"{self.name} <{self.email}>"


class AccountManager:
    def __init__(self, config):
        self.accounts: list[Account] = []
        self.default_account: Account | None = None

        tree = config.config.get_child_optional("accounts")
        if tree is None:
            log.error("accounts: no accounts defined!")
            return

        for id_, sub in tree.items():
            if not isinstance(sub, dict):
                continue
            a = Account(id_, sub, config.std_paths.config_dir)
            self.accounts.append(a)
            log.info("accounts: loaded account: %s", a.full_address())
            if a.default and self.default_account is None:
                self.default_account = a

        if self.default_account is None and self.accounts:
            self.default_account = self.accounts[0]

    def get_account_for_address(self, email: str) -> Account | None:
        for a in self.accounts:
            if a.email.lower() == email.lower():
                return a
        return None
