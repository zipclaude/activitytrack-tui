# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Flavio Visentin <flavio-activitytrack@infraconsulting.it>
"""
The notices of the components of other authors that the Windows build bundles
(issue "Licence: third-party notices"): run by the build with the Python that
makes the executable, it writes into the folder given

  THIRD-PARTY.txt     what is bundled: name, version, licence, where the source is
  <name>-<version>/   the licence files each package installed with

for Python itself and for every package installed beside ours, the build tools
aside. PyInstaller stays in: its bootloader is in the executable.

  python packaging/third_party.py pack/third-party
"""
import os
import re
import shutil
import sys
from importlib import metadata

OURS = re.compile(r"^activitytrack([-_].*)?$")
BUILD_ONLY = {"pip", "setuptools", "wheel", "flake8", "pycodestyle", "pyflakes", "mccabe"}
NOTICE = re.compile(r"(^|/)(LICEN[CS]E|COPYING|COPYRIGHT|NOTICE|AUTHORS)[^/]*$", re.I)
# where the source is, when it is not the package's page on PyPI
SOURCES = {
    "pyqt6-qt6": "https://download.qt.io/official_releases/qt/ (Qt, the version of this package)",
}
HEAD = """{name}: components of other authors in this package

{name} is Copyright (C) 2026 Flavio Visentin
<flavio-activitytrack@infraconsulting.it>, free software under the GNU General
Public License, version 3 or (at your option) any later version (LICENSE); its
source code is at {url}

The executable also contains the components below, each under its own licence:
the licence files they came with are in the folders beside this file. Their
source code is at the addresses given, for the version given.
"""


def key(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def licence_of(meta):
    """The licence a package declares: the expression, the field, or its classifiers."""
    text = meta.get("License-Expression") or meta.get("License") or ""
    if not text or len(text) > 80 or "\n" in text:
        found = [c.split(" :: ")[-1] for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
        text = ", ".join(found) or "see its licence files"
    return text


def python_licence(out):
    folder = os.path.join(out, "Python-%d.%d.%d" % sys.version_info[:3])
    os.makedirs(folder, exist_ok=True)
    for path in (os.path.join(sys.base_prefix, "LICENSE.txt"),
                 os.path.join(os.path.dirname(os.__file__), "LICENSE.txt")):
        if os.path.isfile(path):
            shutil.copyfile(path, os.path.join(folder, "LICENSE.txt"))
            return
    # no file with this Python: its notice, and where the licence is
    with open(os.path.join(folder, "LICENSE.txt"), "w", encoding="utf-8") as f:
        f.write(sys.copyright + "\n\nPython Software Foundation License: "
                "https://docs.python.org/%d.%d/license.html\n" % sys.version_info[:2])


def main(out, name, url):
    os.makedirs(out, exist_ok=True)
    lines = [HEAD.format(name=name, url=url), "",
             "Python %d.%d.%d" % sys.version_info[:3], "  Python Software Foundation License",
             "  https://www.python.org/downloads/source/", ""]
    python_licence(out)
    seen = set()
    for dist in sorted(metadata.distributions(), key=lambda d: key(d.metadata["Name"] or "")):
        meta = dist.metadata
        pkg = meta["Name"]
        if not pkg or key(pkg) in seen or key(pkg) in BUILD_ONLY or OURS.match(key(pkg)):
            continue
        seen.add(key(pkg))
        folder = os.path.join(out, "%s-%s" % (pkg, dist.version))
        copied = 0
        for f in dist.files or []:
            rel = str(f).replace("\\", "/")
            if ".dist-info/" in rel and NOTICE.search(rel):
                src = dist.locate_file(f)
                if os.path.isfile(src):
                    os.makedirs(folder, exist_ok=True)
                    shutil.copyfile(src, os.path.join(folder, os.path.basename(rel)))
                    copied += 1
        lines += ["%s %s" % (pkg, dist.version), "  " + licence_of(meta),
                  "  " + SOURCES.get(key(pkg), "https://pypi.org/project/%s/%s/#files" % (pkg, dist.version))]
        home = meta.get("Home-page") or next((u.split(", ", 1)[1] for u in meta.get_all("Project-URL") or []
                                             if u.lower().startswith(("homepage", "source", "repository"))), None)
        if home:
            lines.append("  " + home)
        if not copied:
            lines.append("  (no licence file in the package: see the address above)")
        lines.append("")
    with open(os.path.join(out, "THIRD-PARTY.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(lines))
    print("%s: %d packages" % (out, len(seen)))


if __name__ == "__main__":
    main(sys.argv[1], *sys.argv[2:4])
