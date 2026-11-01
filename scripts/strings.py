# SPDX-License-Identifier: GPL-3.0-or-later
"""
The interface's texts to translate: every literal passed to _() in the
package. With --check the catalogs (activitytrack/locale/*.json)
must have them all, with the same placeholders.

    python3 scripts/strings.py [--check] [files…]
"""

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "activitytrack_tui"


def strings(files=None):
    found = []
    for f in sorted(files or PACKAGE.glob("*.py")):
        for node in ast.walk(ast.parse(Path(f).read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_" and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                if node.args[0].value not in found:
                    found.append(node.args[0].value)
    return found


def placeholders(text):
    return sorted(re.findall(r"%[sd]", text))


def check():
    errors = []
    texts = strings()
    for catalog in sorted((PACKAGE / "locale").glob("*.json")):
        data = json.loads(catalog.read_text(encoding="utf-8"))
        for t in texts:
            if t not in data:
                errors.append(f"{catalog.name}: missing {t!r}")
            elif placeholders(data[t]) != placeholders(t):
                errors.append(f"{catalog.name}: placeholders of {t!r}")
    return errors


if __name__ == "__main__":
    if "--check" in sys.argv:
        problems = check()
        print("\n".join(problems) or "ok")
        sys.exit(1 if problems else 0)
    print(json.dumps(strings(sys.argv[1:] or None), ensure_ascii=False, indent=1))
