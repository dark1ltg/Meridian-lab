from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MERIDIAN_NO_GL", "1")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from meridian.library import Library
from meridian.ui.search import TrackSearch


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def library(tmp_path: Path) -> Library:
    lib = Library(tmp_path / "search.sqlite")
    with lib.lock:
        for title in ["Alpha Song", "Beta Song", "Gamma Song"]:
            lib.conn.execute(
                "INSERT INTO tracks (path, title, artist, album, valence, energy, added_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (f"/tmp/{title}.mp3", title, "Artist", "Album", 0.5, 0.5, 0.0),
            )
        lib.conn.commit()
    return lib


def _prepare(ts: TrackSearch) -> object:
    if ts.text():
        ts.clear()
        ts._user_query = ""
    QTest.qWait(20)
    QTest.keyClicks(ts, "Song")
    QTest.qWait(150)
    ts._completer.complete()
    QTest.qWait(40)
    popup = ts._completer.popup()
    popup.resize(420, 90)
    popup.show()
    QTest.qWait(40)
    return popup


def test_search_arrow_keeps_query_and_enter_injects_highlight(qapp, library: Library) -> None:
    chosen: list[int] = []
    ts = TrackSearch(library)
    ts.track_chosen.connect(chosen.append)
    ts.resize(420, 36)
    ts.show()
    ts.setFocus()
    popup = _prepare(ts)

    assert ts.text() == "Song"
    assert len(ts._hits) == 3

    QTest.keyClick(popup, Qt.Key.Key_Down)
    QTest.qWait(40)
    QTest.keyClick(popup, Qt.Key.Key_Down)
    QTest.qWait(40)
    assert ts.text() == "Song"
    assert len(ts._hits) == 3
    assert popup.currentIndex().row() == 1

    QTest.keyClick(ts, Qt.Key.Key_Return)
    QTest.qWait(40)
    assert chosen == [2]
    assert ts.text() == ""


def test_search_single_click_does_not_inject(qapp, library: Library) -> None:
    chosen: list[int] = []
    ts = TrackSearch(library)
    ts.track_chosen.connect(chosen.append)
    ts.resize(420, 36)
    ts.show()
    ts.setFocus()
    popup = _prepare(ts)

    idx = popup.model().index(1, 0)
    pt = popup.visualRect(idx).center()
    QTest.mouseClick(popup.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pt)
    QTest.qWait(40)

    assert chosen == []
    assert ts.text() == "Song"
    assert len(ts._hits) == 3
    assert popup.currentIndex().row() == 1


def test_search_double_click_injects(qapp, library: Library) -> None:
    chosen: list[int] = []
    ts = TrackSearch(library)
    ts.track_chosen.connect(chosen.append)
    ts.resize(420, 36)
    ts.show()
    ts.setFocus()
    popup = _prepare(ts)

    idx = popup.model().index(1, 0)
    pt = popup.visualRect(idx).center()
    QTest.mouseDClick(popup.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pt)
    QTest.qWait(40)

    assert chosen == [2]
    assert ts.text() == ""


def test_search_picked_pulls_into_queue(qapp, library: Library) -> None:
    """Main-window slot must still play + queue on track_chosen."""
    from meridian.ui import main_window as mw_mod

    pulls: list[int] = []

    class _FakeMap:
        def lens_mood(self):
            return 0.5, 0.5, 0.2

        def set_lens(self, *_args):
            return None

    class _Stub:
        def __init__(self):
            self.library = library
            self.map = _FakeMap()
            self.settings = type("S", (), {"setValue": lambda *a, **k: None})()
            self._status = ""

        def _set_status(self, msg: str) -> None:
            self._status = msg

        def refresh_plan(self, **_kwargs) -> None:
            return None

        def _pull_and_play(self, track_id: int) -> None:
            pulls.append(track_id)

    stub = _Stub()
    mw_mod.MainWindow._search_picked(stub, 2)
    assert pulls == [2]
    assert "Lens on" in stub._status
