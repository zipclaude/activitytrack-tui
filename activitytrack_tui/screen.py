# SPDX-License-Identifier: GPL-3.0-or-later
"""
The full screen (curses): a header with the sync's state, the rows of
rows.py with a selection, a footer with the keys; popups for the actions,
the choices and the texts to type. The sync runs in a thread: every 30 s,
and right after each change; what another interface writes (the desktop,
the command line) shows within a second.
"""

import curses
import threading
import time

from activitytrack import texts
from activitytrack.app import InputError, describe_error, parse_when
from activitytrack.i18n import _
from activitytrack.work import ACTIVE, PAUSE, STOP, TO_COMPLETE, TRAVEL, WORK, StartRequest, WorkError

from . import rows as R

TICK_MS = 1000
SYNC_EVERY_S = 30
_COLOURS = {"green": curses.COLOR_GREEN, "yellow": curses.COLOR_YELLOW, "blue": curses.COLOR_BLUE,
            "magenta": curses.COLOR_MAGENTA, "red": curses.COLOR_RED, "cyan": curses.COLOR_CYAN}


class Screen:
    def __init__(self, win, app):
        self.win, self.app = win, app
        self.period, self.show = -1, ["TODO", "MISSING"]
        self.rows, self.sel, self.top = [], 0, 0
        self.message, self.message_colour = "", ""
        self.sync_state, self.sync_detail = "idle", ""
        self._sync_lock = threading.Lock()
        self._sync_wanted = threading.Event()
        self._last_sync = 0.0
        self._version = None
        self._stop = False
        self.pairs = {}

    # ---- set-up and the loop ----

    def _colours(self):
        if not curses.has_colors():
            return
        curses.start_color()
        try:
            curses.use_default_colors()
            background = -1
        except curses.error:
            background = curses.COLOR_BLACK
        for i, (name, colour) in enumerate(_COLOURS.items(), 1):
            curses.init_pair(i, colour, background)
            self.pairs[name] = curses.color_pair(i)

    def attr(self, colour, bold=False):
        a = curses.A_BOLD if bold else 0
        if colour == "dim":
            return a | curses.A_DIM
        return a | self.pairs.get(colour, 0)

    def run(self):
        curses.curs_set(0)
        self._colours()
        self.win.keypad(True)
        self.win.timeout(TICK_MS)
        threading.Thread(target=self._sync_loop, daemon=True).start()
        self.want_sync()
        self.refresh()
        while True:
            self.draw()
            try:
                key = self.win.get_wch()
            except curses.error:
                key = None
            if key is None:
                self._tick()
                continue
            if key in ("q", "Q", "\x1b"):
                self._stop = True
                return
            self.message = ""
            try:
                self.key(key)
            except (InputError, WorkError) as e:
                self.say(describe_error(e), "red")

    def _tick(self):
        """Every second: the clock moves, another interface may have written."""
        version = self.app.store.db.execute("PRAGMA data_version").fetchone()[0]
        if version != self._version:
            self._version = version
            self.app.refresh_policy()
            if self.app.store.has_pending():
                self.want_sync()
        self.refresh()

    def refresh(self):
        selected = self.rows[self.sel].record.get("uid") if self.rows and self.sel < len(self.rows) else None
        self.rows = R.rows(self.app, self.period, self.show)
        # the selection follows its record
        for i, r in enumerate(self.rows):
            if r.selectable and r.record.get("uid") == selected:
                self.sel = i
                break
        else:
            self.sel = next((i for i, r in enumerate(self.rows) if r.selectable), 0)

    def say(self, text, colour=""):
        self.message, self.message_colour = text, colour

    # ---- the sync, in its own thread ----

    def want_sync(self):
        self._sync_wanted.set()

    def _sync_loop(self):
        while not self._stop:
            asked = self._sync_wanted.wait(1.0)
            if not asked and time.monotonic() - self._last_sync < SYNC_EVERY_S:
                continue
            self._sync_wanted.clear()
            if not self.app.paired:
                continue
            with self._sync_lock:
                try:
                    self.sync_state, self.sync_detail = self.app.runner.run(full=asked)
                finally:
                    # this thread's connection
                    self.app.store.close()
                self._last_sync = time.monotonic()

    # ---- drawing ----

    def draw(self):
        win = self.win
        win.erase()
        h, w = win.getmaxyx()
        if h < 6 or w < 30:
            self._put(0, 0, _("Finestra troppo piccola."), 0, w)
            win.refresh()
            return
        sync = texts.sync_state(self.app.session, self.sync_state, self.sync_detail)
        attention = texts.sync_needs_attention(self.app.session, self.sync_state)
        head = " ActivityTrack"
        self._put(0, 0, head.ljust(w), curses.A_REVERSE | curses.A_BOLD, w)
        right = sync[: max(0, w - len(head) - 3)]
        self._put(0, max(len(head) + 2, w - len(right) - 1), right,
                  curses.A_REVERSE | (self.pairs.get("red", 0) if attention else 0), w)
        filters = f" {R.period_label(self.period)} · {_('Stato: %s') % R.show_label(self.show)}"
        self._put(1, 0, filters, self.attr("cyan"), w)
        body_top, body_h = 2, h - 4
        lines = []
        for i, r in enumerate(self.rows):
            lines.append((i, 0))
            if r.detail and r.kind != "heading":
                lines.append((i, 1))
        # scroll so that the selection is in sight
        sel_line = next((n for n, (i, part) in enumerate(lines) if i == self.sel and part == 0), 0)
        if sel_line < self.top:
            self.top = sel_line
        if sel_line + 1 >= self.top + body_h:
            self.top = sel_line + 2 - body_h
        self.top = max(0, min(self.top, max(0, len(lines) - body_h)))
        for n, (i, part) in enumerate(lines[self.top:self.top + body_h]):
            self._row(body_top + n, self.rows[i], part, i == self.sel, w)
        if self.message:
            self._put(h - 2, 1, self.message, self.attr(self.message_colour, bold=True), w)
        keys = _("⏎ azioni  n nuova attività  t nuovo Da fare  p pausa  s stop  v viaggio  d fatto  f periodo  "
                 "g stato  r sincronizza  q esci")
        self._put(h - 1, 0, (" " + keys).ljust(w), curses.A_REVERSE, w)
        win.refresh()

    def _row(self, y, row, part, selected, w):
        if row.kind == "heading":
            self._put(y, 1, row.text.upper(), curses.A_BOLD | self.attr("dim"), w)
            return
        if row.kind == "empty":
            self._put(y, 3, row.text, self.attr("dim"), w)
            return
        mark = curses.A_REVERSE if selected else 0
        if part == 0:
            ident = row.record.get("uid", "")[:6]
            right = row.right
            text = f" {ident}  {row.text}"
            room = w - len(right) - 3
            text = text[:room] if len(text) > room else text.ljust(room)
            self._put(y, 1, text, mark | curses.A_BOLD, w)
            self._put(y, w - len(right) - 2, right, mark | self.attr("dim"), w)
        else:
            self._put(y, 10, row.detail, self.attr(row.colour), w)

    def _put(self, y, x, text, attr, w):
        try:
            self.win.addnstr(y, x, text, max(0, w - x - 1), attr)
        except curses.error:
            pass

    # ---- keys ----

    def key(self, key):
        selectable = [i for i, r in enumerate(self.rows) if r.selectable]
        if key in (curses.KEY_DOWN, "j"):
            later = [i for i in selectable if i > self.sel]
            self.sel = later[0] if later else self.sel
        elif key in (curses.KEY_UP, "k"):
            earlier = [i for i in selectable if i < self.sel]
            self.sel = earlier[-1] if earlier else self.sel
        elif key in (curses.KEY_RESIZE,):
            pass
        elif key in ("\n", "\r", curses.KEY_ENTER):
            self._actions()
        elif key == "n":
            self._new_entry()
        elif key == "t":
            self._new_todo()
        elif key == "f":
            self.period = R.next_period(self.period)
        elif key == "g":
            self._choose_show()
        elif key == "r":
            self.want_sync()
            self.say(_("Sincronizzazione…"))
        elif key in R.SHORTCUTS:
            self._shortcut(R.SHORTCUTS[key])
        elif key == "d":
            row = self.current()
            if row and row.kind == "todo":
                self._do(("todo_done",), row)
        else:
            return
        self.refresh()

    def current(self):
        if 0 <= self.sel < len(self.rows) and self.rows[self.sel].selectable:
            return self.rows[self.sel]
        return None

    def _shortcut(self, what):
        row = self.current()
        if not row or row.kind != "entry":
            entry = self.app.running_one(active_only=True)
        else:
            entry = row.record
        if what == "pause":
            cmd = WORK if entry.get("mode") == PAUSE or entry.get("status") != ACTIVE else PAUSE
        else:
            cmd = what
        self._command(entry, cmd)

    def _command(self, entry, cmd):
        status = self.app.work.command(entry["uid"], cmd)
        self.changed()
        if cmd == STOP and status == TO_COMPLETE:
            self._complete(self.app.work.entry(entry["uid"]))

    def changed(self):
        self.want_sync()
        self.refresh()

    # ---- actions ----

    def _actions(self):
        row = self.current()
        if not row:
            return
        acts = R.actions(self.app, row)
        i = self.menu(row.text, [label for label, _a in acts])
        if i is not None:
            self._do(acts[i][1], row)

    def _do(self, action, row):
        app, rec = self.app, row.record
        kind = action[0]
        if kind == "command":
            self._command(rec, action[1])
        elif kind == "describe":
            text = self.prompt(_("Descrizione"), rec.get("activity") or "")
            if text is not None:
                app.work.edit(rec["uid"], activity=text)
        elif kind == "client":
            client = self.pick_client()
            if client is not None:
                app.work.edit(rec["uid"], client=client)
        elif kind == "complete":
            self._complete(rec)
        elif kind == "todo_start":
            running = [e for e in app.work.running() if e.get("status") == ACTIVE]
            # as the desktop: the ones at work or travelling suspended, a paused one left as it is
            others = {e["uid"]: "SUSPEND" for e in running if e.get("mode") != PAUSE}
            app.work.start(StartRequest(activity=rec.get("title") or "", client=rec.get("clientUid"),
                                        company=rec.get("companyUid"), todo=rec["uid"], others=others))
        elif kind == "todo_done":
            app.todos.set_done(rec["uid"])
        elif kind == "todo_postpone":
            app.todos.postpone(rec["uid"])
        elif kind == "todo_ignore":
            app.todos.set_ignored(rec["uid"])
        elif kind == "todo_reopen":
            app.todos.reopen(rec["uid"])
        self.changed()

    def _complete(self, entry):
        """The stop sheet: description, client, the to-do done."""
        text = self.prompt(_("Completa l'attività — descrizione"), entry.get("activity") or "")
        if text is None:
            return
        client = entry.get("clientUid") or self.pick_client()
        self.app.work.edit(entry["uid"], activity=text, client=client or "")
        todo = self.app.store.get("todo", entry["todoUid"]) if entry.get("todoUid") else None
        if todo and not todo.get("done"):
            if self.menu(_("Il Da fare «%s» è completato") % todo.get("title"), [_("Sì"), _("No")]) == 0:
                self.app.todos.set_done(todo["uid"])
        if text:
            self.app.work.complete(entry["uid"])
            self.say(_("Attività chiusa."), "green")
        else:
            self.say(_("Resta tra i dettagli mancanti."), "yellow")
        self.changed()

    def _new_entry(self):
        text = self.prompt(_("Nuova attività — descrizione"))
        if text is None:
            return
        client = self.pick_client()
        if client is None:
            return
        mode = WORK
        if self.app.work.policy.feature("travel"):
            choice = self.menu(_("Tipo"), [texts.KINDS[WORK], texts.KINDS[TRAVEL]])
            if choice is None:
                return
            mode = (WORK, TRAVEL)[choice]
        when = self.prompt(_("Iniziata alle (vuoto: adesso)"))
        if when is None:
            return
        running = [e for e in self.app.work.running() if e.get("status") == ACTIVE]
        # as the desktop: the ones at work or travelling suspended, a paused one left as it is
        others = {e["uid"]: "SUSPEND" for e in running if e.get("mode") != PAUSE}
        self.app.work.start(StartRequest(activity=text, client=client, mode=mode, others=others,
                                         start_ms=parse_when(when) if when else None))
        self.say(_("Attività avviata."), "green")
        self.changed()

    def _new_todo(self):
        title = self.prompt(_("Nuovo Da fare — titolo"))
        if not title:
            return
        client = self.pick_client(allow_none=True)
        self.app.todos.save(None, title, client=client)
        self.changed()

    def _choose_show(self):
        from activitytrack.todos import SHOWS
        options = [("✓ " if s in self.show else "  ") + texts.SHOWS[s] for s in SHOWS]
        i = self.menu(_("Stato"), options)
        if i is not None:
            s = SHOWS[i]
            self.show = [x for x in self.show if x != s] if s in self.show else self.show + [s]

    # ---- popups ----

    def _box(self, height, width, title):
        h, w = self.win.getmaxyx()
        height, width = min(height, h - 2), min(width, w - 4)
        box = curses.newwin(height, width, max(1, (h - height) // 2), max(2, (w - width) // 2))
        box.keypad(True)
        box.box()
        try:
            box.addnstr(0, 2, f" {title} ", width - 4, curses.A_BOLD)
        except curses.error:
            pass
        return box, height, width

    def menu(self, title, options):
        """The index chosen, or None (Esc, q)."""
        if not options:
            return None
        sel = 0
        while True:
            box, height, width = self._box(len(options) + 2, max(len(title), *(len(o) for o in options)) + 8, title)
            for i, o in enumerate(options[: height - 2]):
                try:
                    box.addnstr(1 + i, 2, f"{i + 1:>2} {o}", width - 4, curses.A_REVERSE if i == sel else 0)
                except curses.error:
                    pass
            box.refresh()
            key = box.get_wch()
            if key in ("\x1b", "q"):
                return None
            if key in ("\n", "\r", curses.KEY_ENTER):
                return sel
            if key in (curses.KEY_DOWN, "j"):
                sel = min(sel + 1, len(options) - 1)
            elif key in (curses.KEY_UP, "k"):
                sel = max(sel - 1, 0)
            elif isinstance(key, str) and key.isdigit() and 1 <= int(key) <= len(options):
                return int(key) - 1

    def prompt(self, label, default=""):
        """A line typed (Enter); None with Esc."""
        text = list(default)
        box, _height, width = self._box(3, max(50, len(label) + 6), label)
        curses.curs_set(1)
        try:
            while True:
                shown = "".join(text)[-(width - 5):]
                box.addnstr(1, 2, shown.ljust(width - 4), width - 4)
                box.move(1, 2 + len(shown))
                box.refresh()
                key = box.get_wch()
                if key in ("\n", "\r", curses.KEY_ENTER):
                    return "".join(text).strip()
                if key == "\x1b":
                    return None
                if key in (curses.KEY_BACKSPACE, "\x7f", "\b", "\x08"):
                    if text:
                        text.pop()
                elif isinstance(key, str) and key.isprintable():
                    text.append(key)
        finally:
            curses.curs_set(0)

    def pick_client(self, allow_none=False):
        """A client: typing filters the list; a name not there becomes a new one (the policy permitting)."""
        clients = self.app.work.masters("client")
        typed, sel = "", 0
        title = texts.FIELDS["client"]
        while True:
            found = [c for c in clients if typed.casefold() in (c.get("name") or "").casefold()]
            options = [c.get("name") or "" for c in found[:12]]
            if typed and typed.casefold() not in [o.casefold() for o in options]:
                options.append(_("Nuovo: %s") % typed)
            if allow_none and not typed:
                options.insert(0, _("(nessuno)"))
            sel = min(sel, max(0, len(options) - 1))
            box, height, width = self._box(len(options) + 3, 60, title)
            box.addnstr(1, 2, "> " + typed, width - 4, curses.A_BOLD)
            for i, o in enumerate(options[: height - 3]):
                try:
                    box.addnstr(2 + i, 2, o, width - 4, curses.A_REVERSE if i == sel else 0)
                except curses.error:
                    pass
            box.refresh()
            key = box.get_wch()
            if key == "\x1b":
                return None
            if key in ("\n", "\r", curses.KEY_ENTER):
                if not options:
                    continue
                if allow_none and not typed and sel == 0:
                    return ""
                base = 1 if allow_none and not typed else 0
                if sel - base < len(found[:12]):
                    return found[sel - base]["uid"]
                return typed
            if key in (curses.KEY_DOWN,):
                sel += 1
            elif key in (curses.KEY_UP,):
                sel = max(0, sel - 1)
            elif key in (curses.KEY_BACKSPACE, "\x7f", "\b", "\x08"):
                typed, sel = typed[:-1], 0
            elif isinstance(key, str) and key.isprintable():
                typed, sel = typed + key, 0
