# SPDX-License-Identifier: GPL-3.0-or-later
import contextlib
import curses
import hashlib
import sqlite3
import tempfile
import unittest
from unittest import mock

import activitytrack
from activitytrack.app import App
from activitytrack.pairing import Account
from activitytrack_tui import screen as S


class Box:
    """A popup's window: the keys it is given, what is written in it."""

    def __init__(self, keys):
        self.keys, self.lines = keys, []

    def get_wch(self):
        return self.keys.pop(0)

    def addnstr(self, y, x, text, n, attr=0):
        self.lines.append((y, text[:n], attr))

    def keypad(self, flag):
        pass

    def timeout(self, ms):
        pass

    def getmaxyx(self):
        return 30, 100

    def box(self):
        pass

    def move(self, y, x):
        pass

    def refresh(self):
        pass


class Window(Box):
    def __init__(self, keys=(), size=(30, 100)):
        super().__init__(list(keys))
        self.size = size

    def getmaxyx(self):
        return self.size


class Offline:
    """The runner with the server out of reach; each run writes as the library's of 0.9.4-beta4 did."""

    def __init__(self, store):
        self.store, self.calls, self.state = store, [], "offline"

    def run(self, full=True):
        self.calls.append(full)
        other = sqlite3.connect(self.store.path)
        other.execute("INSERT OR REPLACE INTO state (key, value) VALUES ('last_error', ?)", (f'"{len(self.calls)}"',))
        other.commit()
        other.close()
        return self.state, "no route"


class ScreenTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.app = App(self.dir.name)
        self.screen = S.Screen(Window(), self.app)
        self.screen.refresh = lambda: None
        # the curses window of the tests draws nothing
        self.screen.draw = lambda: None
        self.curs = mock.patch.object(S.curses, "curs_set")
        self.curs.start()

    def tearDown(self):
        self.curs.stop()
        # the sync thread of a screen that ran: it may still hold the database, which Windows will not delete
        self.screen._stop = True
        if self.screen._thread:
            self.screen._thread.join(10)
        self.app.store.close()
        self.dir.cleanup()

    def popup(self, *keys):
        box = Box(list(keys))
        self.screen._box = lambda height, width, title: (box, min(height, 12), min(width, 60))
        return box

    # ---- updates from the paired server ----

    def announced(self, key="signer", component="tui-tar"):
        """The app paired, the store holding a manifest of 9.9.9 signed by the pinned key."""
        import datetime
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        from activitytrack import manifest
        k = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "server")])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
                .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
                .sign(k, hashes.SHA256()))
        pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption())
        sha = hashlib.sha256(b"the package").hexdigest()
        body = {"issued": "2026-11-07T10:00:00Z", "releases": [{"version": "9.9.9", "channel": "prod", "packages": [
            {"component": component, "url": "/api/v1/update/files/" + sha, "sha256": sha}]}]}
        fingerprint = manifest.fingerprint(cert.public_bytes(serialization.Encoding.DER))
        self.app.store.set_state("account", Account("https://at.example.com", None, "d", "anna", "Anna", "user", "X",
                                                    fingerprint if key == "signer" else "cd" * 32).to_json())
        self.app.store.set_state("update.manifest", manifest.sign(body, pem, cert.public_bytes(
            serialization.Encoding.PEM)))
        self.app.runner.refresh_update = lambda: ""

    def patched(self, package_manager=False, body=b"the package"):
        class Down:
            """The server's files, streamed."""
            status = 200

            @contextlib.contextmanager
            def stream(self, path, headers=None):
                self.rest = body
                yield self

            def read(self, n=-1):
                piece, self.rest = self.rest[:n], self.rest[n:]
                return piece
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        activitytrack.set_client("ActivityTrackTui", "0.9.8")
        self.addCleanup(activitytrack.set_client, "ActivityTrack", activitytrack.__version__)
        stack.enter_context(mock.patch("activitytrack.session.Session.http", return_value=Down()))
        stack.enter_context(mock.patch("activitytrack.manifest.from_package_manager", return_value=package_manager))
        stack.enter_context(mock.patch("activitytrack.updates.distro", return_value="deb13"))
        # a Linux installation whatever the system the tests run on (on Windows the package is tui-windows)
        stack.enter_context(mock.patch("sys.platform", "linux"))

    def test_updates_say_why_when_nothing_is_offered(self):
        self.app.runner.refresh_update = lambda: ""
        box = self.popup("\x1b")
        self.screen.key("a")
        self.assertTrue(any("Non associato" in text for _y, text, _a in box.lines))
        self.announced(key="other")
        box = self.popup("\x1b")
        self.screen.key("a")
        self.assertTrue(any("non è firmato" in text for _y, text, _a in box.lines))
        self.assertIsNone(self.screen.news)

    def test_a_newer_version_is_offered_and_downloaded_with_its_sha256_checked(self):
        self.announced()
        self.patched()
        box = self.popup("\n")
        self.screen.key("a")
        shown = [text for _y, text, _a in box.lines]
        self.assertTrue(any("9.9.9" in text for text in shown))
        self.assertTrue(any("Scarica" in text for text in shown))
        self.assertEqual(self.screen.news.version, "9.9.9")
        self.assertIn("Scaricato e controllato", self.screen.message)
        self.assertTrue(next((self.app.session.folder / "updates").glob("*.tar.gz")).read_bytes() == b"the package")
        # another file than the manifest's: refused, nothing left
        self.patched(body=b"another")
        (next((self.app.session.folder / "updates").glob("*.tar.gz"))).unlink()
        self.popup("\n")
        self.screen.key("a")
        self.assertIn("Scaricamento non riuscito", self.screen.message)
        self.assertEqual([], list((self.app.session.folder / "updates").glob("*.tar.gz")))

    def test_the_selection_starts_on_the_download_and_the_digit_still_works(self):
        self.announced()
        self.patched()
        self.popup("\x1b")
        self.screen.key("a")
        self.assertNotIn("Scaricato", self.screen.message)  # Esc: nothing downloaded
        self.popup("\n")
        self.screen.key("a")
        self.assertIn("Scaricato e controllato", self.screen.message)  # Enter at once: downloaded

    def test_a_package_manager_installation_is_not_offered_the_download(self):
        self.announced(component="tui-deb13")
        self.patched(package_manager=True)
        box = self.popup("\x1b")
        self.screen.key("a")
        shown = [text for _y, text, _a in box.lines]
        self.assertTrue(any("gestore di pacchetti" in text for text in shown))
        self.assertFalse(any("Scarica" in text for text in shown))

    def test_the_sync_brings_the_news_to_the_header(self):
        self.announced()
        self.patched()
        self.assertEqual(self.screen._news().version, "9.9.9")

    def test_the_sync_waits_longer_after_each_failure(self):
        app, sc = self.app, self.screen
        app.store.set_state("account", Account("https://127.0.0.1:1", None, "d", "u", "n", "p", "o").to_json())
        app.store.write("client", "c1", {"name": "X"}, 1)
        runner = app.runner = Offline(app.store)
        now = [0.0]
        sc.clock = lambda: now[0]
        sc.want_sync()

        def seconds(n):
            for _ in range(n):
                sc._tick()
                sc._sync_once(sc._sync_wanted.is_set())
                now[0] += 1
        seconds(600)
        # at 0, 30, 90, 210, 510 s, not every second
        self.assertEqual(len(runner.calls), 5)
        # another interface's write does not end the wait
        app.store.write("client", "c2", {"name": "Y"}, 2)
        other = sqlite3.connect(app.store.path)
        other.execute("INSERT OR REPLACE INTO state (key, value) VALUES ('x', '1')")
        other.commit()
        other.close()
        seconds(5)
        self.assertEqual(len(runner.calls), 5)
        # "r", or a change here: at once, and the waits start again from 30 s
        sc.key("r")
        seconds(1)
        self.assertEqual(len(runner.calls), 6)
        seconds(30)
        self.assertEqual(len(runner.calls), 7)
        sc.changed()
        seconds(1)
        self.assertEqual(len(runner.calls), 8)
        # back online: every 30 s
        runner.state = "ok"
        seconds(30)
        self.assertEqual(len(runner.calls), 9)
        seconds(61)
        self.assertEqual(len(runner.calls), 11)
        # its own writes start no sync; another interface's do, at once
        seconds(1)
        other = sqlite3.connect(app.store.path)
        other.execute("INSERT OR REPLACE INTO state (key, value) VALUES ('x', '2')")
        other.commit()
        other.close()
        seconds(1)
        self.assertEqual(len(runner.calls), 12)

    def test_a_menu_takes_ascii_digits_only(self):
        self.popup("²", "2")
        self.assertEqual(self.screen.menu("T", ["a", "b"]), 1)

    def test_a_menu_starts_on_the_selected_entry(self):
        self.popup("\n")
        self.assertEqual(self.screen.menu("T", ["a", "b", "c"], selected=2), 2)
        self.popup("\n")
        self.assertEqual(self.screen.menu("T", ["a", "b"], selected=9), 1)

    def test_a_long_menu_scrolls_with_the_selection(self):
        box = self.popup(*[curses.KEY_DOWN] * 20, "\n")
        self.assertEqual(self.screen.menu("T", [f"option {i}" for i in range(30)]), 20)
        selected = [text for _y, text, attr in box.lines if attr == curses.A_REVERSE]
        self.assertEqual(selected[-1], "21 option 20")

    def test_no_popup_on_a_tiny_terminal(self):
        self.screen.win = Window(size=(4, 20))
        with mock.patch.object(S.curses, "newwin") as newwin:
            self.assertIsNone(self.screen.menu("T", ["a"]))
            self.assertIsNone(self.screen.prompt("Descrizione"))
            self.assertIsNone(self.screen.pick_client())
        newwin.assert_not_called()
        self.assertEqual(self.screen.message, "Finestra troppo piccola.")

    def test_line_breaks_show_and_stay(self):
        box = self.popup("x", "\n")
        self.assertEqual(self.screen.prompt("Descrizione", "Rete\nVPN"), "Rete\nVPNx")
        self.assertIn("Rete⏎VPN", box.lines[0][1])

    def test_a_client_beyond_the_ones_shown_is_not_new(self):
        for i in range(13):
            self.app.store.write("client", f"c{i}", {"name": f"Bx {i:02}"}, 1)
        self.app.store.write("client", "cx", {"name": "x"}, 1)
        box = self.popup("x", "\n")
        self.assertEqual(self.screen.pick_client(), "cx")
        self.assertFalse(any("Nuovo" in text for _y, text, _a in box.lines))

    def test_an_unexpected_error_is_said_not_a_crash(self):
        sc = self.screen
        sc.win = Window(["x", "q"])
        sc._colours = sc.draw = lambda: None
        sc.key = mock.Mock(side_effect=RuntimeError("boom"))
        with self.assertLogs("activitytrack_tui.screen", "ERROR"):
            sc.run()
        self.assertIn("RuntimeError: boom", sc.message)


class FlowsTest(ScreenTest):
    """The commands of the activity model 1.0 through the keys (ACTIVITY-MODEL.md §3, §3.2)."""

    def answers(self, *menus, prompts=()):
        self.asked = []
        menus, prompts = list(menus), list(prompts)

        def menu(title, options, selected=0):
            self.asked.append((title, options))
            return menus.pop(0)
        self.screen.menu = menu
        self.screen.prompt = lambda label, default="": prompts.pop(0)

    def start(self, **kw):
        from activitytrack.work import StartRequest
        self.app.store.write("client", "c", {"name": "Acme", "archived": False}, 1)
        return self.app.work.start(StartRequest(client="c", activity="Rete", **kw))

    def test_travel_from_work_asks_and_yes_closes_the_activity(self):
        uid = self.start()
        self.answers(0)
        self.screen.key("v")
        self.assertEqual(self.asked[0][0], "Hai completato l'attività lavorativa?")
        self.assertEqual(self.app.work.entry(uid)["status"], "CLOSED")
        (new,) = [e for e in self.app.work.running()]
        self.assertEqual((new["uid"] != uid, new["mode"]), (True, "TRAVEL"))

    def test_travel_from_work_no_switches(self):
        uid = self.start()
        self.answers(1)
        self.screen.key("v")
        self.assertEqual(self.app.work.entry(uid)["mode"], "TRAVEL")

    def test_stop_offers_the_return_trip(self):
        from activitytrack.work import TRAVEL, WORK
        now = self.app.work.clock()
        uid = self.start(start_ms=now - 4 * 3_600_000, mode=TRAVEL)
        self.app.work.switch(uid, WORK, at=now - 3 * 3_600_000)
        self.answers(2)
        self.screen.key("s")
        self.assertEqual(len(self.asked[0][1]), 3)
        self.assertEqual(self.app.work.entry(uid)["status"], "CLOSED")
        self.assertEqual([s["kind"] for s in self.app.work.segments(uid)], [TRAVEL, WORK, TRAVEL])

    def test_a_command_tells_what_it_moved_and_r_dismisses_the_sync_notices(self):
        import time
        from activitytrack import texts
        from activitytrack.work import StartRequest
        now = self.app.work.clock()
        uid = self.start(start_ms=now - 3_600_000)
        self.app.work.stop(uid, at=now + 3_600_000, exact=True)
        self.app.work.start(StartRequest(client="c", activity="Cavi"))
        self.screen.key("j")
        self.assertIn("La fine di «Rete» è stata anticipata all'inizio della nuova attività.", self.screen.message)
        self.assertEqual(self.screen.message_colour, "yellow")
        # the next key has nothing to add
        self.screen.message = ""
        self.screen.key("j")
        self.assertEqual(self.screen.message, "")
        self.app.store.set_state("sync.notices", [{"code": "edit_dropped", "title": "Ticket",
                                                   "at": int(time.time() * 1000)}])
        self.assertIn("«Ticket»", texts.sync_notices(self.app.session)[0])
        self.screen.key("r")
        self.assertEqual(texts.sync_notices(self.app.session), [])

    def test_a_plain_stop_asks_nothing_and_no_suspend_key(self):
        uid = self.start()
        self.answers()
        self.screen.key("u")
        self.screen.key("s")
        self.assertEqual((self.asked, self.app.work.entry(uid)["status"]), ([], "CLOSED"))

    def test_the_keys_act_on_the_activity_being_worked_not_on_a_suspended_one(self):
        from activitytrack.work import PAUSE, SUSPEND, StartRequest
        suspended = self.start()
        worked = self.app.work.start(StartRequest(client="c", activity="Stampanti", others={suspended: SUSPEND}))
        self.assertIsNone(self.app.work.open_segment(suspended))
        self.answers()
        self.screen.key("p")
        self.assertEqual(self.app.work.entry(worked)["mode"], PAUSE)
        self.assertEqual(self.asked, [])

    def test_with_only_a_suspended_activity_s_v_and_p_act_on_it(self):
        from activitytrack.work import PAUSE, TRAVEL, WORK
        work = self.app.work

        def suspended():
            uid = self.start()
            work.suspend(uid)
            self.assertIsNone(work.open_segment(uid))
            return uid
        self.answers()
        uid = suspended()
        self.screen.key("s")
        self.assertEqual(work.entry(uid)["status"], "CLOSED")
        uid = suspended()
        self.screen.key("v")
        self.assertEqual(work.open_segment(uid)["kind"], TRAVEL)
        work.stop(uid)
        uid = suspended()
        self.screen.key("p")
        self.assertEqual(work.open_segment(uid)["kind"], WORK)
        self.screen.key("p")
        self.assertEqual(work.entry(uid)["mode"], PAUSE)
        self.assertEqual(self.asked, [])

    def test_reopen_asks_to_confirm(self):
        uid = self.start()
        self.app.work.stop(uid)
        self.screen.refresh = S.Screen.refresh.__get__(self.screen)
        self.screen.period, self.screen.show = 0, ["TODO", "DONE", "MISSING"]
        self.screen.refresh()
        row = next(r for r in self.screen.rows if r.record.get("uid") == uid)
        self.answers(1)
        self.screen._do(("act", "reopen", "WORK"), row)
        self.assertEqual(self.app.work.entry(uid)["status"], "CLOSED")
        self.answers(0)
        self.screen._do(("act", "reopen", "WORK"), row)
        self.assertEqual(self.app.work.entry(uid)["status"], "RUNNING")

    def test_new_todo_with_a_priority_and_a_plan(self):
        self.answers(4, prompts=["Backup", "oggi 23:00 30", ""])
        self.screen.pick_client = lambda allow_none=False: ""
        self.screen._new_todo()
        (t,) = self.app.work.todo_list()
        self.assertEqual((t["activity"], t["priority"], t["planStartMin"], t["durationMin"]), ("Backup", 4, 1380, 30))
        self.assertEqual(self.asked[0][1][4], "Urgente")

    def test_new_absence(self):
        self.screen.pick = lambda type_, title, allow_none=False: ""
        self.answers(2, prompts=["oggi", "", "9:00-11:00"])
        self.screen._new_absence()
        (a,) = [e for e in self.app.work.entries() if e["kind"] == "ABSENCE"]
        self.assertEqual(a["absenceKind"], "LEAVE")
        self.assertEqual(self.app.work.display(a)[0], "ABSENCE")
