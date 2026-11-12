# SPDX-License-Identifier: GPL-3.0-or-later
import os
import sys
import tempfile
import time
import unittest
from datetime import date

from activitytrack.app import App
from activitytrack.work import PAUSE, StartRequest
from activitytrack_tui import rows as R


class RowsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.app = App(self.dir.name)

    def tearDown(self):
        self.app.store.close()
        self.dir.cleanup()

    def test_rows_and_actions(self):
        self.assertEqual([r.kind for r in R.rows(self.app)], ["heading", "empty"])
        a = self.app.work.start(StartRequest(client="Acme", activity="Rete"))
        self.app.work.switch(a, PAUSE)
        self.app.work.save_todo(None, "Backup", plan_date=date.today(), plan_min=23 * 60 + 50, priority=3)
        rows = R.rows(self.app, period=0)
        kinds = [r.kind for r in rows]
        self.assertEqual(kinds[:2], ["heading", "entry"])
        self.assertIn("todo", kinds)
        entry = rows[1]
        self.assertEqual((entry.text, entry.colour), ("Acme · Rete", "yellow"))
        labels = [label for label, _a in R.actions(self.app, entry)]
        self.assertEqual(labels[:3], ["Riprendi", "Viaggio", "Stop"])
        self.assertNotIn("Sospendi", labels)
        self.assertIn("‖ In pausa", entry.detail)
        todo = next(r for r in rows if r.kind == "todo")
        self.assertEqual([a for _l, a in R.actions(self.app, todo)][:3],
                         [("act", "start", "WORK"), ("act", "start", "TRAVEL"), ("act", "tick", None)])
        self.assertIn("Alta", todo.detail)
        self.assertNotIn("P3", todo.detail)
        self.assertEqual(R.next_period(0), -1)
        self.assertEqual(R.show_label(["MISSING", "TODO"]), "Da fare, Dettagli mancanti")

    def test_an_absence_has_no_description_or_client_to_edit(self):
        a = self.app.work.save_absence(None, date(2026, 11, 16), absence_kind="LEAVE", start_min=540, end_min=600)
        absence = R.Row("stopped", "Permesso", "", "", "white", self.app.work.entry(a), "ABSENCE")
        verbs = [action[0] for _label, action in R.actions(self.app, absence)]
        self.assertNotIn("describe", verbs)
        self.assertNotIn("client", verbs)
        b = self.app.work.start(StartRequest(client="Acme", activity="Rete"))
        work = R.Row("entry", "Acme · Rete", "", "", "white", self.app.work.entry(b), "WORK")
        verbs = [action[0] for _label, action in R.actions(self.app, work)]
        self.assertIn("describe", verbs)
        self.assertIn("client", verbs)


@unittest.skipIf(sys.platform == "win32", "a pseudo-terminal")
class TerminalTest(unittest.TestCase):
    def test_it_draws_and_quits(self):
        import pty
        import select
        with tempfile.TemporaryDirectory() as d:
            pid, fd = pty.fork()
            if pid == 0:
                os.environ.update(TERM="xterm", LC_ALL="it_IT.UTF-8", LINES="30", COLUMNS="100")
                os.execvp(sys.executable, [sys.executable, "-m", "activitytrack_tui", "--data", d])
            seen, end = b"", time.time() + 15
            while b"IN CORSO" not in seen and time.time() < end:
                if select.select([fd], [], [], 0.2)[0]:
                    try:
                        seen += os.read(fd, 65536)
                    except OSError:
                        break
            os.write(fd, b"q")
            end = time.time() + 10
            while time.time() < end:
                done, status = os.waitpid(pid, os.WNOHANG)
                if done:
                    break
                if select.select([fd], [], [], 0.2)[0]:
                    try:
                        os.read(fd, 65536)
                    except OSError:
                        pass
            else:
                os.kill(pid, 9)
                os.waitpid(pid, 0)
                self.fail("it did not quit")
            os.close(fd)
            self.assertIn(b"ActivityTrack", seen)
            self.assertIn(b"IN CORSO", seen)
            self.assertEqual(os.waitstatus_to_exitcode(status), 0)
