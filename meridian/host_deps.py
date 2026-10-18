"""Host library checks for AppImage / Qt Multimedia playback and analyze."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import shutil
import sys
from pathlib import Path

# Common sonames across distros (Arch/CachyOS currently ships .165).
_LIBX264_CANDIDATES = (
    "x264",
    "libx264.so.165",
    "libx264.so.164",
    "libx264.so.163",
    "libx264.so.161",
    "libx264.so",
)


def host_libx264_available() -> bool:
    """True when the dynamic linker can load a system libx264."""
    seen: set[str] = set()
    names: list[str] = []
    found = ctypes.util.find_library("x264")
    if found:
        names.append(found)
    names.extend(_LIBX264_CANDIDATES)
    for name in names:
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            ctypes.CDLL(name)
            return True
        except OSError:
            continue
    return False


def host_ffmpeg_available() -> bool:
    """True when the host ``ffmpeg`` CLI is on PATH (needed for mood analyze)."""
    return shutil.which("ffmpeg") is not None


def ffmpeg_media_backend_likely() -> bool:
    """True when Qt is likely to use the FFmpeg multimedia plugin."""
    backend = (os.environ.get("QT_MEDIA_BACKEND") or "").strip().lower()
    if backend == "gstreamer":
        return False
    if backend == "ffmpeg":
        return True
    # AppImage AppRun defaults to ffmpeg; empty often still picks FFmpeg on Linux Qt builds.
    if os.environ.get("APPIMAGE") or getattr(sys, "_MEIPASS", None):
        return True
    return backend == ""


def should_warn_missing_libx264() -> bool:
    return ffmpeg_media_backend_likely() and not host_libx264_available()


def should_warn_missing_ffmpeg() -> bool:
    """Analyze needs a host ffmpeg binary; AppImage does not ship one."""
    return not host_ffmpeg_available()


def libx264_missing_message() -> str:
    return (
        "Meridian’s playback backend needs the system H.264 library "
        "<b>libx264</b>, which is not bundled (GPL-2 licence).\n\n"
        "It was not found on this computer. Without it, Qt’s FFmpeg "
        "audio backend may fail to start and playback can be broken.\n\n"
        "<b>Install libx264 from your distribution, then restart Meridian.</b>\n\n"
        "Examples:\n"
        "• Arch / CachyOS: <code>sudo pacman -S x264</code>\n"
        "• Fedora: <code>sudo dnf install x264-libs</code>\n"
        "• Debian / Ubuntu: <code>sudo apt install libx264-164</code> "
        "(package name may vary by release)\n"
    )


def libx264_missing_status() -> str:
    return "Playback may fail — install system libx264 (e.g. pacman -S x264), then restart."


def ffmpeg_missing_message() -> str:
    return (
        "Meridian needs the host <b>ffmpeg</b> command to decode audio and place "
        "tracks on the mood map. It is not bundled in the AppImage.\n\n"
        "ffmpeg was not found on this computer. Playback can still work, but "
        "scan/analyze will leave stars on genre seeds instead of waveform "
        "placement.\n\n"
        "<b>Install ffmpeg from your distribution, then restart Meridian.</b>\n\n"
        "Examples:\n"
        "• Arch / CachyOS: <code>sudo pacman -S ffmpeg</code>\n"
        "• Fedora: <code>sudo dnf install ffmpeg</code>\n"
        "• Debian / Ubuntu: <code>sudo apt install ffmpeg</code>\n"
    )


def ffmpeg_missing_status() -> str:
    return "Mood analysis needs ffmpeg on this system (e.g. pacman -S ffmpeg), then restart."


def _rotational_from_sysfs(sys_node: Path) -> bool | None:
    """Walk *sys_node* (and parents) for ``queue/rotational``.

    Resolves sysfs symlinks first so partitions like ``nvme0n1p2`` climb to the
    parent disk node that actually owns ``queue/rotational``.
    """
    try:
        cur = sys_node.resolve()
    except OSError:
        cur = sys_node
    for _ in range(8):
        rot = cur / "queue" / "rotational"
        if rot.is_file():
            try:
                return rot.read_text(encoding="ascii").strip() == "1"
            except OSError:
                return None
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return None


def _block_sysfs_from_devnode(dev: str) -> Path | None:
    """Map ``/dev/nvme0n1p2`` → ``/sys/class/block/nvme0n1p2`` when present."""
    name = Path(dev).name
    if not name:
        return None
    candidate = Path("/sys/class/block") / name
    return candidate if candidate.exists() else None


def _findmnt_source(path: Path) -> str | None:
    """Best-effort mount SOURCE for *path* (handles btrfs ``dev[@subvol]``)."""
    import subprocess

    try:
        out = subprocess.check_output(
            ["findmnt", "-n", "-o", "SOURCE", "-T", str(path)],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not out:
        return None
    # btrfs: /dev/nvme0n1p2[/@home] → /dev/nvme0n1p2
    if "[" in out:
        out = out.split("[", 1)[0]
    return out or None


def path_is_rotational(path: str | Path) -> bool | None:
    """True when *path* lives on a rotational disk (HDD), False on SSD/NVMe.

    Returns ``None`` when the device cannot be classified (non-Linux, missing
    sysfs, unreachable path). Used only for silent analyze-worker sizing.
    """
    try:
        target = Path(path).expanduser()
        # Prefer an existing ancestor so library roots still resolve.
        probe = target
        st = None
        for _ in range(8):
            try:
                if probe.exists():
                    st = probe.stat()
                    break
            except OSError:
                return None
            if probe.parent == probe:
                return None
            probe = probe.parent
        if st is None:
            return None
    except OSError:
        return None

    # Fast path: maj:min → /sys/dev/block (fails on some btrfs subvol devices).
    try:
        maj, minor = os.major(st.st_dev), os.minor(st.st_dev)
        cur = Path(f"/sys/dev/block/{maj}:{minor}")
        if cur.exists():
            try:
                flag = _rotational_from_sysfs(cur.resolve())
            except OSError:
                flag = None
            if flag is not None:
                return flag
    except (AttributeError, OverflowError, ValueError, OSError):
        pass

    # Fallback: findmnt SOURCE → /sys/class/block/<name>/queue/rotational.
    source = _findmnt_source(probe)
    if source:
        block = _block_sysfs_from_devnode(source)
        if block is not None:
            flag = _rotational_from_sysfs(block)
            if flag is not None:
                return flag
    return None


def preferred_analyze_workers(folders: list[str] | None = None) -> int:
    """Silent worker count: SSD/NVMe → 2, HDD or unknown → 1.

    Override with ``MERIDIAN_ANALYZE_WORKERS=1`` or ``2`` (tests / power users).
    No UI — analyze just uses more ffmpeg listeners when the library is on flash.
    Mixed libraries (any rotational folder) stay at 1 to avoid HDD thrash.
    """
    env = (os.environ.get("MERIDIAN_ANALYZE_WORKERS") or "").strip()
    if env in {"1", "2"}:
        return int(env)
    folders = [str(f) for f in (folders or []) if f]
    if not folders:
        return 1
    flags = [path_is_rotational(f) for f in folders]
    known = [flag for flag in flags if flag is not None]
    if not known:
        return 1
    if any(known):
        return 1
    return 2
