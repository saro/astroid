# Astroid (Python reimplementation)

Python reimplementation of the astroid mail client on **GTK4 + WebKitGTK 6.0**
via PyGObject, developed alongside the C++ implementation in this repository.

Compatibility goals (hard requirements):

- reads the same `~/.config/astroid/config` (JSON, config version 11) — the
  defaults table is a verbatim port of `src/config.cc` and is tested
  key-by-key against it,
- integrates with the same external tools: notmuch, `poll.sh`, per-account
  `sendmail`, `w3m` quote processor, `cmark`, `xdg-open`, gpg,
- same keybinding action names and the same `~/.config/astroid/keybindings`
  override file format, and the same `~/.config/astroid/searches` file.

Out of scope by design: plugins (libpeas), embedded terminal, embedded
editor (GTK4 removed XEmbed — external editor only, `editor.cmd` with `%1`).

## Status

Phase 0 (scaffolding + spikes) and Phase 1 (read path) — done:

- `astroid_mail.config` / `astroid_mail.ptree_json`: boost-ptree-compatible
  config loading, tested against the C++ defaults table.
- `tests/`: pytest suite incl. a real notmuch database fixture built from
  the C++ test corpus (`../tests/mail/test_mail`).
- Spikes proving the risky architecture choices:
  - `tests/test_spike_notmuch.py` — notmuch2 bindings (queries,
    exclude_tags, lastmod revision, maildir flag sync),
  - `tests/test_spike_gmime.py` — GMime 3.0 via GObject Introspection
    (parsing, MIME tree walk, decoding, message construction),
  - `devel/spike_webkit.py` — WebKitGTK 6.0 thread-view transport:
    UserScript injection, JSON round-trip via `evaluate_javascript`,
    script message handlers, sandboxed `srcdoc` iframes, CSP-based
    remote-image blocking. All green on WebKitGTK 6.0 (2.52.3).

Phase 1 additionally delivered: keybindings engine (same action names +
user override file), db layer (notmuch2, RO/RW gate, maildir flag sync),
action manager with undo, poll with lastmod partial refresh, message
models (GMime), thread index (Gtk.ListView), thread view (tv.js over
evaluate_javascript / script message handlers), main window, command
bar, help mode and the `astroid-py` entry point.

Run against your real notmuch setup:

    cd python && python3 -m astroid_mail    # reads ~/.config/astroid/config

GUI smoke test (builds its own temp maildir + config):

    cd python && xvfb-run -a dbus-run-session -- python3 devel/smoke_gui.py

Phase 2 — compose path — done: ComposeMessage (build/finalize/send
with cancellable send_delay, dryrun, save_sent_to with maildir layout),
Message-Id generator with all C++ fallback branches, reply/forward
helpers (recipient derivation for every ReplyMode, format=flowed,
inline/attachment forward), saved searches model with C++-compatible
duplicate-key JSON, AddSentMessage / AddDraftMessage / RemoveMessage
actions, external editor (Gio.FileMonitor live preview), EditMessage
mode (header grid + switches + embedded ThreadView preview + every
edit_message.* keybinding), RawMessage, LogView, mailto: parser, and
`c` (compose) / `L` (log) main-window keys, plus `r` / `G` / `R` / `f`
/ `V` in the thread view. End-to-end verified by `devel/smoke_gui.py`:
opens a thread, replies, saves draft, sends through a fake sendmail
that captures the RFC5322 bytes.

Next: Phase 3 — fidelity (GPG crypto, markdown compose, gravatar,
theme overrides, remote-image flow, print, format=flowed, hints).

## Development

System dependencies (Debian/Ubuntu):

    apt install python3-gi python3-notmuch2 gir1.2-gtk-4.0 \
        gir1.2-webkit-6.0 gir1.2-gmime-3.0 notmuch python3-pytest

Arch:

    pacman -S python-gobject python-notmuch2 gtk4 webkitgtk-6.0 gmime3 notmuch

Run the tests:

    cd python && python3 -m pytest

Run the WebKit spike (headless):

    cd python && xvfb-run -a dbus-run-session -- python3 devel/spike_webkit.py
