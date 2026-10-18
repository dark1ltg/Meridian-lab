#!/usr/bin/env python3
"""Standalone smoke tests — no pytest required.

Runs the same checks as ``tests/test_smoke.py`` via ``tests.smoke_checks``.
Prefer ``bash scripts/smoke_test.sh`` or ``pytest`` during development.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from tests.smoke_checks import run_all_smoke_checks
    from meridian import __version__

    print(f"Meridian {__version__} — smoke checks")
    with tempfile.TemporaryDirectory(prefix="meridian-smoke-") as td:
        run_all_smoke_checks(Path(td))
    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
