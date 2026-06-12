"""Thread list row (replaces the Cairo CellRenderer with a GTK4 widget).

Layout per row (honouring thread_index.cell.* config):
[flagged/attachment icons] [date] [count] [authors] [tags + subject]
"""

from __future__ import annotations

import html

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from ...db import ThreadSummary  # noqa: E402
from ...utils import tags as tagutils  # noqa: E402
from ...utils.dates import pretty_date  # noqa: E402


class RowConfig:
    def __init__(self, config):
        c = config.config
        self.font_description = c.get_str("thread_index.cell.font_description")
        self.date_length = c.get_int("thread_index.cell.date_length")
        self.message_count_length = c.get_int("thread_index.cell.message_count_length")
        self.authors_length = c.get_int("thread_index.cell.authors_length")
        self.tags_length = c.get_int("thread_index.cell.tags_length")
        self.show_left_icons = c.get_bool("thread_index.cell.show_left_icons")
        self.subject_color = c.get_str("thread_index.cell.subject_color")
        self.subject_color_selected = c.get_str("thread_index.cell.subject_color_selected")
        self.background_color_marked = c.get_str("thread_index.cell.background_color_marked")
        self.hidden_tags = [t.strip() for t in
                            c.get_str("thread_index.cell.hidden_tags").split(",")
                            if t.strip()]
        self.same_year = c.get_str("general.time.same_year")
        self.diff_year = c.get_str("general.time.diff_year")
        self.clock_format = c.get_str("general.time.clock_format")


class ThreadRow(Gtk.Box):
    def __init__(self, cfg: RowConfig):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.cfg = cfg
        self.marked = False

        self.icons = Gtk.Label()
        self.icons.set_width_chars(2)
        self.date = Gtk.Label(xalign=0)
        self.date.set_width_chars(cfg.date_length)
        self.count = Gtk.Label(xalign=1)
        self.count.set_width_chars(cfg.message_count_length)
        self.authors = Gtk.Label(xalign=0)
        self.authors.set_width_chars(cfg.authors_length)
        self.authors.set_max_width_chars(cfg.authors_length)
        self.authors.set_ellipsize(Pango.EllipsizeMode.END)
        self.main = Gtk.Label(xalign=0)
        self.main.set_ellipsize(Pango.EllipsizeMode.END)
        self.main.set_hexpand(True)

        if cfg.font_description not in ("", "default"):
            attrs = Pango.AttrList()
            fd = Pango.FontDescription.from_string(cfg.font_description)
            attrs.insert(Pango.attr_font_desc_new(fd))
            for w in (self.date, self.count, self.authors, self.main):
                w.set_attributes(attrs)

        if cfg.show_left_icons:
            self.append(self.icons)
        self.append(self.date)
        self.append(self.count)
        self.append(self.authors)
        self.append(self.main)

    def bind(self, ts: ThreadSummary) -> None:
        cfg = self.cfg

        icons = ""
        if ts.flagged:
            icons += "★"
        if ts.attachment:
            icons += "📎"
        self.icons.set_text(icons)

        self.date.set_text(pretty_date(ts.newest_date, cfg.same_year,
                                       cfg.diff_year, cfg.clock_format))

        self.count.set_text(f"{ts.total_messages}" if ts.total_messages > 1 else "")

        bold = ts.unread
        authors = ", ".join(
            f"<b>{html.escape(a)}</b>" if unread else html.escape(a)
            for a, unread in ts.authors)
        self.authors.set_markup(authors)

        shown_tags = [t for t in ts.tags if t not in cfg.hidden_tags]
        tag_markup = tagutils.concat_tags_color(shown_tags, pango=True,
                                                maxlen=cfg.tags_length)
        subject = html.escape(ts.subject)
        if bold:
            subject = f"<b>{subject}</b>"
        else:
            subject = f'<span color="{cfg.subject_color}">{subject}</span>'

        sep = "  " if tag_markup else ""
        self.main.set_markup(f"{tag_markup}{sep}{subject}")
