"""Port of src/modes/thread_view/theme.cc.

Loads ui/thread-view.html and compiles ui/thread-view.scss + ui/part.scss
(libsass if importable, else the sassc binary, else a precompiled .css
next to the scss). User overrides in the config dir take precedence
(Resource handles that). First line of each file must carry the
THEME_VERSION marker.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from ...log import log
from ...utils.resource import Resource

THEME_VERSION = 5

_version_re = re.compile(r"(ui|theme)-version:\s*(\d+)")


def _check_version(path: Path) -> None:
    first = path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    m = _version_re.search(first)
    if not m or int(m.group(2)) != THEME_VERSION:
        log.error("theme: %s does not match theme version %s: %s",
                  path, THEME_VERSION, first.strip())


def _compile_scss(path: Path) -> str:
    try:
        import sass  # libsass
        return sass.compile(filename=str(path))
    except ImportError:
        pass

    sassc = shutil.which("sassc")
    if sassc:
        p = subprocess.run([sassc, str(path)], capture_output=True, text=True)
        if p.returncode == 0:
            return p.stdout
        log.error("theme: sassc failed: %s", p.stderr)

    css = path.with_suffix(".css")
    if css.is_file():
        log.warning("theme: using precompiled css: %s", css)
        return css.read_text(encoding="utf-8")

    raise RuntimeError(
        f"theme: cannot compile {path}: install python libsass or sassc")


class Theme:
    thread_view_html: str = ""
    thread_view_css: str = ""
    part_css: str = ""
    _loaded = False

    @classmethod
    def load(cls, reload: bool = False) -> None:
        if cls._loaded and not reload:
            return

        html = Resource.find(True, "ui/thread-view.html")
        scss = Resource.find(True, "ui/thread-view.scss")
        part = Resource.find(True, "ui/part.scss")

        for p in (html, scss, part):
            _check_version(p)

        cls.thread_view_html = html.read_text(encoding="utf-8")
        cls.thread_view_css = _compile_scss(scss)
        cls.part_css = _compile_scss(part)
        cls._loaded = True
        log.info("theme: loaded (html: %s)", html)
