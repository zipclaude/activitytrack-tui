# SPDX-License-Identifier: GPL-3.0-or-later
import curses
import sqlite3
import tempfile
import unittest
from unittest import mock

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
        self.curs = mock.patch.object(S.curses, "curs_set")
        self.curs.start()

    def tearDown(self):
        self.curs.stop()
        self.app.store.close()
        self.dir.cleanup()

    def popup(self, *keys):
        box = Box(list(keys))
        self.screen._box = lambda height, width, title: (box, min(height, 12), min(width, 60))
        return box

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
