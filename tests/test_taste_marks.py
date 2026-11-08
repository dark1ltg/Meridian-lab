"""Taste ticks: only the stretches a listen actually used."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from meridian import features
from meridian.features import (
    format_taste_marks,
    legacy_taste_marks,
    parse_taste_marks,
    taste_marks_for_track,
)
from meridian.library import Library


def test_format_and_parse_round_trip() -> None:
    text = format_taste_marks([("intro", 0.0), ("mid", 81.2), ("late", 198.0)])
    assert parse_taste_marks(text) == [("intro", 0), ("mid", 81200), ("late", 198000)]
    assert parse_taste_marks("") == []
    assert parse_taste_marks(None) == []


def test_legacy_shape_uses_the_plan_and_edges_narrow_it() -> None:
    long = SimpleNamespace(
        duration_ms=210_000,
        brightness=0.4,
        acoustic_flux=0.4,
        confidence_note="tag",
        shape_note="steady",
        taste_marks=None,
    )
    marks = legacy_taste_marks(long)
    assert [role for role, _ms in marks] == ["intro", "mid", "late"]
    assert marks[0][1] == 0
    assert marks[2][1] > marks[1][1] > 0

    intro = SimpleNamespace(
        duration_ms=210_000,
        brightness=0.4,
        acoustic_flux=None,
        confidence_note="PCM fallback · intro only",
        shape_note=None,
        taste_marks=None,
    )
    assert [role for role, _ms in legacy_taste_marks(intro)] == ["intro"]

    seed = SimpleNamespace(
        duration_ms=210_000,
        brightness=None,
        acoustic_flux=None,
        confidence_note="seed only",
        shape_note=None,
        taste_marks=None,
    )
    assert legacy_taste_marks(seed) == []

    stored = SimpleNamespace(
        duration_ms=210_000,
        brightness=0.4,
        acoustic_flux=0.4,
        confidence_note="tag",
        shape_note="steady",
        taste_marks="mid:12000",
    )
    assert taste_marks_for_track(stored) == [("mid", 12000)]
    assert taste_marks_for_track(long) == marks


def test_three_successful_windows_are_the_marks(tmp_path) -> None:
    features.clear_decode_abort()

    def fake_decode(path: str, *, start_s: float = 0.0, duration_s: float = 12.0):
        return np.ones(11025 * 4, dtype=np.float32) * 0.2

    with patch.object(features, "_decode_pcm", side_effect=fake_decode):
        with patch.object(features, "_pcm_signal_ok", return_value=True):
            result = features.analyze_audio(
                "/long.wav",
                "",
                "song",
                "",
                None,
                duration_ms=210_000,
            )
    parsed = parse_taste_marks(result.taste_marks)
    assert [role for role, _ms in parsed] == ["intro", "mid", "late"]
    assert result.pcm_ok is True


def test_failed_middle_keeps_the_edges_that_sounded() -> None:
    features.clear_decode_abort()

    def fake_decode(path: str, *, start_s: float = 0.0, duration_s: float = 12.0):
        # Middle of a 3.5 minute song is the 45% seek, well clear of the ends.
        if 70.0 < float(start_s) < 120.0 and abs(duration_s - features.SECTION_WINDOW_S) < 0.5:
            return None
        return np.ones(11025 * 4, dtype=np.float32) * 0.2

    with patch.object(features, "_decode_pcm", side_effect=fake_decode):
        with patch.object(features, "_pcm_signal_ok", side_effect=lambda pcm: pcm is not None):
            result = features.analyze_audio(
                "/long.wav",
                "",
                "song",
                "",
                None,
                duration_ms=210_000,
            )
    parsed = parse_taste_marks(result.taste_marks)
    assert [role for role, _ms in parsed] == ["intro", "late"]
    assert "ends only" in (result.confidence_note or "")


def test_library_saves_marks_and_leaves_a_prior_listen_alone(tmp_path) -> None:
    lib = Library(tmp_path / "lib.sqlite")
    try:
        track_id = lib.upsert_track(
            {
                "path": str(tmp_path / "a.mp3"),
                "title": "A",
                "artist": "One",
                "album": "",
                "genre": "",
                "duration_ms": 210_000,
                "year": None,
                "bpm": None,
                "valence": 0.5,
                "energy": 0.5,
                "mood_confidence": 0.5,
                "confidence_note": "",
                "low_trust": 0,
                "added_at": 0,
                "mtime": 1.0,
                "analyzed": 0,
            }
        )
        lib.set_analyzed_mood(
            track_id,
            0.4,
            0.6,
            None,
            brightness=0.5,
            acoustic_flux=0.4,
            taste_marks="intro:0,mid:90000,late:180000",
        )
        saved = lib.get(track_id)
        assert saved is not None
        assert saved.taste_marks == "intro:0,mid:90000,late:180000"
        lib.set_analyzed_mood(
            track_id,
            0.5,
            0.5,
            None,
            confidence_note="seed only",
            taste_marks="",
            from_pcm=False,
        )
        kept = lib.get(track_id)
        assert kept is not None
        assert kept.taste_marks == "intro:0,mid:90000,late:180000"
    finally:
        lib.close()
