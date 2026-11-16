# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Flavio Visentin <flavio-activitytrack@infraconsulting.it>
"""The language of the tui: the policy's, else the user's choice, else the system's, else English (#18)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from activitytrack import i18n
from activitytrack.store import Store
from activitytrack_tui import main as tui


def policy(**ui):
    return json.dumps({"format": "activitytrack-policy", "schema": 1, "ui": ui})


class LanguageTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.folder = Path(self.dir.name)

    def tearDown(self):
        i18n.set_language("it")
        self.dir.cleanup()

    def language(self, system="it_IT.UTF-8"):
        with mock.patch("activitytrack_tui.main.system_language", return_value=i18n.language_of(system) or "en"):
            return tui._language(self.folder)

    def keep(self, policy_text=None, choice=None):
        store = Store(self.folder / "activitytrack.sqlite3")
        store.set_state("policy", policy_text)
        store.set_state(i18n.PREFERENCE_KEY, choice)
        store.close()

    def test_the_system_language_without_data(self):
        self.assertEqual(self.language("de_AT.UTF-8"), "de")
        self.assertEqual(i18n._("Lavoro"), "Arbeit")

    def test_an_unknown_system_language_is_english(self):
        self.assertEqual(self.language("fr_FR.UTF-8"), "en")
        self.assertEqual(i18n._("Lavoro"), "Work")

    def test_the_real_system_language(self):
        env = {"LANG": "de_AT.UTF-8"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("sys.platform", "linux"):
            self.assertEqual(tui._language(self.folder), "de")
        with mock.patch.dict(os.environ, {"LC_ALL": "C", "LANG": "it_IT"}, clear=True), \
                mock.patch("sys.platform", "linux"):
            self.assertEqual(tui._language(self.folder), "en")

    def test_the_policy_wins_over_the_choice_and_the_system(self):
        self.keep(policy(language="es"), choice="de")
        self.assertEqual(self.language("it_IT"), "es")
        self.assertEqual(i18n._("Lavoro"), "Trabajo")

    def test_the_choice_wins_over_the_system(self):
        self.keep(choice="de")
        self.assertEqual(self.language("it_IT"), "de")

    def test_a_policy_language_that_is_no_language_forces_english(self):
        # present but invalid: neither the choice nor the system applies; null is as if absent
        for value in ("fr", "de_AT", "", 3, True, ["it"], {}):
            self.keep(policy(language=value), choice="de")
            self.assertEqual(self.language("es_ES"), "en", value)
        self.keep(policy(language=None), choice=None)
        self.assertEqual(self.language("es_ES"), "es")
        # not a policy at all: there is none
        self.keep("not a policy")
        self.assertEqual(self.language("es_ES"), "es")

    def test_unreadable_data_do_not_stop_the_start(self):
        (self.folder / "activitytrack.sqlite3").write_bytes(b"this is not a database" * 100)
        self.assertEqual(self.language("de_DE"), "de")
