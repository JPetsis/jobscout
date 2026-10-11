"""Frozen-app launcher for the jobscout desktop shell.

PyInstaller runs its entry script as a top-level ``__main__`` module, and
``jobscout/app.py`` uses package-relative imports (``from . import api, db as
dbm``), which cannot resolve outside the ``jobscout`` package. Importing the
entry point by its absolute name from a file that lives outside the package
avoids that: the frozen app starts exactly what ``jobscout-app`` runs from
source.
"""
from __future__ import annotations

from jobscout.app import main

if __name__ == "__main__":
    raise SystemExit(main())
