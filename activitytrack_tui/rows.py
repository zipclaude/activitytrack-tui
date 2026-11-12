# SPDX-License-Identifier: GPL-3.0-or-later
"""
What the screen lists, without curses (tested on its own): the activities
running, then the Home list with its filters; each row with its text, its
colour and the actions it allows.
"""

from dataclasses import dataclass, field

from activitytrack import formats, model, texts
from activitytrack.app import one_line
from activitytrack.homelist import SHOWS, home
from activitytrack.i18n import _
from activitytrack.texts import hm

PERIODS = (-1, 24, 72, 168, 720, 0)
# the shortcuts on a selected activity: pause or resume, stop, travel (no suspend: it has no button)
SHORTCUTS = {"p": "pause", "s": "stop", "v": "travel"}


@dataclass
class Row:
    kind: str            # heading, entry, todo, stopped, empty
    text: str
    detail: str = ""
    right: str = ""
    colour: str = ""
    record: dict = field(default_factory=dict)
    state: str = ""      # the displayed state (model.display)

    @property
    def selectable(self):
        return self.kind in ("entry", "todo", "stopped")


def state_text(state, since=""):
    """"▶ In lavoro dalle 09:15": the symbol, the label and since when (§2.2)."""
    text = f"{texts.DISPLAY_SYMBOLS[state]} {texts.display_label(state)}"
    return text + (" " + _("dalle %s") % since if since else "")


def rows(app, period=-1, show=("TODO", "MISSING"), company=None, tag=None):
    out = []
    work = app.work
    running = work.running()
    out.append(Row("heading", _("In corso")))
    if not running:
        out.append(Row("empty", _("Nessuna attività in corso")))
    for e in running:
        info = app.entry_info(e)
        state = info["display"]
        since = info["since"][11:16] if info["since"] else ""
        detail = state_text(state, since)
        if info["needs_review"]:
            detail += " · " + _("da verificare")
        out.append(Row("entry", info["label"], detail, hm(info["work_min"] * 60_000),
                       texts.DISPLAY_COLOURS[state], e, state))
    for section, items in home(work, list(show), period, company, tag):
        out.append(Row("heading", texts.SECTIONS[section]))
        for kind, obj, extra in items:
            if kind == "todo":
                info = app.entry_info(obj)
                state = work.display(obj)[0]
                who = " · ".join(x for x in (info["client"] or info["company"], state_text(state),
                                             info["priority_label"] if info["priority"] else "") if x)
                out.append(Row("todo", info["activity"] or "—", who, info["when"],
                               texts.DISPLAY_COLOURS[state], obj, state))
            else:
                state = obj.get("_display") or model.DONE
                out.append(Row("stopped", one_line(app.label(obj)), state_text(state),
                               _("fino alle %s") % formats.time(extra), texts.DISPLAY_COLOURS[state], obj, state))
    return out


def actions(app, row):
    """[(label, action)] of a row: the buttons its state allows (texts.entry_actions) and the edits."""
    work = app.work
    rec = row.record
    if row.kind not in ("entry", "todo", "stopped"):
        return []
    out = [(a.label, ("act", a.verb, a.kind)) for a in texts.entry_actions(work, rec)]
    if rec.get("deletedUtcMs") is None:
        if row.kind == "todo" and rec.get("planDate") and work.policy.feature("planning") \
                and rec.get("status") == model.TODO:
            out.insert(-1 if out and out[-1][1][1] == "delete" else len(out), (_("Posticipa"), ("postpone",)))
        # an absence has no description or client
        if rec.get("kind") != model.ABSENCE:
            out += [(_("Modifica la descrizione"), ("describe",)), (_("Modifica il cliente"), ("client",))]
    return out


def period_label(period):
    return texts.WINDOWS[period]


def show_label(show):
    return ", ".join(texts.SHOWS[s] for s in SHOWS if s in show) or _("nessuno")


def next_period(period):
    return PERIODS[(PERIODS.index(period) + 1) % len(PERIODS)]
