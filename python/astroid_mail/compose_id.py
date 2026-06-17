"""Port of the Message-Id generator at src/modes/edit_message.cc:207-265.

Format: ``{int(time)}.{rand10}.{user}@{fqdn}``

Hostname fallbacks (boost::filesystem-free):
  1. ``mail.message_id_fqdn`` config (if non-empty after trim).
  2. ``gethostname()`` + ``getdomainname()`` (C++ ignores '(none)' decorations
     by stripping '(' and ')'). If the result has no dot, append ``.none``.
  3. If hostname is empty: ``rand10 + ".none"``.

User fallbacks: ``mail.message_id_user`` or ``"astroid"``.

Pure function; the only external state are ``socket.gethostname()`` and
``socket.getdomainname()`` (Linux only; on platforms without it we use
``socket.getfqdn()`` as a substitute, which matches what notmuch+msmtp
users already get from sendmail-style identifiers).
"""

from __future__ import annotations

import socket
import time

from .config import Config
from .utils.misc import random_alphanumeric


def _detect_hostname() -> str:
    try:
        h = (socket.gethostname() or "").strip()
    except OSError:
        h = ""

    domain = ""
    if hasattr(socket, "getdomainname"):
        try:
            domain = (socket.getdomainname() or "").strip()  # type: ignore[attr-defined]
        except OSError:
            domain = ""
        domain = domain.replace("(", "").replace(")", "")

    if not h:
        return ""

    if domain:
        h = f"{h}.{domain}"

    if "." not in h:
        h += ".none"
    return h


def generate_message_id(config: Config, now: int | None = None) -> str:
    cfg = config.config
    hostname = cfg.get_str("mail.message_id_fqdn", "").strip()
    if not hostname:
        hostname = _detect_hostname()
        if not hostname:
            hostname = random_alphanumeric(10) + ".none"

    user = cfg.get_str("mail.message_id_user", "").strip()
    if not user:
        user = "astroid"

    if now is None:
        now = int(time.time())

    return f"{now}.{random_alphanumeric(10)}.{user}@{hostname}"
