"""PySide QueuedConnection + lambda drops worker finished — scan must use Slots."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MERIDIAN_NO_GL", "1")

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _IntWorker(QObject):
    finished = Signal(int)

    def run(self) -> None:
        self.finished.emit(7)


def test_queued_lambda_on_signal_int_is_dropped(qapp) -> None:
    """Guard: QueuedConnection to a lambda must not be relied on for handoff."""
    got: list = []
    holder: dict = {}

    def start() -> None:
        w = _IntWorker()
        t = QThread()
        holder["w"] = w
        holder["t"] = t
        w.moveToThread(t)
        t.started.connect(w.run)
        w.finished.connect(lambda *_a: t.quit())
        queued = Qt.ConnectionType.QueuedConnection
        w.finished.connect(lambda n, g=2: got.append((n, g)), queued)
        t.start()

        def check() -> None:
            if t.isRunning():
                QTimer.singleShot(20, check)
                return
            qapp.quit()

        QTimer.singleShot(20, check)

    QTimer.singleShot(0, start)
    QTimer.singleShot(3000, qapp.quit)
    qapp.exec()
    assert got == [], f"expected Queued lambda to drop, got {got}"


def test_scan_finish_bridge_delivers_queued(qapp) -> None:
    from meridian.ui.main_window import _ScanFinishBridge

    got: list = []
    holder: dict = {}

    def handler(added: int, gen: int) -> None:
        got.append((added, gen))

    def start() -> None:
        w = _IntWorker()
        t = QThread()
        holder["w"] = w
        holder["t"] = t
        bridge = _ScanFinishBridge(handler, 9)
        holder["bridge"] = bridge
        w.moveToThread(t)
        t.started.connect(w.run)
        w.finished.connect(lambda *_a: t.quit())
        w.finished.connect(bridge.on_finished, Qt.ConnectionType.QueuedConnection)
        t.start()

        def check() -> None:
            if t.isRunning():
                QTimer.singleShot(20, check)
                return
            qapp.quit()

        QTimer.singleShot(20, check)

    QTimer.singleShot(0, start)
    QTimer.singleShot(3000, qapp.quit)
    qapp.exec()
    assert got == [(7, 9)]


def test_analyze_finish_bridge_delivers_queued(qapp) -> None:
    from meridian.ui.main_window import _AnalyzeFinishBridge

    calls: list = []

    class FakeWin:
        def _analyze_done(self, gen, thread, worker) -> None:
            calls.append((gen, thread, worker))

    class VoidWorker(QObject):
        finished = Signal()

        def run(self) -> None:
            self.finished.emit()

    holder: dict = {}
    win = FakeWin()
    thread_sentinel = object()
    worker_sentinel = object()

    def start() -> None:
        w = VoidWorker()
        t = QThread()
        holder["w"] = w
        holder["t"] = t
        bridge = _AnalyzeFinishBridge(win, 4, thread_sentinel, worker_sentinel)  # type: ignore[arg-type]
        holder["bridge"] = bridge
        w.moveToThread(t)
        t.started.connect(w.run)
        w.finished.connect(lambda *_a: t.quit())
        w.finished.connect(bridge.on_finished, Qt.ConnectionType.QueuedConnection)
        t.start()

        def check() -> None:
            if t.isRunning():
                QTimer.singleShot(20, check)
                return
            qapp.quit()

        QTimer.singleShot(20, check)

    QTimer.singleShot(0, start)
    QTimer.singleShot(3000, qapp.quit)
    qapp.exec()
    assert calls == [(4, thread_sentinel, worker_sentinel)]


def test_scanworker_finished_reaches_handler_via_bridge(qapp, tmp_path: Path) -> None:
    """Real ScanWorker + start_worker + Slot bridge (the first-run handoff path)."""
    from meridian.library import Library
    from meridian.scanner import ScanWorker, start_worker
    from meridian.ui.main_window import _ScanFinishBridge

    music = tmp_path / "Music"
    music.mkdir()
    src_dir = Path.home() / "Music" / "Music"
    samples = list(src_dir.glob("*.mp3"))[:2]
    if len(samples) < 2:
        pytest.skip("need sample mp3s under ~/Music/Music")
    for f in samples:
        shutil.copy(f, music / f.name)

    lib = Library(tmp_path / "lib.sqlite")
    lib.add_folder(str(music))

    done: list = []
    holder: dict = {}
    scan_gen = 2

    def on_finished(added: int, gen: int) -> None:
        done.append((added, gen))
        qapp.quit()

    def start() -> None:
        worker = ScanWorker(lib, force=False)
        thread = start_worker(worker)
        holder["worker"] = worker
        holder["thread"] = thread
        bridge = _ScanFinishBridge(on_finished, scan_gen)
        holder["bridge"] = bridge
        worker.finished.connect(bridge.on_finished, Qt.ConnectionType.QueuedConnection)

        def watchdog() -> None:
            if thread.isRunning() and not done:
                QTimer.singleShot(50, watchdog)
                return
            if not done:
                qapp.quit()

        QTimer.singleShot(50, watchdog)

    QTimer.singleShot(0, start)
    QTimer.singleShot(20000, qapp.quit)
    qapp.exec()

    assert done == [(2, scan_gen)], done
    thread = holder["thread"]
    if thread.isRunning():
        thread.quit()
        thread.wait(3000)
    lib.close()


def test_mainwindow_avoids_queued_finished_lambdas() -> None:
    src = Path(__file__).resolve().parents[1] / "meridian" / "ui" / "main_window.py"
    text = src.read_text(encoding="utf-8")
    assert "lambda added, g=gen" not in text
    assert "lambda g=gen, t=thread, w=worker" not in text
    assert "_ScanFinishBridge" in text
    assert "_AnalyzeFinishBridge" in text
