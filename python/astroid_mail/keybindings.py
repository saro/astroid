"""Port of src/modes/keybindings.cc.

Same user override file (~/.config/astroid/keybindings), same format:

    thread_index.next_thread=j        # rebind
    thread_index.next_thread=Down     # repeated name -> alias
    thread_index.label=               # empty spec -> unbind
    thread_index.run(cmd, undo)=C-r   # shell hook (commas escaped with \\)

Key specs: 'k', 'C-k', 'M-k', 'C-M-k'. Multi-char names are GDK key names
(Down, Return, Tab, ...).
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk  # noqa: E402

from .log import log  # noqa: E402

KEY_VOID = Gdk.KEY_VoidSymbol

Handler = Callable[["Key"], bool]
RunHandler = Callable[["Key", str, str], bool]


class KeySpecError(Exception):
    pass


class DuplicateKeyError(Exception):
    pass


@dataclass
class Key:
    ctrl: bool = False
    meta: bool = False
    shift: bool = False
    key: int = 0

    name: str = ""
    help: str = ""

    unbound: bool = False
    userdefined: bool = False
    allow_duplicate_name: bool = False

    hasaliases: bool = False
    isalias: bool = False
    master_key: Optional["Key"] = field(default=None, repr=False)

    # -- construction -------------------------------------------------------

    @staticmethod
    def get_keyval(k: str) -> int:
        if len(k) == 1:
            return Gdk.unicode_to_keyval(ord(k))
        return Gdk.keyval_from_name(k.strip())

    @classmethod
    def from_spec(cls, spec: str, name: str = "", help: str = "") -> "Key":
        parts = [p.strip() for p in spec.split("-")]
        # 'C--' style: a literal '-' produces empty parts; treat a single
        # '-' spec as the dash key
        if spec == "-":
            parts = ["-"]

        # modifiers: C- (ctrl), M- (alt), S- (shift — python extension; only
        # meaningful for caseless keys like space where shift does not
        # already produce a distinct keyval)
        if len(parts) > 4:
            log.error("key spec invalid: %s", spec)
            raise KeySpecError("invalid length of spec")

        k = cls(name=name, help=help)

        for part in parts[:-1]:
            m = part[:1]
            if m not in ("C", "M", "S"):
                log.error("key spec invalid: %s", spec)
                raise KeySpecError("invalid modifier in key spec")
            already = {"C": k.ctrl, "M": k.meta, "S": k.shift}[m]
            if already:
                log.error("key spec invalid: %s", spec)
                raise KeySpecError("modifier already specified")
            if m == "C":
                k.ctrl = True
            elif m == "M":
                k.meta = True
            else:
                k.shift = True

        k.key = cls.get_keyval(parts[-1])
        return k

    @classmethod
    def from_event(cls, keyval: int, state: Gdk.ModifierType) -> "Key":
        # Record shift only for caseless keyvals (space, arrows, ...): for
        # letters shift is already consumed producing the upper-case keyval,
        # and ISO_Left_Tab encodes Shift+Tab in the keyval itself.
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if shift:
            caseless = (Gdk.keyval_to_upper(keyval)
                        == Gdk.keyval_to_lower(keyval))
            if not caseless or keyval == Gdk.KEY_ISO_Left_Tab:
                shift = False
        return cls(ctrl=bool(state & Gdk.ModifierType.CONTROL_MASK),
                   meta=bool(state & Gdk.ModifierType.ALT_MASK),
                   shift=shift,
                   key=keyval)

    # -- representation -------------------------------------------------------

    def spec(self) -> str:
        s = ""
        if self.ctrl:
            s += "C-"
        if self.meta:
            s += "M-"
        if self.shift:
            s += "S-"

        u = Gdk.keyval_to_unicode(self.key)
        c = chr(u) if u else ""
        if c and unicodedata.category(c)[0] not in ("C", "Z"):
            s += c
        else:
            n = Gdk.keyval_name(self.key)
            if n is None:
                log.error("invalid key: %s for: %s", self.key, self.name)
                raise KeySpecError("invalid key")
            s += n
        return s

    def __str__(self) -> str:
        return self.spec()

    def _id(self) -> tuple:
        return (self.ctrl, self.meta, self.shift, self.key)

    def __eq__(self, other) -> bool:
        return isinstance(other, Key) and self._id() == other._id()

    def __hash__(self) -> int:
        return hash(self._id())


class UnboundKey(Key):
    def __init__(self):
        super().__init__(unbound=True, userdefined=False)


class Keybindings:
    """A registry of key -> handler for one mode."""

    USER_BINDINGS_FILE = "keybindings"

    # class-level user bindings, loaded once (port of the statics)
    _user_bindings_loaded = False
    user_bindings: list[Key] = []
    user_run_bindings: list[tuple[Key, tuple[str, str]]] = []

    def __init__(self, title: str = "", prefix: str = ""):
        self.title = title
        self.prefix = prefix
        self.loghandle = False
        # insertion-ordered: help output follows registration order
        self.keys: dict[tuple, tuple[Key, Optional[Handler]]] = {}

    # -- user bindings file (port of Keybindings::init) ----------------------

    @classmethod
    def init(cls, config_dir: Path) -> None:
        if cls._user_bindings_loaded:
            return
        cls._user_bindings_loaded = True

        bindings_file = Path(config_dir) / cls.USER_BINDINGS_FILE
        if not bindings_file.is_file():
            return

        log.info("keybindings: loading user bindings from: %s", bindings_file)

        for raw in bindings_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue

            # cut off trailing comments
            if "#" in line:
                line = line[:line.index("#")].strip()
                if not line:
                    continue

            if ".run" in line:
                cls._parse_run_line(line)
                continue

            parts = [p.strip() for p in line.split("=")]
            if len(parts) == 1:
                spec = ""
            elif len(parts) > 2 or not parts:
                log.error("ky: user bindings: invalid number of parts in: %s", line)
                continue
            else:
                spec = parts[1]

            if not spec:
                k = UnboundKey()
            else:
                try:
                    k = Key.from_spec(spec)
                except KeySpecError:
                    continue
                if k.key == KEY_VOID:
                    log.error("ky: user bindings: invalid key name: %s", spec)
                    continue

            k.name = parts[0]
            k.userdefined = True
            cls.user_bindings.append(k)

    @classmethod
    def _parse_run_line(cls, line: str) -> None:
        log.debug("ky: parsing run-hook: %s", line)

        fnd = line.find(".run")
        name = line[:fnd + 4]

        fnd = line.find("(", fnd)
        if fnd == -1:
            log.error("ky: invalid 'run'-specification: no '('")
            return

        rfnd = line.rfind("=")
        if rfnd == -1:
            log.error("ky: invalid 'run'-specification: no '='")
            return
        if rfnd < fnd:
            log.error("ky: invalid 'run'-specification: '=' before '('")
            return

        keyspec = line[rfnd + 1:].strip()

        rfnd = line.rfind(")", 0, rfnd)
        if rfnd == -1:
            log.error("ky: invalid 'run'-specification: no ')'")
            return
        if rfnd < fnd:
            log.error("ky: invalid 'run'-specification: ')' before '('")
            return

        target = line[fnd + 1:rfnd]
        undo_target = ""

        # split cmd and undo cmd on unescaped comma
        i = 0
        separator = -1
        while i < len(target):
            i = target.find(",", i)
            if i == 0:
                log.error("ky: invalid 'run'-specification: command starts "
                          "with ',', cannot only have undo command.")
                return
            if i == -1:
                break
            if target[i - 1] == "\\":
                target = target[:i - 1] + target[i:]
                # i now points just past the comma in the shortened string
            elif separator == -1:
                separator = i
                i += 1
            else:
                log.error("ky: invalid 'run'-specification: several ',' separators.")
                return

        if separator != -1:
            undo_target = target[separator + 1:].strip()
            target = target[:separator]
        target = target.strip()

        try:
            k = Key.from_spec(keyspec)
        except KeySpecError:
            return
        if k.key == KEY_VOID:
            log.error("ky: user bindings: invalid key name: %s", keyspec)
            return

        log.debug("ky: run: %s(%s): %s", name, k.spec(), target)

        k.name = name
        k.allow_duplicate_name = True
        k.userdefined = True

        cls.user_run_bindings.append((k, (target, undo_target)))

    @classmethod
    def reset_user_bindings(cls) -> None:
        """For tests."""
        cls._user_bindings_loaded = False
        cls.user_bindings = []
        cls.user_run_bindings = []

    # -- registration (port of Keybindings::register_key) --------------------

    def register_key(self, key, name: str, help: str, handler: Handler,
                     aliases: list | None = None) -> None:
        if isinstance(key, str):
            key = Key.from_spec(key)
        aliases = [Key.from_spec(a) if isinstance(a, str) else a
                   for a in (aliases or [])]

        k = key
        userdefined = k.userdefined

        # user overrides for this name?
        user = [u for u in self.user_bindings if u.name == name]
        if user:
            userdefined = True
            uk = user[0]

            if uk.unbound:
                log.debug("ky: key: %s dropped.", k.spec() if not k.unbound else name)
                return

            k = Key(ctrl=uk.ctrl, meta=uk.meta, shift=uk.shift, key=uk.key,
                    unbound=False, userdefined=True,
                    allow_duplicate_name=k.allow_duplicate_name)
            aliases = [Key(ctrl=a.ctrl, meta=a.meta, shift=a.shift, key=a.key, userdefined=True)
                       for a in user[1:]]

        if k.unbound:
            return

        k.name = name
        k.help = help
        k.userdefined = userdefined
        k.isalias = False

        if not k.allow_duplicate_name:
            if any(e.name == k.name for e, _ in self.keys.values()):
                msg = (f"key: {k.spec()}, there is a key with name {k.name} "
                       "registered already")
                log.error(msg)
                raise DuplicateKeyError(msg)

        has_master = True
        existing = self.keys.get(k._id())
        if existing is not None:
            ek = existing[0]
            if not ek.userdefined and k.userdefined:
                log.info("key: %s (%s) already exists in map with name: %s, "
                         "overwriting.", k.spec(), k.name, ek.name)
                self.keys[k._id()] = (k, handler)
            elif ek.userdefined and k.userdefined:
                msg = (f"key: {k.spec()} ({k.name}) is already user-configured "
                       f"in map with name: {ek.name}")
                log.error(msg)
                raise DuplicateKeyError(msg)
            elif ek.userdefined and not k.userdefined:
                log.warning("key: %s (%s) is user-configured in map with "
                            "name: %s, will try aliases.",
                            k.spec(), k.name, ek.name)
                has_master = False
            else:
                msg = (f"key: {k.spec()} ({k.name}) is already mapped "
                       f"with name: {ek.name}")
                log.error(msg)
                raise DuplicateKeyError(msg)
        else:
            self.keys[k._id()] = (k, handler)

        master = k if has_master else None
        has_aliases = False

        for ka in aliases:
            ka.name = k.name
            ka.help = k.help
            ka.userdefined = userdefined
            if has_master:
                ka.isalias = True
                ka.master_key = master

            existing = self.keys.get(ka._id())
            if existing is not None:
                ek = existing[0]
                if not ek.userdefined and ka.userdefined:
                    log.warning("key alias: %s (%s) already exists in map with "
                                "name: %s, overwriting.", ka.spec(), ka.name, ek.name)
                    self.keys[ka._id()] = (ka, None if has_master else handler)
                    if has_master:
                        has_aliases = True
                    else:
                        ka.isalias = False
                        master = ka
                        has_master = True
                elif ek.userdefined and ka.userdefined:
                    msg = (f"key alias: {ka.spec()} ({ka.name}) is already "
                           f"user-configured in map with name: {ek.name}")
                    log.error(msg)
                    raise DuplicateKeyError(msg)
                else:
                    log.warning("key alias: %s (%s) is user-configured in map "
                                "with name: %s, will try other aliases.",
                                ka.spec(), ka.name, ek.name)
            else:
                if not has_master:
                    # first alias that lands becomes the master
                    ka.isalias = False
                    self.keys[ka._id()] = (ka, handler)
                    master = ka
                    has_master = True
                else:
                    self.keys[ka._id()] = (ka, None)
                    has_aliases = True

        if has_master and master is not None:
            master.hasaliases = has_aliases

    def register_run(self, name: str, cb: RunHandler) -> None:
        for k, (target, undo_target) in self.user_run_bindings:
            if k.name != name:
                continue
            log.info("ky: run, binding: %s(%s) to: %s, %s",
                     name, k.spec(), target, undo_target)
            self.register_key(
                Key(ctrl=k.ctrl, meta=k.meta, shift=k.shift, key=k.key,
                    userdefined=True, allow_duplicate_name=True),
                k.name,
                f"Run hook: {target},{undo_target}",
                lambda key, t=target, u=undo_target: cb(key, t, u))

    # -- dispatch -------------------------------------------------------------

    def handle(self, keyval: int, state: Gdk.ModifierType) -> bool:
        if not self.keys:
            return False

        ek = Key.from_event(keyval, state)
        entry = self.keys.get(ek._id())
        if entry is None:
            if self.loghandle:
                log.debug("ky: %s, unknown key: %s", self.title, ek.spec())
            return False

        k, handler = entry
        if self.loghandle:
            log.debug("ky: %s, handling: %s (%s)", self.title, k.spec(), k.name)

        if k.isalias and k.master_key is not None:
            _, mhandler = self.keys[k.master_key._id()]
            return bool(mhandler(k)) if mhandler else False
        return bool(handler(k)) if handler else False

    def handle_name(self, name: str) -> bool:
        for k, handler in self.keys.values():
            if k.name == name:
                if k.isalias and k.master_key is not None:
                    _, handler = self.keys[k.master_key._id()]
                return bool(handler(k)) if handler else False
        raise KeySpecError(f"tried to handle unknown key name: {name}")

    # -- help -------------------------------------------------------------------

    def short_help(self) -> str:
        h = f"<b>{self.title}</b>: "
        h += ", ".join(f"{k.spec()}: {k.help}"
                       for k, _ in self.keys.values())
        return h

    def help(self) -> str:
        h = ""
        for k, _ in self.keys.values():
            if k.isalias:
                continue
            aliases = [a.spec() for a, _ in self.keys.values()
                       if a.isalias and a.master_key == k]
            keystr = ",".join([k.spec()] + aliases)
            h += f"<b>{keystr}</b>: {k.help}\n"
        return h

    def clear(self) -> None:
        self.keys.clear()
