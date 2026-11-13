# SPDX-License-Identifier: GPL-3.0-or-later
"""
The full screen (curses): a header with the sync's state, the rows of
rows.py with a selection, a footer with the keys; popups for the actions,
the choices and the texts to type. The sync runs in a thread: every 30 s,
and right after each change; what another interface writes (the desktop,
the command line) shows within a second. After a sync that did not go,
the next automatic one waits longer each time (30 s … 5 min); a change
here or "r" tries again at once.
"""

import curses
import logging
import textwrap
import threading
import time

import activitytrack
from activitytrack import model, texts, updates
from activitytrack.app import InputError, describe_error, parse_day, parse_minute, parse_when
from activitytrack.homelist import SHOWS
from activitytrack.i18n import _
from activitytrack.manifest import ManifestError
from activitytrack.work import PAUSE, TRAVEL, WORK, StartRequest, WorkError

from . import rows as R

TICK_MS = 1000
SYNC_EVERY_S = 30
# the waits after a sync that did not go, one more at each failure
BACKOFF_S = (30, 60, 120, 300)
# a change seen this soon after a sync is the sync's own (last_sync, the pull)
OWN_WRITE_S = 2
# below it the screen only says it is too small, and no popup opens
MIN_H, MIN_W = 6, 30
log = logging.getLogger(__name__)
_COLOURS = {"green": curses.COLOR_GREEN, "yellow": curses.COLOR_YELLOW, "blue": curses.COLOR_BLUE,
            "magenta": curses.COLOR_MAGENTA, "red": curses.COLOR_RED, "cyan": curses.COLOR_CYAN}
# the colours of texts.DISPLAY_COLOURS beyond the eight: (256-colour number, the eight-colour fallback);
# "white" is the terminal's own foreground
_EXTRA = {"orange": (208, "yellow"), "brown": (130, "red"), "pink": (205, "magenta"), "greenish": (71, "green"),
          "reddish": (167, "red")}


class Screen:
    def __init__(self, win, app):
        self.win, self.app = win, app
        self.period, self.show = -1, ["TODO", "MISSING"]
        self.rows, self.sel, self.top = [], 0, 0
        self.message, self.message_colour = "", ""
        self.notes = []
        self.sync_state, self.sync_detail = "idle", ""
        self._sync_lock = threading.Lock()
        self._sync_wanted = threading.Event()
        self._wanted_lock = threading.Lock()
        self._reset = False
        # the failures in a row, and when the next automatic sync may start
        self._failures, self._next_sync = 0, 0.0
        self._synced_at = float("-inf")
        self.clock = time.monotonic
        self._version = None
        self._stop = False
        self._thread = None
        self.pairs = {}
        # what the last sync brought of a newer version (updates.Status), for the header
        self.news = None

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
        wide = curses.COLORS >= 256
        for i, (name, (number, fallback)) in enumerate(_EXTRA.items(), len(_COLOURS) + 1):
            if wide:
                curses.init_pair(i, number, background)
                self.pairs[name] = curses.color_pair(i)
            else:
                self.pairs[name] = self.pairs.get(fallback, 0)

    def attr(self, colour, bold=False):
        a = curses.A_BOLD if bold else 0
        if colour in ("dim", "grey"):
            return a | curses.A_DIM
        return a | self.pairs.get(colour, 0)

    def run(self):
        curses.curs_set(0)
        self._colours()
        self.win.keypad(True)
        self.win.timeout(TICK_MS)
        self._thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._thread.start()
        self.want_sync()
        self.news = self._news()
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
                self._show_notes()
            except Exception as e:
                # a bug, or the data locked for too long: said and logged, the screen stays
                log.exception("unexpected error")
                self.say(_("Errore inatteso: %s") % f"{type(e).__name__}: {e}", "red")
                self._show_notes()

    def _tick(self):
        """Every second: the clock moves, another interface may have written."""
        version = self.app.store.db.execute("PRAGMA data_version").fetchone()[0]
        if version != self._version:
            self._version = version
            self.app.refresh_policy()
            own = self._sync_lock.locked() or self.clock() - self._synced_at < OWN_WRITE_S
            if self.app.store.has_pending() and not own:
                # another interface's change: as the periodic sync, it waits after a failure
                self.want_sync(reset=False)
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

    def _news(self):
        """The release the last sync announced, or None."""
        try:
            status = self.app.updates.status()
        except Exception:
            log.exception("update status")
            return None
        return status.release if status.state == updates.AVAILABLE else None

    def say(self, text, colour=""):
        self.message, self.message_colour = text, colour

    def _show_notes(self):
        """What the command left to tell (an end moved back, a planned segment removed) follows its message."""
        if self.notes:
            self.message = " ".join(filter(None, [self.message] + self.notes))
            self.message_colour = self.message_colour or "yellow"

    # ---- the sync, in its own thread ----

    def want_sync(self, reset=True):
        """A sync now; [reset] (a change here, "r") also ends the wait after a failure."""
        with self._wanted_lock:
            self._reset = self._reset or reset
            self._sync_wanted.set()

    def _sync_loop(self):
        while not self._stop:
            self._sync_once(self._sync_wanted.wait(1.0))

    def _sync_once(self, asked):
        """One turn of the sync thread: a sync when asked or due, unless waiting after a failure."""
        with self._wanted_lock:
            reset, self._reset = self._reset, False
            self._sync_wanted.clear()
        if reset:
            self._failures = 0
        due = self.clock() >= self._next_sync
        if not (reset or due or (asked and not self._failures)):
            return
        if not self.app.paired:
            return
        with self._sync_lock:
            try:
                self.sync_state, self.sync_detail = self.app.runner.run(full=asked)
                # the server's announcement came with it: checked here, in the thread that has the data
                self.news = self._news()
            finally:
                # this thread's connection
                self.app.store.close()
            if self.sync_state in ("ok", "idle"):
                self._failures, wait = 0, SYNC_EVERY_S
            else:
                wait = BACKOFF_S[min(self._failures, len(BACKOFF_S) - 1)]
                self._failures += 1
            self._synced_at = self.clock()
            self._next_sync = self._synced_at + wait

    # ---- drawing ----

    def draw(self):
        win = self.win
        win.erase()
        h, w = win.getmaxyx()
        if h < MIN_H or w < MIN_W:
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
        news = self.news
        if news is not None:
            note = _("Versione %s disponibile: a") % news.version
            self._put(1, max(len(filters) + 2, w - len(note) - 1), note, self.attr("yellow", bold=True), w)
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
        keys = _("⏎ azioni  n nuovo  p pausa  s stop  v viaggio  d fatto  f periodo  "
                 "g stato  r sincronizza  a aggiornamenti  q esci")
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
        try:
            self._key(key)
        finally:
            # the notices of the commands the key ran, also when one failed after the first
            self.notes = self.app.work.take_notices()
            self._show_notes()

    def _key(self, key):
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
            self._new()
        elif key == "t":
            self._new_todo()
        elif key == "f":
            self.period = R.next_period(self.period)
        elif key == "g":
            self._choose_show()
        elif key == "r":
            # the notices of the sync are in the header, read: gone (those of the sync to come stay)
            self.app.store.dismiss_notices()
            self.want_sync()
            self.say(_("Sincronizzazione…"))
        elif key == "a":
            self._updates()
        elif key in R.SHORTCUTS:
            self._shortcut(R.SHORTCUTS[key])
        elif key == "d":
            row = self.current()
            if row and row.kind == "todo" and row.state != "DELETED":
                self._do(("act", "tick", None), row)
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
        acts = texts.entry_actions(self.app.work, entry)
        if what == "stop":
            pick = next((a for a in acts if a.verb == "stop"), None)
        elif what == "travel":
            pick = next((a for a in acts if a.kind == TRAVEL), None)
        else:
            # pause, or resume from it: the second button of a pause is "Riprendi"
            pick = next((a for a in acts if a.kind == PAUSE or a.label == _("Riprendi")), None)
        if pick is None:
            raise InputError(_("Comando non possibile in questo stato."))
        self._act(entry, pick.verb, pick.kind)

    def _ask(self, title, options):
        """The index chosen of a question's answers, None when cancelled."""
        return self.menu(title, options)

    def _act(self, entry, verb, kind):
        """One button of an activity, with the questions of the spec (§3, §3.2) put first."""
        work, uid = self.app.work, entry["uid"]
        if verb == "travel_from_work":
            i = self._ask(_("Hai completato l'attività lavorativa?"), [_("Sì"), _("No")])
            if i is None:
                return
            work.travel_from_work(uid, i == 0)
        elif verb == "stop" and work.return_trip_offered(uid):
            options = [_("Avvia viaggio di ritorno"), _("Termina l'attività")]
            if work.return_trip_addable(uid):
                options.append(_("Termina l'attività ed aggiungi il viaggio di ritorno"))
            i = self._ask(_("Terminare l'attività?"), options)
            if i is None:
                return
            if i == 0:
                work.travel_back(uid)
            else:
                work.stop(uid, return_trip=i == 2)
        elif verb in ("reopen", "delete"):
            question = _("Riaprire l'attività?") if verb == "reopen" else _("Eliminare l'attività?")
            if self._ask(question, [_("Sì"), _("No")]) != 0:
                return
            work.perform(uid, verb, kind)
        else:
            work.perform(uid, verb, kind)
        self.changed()

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
        if kind == "act":
            self._act(rec, action[1], action[2])
            return
        if kind == "describe":
            text = self.prompt(_("Descrizione"), rec.get("activity") or "")
            if text is not None:
                app.work.edit(rec["uid"], activity=text)
        elif kind == "client":
            client = self.pick_client()
            if client is not None:
                app.work.edit(rec["uid"], client=client)
        elif kind == "postpone":
            app.work.postpone(rec["uid"])
        self.changed()

    def _updates(self):
        """Asks the server for its manifest (the key of the pairing checks it) and offers the download."""
        self.say(_("Controllo…"))
        self.draw()
        status = self.app.updates.check()
        self.news = status.release if status.state == updates.AVAILABLE else None
        text = texts.update_state(status, activitytrack.client()["version"])
        # the box's width: the text in lines, then the choices
        lines = textwrap.wrap(text, 56) or [text]
        options = lines + ([_("Scarica")] if status.downloadable else [])
        # Enter right away downloads: the selection starts on "Scarica", not on the text
        i = self.menu(_("Aggiornamenti"), options, selected=len(lines) if status.downloadable else 0)
        self.say("")
        if i is None or i < len(lines):
            return
        self.say(_("Scarico…"))
        self.draw()
        try:
            path = self.app.updates.download(status.release)
        except ManifestError as e:
            self.say(_("Scaricamento non riuscito: %s") % e, "red")
        else:
            self.say(_("Scaricato e controllato: %s") % path, "green")

    def _new(self):
        """The four creations (§3): Da fare, Lavoro, Viaggio, Assenza."""
        policy = self.app.work.policy
        modes = [("todo", _("Da fare")), ("work", texts.KINDS[WORK])]
        if policy.feature("travel"):
            modes.append(("travel", texts.KINDS[TRAVEL]))
        modes.append(("absence", texts.KINDS[model.ABSENCE]))
        i = self.menu(_("Nuovo"), [label for _m, label in modes])
        if i is None:
            return
        {"todo": self._new_todo, "work": self._new_work, "travel": self._new_travel,
         "absence": self._new_absence}[modes[i][0]]()

    def _new_work(self):
        text = self.prompt(_("Nuova attività — descrizione"))
        if text is None:
            return
        client = self.pick_client()
        if client is None:
            return
        when = self.menu(_("Quando"), [_("Adesso"), _("Già iniziata"), _("Passata")])
        if when is None:
            return
        start = end = None
        if when:
            start = self.prompt(_("Iniziata alle (HH:MM, o giorno e ora)"))
            if not start:
                return
            start = parse_when(start)
        if when == 2:
            end = self.prompt(_("Finita alle (HH:MM, o giorno e ora)"))
            if not end:
                return
            end = parse_when(end)
        self.app.work.start(StartRequest(activity=text, client=client, mode=WORK, start_ms=start, end_ms=end))
        self.say(_("Attività salvata.") if end else _("Attività avviata."), "green")
        self.changed()

    def _new_travel(self):
        text = self.prompt(_("Nuovo viaggio — descrizione (facoltativa)"))
        if text is None:
            return
        self.app.work.start(StartRequest(activity=text, mode=TRAVEL))
        self.say(_("Attività avviata."), "green")
        self.changed()

    def _new_absence(self):
        work = self.app.work
        company = self.pick("company", texts.FIELDS["company"], allow_none=True)
        if company is None:
            return
        kinds = list(model.ABSENCE_KINDS)
        k = self.menu(_("Tipo di assenza"), [texts.ABSENCE_KINDS[x] for x in kinds])
        if k is None:
            return
        first = self.prompt(_("Dal giorno (per esempio oggi, 3/11)"), "oggi")
        if not first:
            return
        last = self.prompt(_("Al giorno (vuoto: lo stesso)"))
        if last is None:
            return
        times = self.prompt(_("Orario, da-a (per esempio 9:00-11:30; vuoto: la giornata)"))
        if times is None:
            return
        start_min = end_min = None
        if times:
            parts = [x.strip() for x in times.replace("–", "-").split("-")]
            if len(parts) != 2:
                raise InputError(_("Orario non valido: «%s» (per esempio 9:30).") % times)
            start_min, end_min = parse_minute(parts[0]), parse_minute(parts[1])
        first_day = parse_day(first)
        work.save_absence(None, first_day, parse_day(last) if last else None, company=company or None,
                          absence_kind=kinds[k], start_min=start_min, end_min=end_min)
        self.say(_("Assenza salvata."), "green")
        self.changed()

    def _new_todo(self):
        title = self.prompt(_("Nuovo Da fare — titolo"))
        if not title:
            return
        client = self.pick_client(allow_none=True)
        if client is None:
            return
        p = self.menu(_("Priorità"), [texts.PRIORITIES[n] for n in model.PRIORITIES])
        if p is None:
            return
        plan = self.prompt(_("Pianificazione: giorno [ora [durata in minuti]] (vuoto: nessuna)"))
        if plan is None:
            return
        due = self.prompt(_("Scadenza: giorno [ora] (vuoto: nessuna)"))
        if due is None:
            return
        kw = {}
        if plan.split():
            parts = plan.split()
            kw["plan_date"] = parse_day(parts[0])
            kw["plan_min"] = parse_minute(parts[1]) if len(parts) > 1 else None
            kw["duration_min"] = int(parts[2]) if len(parts) > 2 and parts[2].isdecimal() else None
        if due.split():
            parts = due.split()
            kw["due_date"] = parse_day(parts[0])
            kw["due_min"] = parse_minute(parts[1]) if len(parts) > 1 else None
        self.app.work.save_todo(None, title, client=client or None, priority=p, **kw)
        self.changed()

    def _choose_show(self):
        options = [("✓ " if s in self.show else "  ") + texts.SHOWS[s] for s in SHOWS]
        i = self.menu(_("Stato"), options)
        if i is not None:
            s = SHOWS[i]
            self.show = [x for x in self.show if x != s] if s in self.show else self.show + [s]

    # ---- popups ----

    def _box(self, height, width, title):
        """The popup's window; None when the terminal is too small for one (the popup is cancelled)."""
        h, w = self.win.getmaxyx()
        if h < MIN_H or w < MIN_W:
            self.say(_("Finestra troppo piccola."), "red")
            return None, 0, 0
        height, width = min(height, h - 2), min(width, w - 4)
        box = curses.newwin(height, width, max(1, (h - height) // 2), max(2, (w - width) // 2))
        box.keypad(True)
        box.box()
        try:
            box.addnstr(0, 2, f" {title} ", width - 4, curses.A_BOLD)
        except curses.error:
            pass
        return box, height, width

    def menu(self, title, options, selected=0):
        """The index chosen, or None (Esc, q); [selected] is the entry the selection starts on."""
        if not options:
            return None
        sel, top = min(max(selected, 0), len(options) - 1), 0
        while True:
            box, height, width = self._box(len(options) + 2, max(len(title), *(len(o) for o in options)) + 8, title)
            if box is None:
                return None
            # a long list scrolls with the selection
            top = _scrolled(top, sel, height - 2)
            for n, o in enumerate(options[top:top + height - 2]):
                i = top + n
                try:
                    box.addnstr(1 + n, 2, f"{i + 1:>2} {o}", width - 4, curses.A_REVERSE if i == sel else 0)
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
            # ASCII digits only: "²" is a digit to isdigit() but not to int()
            elif isinstance(key, str) and key.isascii() and key.isdecimal() and 1 <= int(key) <= len(options):
                return int(key) - 1

    def prompt(self, label, default=""):
        """
        A line typed (Enter); None with Esc. The line breaks of a text written
        elsewhere (the desktop) show as ⏎ and stay in the text.
        """
        text = list(default)
        box, _height, width = self._box(3, max(50, len(label) + 6), label)
        if box is None:
            return None
        curses.curs_set(1)
        try:
            while True:
                shown = "".join(text).replace("\n", "⏎")[-(width - 5):]
                try:
                    box.addnstr(1, 2, shown.ljust(width - 4), width - 4)
                    box.move(1, 2 + len(shown))
                except curses.error:
                    pass
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
        return self.pick("client", texts.FIELDS["client"], allow_none)

    def pick(self, type_, title, allow_none=False):
        """A record: typing filters the list; a name not there becomes a new one (the policy permitting)."""
        clients = self.app.work.masters(type_)
        typed, sel = "", 0
        top = 0
        while True:
            found = [c for c in clients if typed.casefold() in (c.get("name") or "").casefold()]
            # the client of that very name first, also when it would be beyond the twelve shown
            found.sort(key=lambda c: (c.get("name") or "").casefold() != typed.casefold())
            options = [c.get("name") or "" for c in found[:12]]
            if typed and typed.casefold() not in [o.casefold() for o in options]:
                options.append(_("Nuovo: %s") % typed)
            if allow_none and not typed:
                options.insert(0, _("(nessuno)"))
            sel = min(sel, max(0, len(options) - 1))
            box, height, width = self._box(len(options) + 3, 60, title)
            if box is None:
                return None
            top = _scrolled(top, sel, height - 3)
            try:
                box.addnstr(1, 2, "> " + typed, width - 4, curses.A_BOLD)
            except curses.error:
                pass
            for n, o in enumerate(options[top:top + height - 3]):
                try:
                    box.addnstr(2 + n, 2, o, width - 4, curses.A_REVERSE if top + n == sel else 0)
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


def _scrolled(top, sel, room):
    """The first option shown of a list in [room] lines, so that [sel] is in sight."""
    room = max(1, room)
    if sel < top:
        return sel
    if sel >= top + room:
        return sel - room + 1
    return top
