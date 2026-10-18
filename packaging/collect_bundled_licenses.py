#!/usr/bin/env python3
"""Record bundled native libraries and install SPDX / package licence texts.

Used by packaging/build-appimage.sh so AppImage docs match what is actually
shipped (Qt, FFmpeg stack, codecs, OpenSSL, …).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# Map shared-object name prefixes → (notice_folder, pacman_pkg_hint, spdx_files)
# spdx_files are looked up under resources/licenses/spdx/
NATIVE_MAP: list[tuple[str, str, str | None, tuple[str, ...]]] = [
    ("libQt6", "qt6", "qt6-base", ("LGPL-3.0-only.txt", "GPL-3.0-only.txt")),
    ("libpyside6", "pyside6", None, ("LGPL-3.0-only.txt", "GPL-3.0-only.txt")),
    ("libshiboken6", "shiboken6", None, ("LGPL-3.0-only.txt", "GPL-3.0-only.txt")),
    ("libavcodec", "ffmpeg-libs", "ffmpeg", ("GPL-3.0-only.txt",)),
    ("libavformat", "ffmpeg-libs", "ffmpeg", ("GPL-3.0-only.txt",)),
    ("libavutil", "ffmpeg-libs", "ffmpeg", ("GPL-3.0-only.txt",)),
    ("libswresample", "ffmpeg-libs", "ffmpeg", ("GPL-3.0-only.txt",)),
    ("libswscale", "ffmpeg-libs", "ffmpeg", ("GPL-3.0-only.txt",)),
    ("libffmpegmediaplugin", "ffmpeg-libs", "qt6-multimedia", ("LGPL-3.0-only.txt", "GPL-3.0-only.txt")),
    ("libx265", "x265", "x265", ("GPL-2.0-or-later.txt",)),
    ("libmp3lame", "lame", "lame", ("LGPL-2.0.txt",)),
    ("libbluray", "libbluray", "libbluray", ("LGPL-2.1-or-later.txt",)),
    ("libheif", "libheif", "libheif", ("LGPL-3.0-only.txt",)),
    ("libssl", "openssl", "openssl", ("Apache-2.0-OpenSSL.txt",)),
    ("libcrypto", "openssl", "openssl", ("Apache-2.0-OpenSSL.txt",)),
    ("libvpx", "libvpx", "libvpx", ("BSD-libvpx.txt",)),
    ("libaom", "aom", "aom", ("BSD-aom.txt",)),
    ("libopus", "opus", "opus", ("BSD-opus.txt",)),
    ("libdav1d", "dav1d", "dav1d", ("BSD-dav1d.txt",)),
    ("libopenh264", "openh264", "openh264", ("BSD-openh264.txt",)),
    ("libvorbis", "libvorbis", "libvorbis", ("BSD-libvorbis.txt",)),
]


def _pacman_qi(pkg: str) -> tuple[str, str, str] | None:
    try:
        out = subprocess.check_output(
            ["pacman", "-Qi", pkg],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    name = ver = lic = ""
    for line in out.splitlines():
        if line.startswith("Name"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("Version"):
            ver = line.split(":", 1)[1].strip()
        elif line.startswith("Licenses") or line.startswith("License"):
            lic = line.split(":", 1)[1].strip()
    if not name:
        return None
    return name, ver, lic


def _match_prefix(soname: str) -> tuple[str, str, str | None, tuple[str, ...]] | None:
    base = soname.split(".so")[0] + ".so" if ".so" in soname else soname
    # Prefer longest prefix match
    hits = [row for row in NATIVE_MAP if soname.startswith(row[0]) or base.startswith(row[0])]
    if not hits:
        return None
    return max(hits, key=lambda r: len(r[0]))


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} APPDIR SPDX_DIR", file=sys.stderr)
        return 2
    appdir = Path(sys.argv[1])
    spdx = Path(sys.argv[2])
    dest_root = appdir / "usr" / "share" / "doc" / "meridian" / "third-party"
    dest_root.mkdir(parents=True, exist_ok=True)

    # Always install core SPDX texts for Qt / LGPL compliance.
    qt_dest = dest_root / "qt6"
    qt_dest.mkdir(parents=True, exist_ok=True)
    for name in ("LGPL-3.0-only.txt", "GPL-3.0-only.txt", "GPL-2.0-only.txt", "LGPL-2.0.txt", "LGPL-2.1-or-later.txt"):
        src = spdx / name
        if src.is_file():
            shutil.copy2(src, qt_dest / name)
            # Also beside PySide notice
            pyside = dest_root / "pyside6"
            pyside.mkdir(parents=True, exist_ok=True)
            if name.startswith("LGPL-3") or name.startswith("GPL-3"):
                shutil.copy2(src, pyside / name)

    search_roots = [
        appdir / "usr" / "bin" / "_internal",
        appdir / "usr" / "bin",
    ]
    found: dict[str, set[str]] = {}
    for root in search_roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            name = path.name
            if ".so" not in name:
                continue
            matched = _match_prefix(name)
            if not matched:
                continue
            _prefix, folder, _pkg, _spdx = matched
            found.setdefault(folder, set()).add(name)

    lines: list[str] = [
        "Meridian AppImage — bundled native libraries (build host record)",
        "================================================================",
        "",
        "This file lists shared libraries detected in the AppImage that map to",
        "known third-party components, plus build-host package versions when",
        "available (Arch/CachyOS pacman). Corresponding Source for GPL",
        "components redistributed here may be obtained from the same",
        "distribution's package sources at these versions.",
        "",
        "libx264 is intentionally NOT bundled (GPL-2.0-only vs Meridian GPL-3).",
        "",
    ]

    for folder in sorted(found):
        row = next(r for r in NATIVE_MAP if r[1] == folder)
        _prefix, _folder, pkg, spdx_names = row
        dest = dest_root / folder
        dest.mkdir(parents=True, exist_ok=True)
        for spdx_name in spdx_names:
            src = spdx / spdx_name
            if src.is_file():
                shutil.copy2(src, dest / spdx_name)
        # Copy from /usr/share/licenses/<pkg> when present
        if pkg:
            lic_dir = Path("/usr/share/licenses") / pkg
            if lic_dir.is_dir():
                for f in sorted(lic_dir.iterdir()):
                    if f.is_file() and f.stat().st_size < 2_000_000:
                        shutil.copy2(f, dest / f.name)
        info = _pacman_qi(pkg) if pkg else None
        lines.append(f"[{folder}]")
        lines.append(f"  bundled examples: {', '.join(sorted(found[folder])[:8])}")
        if info:
            lines.append(f"  build-host package: {info[0]} {info[1]}")
            lines.append(f"  build-host licences: {info[2]}")
        elif pkg:
            lines.append(f"  build-host package hint: {pkg} (metadata unavailable)")
        lines.append(f"  texts: third-party/{folder}/")
        lines.append("")

    # Explicit host-only note
    lines.extend(
        [
            "[host-only — not bundled]",
            "  libx264 / x264 — install from your distribution for H.264 via Qt FFmpeg plugin",
            "  ffmpeg CLI — used for mood analysis decode; install on PATH",
            "",
        ]
    )

    (dest_root / "BUILD_LIBRARIES.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {dest_root / 'BUILD_LIBRARIES.txt'} ({len(found)} component groups)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
