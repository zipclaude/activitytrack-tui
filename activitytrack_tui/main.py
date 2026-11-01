# SPDX-License-Identifier: GPL-3.0-or-later
"""activitytrack-tui: the options, the language, then the full screen."""

import argparse
import sys
from pathlib import Path

import activitytrack
from activitytrack.i18n import _, add_catalogs, set_language, system_language

from . import __version__


def _language(folder):
    from activitytrack.policy import stored
    from activitytrack.store import Store
    forced = None
    db = Path(folder) / "activitytrack.sqlite3"
    if db.exists():
        store = Store(db)
        forced = stored(store).language
        store.close()
    add_catalogs(Path(__file__).with_name("locale"))
    return set_language(forced or system_language())


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    activitytrack.set_client("ActivityTrackTui", __version__)
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--data")
    known, _rest = pre.parse_known_args(argv)
    from activitytrack.paths import data_dir
    folder = Path(known.data) if known.data else data_dir()
    # the texts are built in the language of the policy or the system
    _language(folder)
    p = argparse.ArgumentParser(prog="activitytrack-tui", description=_(
        "ActivityTrack nel terminale, a schermo intero: le attività in corso e la lista della Home, con i "
        "comandi da tastiera."))
    p.add_argument("--version", action="version", version=f"activitytrack-tui {__version__}")
    p.add_argument("--data", help=_("cartella dei dati (predefinita: quella condivisa con l'app desktop)"))
    p.add_argument("--dev", action="store_true", help=argparse.SUPPRESS)
    args = p.parse_args(argv)

    import curses

    from activitytrack.app import App

    from .screen import Screen
    app = App(folder, dev=args.dev)
    try:
        curses.wrapper(lambda win: Screen(win, app).run())
    except KeyboardInterrupt:
        pass
    finally:
        app.store.close()
    return 0
