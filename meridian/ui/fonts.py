from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase

_REGISTERED = False
_SANS_FAMILY = "Ubuntu Sans"
_CONDENSED_FAMILY = "Ubuntu Condensed"


def _candidate_font_dirs() -> list[Path]:
    """Standalone font dirs only — never from a binary-embedded archive."""
    dirs: list[Path] = []
    env = os.environ.get("MERIDIAN_FONTS_DIR", "").strip()
    if env:
        dirs.append(Path(env))

    appdir = os.environ.get("APPDIR", "").strip()
    if appdir:
        dirs.append(Path(appdir) / "usr" / "share" / "fonts" / "truetype" / "meridian")

    # AppImage layout: <AppDir>/usr/bin/Meridian → ../../share/fonts/truetype/meridian
    exe = Path(sys.executable).resolve()
    dirs.append(exe.parent.parent / "share" / "fonts" / "truetype" / "meridian")

    # Frozen onedir next to a share/ sibling (defensive)
    if getattr(sys, "frozen", False):
        dirs.append(exe.parent / "share" / "fonts" / "truetype" / "meridian")

    # Source / editable install: repo resources/fonts
    dirs.append(Path(__file__).resolve().parents[2] / "resources" / "fonts")
    return dirs


def fonts_dir() -> Path | None:
    for path in _candidate_font_dirs():
        if path.is_dir() and any(path.glob("*.ttf")):
            return path
    return None


def _available(name: str) -> bool:
    return name in QFontDatabase.families()


def resolve_family(*candidates: str) -> str:
    for name in candidates:
        if _available(name):
            return name
    return candidates[-1]


def register_bundled_fonts() -> list[str]:
    """Load standalone TTF files from the AppImage/share (or repo) font directory."""
    global _REGISTERED, _SANS_FAMILY, _CONDENSED_FAMILY
    if _REGISTERED:
        return [_SANS_FAMILY, _CONDENSED_FAMILY]
    _REGISTERED = True

    loaded: list[str] = []
    directory = fonts_dir()
    if directory is not None:
        for path in sorted(directory.glob("*.ttf")):
            fid = QFontDatabase.addApplicationFont(str(path))
            if fid < 0:
                continue
            for family in QFontDatabase.applicationFontFamilies(fid):
                loaded.append(family)
                if "Condensed" in family:
                    _CONDENSED_FAMILY = family
                elif "Sans" in family or family == "Ubuntu":
                    _SANS_FAMILY = family

    if not _available(_SANS_FAMILY):
        _SANS_FAMILY = resolve_family("Ubuntu Sans", "Ubuntu", "Noto Sans", "DejaVu Sans")
    if not _available(_CONDENSED_FAMILY):
        _CONDENSED_FAMILY = resolve_family(
            "Ubuntu Condensed",
            "Ubuntu Sans Condensed",
            _SANS_FAMILY,
            "Noto Sans",
        )
    return loaded


def sans(size_px: int, medium: bool = False) -> QFont:
    font = QFont(_SANS_FAMILY if _available(_SANS_FAMILY) else resolve_family("Ubuntu Sans", "Ubuntu", "Noto Sans"))
    font.setPixelSize(size_px)
    font.setWeight(QFont.Weight.Medium if medium else QFont.Weight.Normal)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def condensed(size_px: int) -> QFont:
    family = (
        _CONDENSED_FAMILY
        if _available(_CONDENSED_FAMILY)
        else resolve_family("Ubuntu Condensed", "Ubuntu Sans Condensed", "Ubuntu Sans", "Ubuntu")
    )
    font = QFont(family)
    font.setPixelSize(size_px)
    font.setWeight(QFont.Weight.Normal)
    if "Condensed" not in family:
        font.setStretch(QFont.Stretch.Condensed)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font
