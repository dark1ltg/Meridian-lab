from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from meridian import APP_NAME, ORG_NAME, __version__
from meridian.ui.fonts import register_bundled_fonts, sans
from meridian.ui.main_window import MainWindow

STYLE = """
QWidget {
    background: #0b1020;
    color: #d7deee;
    font-family: "Ubuntu Sans", "Ubuntu", "Noto Sans", "DejaVu Sans", sans-serif;
    font-size: 13px;
    font-weight: 400;
}
QMainWindow, QStatusBar {
    background: #0b1020;
}
QLabel#brand {
    font-family: "Ubuntu Sans", "Ubuntu", sans-serif;
    font-size: 16px;
    font-weight: 500;
    letter-spacing: 2px;
    color: #e8b86d;
}
QLabel#chip {
    background: #1a243c;
    color: #6ec8c5;
    padding: 4px 10px;
    border-radius: 11px;
    font-family: "Ubuntu Sans", "Ubuntu", sans-serif;
    font-size: 13px;
    font-weight: 400;
    letter-spacing: 0.5px;
}
QLabel#hint {
    color: #8b95ad;
    font-family: "Ubuntu Sans", "Ubuntu", sans-serif;
    font-size: 13px;
    font-weight: 400;
}
QLabel#section {
    color: #8b95ad;
    font-family: "Ubuntu Sans", "Ubuntu", sans-serif;
    font-size: 11px;
    font-weight: 400;
    letter-spacing: 1.0px;
}
QLabel#nowTitle {
    font-family: "Ubuntu Sans", "Ubuntu", sans-serif;
    font-size: 13px;
    font-weight: 400;
    color: #f4f7ff;
}
QLabel#nowMeta {
    color: #8b95ad;
    font-family: "Ubuntu Sans", "Ubuntu", sans-serif;
    font-size: 12px;
    font-weight: 400;
}
QLabel#timeLabel, QLabel#volLabel {
    color: #8b95ad;
    font-family: "Ubuntu Sans", "Ubuntu", sans-serif;
    font-size: 12px;
    font-weight: 400;
}
QLabel#quadSub {
    color: #8b95ad;
    font-family: "Ubuntu Sans", "Ubuntu", sans-serif;
    font-size: 11px;
    font-weight: 400;
}
QLabel#quadTitle {
    font-family: "Ubuntu Sans", "Ubuntu", sans-serif;
    font-size: 11px;
    font-weight: 500;
    letter-spacing: 1.5px;
}
QPushButton#playBtn {
    background: #e8b86d;
    color: #1a1408;
    border: none;
    border-radius: 21px;
    font-family: "Ubuntu Sans", "Ubuntu", sans-serif;
    font-size: 13px;
    font-weight: 500;
}
QPushButton#playBtn:hover { background: #f3cc8d; }
QPushButton#ghostBtn {
    background: #182238;
    color: #d7deee;
    border: 1px solid #2a3a58;
    border-radius: 10px;
    padding: 6px 10px;
    font-family: "Ubuntu Sans", "Ubuntu", sans-serif;
    font-size: 13px;
    font-weight: 400;
}
QPushButton#ghostBtn:hover { border-color: #e8b86d; }
QComboBox {
    background: #182238;
    border: 1px solid #2a3a58;
    border-radius: 8px;
    padding: 4px 10px;
    min-width: 110px;
    font-family: "Ubuntu Sans", "Ubuntu", sans-serif;
    font-size: 13px;
    font-weight: 400;
}
QComboBox QAbstractItemView {
    background: #121a2e;
    selection-background-color: #243049;
    font-family: "Ubuntu Sans", "Ubuntu", sans-serif;
    font-size: 13px;
}
QLineEdit#trackSearch {
    background: #182238;
    border: 1px solid #2a3a58;
    border-radius: 8px;
    padding: 6px 10px;
    min-width: 220px;
    color: #d7deee;
    font-family: "Ubuntu Sans", "Ubuntu", sans-serif;
    font-size: 13px;
    font-weight: 400;
}
QLineEdit#trackSearch:focus { border-color: #e8b86d; }
QCompleter QAbstractItemView {
    background: #121a2e;
    color: #d7deee;
    border: 1px solid #2a3a58;
    selection-background-color: #243049;
    outline: none;
    font-family: "Ubuntu Sans", "Ubuntu", sans-serif;
    font-size: 13px;
}
QToolTip {
    background: #182238;
    color: #e8edf7;
    border: 1px solid #2a3a58;
    padding: 6px 8px;
    border-radius: 6px;
    font-family: "Ubuntu Sans", "Ubuntu", sans-serif;
    font-size: 12px;
    font-weight: 500;
}
QSlider::groove:horizontal {
    height: 4px;
    background: #243049;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 12px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
    background: #e8b86d;
}
QSlider::sub-page:horizontal { background: #e8b86d; border-radius: 2px; }
QSlider#volumeSlider::groove:horizontal,
QSlider#volumeSlider::handle:horizontal,
QSlider#volumeSlider::sub-page:horizontal {
    background: transparent;
    border: none;
    width: 0px;
    height: 0px;
    margin: 0px;
}
QListWidget, QListWidget#quadList, QListWidget#queueList {
    background: transparent;
    border: none;
    outline: none;
    font-family: "Ubuntu Condensed", "Ubuntu Sans", "Ubuntu", sans-serif;
    font-size: 12px;
    font-weight: 400;
}
QListWidget#quadList QScrollBar:horizontal,
QListWidget#queueList QScrollBar:horizontal,
QListWidget QScrollBar:horizontal {
    height: 0px;
    max-height: 0px;
    min-height: 0px;
    width: 0px;
    border: none;
    margin: 0px;
    padding: 0px;
    background: transparent;
}
QListWidget::item {
    padding: 6px 4px;
    border-radius: 6px;
    font-family: "Ubuntu Condensed", "Ubuntu Sans", "Ubuntu", sans-serif;
    font-size: 12px;
    font-weight: 400;
}
QListWidget#quadList::item {
    padding: 8px 4px;
}
QListWidget::item:selected {
    background: #243049;
    color: #f4f7ff;
}
QListWidget::item:hover { background: #1a243c; }
QSplitter::handle { background: #0b1020; width: 18px; height: 18px; }
QFrame#queuePanel {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #160b28, stop:1 #0c0616);
    border: 1px solid #2a2044;
    border-radius: 10px;
}
QStatusBar {
    color: #8b95ad;
    font-family: "Ubuntu Sans", "Ubuntu", sans-serif;
    font-size: 12px;
    font-weight: 400;
}
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #2a3a58;
    border-radius: 4px;
    min-height: 24px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    height: 0px;
    width: 0px;
    background: transparent;
}
#transport {
    background: #10182a;
    border: 1px solid #243049;
    border-radius: 14px;
}
"""


def resource_path(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)


def run() -> int:
    import os

    # Frozen AppImage re-execs itself for the GL probe. Leave before any window.
    if os.environ.get("MERIDIAN_GL_PROBE_CHILD", "").strip().lower() in {"1", "true", "yes"}:
        from meridian.gl_viewport import child_probe_main

        return child_probe_main()

    from meridian.gl_viewport import ensure_hardware_gl, software_forced

    # Hardware mood-map viewport is the default. The probe is a child process
    # because a broken EGL/DRI driver can abort, and try/except cannot catch that.
    explicit_soft = software_forced()
    prefer_soft = os.environ.get("QT_OPENGL", "").strip().lower() == "software" or os.environ.get(
        "LIBGL_ALWAYS_SOFTWARE", ""
    ).strip().lower() in {"1", "true", "yes"}
    use_hw = ensure_hardware_gl()
    try:
        from PySide6.QtGui import QSurfaceFormat

        if use_hw:
            QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseDesktopOpenGL, True)
            fmt = QSurfaceFormat()
            fmt.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
            fmt.setSwapBehavior(QSurfaceFormat.SwapBehavior.DoubleBuffer)
            fmt.setSwapInterval(1)
            fmt.setSamples(4)
            fmt.setDepthBufferSize(24)
            QSurfaceFormat.setDefaultFormat(fmt)
        elif prefer_soft or not explicit_soft:
            # Explicit QT/LIBGL software, or the hardware probe failed.
            QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL, True)
    except Exception:
        if use_hw:
            os.environ["MERIDIAN_NO_GL"] = "1"
            os.environ["MERIDIAN_GL_PROBE"] = "fail"

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationDisplayName(f"Meridian {__version__}")
    icon = resource_path("resources", "meridian.png")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    register_bundled_fonts()
    app.setFont(sans(13))
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    return app.exec()
