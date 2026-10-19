"""Unheard songs stay off the center, and a real listen can separate trusted stars."""

from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MERIDIAN_NO_GL", "1")

from meridian.context import Mode, make_context
from meridian.library import Library, Track
from meridian.queue_engine import classify
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

    from meridian.queue_engine import Quadrant
    from meridian.ui.mood_map import MoodMap

    QApplication.instance() or QApplication([])
    ctx = make_context(Mode.WANDER, 0.5, 0.48, 0.22, 0.0)
    unheard = _track(1)
    tagged = _track(2, confidence_note="tag:indie", valence=0.54, energy=0.5)
    heard = _track(3, analyzed=True, valence=0.5, energy=0.48, mood_confidence=0.8)
    ranked_list = classify([unheard, tagged, heard], ctx, set())
    ranked = {r.track.id: r for r in ranked_list}
    assert set(ranked) == {1, 2, 3}
    assert ranked[1].quadrant == Quadrant.SHELF
    sky = MoodMap()
    sky.set_tracks(ranked_list, None)
    assert 1 not in sky._positions
    assert 2 in sky._positions
    assert 3 in sky._positions


def test_listen_finish_moves_trusted_songs(tmp_path) -> None:
    lib = Library(tmp_path / "listen.sqlite")
    lib.conn.executemany(
        """
        INSERT INTO tracks (
            path, title, valence, energy, mood_confidence, confidence_note,
            analyzed, pinned, brightness, acoustic_flux
        ) VALUES (?, ?, 0.50, 0.50, 0.9, 'PCM', 1, ?, ?, ?)
        """,
        [
            ("/a.flac", "bright", 0, 0.86, 0.72),
            ("/b.flac", "dim", 0, 0.18, 0.22),
            ("/c.flac", "pin", 1, 0.9, 0.9),
        ],
    )
    lib.conn.commit()
    moved = lib.finish_listen_points()
    assert moved == 2
    rows = {
        r["path"]: r
        for r in lib.conn.execute("SELECT path, valence, energy, confidence_note FROM tracks")
    }
    assert rows["/a.flac"]["valence"] > 0.50
    assert rows["/a.flac"]["energy"] > 0.50
    assert rows["/b.flac"]["valence"] < 0.50
    assert rows["/b.flac"]["energy"] < 0.50
    assert abs(rows["/a.flac"]["valence"] - 0.50) <= 0.055 + 1e-9
    assert rows["/c.flac"]["valence"] == pytest.approx(0.50)
    assert "listen finish" in rows["/a.flac"]["confidence_note"]
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
    moved_clear = abs(clear.energy - seed.energy)
    assert moved_clear > SOFT_PCM_MAX_SHIFT + 1e-9
    assert moved_clear <= EVIDENCE_SOFT_ENERGY_MAX + 1e-9
    assert abs(muffled.energy - seed.energy) <= SOFT_PCM_MAX_SHIFT + 1e-9
    assert abs(fighting.energy - seed.energy) <= SOFT_PCM_MAX_SHIFT + 1e-9


def test_tonal_mark_is_hard_and_noisy_mark_is_soft() -> None:
    color = QColor(236, 238, 246, 230)
    hard_c, hard_r, hard_b = apply_timbre_mark(color, 3.0, 32, "tonal", current=False)
    soft_c, soft_r, soft_b = apply_timbre_mark(color, 3.0, 32, "noisy", current=False)
    assert hard_b == 0
    assert hard_c.alpha() >= 235
    assert hard_r == pytest.approx(3.0)
    assert soft_b >= 42
    assert soft_c.alpha() < color.alpha()
    assert soft_r < 3.0
