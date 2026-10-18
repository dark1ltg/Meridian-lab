"""Silent SSD→2 / HDD→1 analyze workers and shared track queue."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

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


def test_preferred_analyze_workers_env_override() -> None:
    from meridian.host_deps import preferred_analyze_workers

    with patch.dict(os.environ, {"MERIDIAN_ANALYZE_WORKERS": "2"}):
        assert preferred_analyze_workers(["/any"]) == 2
    with patch.dict(os.environ, {"MERIDIAN_ANALYZE_WORKERS": "1"}):
        assert preferred_analyze_workers(["/any"]) == 1


def test_preferred_analyze_workers_ssd_vs_hdd() -> None:
    from meridian import host_deps

    with patch.dict(os.environ, {"MERIDIAN_ANALYZE_WORKERS": ""}, clear=False):
        os.environ.pop("MERIDIAN_ANALYZE_WORKERS", None)
        with patch.object(host_deps, "path_is_rotational", return_value=False):
            assert host_deps.preferred_analyze_workers(["/music"]) == 2
        with patch.object(host_deps, "path_is_rotational", return_value=True):
            assert host_deps.preferred_analyze_workers(["/music"]) == 1
        with patch.object(host_deps, "path_is_rotational", side_effect=[False, True]):
            assert host_deps.preferred_analyze_workers(["/ssd", "/hdd"]) == 1
        with patch.object(host_deps, "path_is_rotational", return_value=None):
            assert host_deps.preferred_analyze_workers(["/unknown"]) == 1


def test_analyze_track_queue_claims_unique(tmp_path: Path) -> None:
    from meridian.scanner import AnalyzeTrackQueue

    q = AnalyzeTrackQueue([1, 2, 3])
    seen = set()
    while True:
        tid = q.claim()
        if tid is None:
            break
        seen.add(tid)
        done, total = q.mark_done()
        assert total == 3
        assert done == len(seen)
    assert seen == {1, 2, 3}


def test_dual_workers_split_queue(tmp_path: Path, qapp) -> None:
    """Two workers on one queue analyze each id once and tidy only when asked."""
    from meridian.library import Library
    from meridian.scanner import AnalyzeTrackQueue, AnalyzeWorker
    from meridian.features import MoodResult

    lib = Library(tmp_path / "dual.sqlite")
    try:
        lib.add_folder(str(tmp_path))
        ids = []
        for i in range(4):
            tid = lib.upsert_track(
                {
                    "path": str(tmp_path / f"t{i}.mp3"),
                    "title": f"T{i}",
                    "artist": "A",
                    "album": "L",
                    "genre": "Rock",
                    "duration_ms": 1000,
                    "year": None,
                    "bpm": None,
                    "valence": 0.5,
                    "energy": 0.5,
                    "mood_confidence": 0.3,
                    "confidence_note": "tag:rock",
                    "low_trust": 1,
                    "added_at": 0,
                    "mtime": 1,
                    "analyzed": 0,
                }
            )
            ids.append(tid)
        result = MoodResult(
            valence=0.6,
            energy=0.6,
            bpm=None,
            confidence=0.8,
            low_trust=False,
            confidence_note="pcm",
            pcm_ok=True,
            brightness=0.5,
            acoustic_flux=0.3,
            onset_consistency=0.7,
        )
        queue = AnalyzeTrackQueue(ids)
        tidy_calls = {"n": 0}
        lib.smooth_album_moods = lambda: tidy_calls.__setitem__("n", tidy_calls["n"] + 1)  # type: ignore
        lib.smooth_artist_moods = lambda: None  # type: ignore
        lib.spread_album_acoustics = lambda: None  # type: ignore
        lib.rescale_moods_by_percentile = lambda: None  # type: ignore
        with patch("meridian.scanner.read_tags", return_value={
            "genre": "Rock",
            "title": "T",
            "artist": "A",
            "album": "L",
            "albumartist": "",
            "composer": "",
            "year": None,
            "bpm": None,
            "duration_ms": 1000,
            "extra_text": "",
            "replaygain_db": None,
        }):
            with patch("meridian.scanner.analyze_audio", return_value=result):
                w1 = AnalyzeWorker(
                    lib, queue=queue, run_tidy=False, reset_session=False, clear_abort=False
                )
                w2 = AnalyzeWorker(
                    lib, queue=queue, run_tidy=False, reset_session=False, clear_abort=False
                )
                w1.run()
                w2.run()
        assert tidy_calls["n"] == 0
        for tid in ids:
            t = lib.get(tid)
            assert t is not None and t.analyzed is True
        pcm_ok, deferred = lib.analyze_session_stats()
        assert pcm_ok == 4
        assert deferred == 0
    finally:
        lib.close()


def test_decode_abort_kills_all_registered_procs() -> None:
    from meridian import features

    features.clear_decode_abort()
    killed: list = []

    class FakeProc:
        def kill(self) -> None:
            killed.append(self)

    a, b = FakeProc(), FakeProc()
    assert features._register_decode_proc(a)
    assert features._register_decode_proc(b)
    features.request_decode_abort()
    assert set(killed) == {a, b}
    assert not features._decode_procs
    features.clear_decode_abort()


def test_path_is_rotational_on_this_home() -> None:
    from meridian.host_deps import path_is_rotational

    flag = path_is_rotational(Path.home())
    # This CachyOS box is NVMe — expect False when sysfs is readable.
    if flag is not None:
        assert flag is False
