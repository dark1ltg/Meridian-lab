"""A tight album can dim a shaky outlier. It does not move the star."""

from __future__ import annotations

from pathlib import Path

from meridian.features import CONFIDENCE_LOW, UNSURE_STEER_MAX
from meridian.library import Library


def _track(lib: Library, root: Path, name: str, **overrides) -> int:
    payload = {
        "path": str(root / f"{name}.mp3"),
        "title": name,
        "artist": "Band",
        "album": "Record",
        "albumartist": "Band",
        "genre": "Rock",
        "duration_ms": 180_000,
        "year": None,
        "bpm": None,
        "valence": 0.70,
        "energy": 0.65,
        "mood_confidence": 0.80,
        "confidence_note": "pcm",
        "low_trust": 0,
        "steer_weight": 0.95,
        "added_at": 0,
        "mtime": 1,
        "analyzed": 1,
        "brightness": 0.5,
        "acoustic_flux": 0.3,
    }
    payload.update(overrides)
    return lib.upsert_track(payload)


def test_a_shaky_outlier_dims_and_stays_put(tmp_path: Path) -> None:
    lib = Library(tmp_path / "outlier.sqlite")
    try:
        lib.add_folder(str(tmp_path))
        for i, (v, e) in enumerate([(0.70, 0.65), (0.72, 0.66), (0.68, 0.64)]):
            _track(lib, tmp_path, f"trusted{i}", valence=v, energy=e)
        seed_id = _track(
            lib,
            tmp_path,
            "seed",
            valence=0.20,
            energy=0.20,
            mood_confidence=0.40,
            confidence_note="tag:rock · seed only",
            low_trust=1,
            steer_weight=0.70,
        )
        before = lib.get(seed_id)
        assert before is not None
        n = lib.smooth_album_moods()
        assert n == 1
        after = lib.get(seed_id)
        assert after is not None
        assert after.valence == before.valence
        assert after.energy == before.energy
        assert after.mood_confidence < before.mood_confidence
        assert after.mood_confidence < CONFIDENCE_LOW
        assert after.low_trust is True
        assert "album outlier" in (after.confidence_note or "")
        assert after.steer_weight is not None
        assert after.steer_weight <= UNSURE_STEER_MAX
        assert lib.smooth_album_moods() == 0
        again = lib.get(seed_id)
        assert again is not None
        assert again.mood_confidence == after.mood_confidence
    finally:
        lib.close()


def test_a_steady_song_far_from_the_album_stays_bright(tmp_path: Path) -> None:
    lib = Library(tmp_path / "ballad.sqlite")
    try:
        lib.add_folder(str(tmp_path))
        for i, (v, e) in enumerate([(0.70, 0.65), (0.72, 0.66), (0.68, 0.64)]):
            _track(lib, tmp_path, f"dance{i}", valence=v, energy=e)
        ballad_id = _track(
            lib,
            tmp_path,
            "ballad",
            valence=0.20,
            energy=0.20,
            mood_confidence=0.86,
            low_trust=0,
        )
        before = lib.get(ballad_id)
        assert before is not None
        assert lib.smooth_album_moods() == 0
        after = lib.get(ballad_id)
        assert after is not None
        assert after.valence == before.valence
        assert after.energy == before.energy
        assert after.mood_confidence == before.mood_confidence
        assert "album outlier" not in (after.confidence_note or "")
    finally:
        lib.close()


def test_a_scattered_album_dims_nobody(tmp_path: Path) -> None:
    lib = Library(tmp_path / "compilation.sqlite")
    try:
        lib.add_folder(str(tmp_path))
        for i, (v, e) in enumerate([(0.15, 0.20), (0.80, 0.85), (0.40, 0.55)]):
            _track(lib, tmp_path, f"mix{i}", valence=v, energy=e)
        shaky_id = _track(
            lib,
            tmp_path,
            "shaky",
            valence=0.95,
            energy=0.10,
            mood_confidence=0.30,
            low_trust=1,
            steer_weight=0.55,
        )
        before = lib.get(shaky_id)
        assert before is not None
        assert lib.smooth_album_moods() == 0
        after = lib.get(shaky_id)
        assert after is not None
        assert after.mood_confidence == before.mood_confidence
        assert after.steer_weight == before.steer_weight
    finally:
        lib.close()


def test_a_pin_is_not_dimmed(tmp_path: Path) -> None:
    lib = Library(tmp_path / "pin.sqlite")
    try:
        lib.add_folder(str(tmp_path))
        for i, (v, e) in enumerate([(0.70, 0.65), (0.72, 0.66), (0.68, 0.64)]):
            _track(lib, tmp_path, f"trusted{i}", valence=v, energy=e)
        pin_id = _track(
            lib,
            tmp_path,
            "pinned",
            valence=0.15,
            energy=0.15,
            mood_confidence=0.30,
            low_trust=1,
        )
        lib.set_mood(pin_id, 0.15, 0.15, pinned=True)
        assert lib.smooth_album_moods() == 0
        after = lib.get(pin_id)
        assert after is not None
        assert after.pinned
        assert after.valence == 0.15
        assert after.mood_confidence == 1.0
    finally:
        lib.close()


def test_artist_neighbors_and_stacks_stay_put(tmp_path: Path) -> None:
    lib = Library(tmp_path / "still.sqlite")
    try:
        lib.add_folder(str(tmp_path))
        ids = []
        for i in range(6):
            ids.append(
                _track(
                    lib,
                    tmp_path,
                    f"song{i}",
                    album=f"LP{i}",
                    valence=0.20 + i * 0.1,
                    energy=0.20,
                    mood_confidence=0.30,
                    low_trust=1,
                )
            )
        before = {tid: lib.get(tid) for tid in ids}
        assert lib.smooth_artist_moods() == 0
        assert lib.spread_album_acoustics() == 0
        for tid, row in before.items():
            after = lib.get(tid)
            assert row is not None and after is not None
            assert after.valence == row.valence
            assert after.energy == row.energy
            assert after.mood_confidence == row.mood_confidence
    finally:
        lib.close()
