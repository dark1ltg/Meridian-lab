"""Regression tests for score 7–10 bugs (FD leak, scan/analyze races, loved FILL, decode timeout)."""

from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MERIDIAN_NO_GL", "1")

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _fd_count() -> int:
    return len(os.listdir(f"/proc/{os.getpid()}/fd"))


def test_decode_pcm_timeout_closes_pipes_and_restores_fds() -> None:
    """AB1/H3: timed-out ffmpeg PIPEs must not leak FDs."""
    from meridian import features

    features.clear_decode_abort()
    baseline = _fd_count()

    class FakeProc:
        def __init__(self) -> None:
            self.stdout = MagicMock()
            self.stderr = MagicMock()
            self.stdout.closed = False
            self.stderr.closed = False
            self.returncode = -9
            self._killed = False

        def communicate(self, timeout=None):
            if not self._killed:
                raise subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=timeout)
            self.stdout.closed = True
            self.stderr.closed = True
            return b"", b""

        def kill(self) -> None:
            self._killed = True

        def wait(self, timeout=None) -> int:
            return self.returncode

        def poll(self):
            return self.returncode if self._killed else None

    fake = FakeProc()
    with patch("meridian.features.shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch("meridian.features.subprocess.Popen", return_value=fake):
            for _ in range(8):
                assert features._decode_pcm("/missing.wav", duration_s=28.0) is None

    assert fake.stdout.close.called or fake.stdout.closed
    assert fake.stderr.close.called or fake.stderr.closed
    # Allow small fd churn from mocks; must not grow by ~2 per timeout.
    assert _fd_count() <= baseline + 4


def test_decode_pcm_wall_timeout_covers_duration_window() -> None:
    """M1: communicate timeout must be >= duration_s + IO slack."""
    from meridian import features

    features.clear_decode_abort()
    seen: dict[str, float] = {}

    class FakeProc:
        def __init__(self) -> None:
            self.stdout = MagicMock()
            self.stderr = MagicMock()
            self.stdout.closed = False
            self.stderr.closed = False
            self.returncode = 0

        def communicate(self, timeout=None):
            seen["timeout"] = float(timeout)
            self.stdout.closed = True
            self.stderr.closed = True
            # Enough float32 samples to pass the size gate.
            return (b"\x00\x00\x80\x3f" * 3000), b""

        def kill(self) -> None:
            pass

        def wait(self, timeout=None) -> int:
            return 0

    with patch("meridian.features.shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch("meridian.features.subprocess.Popen", return_value=FakeProc()):
            pcm = features._decode_pcm("/x.wav", duration_s=28.0)
    assert pcm is not None
    assert seen["timeout"] >= 38.0  # 28 + 10 slack


def test_decode_pcm_abort_closes_pipes() -> None:
    """AB1/H3: abort path must drain/close PIPEs after kill."""
    from meridian import features

    features.clear_decode_abort()

    class FakeProc:
        def __init__(self) -> None:
            self.stdout = MagicMock()
            self.stderr = MagicMock()
            self.stdout.closed = False
            self.stderr.closed = False
            self.returncode = -9
            self.communicate_calls = 0

        def communicate(self, timeout=None):
            self.communicate_calls += 1
            self.stdout.closed = True
            self.stderr.closed = True
            return b"", b""

        def kill(self) -> None:
            pass

        def wait(self, timeout=None) -> int:
            return self.returncode

    # Abort checked after Popen publish — simulate pre-communicate abort.
    fake = FakeProc()

    def popen(*_a, **_k):
        features.request_decode_abort()
        return fake

    with patch("meridian.features.shutil.which", return_value="/usr/bin/ffmpeg"):
        with patch("meridian.features.subprocess.Popen", side_effect=popen):
            assert features._decode_pcm("/x.wav") is None

    assert fake.communicate_calls >= 1
    features.clear_decode_abort()


def test_stale_scan_finished_does_not_reap_new_scan(qapp) -> None:
    """AB2/H1: stale finished with old gen must ignore current scan refs."""
    from meridian.ui.main_window import MainWindow

    win = MainWindow.__new__(MainWindow)
    win._closing = False
    win._lingering_workers = []
    win._scan_gen = 2
    win._scan_worker = object()
    win._scan_thread = object()
    win._clear_job_status = lambda: None
    win._set_status = lambda *_a: None
    win._set_job_status = lambda *_a: None
    win.refresh_plan = lambda **_k: None
    win.start_analyze = lambda: None
    reaped: list[tuple] = []

    def fake_reap(thread, worker, *, wait_ms=8000):
        reaped.append((thread, worker))
        return True

    win._reap_worker_thread = fake_reap  # type: ignore[method-assign]

    # Stale gen from previous scan.
    win._scan_done(3, gen=1)
    assert reaped == []
    assert win._scan_worker is not None
    assert win._scan_thread is not None

    # Matching gen reaps current refs.
    cur_w, cur_t = win._scan_worker, win._scan_thread
    win._scan_done(3, gen=2)
    assert reaped == [(cur_t, cur_w)]
    assert win._scan_worker is None
    assert win._scan_thread is None


def test_start_scan_refuses_while_prior_refs_linger(qapp) -> None:
    """AB2/H1: do not install a new scan while unreaped refs remain."""
    from meridian.ui.main_window import MainWindow

    win = MainWindow.__new__(MainWindow)
    win._closing = False
    win._lingering_workers = []
    win._scan_gen = 1
    win._scan_worker = object()
    win._scan_thread = MagicMock()
    win._scan_thread.isRunning.return_value = False
    win._set_job_status = lambda *_a: None
    started = {"n": 0}
    win._start_scan_worker = lambda **_k: started.__setitem__("n", started["n"] + 1)  # type: ignore

    win.start_scan()
    assert started["n"] == 0


def test_analyze_done_disposes_stale_generation(qapp) -> None:
    """AB3/H2: gen mismatch still deleteLaters the finishing thread/worker."""
    from meridian.ui.main_window import MainWindow

    win = MainWindow.__new__(MainWindow)
    win._closing = False
    win._lingering_workers = []
    win._analyze_gen = 5
    win._analyze_thread = object()  # newer analyze
    win._analyze_worker = object()
    win._analyze_pool = []
    win._analyze_remaining = 0
    win._analyze_run_tidy = False
    win._clear_job_status = lambda: None
    win._set_status = lambda *_a: None
    win._set_job_status = lambda *_a: None
    win.refresh_plan = lambda **_k: None
    win.start_analyze = lambda: None
    reaped: list[tuple] = []

    def fake_reap(thread, worker, *, wait_ms=8000):
        reaped.append((thread, worker))
        return True

    win._reap_worker_thread = fake_reap  # type: ignore[method-assign]

    old_thread, old_worker = object(), object()
    win._analyze_done(4, old_thread, old_worker)
    assert reaped == [(old_thread, old_worker)]
    # Current (newer) refs untouched.
    assert win._analyze_thread is not None
    assert win._analyze_worker is not None


def test_start_analyze_reaps_finished_before_overwrite(qapp) -> None:
    """AB3/H2: start_analyze disposes finished refs before assigning a new worker."""
    from meridian.ui.main_window import MainWindow

    class FakeLib:
        closed = False

        def unanalyzed_ids(self):
            return []

        def analyze_session_stats(self):
            return 0, 0

        def requeue_seed_only_without_pcm(self):
            return 0

        def folders(self):
            return []

    win = MainWindow.__new__(MainWindow)
    win._closing = False
    win._lingering_workers = []
    win._pending_analyze_after_linger = False
    win._analyze_gen = 1
    win._analyze_map_tick = 0
    win._last_analyze_map_refresh = 0.0
    win._analyze_pool = []
    win._seed_retry_wave = True
    win.library = FakeLib()  # type: ignore[assignment]
    win._clear_job_status = lambda: None
    win._set_status = lambda *_a: None
    win._set_job_status = lambda *_a: None

    prev_thread = MagicMock()
    prev_thread.isRunning.return_value = False
    prev_worker = object()
    win._analyze_thread = prev_thread
    win._analyze_worker = prev_worker
    reaped: list[tuple] = []

    def fake_reap(thread, worker, *, wait_ms=8000):
        reaped.append((thread, worker))
        return True

    win._reap_worker_thread = fake_reap  # type: ignore[method-assign]

    win.start_analyze()
    assert reaped == [(prev_thread, prev_worker)]
    assert win._analyze_thread is None
    assert win._analyze_worker is None


def test_loved_fill_promoted_under_day_band() -> None:
    """H4: loved at lens center must not stay FILL under DAY bias."""
    from unittest.mock import patch

    from meridian.context import Mode, make_context
    from meridian.library import Track
    from meridian.queue_engine import Quadrant, RenewalContext, build_plan, classify

    def t(i, *, valence=0.5, energy=0.5, loved=False):
        return Track(
            id=i,
            path=f"/m/{i}.mp3",
            title=f"t{i}",
            artist=f"A{i}",
            album=f"L{i}",
            albumartist=f"A{i}",
            genre="Metal",
            duration_ms=1000,
            year=None,
            bpm=120.0,
            valence=valence,
            energy=energy,
            mood_confidence=0.8,
            confidence_note="seed",
            low_trust=False,
            pinned=False,
            loved=loved,
            play_count=0,
            skip_count=0,
            last_played=None,
            added_at=0.0,
            mtime=0.0,
            analyzed=True,
        )

    with patch("meridian.context.current_hour", return_value=13):
        ctx = make_context(Mode.WANDER, 0.5, 0.5, 0.28, 0.0)
        loved = t(1, valence=0.50, energy=0.50, loved=True)
        others = [t(i, valence=0.51, energy=0.51) for i in range(2, 30)]
        ranked = classify([loved] + others, ctx, set())
        love_q = next(r.quadrant for r in ranked if r.track.id == 1)
        assert love_q in (Quadrant.NOW, Quadrant.DEEP), love_q
        plan = build_plan(
            [loved] + others,
            ctx,
            [],
            length=12,
            renewal=RenewalContext(prior_queue_ids=frozenset({1}), renew_streak=0),
        )
        assert 1 in plan.order


def test_queue_prefers_sure_pins_over_low_trust() -> None:
    """Path-to-9: subtle importance/fit lift for high-confidence pins vs dim scrapes."""
    from unittest.mock import patch

    from meridian.context import Mode, make_context
    from meridian.library import Track
    from meridian.queue_engine import classify

    def t(i, *, conf: float, low_trust: bool, valence: float = 0.50, energy: float = 0.50,
          onset: float | None = None):
        return Track(
            id=i,
            path=f"/m/{i}.mp3",
            title=f"t{i}",
            artist=f"A{i}",
            album=f"L{i}",
            albumartist=f"A{i}",
            genre="Metal",
            duration_ms=1000,
            year=None,
            bpm=120.0,
            valence=valence,
            energy=energy,
            mood_confidence=conf,
            confidence_note="test",
            low_trust=low_trust,
            onset_consistency=onset,
            pinned=False,
            loved=False,
            play_count=2,
            skip_count=0,
            last_played=None,
            added_at=0.0,
            mtime=0.0,
            analyzed=True,
        )

    sure = t(1, conf=0.88, low_trust=False)
    scrape = t(2, conf=0.30, low_trust=True)
    with patch("meridian.context.current_hour", return_value=13):
        ctx = make_context(Mode.FOCUS, 0.5, 0.5, 0.28, 0.0)
        ranked = classify([sure, scrape], ctx, set())
    by_id = {r.track.id: r for r in ranked}
    # Same distance to lens — sure pin should outrank the dim scrape.
    score = lambda r: r.fit * 0.7 + r.importance * 0.3
    assert score(by_id[1]) > score(by_id[2])
    assert by_id[1].importance > by_id[2].importance


def test_queue_closer_dim_competes_vs_farther_sure_focus() -> None:
    """Hunt #4: confidence nudge must not bury a closer dim neighbor under Focus onset stack."""
    from unittest.mock import patch

    from meridian.context import Mode, make_context
    from meridian.library import Track
    from meridian.queue_engine import classify

    def t(i, *, conf: float, low_trust: bool, valence: float, energy: float,
          onset: float | None):
        return Track(
            id=i,
            path=f"/m/{i}.mp3",
            title=f"t{i}",
            artist=f"A{i}",
            album=f"L{i}",
            albumartist=f"A{i}",
            genre="Metal",
            duration_ms=1000,
            year=None,
            bpm=120.0,
            valence=valence,
            energy=energy,
            mood_confidence=conf,
            confidence_note="test",
            low_trust=low_trust,
            onset_consistency=onset,
            pinned=False,
            loved=False,
            play_count=2,
            skip_count=0,
            last_played=None,
            added_at=0.0,
            mtime=0.0,
            analyzed=True,
        )

    # Lens at (0.5, 0.5): scrape ~0.02 away, sure ~0.10 away.
    sure = t(1, conf=0.90, low_trust=False, valence=0.57, energy=0.57, onset=0.90)
    scrape = t(2, conf=0.50, low_trust=True, valence=0.51, energy=0.51, onset=None)
    with patch("meridian.context.current_hour", return_value=13):
        ctx = make_context(Mode.FOCUS, 0.5, 0.5, 0.28, 0.0)
        ranked = classify([sure, scrape], ctx, set())
    by_id = {r.track.id: r for r in ranked}
    score = lambda r: r.fit * 0.7 + r.importance * 0.3
    # Closer dim scrape must still win (or tie) the combined ranking.
    assert score(by_id[2]) >= score(by_id[1]) - 1e-9


def test_queue_confidence_zero_treated_as_unsure() -> None:
    """Hunt #1: mood_confidence exactly 0.0 is low/unsure, not medium via falsy or."""
    from unittest.mock import patch

    from meridian.context import Mode, make_context
    from meridian.library import Track
    from meridian.queue_engine import classify

    def t(i, *, conf: float | None):
        return Track(
            id=i,
            path=f"/m/{i}.mp3",
            title=f"t{i}",
            artist=f"A{i}",
            album=f"L{i}",
            albumartist=f"A{i}",
            genre="Metal",
            duration_ms=1000,
            year=None,
            bpm=120.0,
            valence=0.50,
            energy=0.50,
            mood_confidence=conf if conf is not None else 0.5,
            confidence_note="test",
            low_trust=False,
            pinned=False,
            loved=False,
            play_count=2,
            skip_count=0,
            last_played=None,
            added_at=0.0,
            mtime=0.0,
            analyzed=True,
        )

    zero = t(1, conf=0.0)
    medium = t(2, conf=0.5)
    zero.mood_confidence = 0.0
    with patch("meridian.context.current_hour", return_value=13):
        ctx = make_context(Mode.FOCUS, 0.5, 0.5, 0.28, 0.0)
        ranked = classify([zero, medium], ctx, set())
    by_id = {r.track.id: r for r in ranked}
    # Exact 0 must not get a medium-confidence lift over a real 0.5.
    assert by_id[1].importance < by_id[2].importance
    assert by_id[1].fit <= by_id[2].fit + 1e-9
