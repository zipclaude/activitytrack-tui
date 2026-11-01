# SPDX-License-Identifier: GPL-3.0-or-later
"""
What the screen lists, without curses (tested on its own): the activities
running, then the Home list with its filters; each row with its text, its
colour and the actions it allows.
"""

from dataclasses import dataclass, field

from activitytrack import texts
from activitytrack.app import one_line
from activitytrack.i18n import _
from activitytrack.texts import hm
from activitytrack.todos import SHOWS
from activitytrack.work import ACTIVE, CLOSED, PAUSE, STOP, SUSPEND, TO_COMPLETE, TRAVEL, WORK

PERIODS = (-1, 24, 72, 168, 720, 0)
# the app's colours of the modes and states
COLOURS = {WORK: "green", PAUSE: "yellow", TRAVEL: "blue", "SUSPENDED": "magenta", TO_COMPLETE: "red"}


@dataclass
class Row:
    kind: str            # heading, entry, todo, stopped, empty
    text: str
    detail: str = ""
    right: str = ""
    colour: str = ""
    record: dict = field(default_factory=dict)

    @property
    def selectable(self):
        return self.kind in ("entry", "todo", "stopped")


def rows(app, period=-1, show=("TODO", "MISSING"), company=None, tag=None):
    out = []
    running = app.work.running()
    out.append(Row("heading", _("In corso")))
    if not running:
        out.append(Row("empty", _("Nessuna attività in corso")))
    for e in running:
        info = app.entry_info(e)
        status = e.get("status")
        colour = COLOURS.get(e.get("mode") if status == ACTIVE else status, "")
        since = info["since"][11:16] if info["since"] else ""
        detail = info["state"] + (" " + _("dalle %s") % since if since else "")
        if info["needs_review"]:
            detail += " · " + _("da verificare")
        out.append(Row("entry", info["label"], detail, hm(info["work_min"] * 60_000), colour, e))
    for section, items in app.todos.home(list(show), period, company, tag):
        out.append(Row("heading", texts.SECTIONS[section]))
        for kind, obj, extra in items:
            if kind == "todo":
                info = app.todo_info(obj)
                who = " · ".join(x for x in (info["client"] or info["company"], info["state_label"],
                                             f"P{info['priority']}" if info["priority"] else "") if x)
                colour = "red" if info["state"] == "overdue" else "dim" if info["state"] in ("done", "ignored") else ""
                out.append(Row("todo", info["title"], who, info["when"], colour, obj))
            else:
                missing = obj.get("status") == TO_COMPLETE
                out.append(Row("stopped", one_line(app.work.label(obj)), texts.state_label(obj),
                               _("fino alle %s") % extra.strftime("%H:%M"), "red" if missing else "dim", obj))
    return out


def actions(app, row):
    """[(label, action)] of a row: the commands of its state, as the app's."""
    policy = app.work.policy
    if row.kind == "entry":
        out = [(label, ("command", cmd)) for label, cmd in texts.entry_commands(row.record, policy)]
        return out + [(_("Modifica la descrizione"), ("describe",)), (_("Modifica il cliente"), ("client",))]
    if row.kind == "stopped":
        out = []
        if row.record.get("status") == TO_COMPLETE:
            out.append((_("Completa…"), ("complete",)))
        if row.record.get("status") in (TO_COMPLETE, CLOSED):
            out.append((_("Riapri"), ("command", "REOPEN")))
        return out + [(_("Modifica la descrizione"), ("describe",))]
    if row.kind == "todo":
        t = row.record
        if t.get("done") or t.get("ignoredUtcMs"):
            return [(_("Riapri"), ("todo_reopen",))]
        out = []
        if policy.feature("entries"):
            out.append((_("Avvia"), ("todo_start",)))
        out.append((_("Fatto"), ("todo_done",)))
        if t.get("planDate") and policy.feature("planning"):
            out.append((_("Posticipa"), ("todo_postpone",)))
        if policy.feature("ignoreTodos"):
            out.append((_("Ignora"), ("todo_ignore",)))
        return out
    return []


def period_label(period):
    return texts.WINDOWS[period]


def show_label(show):
    return ", ".join(texts.SHOWS[s] for s in SHOWS if s in show) or _("nessuno")


def next_period(period):
    return PERIODS[(PERIODS.index(period) + 1) % len(PERIODS)]


# the shortcuts on a selected activity
SHORTCUTS = {"p": "pause", "s": STOP, "v": TRAVEL, "u": SUSPEND}
