"""Regression tests for bug-hunt scores 8/7/6 (queue advance, thrash, analyze status)."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MERIDIAN_NO_GL", "1")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from meridian.host_deps import (
    ffmpeg_missing_message,
    ffmpeg_missing_status,
    host_ffmpeg_available,
    should_warn_missing_ffmpeg,
)
from meridian.library import Library
from meridian.player import Player
from meridian.ui.main_window import MainWindow
from meridian.ui.mood_map import VIEW_ZOOM_MAX, MoodMap


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_rescan_order_preserves_pcm_coords(tmp_path: Path) -> None:
    lib = Library(tmp_path / "rescan.sqlite")
    try:
        lib.upsert_track(
            {
                "path": "/t/a.mp3",
                "title": "a",
                "artist": "A",
                "album": "L",
                "genre": "Rock",
                "duration_ms": 1,
                "year": None,
                "bpm": None,
                "valence": 0.91,
                "energy": 0.12,
                "mood_confidence": 0.85,
                "confidence_note": "pcm",
                "low_trust": 0,
                "added_at": 0,
                "mtime": 1,
                "analyzed": 1,
            }
        )
        lib.set_analyzed_mood(
            1, 0.91, 0.12, None, confidence=0.85, brightness=0.7, acoustic_flux=0.3
        )
        lib.mark_all_pending_analysis()
        lib.upsert_track(
            {
                "path": "/t/a.mp3",
                "title": "a",
                "artist": "A",
                "album": "L",
                "genre": "Rock",
                "duration_ms": 1,
                "year": None,
                "bpm": None,
                "valence": 0.42,
                "energy": 0.67,
                "mood_confidence": 0.25,
                "confidence_note": "seed",
                "low_trust": 1,
                "added_at": 0,
                "mtime": 2,
                "analyzed": 0,
            }
        )
        t = lib.get(1)
        assert t is not None
        assert abs(t.valence - 0.91) < 1e-9
        assert abs(t.energy - 0.12) < 1e-9
        assert not t.analyzed
    finally:
        lib.close()


def test_pull_and_play_current_track_does_not_strand_queue(qapp) -> None:
    w = MainWindow.__new__(MainWindow)
    w.session_queue = [1, 2, 3, 4]
    w.queue_index = 0
    w.ephemeral = set()
    w.explicit = []
    w.player = MagicMock()
    w.player.current = SimpleNamespace(id=1)
    played: list[int] = []
    w.play_id = lambda tid: played.append(tid)  # type: ignore[method-assign]

    MainWindow._pull_and_play(w, 1)

    assert w.session_queue == [1, 2, 3, 4]
    assert w.queue_index == 0
    assert w.ephemeral == set()
    assert played == [1]


def test_pull_and_play_other_track_still_inserts(qapp) -> None:
    w = MainWindow.__new__(MainWindow)
    w.session_queue = [1, 2, 3]
    w.queue_index = 0
    w.ephemeral = set()
    w.explicit = []
    w.player = MagicMock()
    w.player.current = SimpleNamespace(id=1)
    played: list[int] = []
    w.play_id = lambda tid: played.append(tid)  # type: ignore[method-assign]

    MainWindow._pull_and_play(w, 9)

    assert w.session_queue == [1, 9, 2, 3]
    assert w.queue_index == 1
    assert 9 in w.ephemeral
    assert played == [9]


def test_zoom_past_ceiling_schedules_lod(qapp) -> None:
    m = MoodMap()

    class FakeStar:
        def __init__(self) -> None:
            self._shown = True
            self._press_scene = None

        def show(self) -> None:
            self._shown = True

        def hide(self) -> None:
            self._shown = False

    star = FakeStar()
    m._stars = {1: star}
    m._user_zoom = VIEW_ZOOM_MAX
    m._zoom_target = VIEW_ZOOM_MAX
    m._zoom_active = False
    m._sync_live_stars = lambda force=False: star.show()  # type: ignore[method-assign]
    m._set_zoom_target(VIEW_ZOOM_MAX * 1.06, QPoint(10, 10))
    assert m._zoom_active is True
    assert star._shown is False
    assert m._lod_timer.isActive() is True
    m._flush_zoom_lod()
    assert m._zoom_active is False
    assert star._shown is True


def test_ffmpeg_missing_warning_helpers(monkeypatch) -> None:
    monkeypatch.setattr("meridian.host_deps.shutil.which", lambda _name: None)
    assert host_ffmpeg_available() is False
    assert should_warn_missing_ffmpeg() is True
    assert "ffmpeg" in ffmpeg_missing_message().lower()
    assert "ffmpeg" in ffmpeg_missing_status().lower()

    monkeypatch.setattr("meridian.host_deps.shutil.which", lambda _name: "/usr/bin/ffmpeg")
    assert host_ffmpeg_available() is True
    assert should_warn_missing_ffmpeg() is False


def _bare_window() -> MainWindow:
    w = MainWindow.__new__(MainWindow)
    w._closing = False
    w._expect_natural_advance = False
    w._crossfade_outgoing_id = None
    w._outgoing_settle_finish = False
    w._pending_play_credit = None
    w._last_hard_play_credit = None
    w._abandon_credited_id = None
    w._rebuild_lock = False
    w._pending_plan_refresh = False
    w.plan = None
    w.played_history = []
    w.skips_window = []
    w.ephemeral = set()
    w.explicit = []
    w.session_queue = []
    w.queue_index = 0
    w.library = MagicMock()
    w.player = MagicMock()
    w.transport = MagicMock()
    w.map = MagicMock()
    w.matrix = MagicMock()
    w.band_chip = MagicMock()
    statuses: list[str] = []
    w._statuses = statuses
    w._set_status = lambda m: statuses.append(m)  # type: ignore[method-assign]
    w._fill_queue = lambda: None  # type: ignore[method-assign]
    w._listen_nudge = lambda *_a, **_k: None  # type: ignore[method-assign]
    w._clear_crossfade_credit = lambda: None  # type: ignore[method-assign]
    w._flush_pending_plan_refresh = lambda: None  # type: ignore[method-assign]
    return w


def test_dead_next_does_not_hard_restart_current(qapp, tmp_path: Path) -> None:
    """Score 8: missing/denied next must not stop+restart the playing track."""
    live = tmp_path / "a.mp3"
    live.write_bytes(b"x")
    dead = tmp_path / "missing.mp3"

    lib = Library(tmp_path / "dead-next.sqlite")
    try:
        lib.upsert_track(
            {
                "path": str(live),
                "title": "a",
                "artist": "A",
                "album": "L",
                "genre": "Rock",
                "duration_ms": 180_000,
                "year": None,
                "bpm": None,
                "valence": 0.5,
                "energy": 0.5,
                "mood_confidence": 0.5,
                "confidence_note": "",
                "low_trust": 0,
                "added_at": 0,
                "mtime": 1,
                "analyzed": 1,
            }
        )
        lib.upsert_track(
            {
                "path": str(dead),
                "title": "b",
                "artist": "B",
                "album": "L",
                "genre": "Rock",
                "duration_ms": 180_000,
                "year": None,
                "bpm": None,
                "valence": 0.5,
                "energy": 0.5,
                "mood_confidence": 0.5,
                "confidence_note": "",
                "low_trust": 0,
                "added_at": 0,
                "mtime": 1,
                "analyzed": 1,
            }
        )
        a_id = lib.all_tracks()[0].id
        b_id = lib.all_tracks()[1].id

        w = _bare_window()
        w.library = lib
        w.session_queue = [a_id, b_id]
        w.queue_index = 0
        w.player.current = lib.get(a_id)
        w.player.is_crossfading.return_value = False
        w.player.backend.position.return_value = 50_000
        stops: list[bool] = []
        plays: list[int] = []
        w.player.stop = lambda: stops.append(True)  # type: ignore[method-assign]
        real_play = w.player.play_track

        def capture_play(track):
            plays.append(track.id)
            return real_play(track)

        w.player.play_track = capture_play  # type: ignore[method-assign]
        w._playable_tracks = lambda: [t for t in lib.all_tracks() if Path(t.path).exists()]  # type: ignore[method-assign]
        w.current_context = lambda: SimpleNamespace(band_label="Day")  # type: ignore[method-assign]

        w._replenish_queue = lambda **_k: setattr(w, "session_queue", [a_id]) or setattr(  # type: ignore[method-assign]
            w, "queue_index", 0
        )

        MainWindow.play_id(w, b_id)

        assert stops == [], "must not stop the still-playing track"
        assert plays == [], "must not restart current via play_track"
        assert w.player.current.id == a_id
        assert a_id in w.session_queue
        assert any("waiting" in s.lower() for s in w._statuses)
    finally:
        lib.close()


def test_release_advance_lock_holds_fade_rearm(qapp) -> None:
    """Score 7: failed advance must not re-arm nearly_finished every position tick."""
    p = Player()
    armed: list[bool] = []
    p.track_nearly_finished.connect(lambda: armed.append(True))
    p._active = 0
    p._crossfading = False
    p._advance_emitted = True

    backend = MagicMock()
    p._decks[0].player = backend  # type: ignore[index]
    duration = 180_000
    fade = p._fade_ms(duration)
    backend.duration.return_value = duration
    backend.position.return_value = duration - fade // 2

    p.release_advance_lock()
    assert p._advance_emitted is False
    assert p._seek_hold_advance is True

    # Still inside the fade window — must not re-arm.
    for pos in range(duration - fade, duration, 100):
        backend.position.return_value = pos
        p._on_position(0, pos)
    assert armed == []

    # Leave the fade window (seek back) — hold clears on next tick past fade.
    leave = duration - fade - 500
    backend.position.return_value = leave
    p._on_position(0, leave)
    assert p._seek_hold_advance is False


def test_next_on_sole_playable_keeps_queue_and_skips_credit(qapp) -> None:
    """Score 6: sole-track Next must not empty the queue or record_skip."""
    w = _bare_window()
    w.session_queue = [1]
    w.queue_index = 0
    w.player.current = SimpleNamespace(id=1)
    w.player.is_crossfading.return_value = False
    w.player.backend.position.return_value = 1500
    w.library.record_skip = MagicMock()
    w._playable_tracks = lambda: [SimpleNamespace(id=1, path="/t/a.mp3")]  # type: ignore[method-assign]
    w.current_context = lambda: SimpleNamespace(band_label="Day")  # type: ignore[method-assign]

    def fake_replenish(*, avoid_id=None):
        # Mirror fixed sole-track behavior: keep current queued.
        w.session_queue = [1]
        w.queue_index = 0

    w._replenish_queue = fake_replenish  # type: ignore[method-assign]
    played: list[int] = []
    w.play_id = lambda tid: played.append(tid)  # type: ignore[method-assign]

    MainWindow.play_next(w)

    assert w.session_queue == [1]
    assert played == []
    w.library.record_skip.assert_not_called()
    assert any("waiting" in s.lower() for s in w._statuses)


def test_analyze_done_reports_deferred_not_success(qapp) -> None:
    """Score 6: all-deferred analyze must not claim mood map updated from audio."""
    w = MainWindow.__new__(MainWindow)
    w._closing = False
    w._analyze_gen = 1
    w._analyze_thread = object()
    w._analyze_worker = object()
    w._analyze_pool = []
    w._analyze_remaining = 0
    w._analyze_run_tidy = False
    w._lingering_workers = []
    statuses: list[str] = []
    w._clear_job_status = lambda: None  # type: ignore[method-assign]
    w._set_status = lambda m: statuses.append(m)  # type: ignore[method-assign]
    w._set_job_status = lambda m: None  # type: ignore[method-assign]
    w.refresh_plan = lambda **_k: None  # type: ignore[method-assign]
    w.start_analyze = lambda: None  # type: ignore[method-assign]
    w._reap_worker_thread = lambda *_a, **_k: True  # type: ignore[method-assign]

    class FakeLib:
        def unanalyzed_ids(self):
            return []

        def analyze_session_stats(self):
            return 0, 3

    w.library = FakeLib()  # type: ignore[assignment]
    thread, worker = object(), object()
    w._analyze_thread = thread
    w._analyze_worker = worker
    MainWindow._analyze_done(w, 1, thread, worker)
    assert statuses
    assert "another listen" in statuses[-1].lower() or "pending" in statuses[-1].lower()
    assert "updated from local audio" not in statuses[-1].lower()


def test_analyze_progress_ignores_backward_jumps(qapp) -> None:
    w = MainWindow.__new__(MainWindow)
    w._analyze_progress_shown = 0
    w._analyze_map_tick = 0
    w._last_analyze_map_refresh = 0.0
    statuses: list[str] = []
    w._set_job_status = lambda m: statuses.append(m)  # type: ignore[method-assign]
    w.refresh_plan = lambda **_k: None  # type: ignore[method-assign]
    MainWindow._on_analyze_progress(w, "A", 2, 10)
    MainWindow._on_analyze_progress(w, "B", 1, 10)  # out of order
    MainWindow._on_analyze_progress(w, "C", 3, 10)
    assert len(statuses) == 2
    assert "2/10" in statuses[0]
    assert "3/10" in statuses[1]


def test_start_scan_waits_while_analyze_busy(qapp) -> None:
    w = MainWindow.__new__(MainWindow)
    w._closing = False
    w._lingering_workers = []
    w._scan_thread = None
    w._scan_worker = None
    busy = {"n": 0}
    w._analyze_busy = lambda: True  # type: ignore[method-assign]
    jobs: list[str] = []
    w._set_job_status = lambda m: jobs.append(m)  # type: ignore[method-assign]
    w._start_scan_worker = lambda **_k: busy.__setitem__("n", busy["n"] + 1)  # type: ignore
    MainWindow.start_scan(w)
    assert busy["n"] == 0
    assert jobs and "analyze" in jobs[-1].lower()


def test_analyze_done_reports_all_seed_not_listen(qapp) -> None:
    """Sev 3: pcm_ok==0 and deferred==0 must not imply a waveform listen."""
    w = MainWindow.__new__(MainWindow)
    w._closing = False
    w._analyze_gen = 1
    w._analyze_thread = object()
    w._analyze_worker = object()
    w._analyze_pool = []
    w._analyze_remaining = 0
    w._analyze_run_tidy = False
    w._lingering_workers = []
    statuses: list[str] = []
    w._clear_job_status = lambda: None  # type: ignore[method-assign]
    w._set_status = lambda m: statuses.append(m)  # type: ignore[method-assign]
    w._set_job_status = lambda m: None  # type: ignore[method-assign]
    w.refresh_plan = lambda **_k: None  # type: ignore[method-assign]
    w.start_analyze = lambda: None  # type: ignore[method-assign]
    w._reap_worker_thread = lambda *_a, **_k: True  # type: ignore[method-assign]

    class FakeLib:
        def unanalyzed_ids(self):
            return []

        def analyze_session_stats(self):
            return 0, 0

    w.library = FakeLib()  # type: ignore[assignment]
    thread, worker = object(), object()
    w._analyze_thread = thread
    w._analyze_worker = worker
    MainWindow._analyze_done(w, 1, thread, worker)
    assert statuses
    assert "tags" in statuses[-1].lower()
    assert "waveform" in statuses[-1].lower() or "no waveform" in statuses[-1].lower()
    assert "updated from local audio" not in statuses[-1].lower()


def test_defer_analyze_updates_session_stats(tmp_path: Path) -> None:
    lib = Library(tmp_path / "defer-stats.sqlite")
    try:
        lib.upsert_track(
            {
                "path": "/t/a.mp3",
                "title": "a",
                "artist": "A",
                "album": "L",
                "genre": "Rock",
                "duration_ms": 1,
                "year": None,
                "bpm": None,
                "valence": 0.4,
                "energy": 0.4,
                "mood_confidence": 0.25,
                "confidence_note": "seed",
                "low_trust": 1,
                "added_at": 0,
                "mtime": 1,
                "analyzed": 0,
            }
        )
        lib.reset_analyze_session_stats()
        lib.defer_analyze(1)
        pcm_ok, deferred = lib.analyze_session_stats()
        assert pcm_ok == 0
        assert deferred == 1
        assert lib.unanalyzed_ids() == []
        t = lib.get(1)
        assert t is not None and not t.analyzed
    finally:
        lib.close()
