# SPDX-License-Identifier: GPL-3.0-or-later
"""The catalogs of the tui are complete and agree with the library's (activitytrack-tui#18)."""

import ast
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import activitytrack_tui
from activitytrack import i18n

# the code under test, wherever it is: the checkout, or the installed package in the packages' job
PACKAGE = Path(activitytrack_tui.__file__).resolve().parent
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "strings.py"
LIBRARY = Path(i18n.__file__).resolve().parent / "locale"

# the texts that are the same word in the language as in Italian (the catalogs' keys), and in English
SAME_AS_ITALIAN = {"en": {"No"}, "de": set(), "es": {"No"}}
SAME_AS_ENGLISH = {"de": set(), "es": {"No"}}
# the Italian words of the sources that are not interface texts (none now)
NOT_TEXTS = set()


def _strings_module():
    spec = importlib.util.spec_from_file_location("strings", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load(folder):
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(folder).glob("*.json"))}


class CatalogsTest(unittest.TestCase):
    def test_the_languages_are_the_libraries(self):
        # as the app (Italian, English, German, Spanish); Italian is the language of the sources
        self.assertEqual(sorted(_load(PACKAGE / "locale")), sorted(set(i18n.LANGUAGES) - {"it"}))
        self.assertEqual(sorted(_load(PACKAGE / "locale")), sorted(_load(LIBRARY)))

    @unittest.skipUnless(SCRIPT.exists(), "not in a source tree")
    def test_every_text_is_translated_and_none_is_stale(self):
        self.assertEqual(_strings_module().check(), [])

    def test_the_catalogs_have_the_same_texts(self):
        catalogs = _load(PACKAGE / "locale")
        for lang in ("de", "es"):
            self.assertEqual(sorted(catalogs[lang]), sorted(catalogs["en"]), lang)

    def test_nothing_is_left_in_italian_or_english(self):
        catalogs = _load(PACKAGE / "locale")
        for lang, catalog in catalogs.items():
            same = sorted(k for k, v in catalog.items() if v == k)
            self.assertEqual(same, sorted(SAME_AS_ITALIAN[lang]), f"{lang}: left in Italian")
            for k, v in catalog.items():
                self.assertTrue(v.strip(), f"{lang}: {k!r} is empty")
        for lang in ("de", "es"):
            same = sorted(k for k, v in catalogs[lang].items() if v == catalogs["en"][k])
            self.assertEqual(same, sorted(SAME_AS_ENGLISH[lang]), f"{lang}: left in English")

    @unittest.skipUnless(SCRIPT.exists(), "not in a source tree")
    def test_the_placeholders_are_those_of_the_text(self):
        placeholders = _strings_module().placeholders
        for lang, catalog in _load(PACKAGE / "locale").items():
            for k, v in catalog.items():
                self.assertEqual(placeholders(v), placeholders(k), f"{lang}: {k!r}")

    def test_a_text_of_the_library_says_the_same_in_every_interface(self):
        # the catalogs are merged, the interface's over the library's: two wordings would make one
        # text read differently in the desktop, the cli and here
        ours, lib = _load(PACKAGE / "locale"), _load(LIBRARY)
        for lang, catalog in ours.items():
            for k, v in catalog.items():
                if k in lib[lang]:
                    self.assertEqual(v, lib[lang][k], f"{lang}: {k!r}")

    @unittest.skipUnless(SCRIPT.exists(), "not in a source tree")
    def test_the_check_finds_what_is_wrong(self):
        strings = _strings_module()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "locale").mkdir()
            (root / "m.py").write_text('_("uno %s")\n_("due")\n_("quattro")\n', encoding="utf-8")
            (root / "locale" / "xx.json").write_text(json.dumps({
                "uno %s": "one %d", "due": "", "vecchio": "old"}), encoding="utf-8")
            old, strings.PACKAGE = strings.PACKAGE, root
            try:
                errors = strings.check()
            finally:
                strings.PACKAGE = old
        self.assertEqual(sorted(e.split(": ", 1)[1] for e in errors), sorted([
            "placeholders of 'uno %s'", "empty 'due'", "missing 'quattro'", "stale 'vecchio'"]))

    def test_no_text_bypasses_the_catalogs(self):
        # an Italian text of the catalogs written as a plain literal would stay Italian in every language
        keys = set(_load(PACKAGE / "locale")["en"]) | set(_load(LIBRARY)["en"])
        keys = {k.split("|")[-1] for k in keys}
        for path in sorted(PACKAGE.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            wrapped = {id(n) for call in ast.walk(tree) if isinstance(call, ast.Call)
                       and getattr(call.func, "id", None) in ("_", "pgettext") for n in ast.walk(call)}
            for node in ast.walk(tree):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in keys
                        and id(node) not in wrapped and (path.name, node.value) not in NOT_TEXTS):
                    self.fail(f"{path.name}:{node.lineno}: {node.value!r} is not passed to _()")

    def test_the_default_day_of_an_absence_is_read_back_in_every_language(self):
        # the prompt of a new absence suggests _("oggi"); Enter gives it to parse_day as it is
        from datetime import date

        from activitytrack.app import parse_day
        today = date(2026, 11, 15)
        self.assertEqual(parse_day("oggi", today), today)
        for lang in ("en", "de", "es"):
            try:
                self.assertEqual(i18n.set_language(lang), lang)
                i18n.add_catalogs(PACKAGE / "locale")
                self.assertNotEqual(i18n._("oggi"), "oggi", lang)
                self.assertEqual(parse_day(i18n._("oggi"), today), today, lang)
            finally:
                i18n.set_language("it")
