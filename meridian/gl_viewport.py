"""Hardware OpenGL probe for the mood-map viewport.

Creating ``QOpenGLWidget`` on a broken EGL/DRI stack can abort the process.
``try/except`` cannot catch that, so the check runs in a child process.
A failed probe, or an explicit software override, keeps the plain widget.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROBE_TIMEOUT_S = 8
_PROBE_OK = "ok"
_PROBE_FAIL = "fail"

_SOFTWARE_GL = {"0", "false", "no", "off", "software"}
_TRUTHY = {"1", "true", "yes"}


def _flag(name: str, allowed: set[str]) -> bool:
    return os.environ.get(name, "").strip().lower() in allowed


def software_forced() -> bool:
    """True when the user asked for a software mood-map viewport."""
    if _flag("MERIDIAN_NO_GL", _TRUTHY):
        return True
    if os.environ.get("QT_OPENGL", "").strip().lower() == "software":
        return True
    if _flag("LIBGL_ALWAYS_SOFTWARE", _TRUTHY):
        return True
    if os.environ.get("MERIDIAN_GL", "").strip().lower() in _SOFTWARE_GL:
        return True
    return False


def want_hardware_gl() -> bool:
    """Hardware is the default unless a software override is set."""
    return not software_forced()


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def child_probe_main() -> int:
    """Return 0 when a tiny desktop-GL widget gets a valid context.

    Must not call :func:`ensure_hardware_gl` (that would recurse).
    """
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QSurfaceFormat
        from PySide6.QtOpenGLWidgets import QOpenGLWidget
        from PySide6.QtWidgets import QApplication
    except Exception:
        return 1
    try:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseDesktopOpenGL, True)
        fmt = QSurfaceFormat()
        fmt.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
        fmt.setSwapBehavior(QSurfaceFormat.SwapBehavior.DoubleBuffer)
        fmt.setSwapInterval(1)
        fmt.setSamples(4)
        fmt.setDepthBufferSize(24)
        QSurfaceFormat.setDefaultFormat(fmt)
        app = QApplication.instance() or QApplication([])
        gl = QOpenGLWidget()
        gl.setFormat(fmt)
        gl.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)
        gl.resize(32, 32)
        gl.show()
        app.processEvents()
        ctx = gl.context()
        if ctx is None or not ctx.isValid():
            gl.grabFramebuffer()
            app.processEvents()
            ctx = gl.context()
        ok = ctx is not None and ctx.isValid()
        gl.hide()
        gl.deleteLater()
        app.processEvents()
        return 0 if ok else 1
    except Exception:
        return 1


def probe_hardware_gl() -> bool:
    """Run :func:`child_probe_main` in a child. Signals and timeouts are failure."""
    env = os.environ.copy()
    env["MERIDIAN_GL_PROBE_CHILD"] = "1"
    if _frozen():
        cmd = [sys.executable]
    else:
        root = str(Path(__file__).resolve().parent.parent)
        prev = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = root if not prev else root + os.pathsep + prev
        cmd = [
            sys.executable,
            "-c",
            "from meridian.gl_viewport import child_probe_main; raise SystemExit(child_probe_main())",
        ]
    try:
        proc = subprocess.run(
            cmd,
            env=env,
            timeout=PROBE_TIMEOUT_S,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def ensure_hardware_gl() -> bool:
    """Probe once per process. Failure sets ``MERIDIAN_NO_GL`` so the map stays software."""
    if software_forced():
        return False
    cached = os.environ.get("MERIDIAN_GL_PROBE", "").strip().lower()
    if cached == _PROBE_OK:
        return True
    if cached == _PROBE_FAIL:
        os.environ["MERIDIAN_NO_GL"] = "1"
        return False
    if probe_hardware_gl():
        os.environ["MERIDIAN_GL_PROBE"] = _PROBE_OK
        return True
    os.environ["MERIDIAN_NO_GL"] = "1"
    os.environ["MERIDIAN_GL_PROBE"] = _PROBE_FAIL
    return False
