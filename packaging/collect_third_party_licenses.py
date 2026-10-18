#!/usr/bin/env python3
"""Collect third-party licence texts from installed packages into a doc tree."""

from __future__ import annotations

import importlib
import importlib.metadata
import shutil
import sys
from pathlib import Path

# Patterns to copy when walking a package or dist-info tree.
NAME_HINTS = (
    "license",
    "licence",
    "copying",
    "copyright",
    "notice",
    "authors",
    "lgpl",
    "gpl",
)


def _interesting(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix.lower() not in {".txt", ".md", "", ".rst", ".html"} and "license" not in path.name.lower():
        # Allow extensionless COPYING / LICENSE
        if path.name.upper() not in {"COPYING", "LICENSE", "LICENCE", "NOTICE", "AUTHORS"}:
            if "license" not in path.name.lower() and "licence" not in path.name.lower():
                return False
    lower = path.name.lower()
    return any(h in lower for h in NAME_HINTS)


def _copy_file(src: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / src.name
    if target.exists() and target.stat().st_size == src.stat().st_size:
        return
    # Avoid collisions by prefixing parent when needed.
    if target.exists() and target.read_bytes() != src.read_bytes():
        target = dest_dir / f"{src.parent.name}-{src.name}"
    shutil.copy2(src, target)


def _walk_copy(root: Path, dest_dir: Path, max_files: int = 40) -> int:
    if not root.exists():
        return 0
    count = 0
    for path in sorted(root.rglob("*")):
        if count >= max_files:
            break
        if not _interesting(path):
            continue
        # Skip huge HTML trees / binary
        if path.stat().st_size > 2_000_000:
            continue
        rel = path.relative_to(root)
        # Keep shallow copies for clarity
        if len(rel.parts) > 4:
            continue
        _copy_file(path, dest_dir)
        count += 1
    return count


def _dist_files(name: str, dest_dir: Path) -> int:
    try:
        dist = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return 0
    count = 0
    # Preferred: licenses/ under dist-info
    for base in (dist._path,):  # type: ignore[attr-defined]
        if base is None:
            continue
        base = Path(base)
        for sub in ("licenses", "license_files", ""):
            root = base / sub if sub else base
            if root.is_dir():
                count += _walk_copy(root, dest_dir)
    # Also scan package import path
    try:
        mod = importlib.import_module(name)
    except Exception:
        return count
    paths: list[Path] = []
    if getattr(mod, "__file__", None):
        paths.append(Path(mod.__file__).resolve().parent)
    for p in getattr(mod, "__path__", []):
        paths.append(Path(p).resolve())
    for p in paths:
        count += _walk_copy(p, dest_dir, max_files=30)
        # Qt often keeps Licenses next to the package
        for extra in ("Licenses", "licenses", "LICENSE", "metatypes"):
            count += _walk_copy(p / extra, dest_dir, max_files=30)
    return count


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} DEST_DIR", file=sys.stderr)
        return 2
    dest = Path(sys.argv[1])
    dest.mkdir(parents=True, exist_ok=True)

    packages = {
        "mutagen": "mutagen",
        "numpy": "numpy",
        "aubio": "aubio",
        "PySide6": "pyside6",
        "shiboken6": "shiboken6",
        "pyinstaller": "pyinstaller",
    }
    report: dict[str, int] = {}
    for dist_name, folder in packages.items():
        n = _dist_files(dist_name, dest / folder)
        report[dist_name] = n
        print(f"{dist_name}: {n} licence file(s) -> {dest / folder}")

    # Hard requirements: prefer real package files; fallbacks installed by build script.
    # Exit 0 even if some are empty so build-appimage.sh can apply NOTICE fallbacks.
    weak = [name for name, n in report.items() if n < 1]
    if weak:
        print("WARN: no licence files found for: " + ", ".join(weak), file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
