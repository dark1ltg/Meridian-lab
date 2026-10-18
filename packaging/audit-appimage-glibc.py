#!/usr/bin/env python3
"""Report the highest GLIBC symbol version required by an AppDir / AppImage extract."""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

GLIBC_RE = re.compile(r"GLIBC_(\d+)\.(\d+)")


def glibc_versions(path: Path) -> set[tuple[int, int]]:
    try:
        out = subprocess.check_output(
            ["objdump", "-T", str(path)],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()
    return {(int(a), int(b)) for a, b in GLIBC_RE.findall(out)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", type=Path, help="AppDir or extracted AppImage root")
    ap.add_argument(
        "--max",
        default="2.39",
        help="Maximum allowed GLIBC (default 2.39 = Ubuntu 24.04)",
    )
    args = ap.parse_args()
    root: Path = args.root
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 2

    maj_s, min_s = args.max.split(".", 1)
    limit = (int(maj_s), int(min_s))

    worst: tuple[int, int] = (0, 0)
    offenders: list[tuple[tuple[int, int], Path]] = []
    scanned = 0
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        if not (
            ".so" in name
            or path.suffix == ""
            or name in {"Meridian", "python", "python3"}
        ):
            # Still check ELF binaries without extension (Meridian launcher).
            pass
        # Cheap ELF magic check
        try:
            with path.open("rb") as fh:
                if fh.read(4) != b"\x7fELF":
                    continue
        except OSError:
            continue
        scanned += 1
        versions = glibc_versions(path)
        if not versions:
            continue
        local_max = max(versions)
        if local_max > worst:
            worst = local_max
        if local_max > limit:
            offenders.append((local_max, path))

    print(f"Scanned {scanned} ELF files under {root}")
    print(f"Highest GLIBC required: {worst[0]}.{worst[1]}")
    print(f"Limit (Ubuntu 24.04 target): {limit[0]}.{limit[1]}")
    if offenders:
        print("FAIL: files requiring newer GLIBC than limit:")
        for ver, path in sorted(offenders, key=lambda x: (-x[0][0], -x[0][1], str(x[1])))[
            :40
        ]:
            rel = path.relative_to(root) if path.is_relative_to(root) else path
            print(f"  GLIBC_{ver[0]}.{ver[1]}  {rel}")
        if len(offenders) > 40:
            print(f"  … and {len(offenders) - 40} more")
        return 1
    print("OK: within Ubuntu 24.04 glibc ceiling")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
