"""Every imported song lands on the mood map after analyze."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MERIDIAN_NO_GL", "1")

from PySide6.QtWidgets import QApplication

from meridian.context import LENS_RADIUS_DEFAULT, Mode, make_context
from meridian.features import NO_LISTEN_NOTE, track_is_unplaced
from meridian.queue_engine import classify


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _row(path: str, **overrides) -> dict:
    values = {
        "path": path,
        "title": "Song",
        "artist": "",
        "album": "",
        "genre": "",
        "duration_ms": 180_000,
        "year": None,
        "bpm": None,
        "valence": 0.2,
        "energy": 0.2,
        "mood_confidence": 0.2,
        "confidence_note": "no evidence",
        "low_trust": 1,
        "added_at": 0,
        "mtime": 1,
        "analyzed": 0,
    }
    values.update(overrides)
    return values


def _tags(**overrides) -> dict:
    tags = {
        "genre": "",
        "title": "Song",
        "artist": "",
        "album": "",
        "albumartist": "",
        "composer": "",
        "year": None,
        "bpm": None,
        "duration_ms": 180_000,
        "extra_text": "",
        "replaygain_db": None,
    }
    tags.update(overrides)
    return tags


def _on_sky(tracks, current_id=None):
    from meridian.ui.mood_map import MoodMap

    ctx = make_context(Mode.WANDER, 0.5, 0.5, LENS_RADIUS_DEFAULT, 0.0)
    ranked = classify(tracks, ctx, set())
    sky = MoodMap()
    sky.set_tracks(ranked, current_id)
    return sky, {item.track.id: item for item in ranked}


def test_listen_places_the_song(tmp_path: Path, qapp) -> None:
    from meridian.features import analyze_audio
    from meridian.library import Library
    from meridian.scanner import AnalyzeWorker
    from meridian.features import MoodResult

    tone = np.sin(np.linspace(0, 80, 22050, dtype=np.float32)).astype(np.float32) * 0.2
    with patch(
        "meridian.features._decode_pcm_with_fallback",
        return_value=(tone, False, None, None),
    ):
        heard = analyze_audio(
            "/music/plain/song.mp3", "", "Song", "Band", None, duration_ms=180_000
        )
    assert heard.pcm_ok is True
    assert heard.confidence > 0.0
    assert NO_LISTEN_NOTE not in (heard.confidence_note or "")

    lib = Library(tmp_path / "listen.sqlite")
    try:
        lib.add_folder(str(tmp_path))
        tid = lib.upsert_track(_row(str(tmp_path / "song.mp3"), artist="Band"))
        placed = MoodResult(
            valence=0.33,
            energy=0.77,
            bpm=None,
            confidence=0.82,
            low_trust=False,
            confidence_note="PCM only",
            pcm_ok=True,
            brightness=0.61,
            acoustic_flux=0.42,
            onset_consistency=0.8,
        )
        with patch("meridian.scanner.read_tags", return_value=_tags(artist="Band")):
            with patch("meridian.scanner.analyze_audio", return_value=placed):
                AnalyzeWorker(lib, run_tidy=False).run()
        track = lib.get(tid)
        assert track is not None
        assert track.analyzed is True
        assert track.valence == pytest.approx(0.33)
        assert track.energy == pytest.approx(0.77)
        assert not track_is_unplaced(track)
        sky, ranked = _on_sky([track])
        assert tid in sky._positions
        assert tid in ranked
    finally:
        lib.close()


def test_tags_place_the_song_when_pcm_fails(tmp_path: Path, qapp) -> None:
    from meridian.features import analyze_audio
    from meridian.library import Library
    from meridian.scanner import AnalyzeWorker
    from meridian.features import MoodResult

    with patch(
        "meridian.features._decode_pcm_with_fallback",
        return_value=(None, False, None, None),
    ):
        tagged = analyze_audio(
            "/music/elsewhere/cut.mp3", "rock", "Cut", "Band", None, duration_ms=180_000
        )
        words = analyze_audio(
            "/tmp/plain.mp3", "", "Slow Rain", "", None, duration_ms=180_000
        )
    assert tagged.pcm_ok is False
    assert tagged.seed_evidence is True
    assert "seed only" in (tagged.confidence_note or "")
    assert tagged.confidence > 0.0
    assert words.pcm_ok is False
    assert words.seed_evidence is True
    assert words.confidence_note != NO_LISTEN_NOTE

    lib = Library(tmp_path / "tags.sqlite")
    try:
        lib.add_folder(str(tmp_path))
        tid = lib.upsert_track(
            _row(str(tmp_path / "cut.mp3"), title="Cut", artist="Band", genre="Rock")
        )
        seed = MoodResult(
            valence=0.62,
            energy=0.71,
            bpm=None,
            confidence=0.44,
            low_trust=True,
            confidence_note="tag:rock · seed only",
            pcm_ok=False,
            seed_evidence=True,
        )
        with patch(
            "meridian.scanner.read_tags",
            return_value=_tags(genre="Rock", title="Cut", artist="Band"),
        ):
            with patch("meridian.scanner.analyze_audio", return_value=seed):
                AnalyzeWorker(lib).run()
        track = lib.get(tid)
        assert track is not None
        assert track.analyzed is True
        assert track.valence == pytest.approx(0.62)
        assert track.energy == pytest.approx(0.71)
        assert "seed only" in (track.confidence_note or "")
        assert not track_is_unplaced(track)
        sky, ranked = _on_sky([track])
        assert tid in sky._positions
        assert tid in ranked
    finally:
        lib.close()


def test_no_listen_and_no_sticker_is_a_dim_center_star(tmp_path: Path, qapp) -> None:
    from meridian.features import MAP_CENTER_ENERGY, MAP_CENTER_VALENCE, analyze_audio
    from meridian.library import Library
    from meridian.scanner import AnalyzeWorker
    from meridian.features import MoodResult

    with patch(
        "meridian.features._decode_pcm_with_fallback",
        return_value=(None, False, None, None),
    ):
        bare = analyze_audio(
            "/tmp/untitled.wav", "", "Track", "", None, duration_ms=180_000
        )
    assert bare.pcm_ok is False
    assert bare.seed_evidence is False
    assert bare.valence == pytest.approx(MAP_CENTER_VALENCE)
    assert bare.energy == pytest.approx(MAP_CENTER_ENERGY)
    assert bare.confidence == pytest.approx(0.0)
    assert bare.low_trust is True
    assert bare.confidence_note == NO_LISTEN_NOTE

    lib = Library(tmp_path / "center.sqlite")
    try:
        lib.add_folder(str(tmp_path))
        tid = lib.upsert_track(_row(str(tmp_path / "untitled.wav"), title="Track"))
        pin_id = lib.upsert_track(_row(str(tmp_path / "pinned.wav"), title="Pinned"))
        lib.set_mood(pin_id, 0.22, 0.81, pinned=True)
        heard_id = lib.upsert_track(_row(str(tmp_path / "heard.wav"), title="Heard"))
        lib.set_analyzed_mood(
            heard_id,
            0.64,
            0.28,
            None,
            confidence=0.8,
            low_trust=False,
            confidence_note="PCM only",
            brightness=0.55,
            acoustic_flux=0.33,
            from_pcm=True,
        )
        lib.conn.execute("UPDATE tracks SET analyzed = 0 WHERE id = ?", (heard_id,))
        lib.conn.commit()
        lib._analyze_denylist.discard(heard_id)

        bare_result = MoodResult(
            valence=0.11,
            energy=0.11,
            bpm=None,
            confidence=0.2,
            low_trust=True,
            confidence_note="no evidence",
            pcm_ok=False,
            seed_evidence=False,
        )
        with patch("meridian.scanner.read_tags", return_value=_tags(title="Track")):
            with patch("meridian.scanner.analyze_audio", return_value=bare_result):
                AnalyzeWorker(lib).run()

        track = lib.get(tid)
        assert track is not None
        assert track.analyzed is True
        assert track.valence == pytest.approx(MAP_CENTER_VALENCE)
        assert track.energy == pytest.approx(MAP_CENTER_ENERGY)
        assert track.mood_confidence == pytest.approx(0.0)
        assert track.low_trust is True
        assert track.confidence_note == NO_LISTEN_NOTE
        assert not track_is_unplaced(track)

        pin = lib.get(pin_id)
        assert pin is not None
        assert pin.pinned is True
        assert pin.valence == pytest.approx(0.22)
        assert pin.energy == pytest.approx(0.81)
        assert pin.mood_confidence == pytest.approx(1.0)
        assert pin.confidence_note == "pinned"

        heard = lib.get(heard_id)
        assert heard is not None
        assert heard.analyzed is True
        assert heard.valence == pytest.approx(0.64)
        assert heard.energy == pytest.approx(0.28)
        assert heard.brightness == pytest.approx(0.55)
        assert "kept prior" in (heard.confidence_note or "")
        assert NO_LISTEN_NOTE not in (heard.confidence_note or "")

        sky, ranked = _on_sky([track, pin, heard])
        assert tid in sky._positions
        assert tid in ranked
        assert sky._confidence_band(ranked[tid]) == 0
        assert pin_id in sky._positions
        assert heard_id in sky._positions

        for i in range(12):
            lib.upsert_track(
                _row(
                    f"/r/{i}.mp3",
                    title=f"r{i}",
                    genre="Metal",
                    valence=0.2 + i * 0.04,
                    energy=0.3 + (i % 3) * 0.1,
                    mood_confidence=0.8,
                    confidence_note="PCM",
                    low_trust=0,
                    analyzed=1,
                )
            )
        lib.rescale_moods_by_percentile(min_group=12)
        stayed = lib.get(tid)
        assert stayed is not None
        assert stayed.valence == pytest.approx(MAP_CENTER_VALENCE)
        assert stayed.energy == pytest.approx(MAP_CENTER_ENERGY)
        assert "relative rescale" not in (stayed.confidence_note or "")
    finally:
        lib.close()
