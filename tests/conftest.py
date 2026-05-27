"""Pytest configuration for community bot tests.

Adds the project root and the meshcore-bot submodule to sys.path so that
both ``community.*`` and ``modules.*`` are importable without installation.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUBMODULE = ROOT / "meshcore-bot"

for path in (str(ROOT), str(SUBMODULE)):
    if path not in sys.path:
        sys.path.insert(0, path)
