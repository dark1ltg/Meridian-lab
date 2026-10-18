"""Album/artist smooth clusters rough pins without promoting them past 0.45."""

from __future__ import annotations

from pathlib import Path

from meridian.features import CONFIDENCE_LOW
from meridian.library import Library


def test_album_smooth_clusters_but_keeps_sub_045(tmp_path: Path) -> None:
    lib = Library(tmp_path / "smooth-low-trust.sqlite")
    try:
        lib.add_folder(str(tmp_path))
        trusted_ids = []
        for i, (v, e) in enumerate([(0.70, 0.65), (0.72, 0.66), (0.68, 0.64)]):
            tid = lib.upsert_track(
                {
                    "path": str(tmp_path / f"trusted{i}.mp3"),
                    "title": f"T{i}",
                    "artist": "Band",
                    "album": "Record",
                    "albumartist": "Band",
                    "genre": "Rock",
                    "duration_ms": 180_000,
                    "year": None,
                    "bpm": None,
                    "valence": v,
                    "energy": e,
                    "mood_confidence": 0.80,
                    "confidence_note": "pcm",
                    "low_trust": 0,
                    "added_at": 0,
                    "mtime": 1,
                    "analyzed": 1,
                    "brightness": 0.5,
                    "acoustic_flux": 0.3,
                }
            )
            trusted_ids.append(tid)

        seed_id = lib.upsert_track(
            {
                "path": str(tmp_path / "seed.mp3"),
                "title": "Seed",
                "artist": "Band",
                "album": "Record",
                "albumartist": "Band",
                "genre": "Rock",
                "duration_ms": 180_000,
                "year": None,
                "bpm": None,
                "valence": 0.40,
                "energy": 0.40,
                "mood_confidence": 0.44,
                "confidence_note": "tag:rock · seed only",
                "low_trust": 1,
                "added_at": 0,
                "mtime": 1,
                "analyzed": 1,
            }
        )
        before = lib.get(seed_id)
        assert before is not None
        n = lib.smooth_album_moods()
        assert n >= 1
        after = lib.get(seed_id)
        assert after is not None
        # Pulled toward album mates.
        assert after.valence > before.valence
        assert after.energy > before.energy
        assert "album smooth" in (after.confidence_note or "")
        # Still unsure — dim / low-trust band.
        assert after.mood_confidence < CONFIDENCE_LOW
        assert after.low_trust is True
        assert after.mood_confidence <= CONFIDENCE_LOW - 0.01
    finally:
        lib.close()


def test_album_smooth_very_low_conf_can_rise_inside_band(tmp_path: Path) -> None:
    lib = Library(tmp_path / "smooth-bump-inside.sqlite")
    try:
        lib.add_folder(str(tmp_path))
        for i, (v, e) in enumerate([(0.70, 0.65), (0.72, 0.66), (0.68, 0.64)]):
            lib.upsert_track(
                {
                    "path": str(tmp_path / f"t{i}.mp3"),
                    "title": f"T{i}",
                    "artist": "Band",
                    "album": "LP",
                    "albumartist": "Band",
                    "genre": "Rock",
                    "duration_ms": 1000,
                    "year": None,
                    "bpm": None,
                    "valence": v,
                    "energy": e,
                    "mood_confidence": 0.80,
                    "confidence_note": "pcm",
                    "low_trust": 0,
                    "added_at": 0,
                    "mtime": 1,
                    "analyzed": 1,
                }
            )
        rough_id = lib.upsert_track(
            {
                "path": str(tmp_path / "rough.mp3"),
                "title": "Rough",
                "artist": "Band",
                "album": "LP",
                "albumartist": "Band",
                "genre": "Rock",
                "duration_ms": 1000,
                "year": None,
                "bpm": None,
                "valence": 0.20,
                "energy": 0.20,
                "mood_confidence": 0.20,
                "confidence_note": "PCM fallback · intro only",
                "low_trust": 1,
                "added_at": 0,
                "mtime": 1,
                "analyzed": 1,
            }
        )
        before = lib.get(rough_id)
        assert before is not None
        lib.smooth_album_moods()
        after = lib.get(rough_id)
        assert after is not None
        assert after.valence > before.valence
        assert after.mood_confidence > before.mood_confidence
        assert after.mood_confidence < CONFIDENCE_LOW
        assert after.low_trust is True
    finally:
        lib.close()
