# SPDX-License-Identifier: GPL-3.0-or-later
"""The entry point PyInstaller bundles for Windows: activitytrack-tui.exe."""
import sys

from activitytrack_tui.main import main

sys.exit(main())
