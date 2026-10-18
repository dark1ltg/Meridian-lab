"""Worker QThread reaping must never call wait() on the current thread."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MERIDIAN_NO_GL", "1")

from PySide6.QtCore import QMetaObject, QObject, Qt, QThread, Slot
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_reap_from_worker_thread_does_not_wait_on_self(qapp) -> None:
    from meridian.ui.main_window import MainWindow

    win = MainWindow.__new__(MainWindow)
    win._lingering_workers = []
    win._close_library_when_idle = False
    win._pending_analyze_after_linger = False
    win._quit_when_idle = False
    win._closing = False

    thread = QThread()
    # Keep the thread's event loop alive until we quit it.
    thread.start()
    assert thread.isRunning()

    result_box: dict[str, object] = {}

    class Invoker(QObject):
        @Slot()
        def go(self) -> None:
            result_box["ok"] = win._reap_worker_thread(thread, None, wait_ms=50)
            result_box["lingered"] = any(t is thread for t, _w in win._lingering_workers)

    inv = Invoker()
    inv.moveToThread(thread)
    ok = QMetaObject.invokeMethod(inv, "go", Qt.ConnectionType.BlockingQueuedConnection)
    assert ok
    assert result_box["ok"] is False
    assert result_box["lingered"] is True

    # Linger path connected quit; ensure exit without process abort.
    if thread.isRunning():
        thread.quit()
    assert thread.wait(3000)
    win._lingering_workers.clear()
    inv.deleteLater()
    thread.deleteLater()
    qapp.processEvents()


def test_reap_from_gui_thread_waits_normally(qapp) -> None:
    from meridian.ui.main_window import MainWindow

    win = MainWindow.__new__(MainWindow)
    win._lingering_workers = []
    win._close_library_when_idle = False
    win._pending_analyze_after_linger = False
    win._quit_when_idle = False
    win._closing = False

    thread = QThread()
    thread.start()
    assert thread.isRunning()
    # GUI thread reap should wait and fully stop.
    assert win._reap_worker_thread(thread, None, wait_ms=3000) is True
    assert not thread.isRunning()
    assert win._lingering_workers == []
    qapp.processEvents()


def test_linger_reaps_already_finished_thread(qapp) -> None:
    """If the thread exits before finished is connected, linger must still reap."""
    from meridian.ui.main_window import MainWindow

    win = MainWindow.__new__(MainWindow)
    win._lingering_workers = []
    win._close_library_when_idle = False
    win._pending_analyze_after_linger = False
    win._quit_when_idle = False
    win._closing = False

    thread = QThread()
    thread.start()
    thread.quit()
    assert thread.wait(3000)
    assert not thread.isRunning()

    win._linger_worker(thread, None)
    assert any(t is thread for t, _w in win._lingering_workers)
    qapp.processEvents()
    assert win._lingering_workers == []
    thread.deleteLater()
    qapp.processEvents()
