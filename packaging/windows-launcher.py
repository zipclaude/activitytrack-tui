# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 Flavio Visentin <flavio-activitytrack@infraconsulting.it>
"""The entry point PyInstaller bundles for Windows: activitytrack-tui.exe."""
import sys

from activitytrack_tui.main import main

sys.exit(main())
