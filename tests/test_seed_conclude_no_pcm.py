"""No waveform: conclude on tags; defer only with no audio and no tag evidence."""

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


def test_analyze_audio_seed_evidence_without_pcm() -> None:
    from meridian import features

    features.clear_decode_abort()
    with patch.object(
        features,
        "_decode_pcm_with_fallback",
        return_value=(None, False, None, None),
    ):
        tagged = features.analyze_audio(
            "/music/Rock/cut.mp3", "rock", "Cut", "Band", None, duration_ms=180_000
        )
        bare = features.analyze_audio(
            "/tmp/untitled.wav", "", "Track", "", None, duration_ms=180_000
        )
    assert tagged.pcm_ok is False
    assert tagged.seed_evidence is True
    assert tagged.low_trust is True
    assert "seed only" in (tagged.confidence_note or "")
    assert bare.pcm_ok is False
    assert bare.seed_evidence is False


def test_analyze_worker_concludes_seed_does_not_defer(tmp_path: Path, qapp) -> None:
    from meridian.library import Library
    from meridian.scanner import AnalyzeWorker
    from meridian.features import MoodResult

    lib = Library(tmp_path / "seed-conclude.sqlite")
    try:
        lib.add_folder(str(tmp_path))
        tid = lib.upsert_track(
            {
                "path": str(tmp_path / "a.mp3"),
                "title": "A",
                "artist": "Art",
                "album": "Al",
                "genre": "Rock",
                "duration_ms": 1000,
                "year": None,
                "bpm": None,
                "valence": 0.55,
                "energy": 0.55,
                "mood_confidence": 0.3,
                "confidence_note": "tag:rock",
                "low_trust": 1,
                "added_at": 0,
                "mtime": 1,
                "analyzed": 0,
            }
        )
        seed_result = MoodResult(
            valence=0.56,
            energy=0.54,
            bpm=None,
            confidence=0.44,
            low_trust=True,
            confidence_note="tag:rock · seed only",
            pcm_ok=False,
            seed_evidence=True,
        )
        with patch("meridian.scanner.read_tags", return_value={
            "genre": "Rock",
            "title": "A",
            "artist": "Art",
            "album": "Al",
            "albumartist": "",
            "composer": "",
            "year": None,
            "bpm": None,
            "duration_ms": 1000,
            "extra_text": "",
            "replaygain_db": None,
        }):
            with patch("meridian.scanner.analyze_audio", return_value=seed_result):
                worker = AnalyzeWorker(lib)
                worker.run()
        t = lib.get(tid)
        assert t is not None
        assert t.analyzed is True
        assert "seed only" in (t.confidence_note or "")
        pcm_ok, deferred = lib.analyze_session_stats()
        assert pcm_ok == 0
        assert deferred == 0
        assert lib.unanalyzed_ids() == []
    finally:
        lib.close()


def test_analyze_worker_keeps_prior_pcm_when_decode_misses(tmp_path: Path, qapp) -> None:
    """Sev 8/7: prior waveform placement must not be wiped by seed-conclude."""
    from meridian.library import Library
    from meridian.scanner import AnalyzeWorker
    from meridian.features import MoodResult

    lib = Library(tmp_path / "keep-prior.sqlite")
    try:
        lib.add_folder(str(tmp_path))
        tid = lib.upsert_track(
            {
                "path": str(tmp_path / "a.mp3"),
                "title": "A",
                "artist": "Art",
                "album": "Al",
                "genre": "Rock",
                "duration_ms": 180_000,
                "year": None,
                "bpm": 120.0,
                "valence": 0.4,
                "energy": 0.4,
                "mood_confidence": 0.3,
                "confidence_note": "tag:rock",
                "low_trust": 1,
                "added_at": 0,
                "mtime": 1,
                "analyzed": 0,
            }
        )
        lib.set_analyzed_mood(
            tid,
            0.62,
            0.71,
            120.0,
            confidence=0.82,
            low_trust=False,
            confidence_note="pcm multi-window",
            onset_consistency=0.85,
            acoustic_flux=0.42,
            brightness=0.61,
            from_pcm=True,
        )
        lib.conn.execute("UPDATE tracks SET analyzed = 0 WHERE id = ?", (tid,))
        lib.conn.commit()
        lib._analyze_denylist.discard(tid)

        seed = MoodResult(
            valence=0.55,
            energy=0.55,
            bpm=None,
            confidence=0.44,
            low_trust=True,
            confidence_note="tag:rock · seed only",
            pcm_ok=False,
            seed_evidence=True,
        )
        with patch(
            "meridian.scanner.read_tags",
            return_value={
                "genre": "Rock",
                "title": "A",
                "artist": "Art",
                "album": "Al",
                "albumartist": "",
                "composer": "",
                "year": None,
                "bpm": None,
                "duration_ms": 180_000,
                "extra_text": "",
                "replaygain_db": None,
            },
        ):
            with patch("meridian.scanner.analyze_audio", return_value=seed):
                AnalyzeWorker(lib).run()
        t = lib.get(tid)
        assert t is not None
        # Kept-prior must conclude analyzed=1 — deferring forever re-queues the
        # whole library on every launch (live 7163 backlog).
        assert t.analyzed is True
        assert tid not in lib.unanalyzed_ids()
        assert abs(t.valence - 0.62) < 1e-9
        assert abs(t.energy - 0.71) < 1e-9
        assert t.brightness == 0.61
        assert t.acoustic_flux == 0.42
        assert "kept prior" in (t.confidence_note or "")
        # Belt-and-suspenders: seed write must not NULL acoustics or relabel PCM.
        lib.set_analyzed_mood(
            tid,
            0.1,
            0.1,
            None,
            confidence=0.2,
            low_trust=True,
            confidence_note="tag:rock · seed only",
            from_pcm=False,
        )
        t2 = lib.get(tid)
        assert t2 is not None
        assert abs(t2.valence - 0.62) < 1e-9
        assert t2.brightness == 0.61
        assert "pcm multi-window" in (t2.confidence_note or "")
        assert "seed only" not in (t2.confidence_note or "")
    finally:
        lib.close()


def test_requeue_seed_only_without_pcm(tmp_path: Path) -> None:
    from meridian.library import Library

    lib = Library(tmp_path / "requeue-seed.sqlite")
    try:
        lib.add_folder(str(tmp_path))
        seed_id = lib.upsert_track(
            {
                "path": str(tmp_path / "seed.mp3"),
                "title": "S",
                "artist": "A",
                "album": "L",
                "genre": "Rock",
                "duration_ms": 1000,
                "year": None,
                "bpm": None,
                "valence": 0.5,
                "energy": 0.5,
                "mood_confidence": 0.44,
                "confidence_note": "tag:rock · seed only",
                "low_trust": 1,
                "added_at": 0,
                "mtime": 1,
                "analyzed": 1,
            }
        )
        pcm_id = lib.upsert_track(
            {
                "path": str(tmp_path / "pcm.mp3"),
                "title": "P",
                "artist": "A",
                "album": "L",
                "genre": "Rock",
                "duration_ms": 1000,
                "year": None,
                "bpm": None,
                "valence": 0.6,
                "energy": 0.6,
                "mood_confidence": 0.8,
                "confidence_note": "pcm · seed only",  # note alone must not requeue if acoustics exist
                "low_trust": 0,
                "added_at": 0,
                "mtime": 1,
                "analyzed": 1,
                "brightness": 0.5,
                "acoustic_flux": 0.3,
            }
        )
        n = lib.requeue_seed_only_without_pcm()
        assert n == 1
        assert lib.get(seed_id).analyzed is False
        assert lib.get(pcm_id).analyzed is True
    finally:
        lib.close()


def test_analyze_worker_defers_without_audio_or_tags(tmp_path: Path, qapp) -> None:
    from meridian.library import Library
    from meridian.scanner import AnalyzeWorker
    from meridian.features import MoodResult

    lib = Library(tmp_path / "defer-bare.sqlite")
    try:
        lib.add_folder(str(tmp_path))
        tid = lib.upsert_track(
            {
                "path": str(tmp_path / "bare.wav"),
                "title": "Bare",
                "artist": "",
                "album": "",
                "genre": "",
                "duration_ms": 1000,
                "year": None,
                "bpm": None,
                "valence": 0.5,
                "energy": 0.5,
                "mood_confidence": 0.0,
                "confidence_note": "no evidence",
                "low_trust": 1,
                "added_at": 0,
                "mtime": 1,
                "analyzed": 0,
            }
        )
        bare = MoodResult(
            valence=0.5,
            energy=0.5,
            bpm=None,
            confidence=0.2,
            low_trust=True,
            confidence_note="no evidence",
            pcm_ok=False,
            seed_evidence=False,
        )
        with patch("meridian.scanner.read_tags", return_value={
            "genre": "",
            "title": "Bare",
            "artist": "",
            "album": "",
            "albumartist": "",
            "composer": "",
            "year": None,
            "bpm": None,
            "duration_ms": 1000,
            "extra_text": "",
            "replaygain_db": None,
        }):
            with patch("meridian.scanner.analyze_audio", return_value=bare):
                AnalyzeWorker(lib).run()
        t = lib.get(tid)
        assert t is not None
        assert t.analyzed is False
        assert "pcm pending" in (t.confidence_note or "")
        _pcm, deferred = lib.analyze_session_stats()
        assert deferred == 1
    finally:
        lib.close()
