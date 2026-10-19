"""Unheard songs stay off the center, and a real listen can separate trusted stars."""

from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MERIDIAN_NO_GL", "1")

from meridian.context import Mode, make_context
from meridian.library import Library, Track
from meridian.queue_engine import Quadrant, QueuePlan, RankedTrack, classify
from meridian.ui.mood_map import apply_timbre_mark
from PySide6.QtGui import QColor


def _track(tid: int, **overrides) -> Track:
    fields = dict(
        id=tid,
        path=f"/m/{tid}.mp3",
        title=f"t{tid}",
        artist="Band",
        album="LP",
        albumartist="Band",
        genre="",
        duration_ms=180_000,
        year=None,
        bpm=None,
        valence=0.5,
        energy=0.48,
        mood_confidence=0.2,
        confidence_note="no evidence",
        low_trust=True,
        pinned=False,
        loved=False,
        play_count=0,
        skip_count=0,
        last_played=None,
        added_at=0.0,
        mtime=0.0,
        analyzed=False,
    )
    fields.update(overrides)
    return Track(**fields)


def test_unheard_without_a_sticker_is_not_a_middle_star() -> None:
    from PySide6.QtWidgets import QApplication

    from meridian.ui.mood_map import MoodMap

    QApplication.instance() or QApplication([])
    ctx = make_context(Mode.WANDER, 0.5, 0.48, 0.22, 0.0)
    unheard = _track(1)
    tagged = _track(2, confidence_note="tag:indie", valence=0.54, energy=0.5)
    heard = _track(3, analyzed=True, valence=0.5, energy=0.48, mood_confidence=0.8)
    keywords = _track(4, confidence_note="keywords", valence=0.62, energy=0.4)
    soundtrack = _track(5, confidence_note="tag:soundtrack", valence=0.50, energy=0.40)
    ranked_list = classify([unheard, tagged, heard, keywords, soundtrack], ctx, set())
    ranked = {r.track.id: r for r in ranked_list}
    assert set(ranked) == {2, 3, 4}
    sky = MoodMap()
    sky.set_tracks(ranked_list, None)
    assert 1 not in sky._positions
    assert 5 not in sky._positions
    assert 2 in sky._positions
    assert 3 in sky._positions
    assert 4 in sky._positions
    playing = RankedTrack(
        track=unheard,
        fit=0.0,
        importance=0.0,
        urgency=0.0,
        quadrant=Quadrant.SHELF,
    )
    sky.set_tracks([playing], 1)
    assert 1 in sky._positions


def test_playing_unplaced_song_reaches_the_sky_only() -> None:
    from meridian.ui.main_window import MainWindow

    unheard = _track(1)
    tagged = _track(2, confidence_note="tag:indie")
    placed = _track(9, analyzed=True, confidence_note="pcm")
    ctx = make_context(Mode.WANDER, 0.5, 0.48, 0.22, 0.0)
    window = MainWindow.__new__(MainWindow)
    window.plan = QueuePlan(ranked=classify([tagged], ctx, set()))
    window.library = type("Lib", (), {"get": lambda self, tid: unheard if tid == 1 else None})()
    shown = MainWindow._sky_ranked(window, 1)
    assert {r.track.id for r in shown} == {1, 2}
    assert [r.track.id for r in shown].count(2) == 1
    again = MainWindow._sky_ranked(window, 2)
    assert [r.track.id for r in again].count(2) == 1
    assert 1 not in {r.track.id for r in again}
    window.library = type("Lib", (), {"get": lambda self, tid: placed if tid == 9 else None})()
    missing = MainWindow._sky_ranked(window, 9)
    assert 9 not in {r.track.id for r in missing}


def test_listen_finish_moves_trusted_songs(tmp_path) -> None:
    lib = Library(tmp_path / "listen.sqlite")
    lib.conn.executemany(
        """
        INSERT INTO tracks (
            path, title, valence, energy, mood_confidence, confidence_note,
            analyzed, pinned, brightness, acoustic_flux
        ) VALUES (?, ?, 0.50, 0.50, 0.9, ?, 1, ?, ?, ?)
        """,
        [
            ("/a.flac", "bright", "PCM", 0, 0.86, 0.72),
            ("/b.flac", "dim", "PCM", 0, 0.18, 0.22),
            ("/c.flac", "pin", "PCM", 1, 0.9, 0.9),
            ("/d.flac", "played", "pcm · listen finish", 0, 0.95, 0.90),
        ],
    )
    lib.conn.commit()
    moved = lib.finish_listen_points()
    assert moved == 3
    rows = {
        r["path"]: r
        for r in lib.conn.execute("SELECT path, valence, energy, confidence_note FROM tracks")
    }
    assert rows["/a.flac"]["valence"] > 0.50
    assert rows["/a.flac"]["energy"] > 0.50
    assert rows["/b.flac"]["valence"] < 0.50
    assert rows["/b.flac"]["energy"] < 0.50
    assert abs(rows["/a.flac"]["valence"] - 0.50) <= 0.055 + 1e-9
    assert abs(rows["/d.flac"]["valence"] - 0.50) <= 0.055 + 1e-9
    assert rows["/d.flac"]["valence"] > 0.50
    assert rows["/d.flac"]["energy"] > 0.50
    assert rows["/c.flac"]["valence"] == pytest.approx(0.50)
    assert "acoustic point" in rows["/a.flac"]["confidence_note"]
    assert "listen finish" in rows["/d.flac"]["confidence_note"]
    assert "acoustic point" in rows["/d.flac"]["confidence_note"]
    assert lib.finish_listen_points() == 0


def test_clear_agreeing_listen_keeps_the_evidence_widen() -> None:
    from unittest.mock import patch

    from meridian.acoustic import AcousticProfile
    from meridian.features import (
        EVIDENCE_SOFT_ENERGY_MAX,
        SOFT_PCM_MAX_SHIFT,
        analyze_audio,
        genre_seed,
    )

    path = "/music/Metal/Album/clear.flac"
    seed = genre_seed("metal", "x", "y", path=path)
    pcm = np.zeros(8, dtype=np.float32)

    def _run(slope: float, flux: float):
        profile = AcousticProfile(
            valence=seed.valence,
            energy=0.20,
            bpm=96.0,
            unstable=False,
            brightness=0.50,
            flux=flux,
            onset_consistency=0.88,
            variation=0.06,
            window_count=3,
            pcm_samples=8000,
            spectral_slope=slope,
        )
        with patch(
            "meridian.features._decode_pcm_with_fallback",
            return_value=(pcm, False, profile, None),
        ):
            return analyze_audio(path, "metal", "x", "y", 180.0)

    clear = _run(0.72, 0.20)
    muffled = _run(0.20, 0.20)
    fighting = _run(0.72, 0.90)
    muddled = _run(0.72, 0.55)
    moved_clear = abs(clear.energy - seed.energy)
    assert moved_clear > SOFT_PCM_MAX_SHIFT + 1e-9
    assert moved_clear <= EVIDENCE_SOFT_ENERGY_MAX + 1e-9
    assert abs(muffled.energy - seed.energy) <= SOFT_PCM_MAX_SHIFT + 1e-9
    assert abs(fighting.energy - seed.energy) <= SOFT_PCM_MAX_SHIFT + 1e-9
    assert abs(muddled.energy - seed.energy) <= SOFT_PCM_MAX_SHIFT + 1e-9


def test_tonal_mark_is_hard_and_noisy_mark_is_soft() -> None:
    color = QColor(236, 238, 246, 230)
    hard_c, hard_r, hard_b = apply_timbre_mark(color, 3.0, 32, "tonal", current=False)
    soft_c, soft_r, soft_b = apply_timbre_mark(color, 3.0, 32, "noisy", current=False)
    assert hard_b == 0
    assert hard_c.alpha() >= 235
    assert hard_r == pytest.approx(3.0)
    assert soft_b == 32
    assert soft_c.alpha() < color.alpha()
    assert soft_r == pytest.approx(3.0)
    bare_c, bare_r, bare_b = apply_timbre_mark(color, 3.0, 0, "noisy", current=False)
    assert bare_b == 0
    assert bare_r == pytest.approx(3.0)
    assert bare_c.alpha() < color.alpha()


def test_strong_clean_brightness_survives_the_genre_clamp() -> None:
    from unittest.mock import patch

    from meridian.acoustic import AcousticProfile
    from meridian.features import EVIDENCE_SOFT_VALENCE_MAX, SOFT_PCM_MAX_SHIFT, analyze_audio, genre_seed

    path = "/music/Metal/Album/glow.flac"
    seed = genre_seed("metal", "x", "y", path=path)
    pcm = np.zeros(8, dtype=np.float32)
    profile = AcousticProfile(
        valence=seed.valence,
        energy=seed.energy,
        bpm=96.0,
        unstable=False,
        brightness=0.90,
        flux=0.50,
        onset_consistency=0.88,
        variation=0.06,
        window_count=3,
        pcm_samples=8000,
        spectral_slope=0.72,
    )
    with patch(
        "meridian.features._decode_pcm_with_fallback",
        return_value=(pcm, False, profile, None),
    ):
        result = analyze_audio(path, "metal", "x", "y", 180.0)
    moved = result.valence - seed.valence
    assert moved > 0.04
    assert moved <= EVIDENCE_SOFT_VALENCE_MAX + 1e-9
    assert abs(result.energy - seed.energy) <= SOFT_PCM_MAX_SHIFT + 1e-9
