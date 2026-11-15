# SPDX-License-Identifier: GPL-3.0-or-later
"""
The interface's texts to translate: every literal passed to _() in the
package, and "context|text" for those of pgettext(context, text). With
--check the catalogs (activitytrack_tui/locale/*.json) must have them all and
no other (a text that was changed or dropped leaves its old translation
behind), with the same placeholders.

    python3 scripts/strings.py [--check] [files…]
"""

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "activitytrack_tui"


def _key(node):
    """The catalog key of a call of _() or pgettext() with literal arguments, else None."""
    name = getattr(node.func, "id", None)
    want = 1 if name == "_" else 2 if name == "pgettext" else 0
    args = node.args[:want]
    if not want or len(args) != want or not all(isinstance(a, ast.Constant) and isinstance(a.value, str)
                                                for a in args):
        return None
    return "|".join(a.value for a in args)


def strings(files=None):
    found = []
    for f in sorted(files or PACKAGE.glob("*.py")):
        for node in ast.walk(ast.parse(Path(f).read_text(encoding="utf-8"))):
            key = _key(node) if isinstance(node, ast.Call) else None
            if key is not None and key not in found:
                found.append(key)
    return found


def placeholders(text):
    return sorted(re.findall(r"%(?:\(\w+\))?[-#0 +]*\d*(?:\.\d+)?[sdif%]", text))


def check():
    errors = []
    texts = strings()
    for catalog in sorted((PACKAGE / "locale").glob("*.json")):
        data = json.loads(catalog.read_text(encoding="utf-8"))
        for t in texts:
            if t not in data:
                errors.append(f"{catalog.name}: missing {t!r}")
            elif not data[t].strip():
                errors.append(f"{catalog.name}: empty {t!r}")
            elif placeholders(data[t]) != placeholders(t.split("|")[-1]):
                errors.append(f"{catalog.name}: placeholders of {t!r}")
        errors += [f"{catalog.name}: stale {k!r}" for k in data if k not in texts]
    return errors


if __name__ == "__main__":
    if "--check" in sys.argv:
        problems = check()
        print("\n".join(problems) or "ok")
        sys.exit(1 if problems else 0)
    print(json.dumps(strings(sys.argv[1:] or None), ensure_ascii=False, indent=1))
