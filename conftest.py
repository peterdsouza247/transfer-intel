"""Import setup for the test suite.

pytest loads this before it imports a single test module, which is the only
place the problem below can be fixed.

Every test file inserts its own path into `sys.path` at import time. That
works when there is exactly one copy of the repository on disk and fails
confusingly when there is more than one: whichever test module runs first
imports `transferintel`, Python caches it in `sys.modules`, and every later
module silently gets that copy no matter what path it inserts afterwards.
Extract a release archive inside an existing checkout and the suite reports a
missing module that is sitting right there on disk.

The error it produces is `cannot import name 'digest' from 'transferintel'
(unknown location)`. "Unknown location" is Python saying it found a directory
with that name and no `__init__.py`, so there was nothing in it to import.

This file makes the resolution deterministic and, when it cannot, says which
copy won and where the other one is.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
EXPECTED = SCRIPTS / "transferintel"


def _fail(problem: str, detail: str) -> None:
    raise RuntimeError(
        f"\n\nTest import setup failed: {problem}\n\n{detail}\n\n"
        f"This checkout is: {ROOT}\n"
        f"It expects the package at: {EXPECTED}\n"
    )


if not (EXPECTED / "__init__.py").exists():
    _fail(
        "the transferintel package is incomplete",
        "There is no __init__.py, so Python cannot treat this directory as a\n"
        "package. This usually means a partial archive was extracted over the\n"
        "top of it. Replace the whole scripts/transferintel directory from a\n"
        "complete copy of the repository.",
    )

# Ahead of everything else, so a second checkout elsewhere on sys.path cannot
# win on ordering.
sys.path.insert(0, str(SCRIPTS))

# If something already imported it, that happened against a path we do not
# control, and the cache would override everything above.
_already = sys.modules.get("transferintel")
if _already is not None:
    _where = getattr(_already, "__file__", None)
    _dir = Path(_where).resolve().parent if _where else None
    if _dir != EXPECTED:
        for _name in [n for n in sys.modules
                      if n == "transferintel" or n.startswith("transferintel.")]:
            del sys.modules[_name]

import transferintel  # noqa: E402

_loc = getattr(transferintel, "__file__", None)
if _loc is None:
    _fail(
        "transferintel resolved to a namespace package",
        "Python found a directory with that name but no __init__.py in it.\n"
        "Search path used:\n  " + "\n  ".join(
            p for p in transferintel.__path__  # type: ignore[attr-defined]
        ),
    )

_found = Path(_loc).resolve().parent
if _found != EXPECTED:
    _fail(
        "the wrong copy of transferintel was imported",
        f"Imported from : {_found}\n"
        f"Should be from: {EXPECTED}\n\n"
        "There is more than one copy of this repository on the import path.\n"
        "Delete the one you are not working in, or run pytest from inside the\n"
        "checkout you want rather than from a directory containing both.",
    )
