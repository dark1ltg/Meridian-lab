"""Kept-prior listen miss must not eternally re-queue (live 7163 backlog)."""

from __future__ import annotations

from pathlib import Path


def _track(lib, path: Path, *, analyzed: int = 0, note: str = "pcm") -> int:
    return lib.upsert_track(
        {
            "path": str(path),
            "title": path.stem,
            "artist": "A",
            "album": "L",
            "genre": "Rock",
            "duration_ms": 1000,
            "year": None,
            "bpm": 120.0,
            "valence": 0.4,
            "energy": 0.4,
            "mood_confidence": 0.8,
            "confidence_note": note,
            "low_trust": 0,
            "added_at": 0,
            "mtime": 1,
            "analyzed": analyzed,
        }
    )


def test_conclude_kept_prior_marks_analyzed_keeps_mood(tmp_path: Path) -> None:
    from meridian.library import Library

    lib = Library(tmp_path / "conclude.sqlite")
    try:
        tid = _track(lib, tmp_path / "a.flac", analyzed=0, note="tag:game · PCM · multi-window")
        lib.set_analyzed_mood(
            tid,
            0.62,
            0.71,
            120.0,
            confidence=0.82,
            confidence_note="tag:game · PCM · multi-window",
            acoustic_flux=0.4,
            brightness=0.5,
            from_pcm=True,
        )
        lib.conn.execute("UPDATE tracks SET analyzed = 0 WHERE id = ?", (tid,))
        lib.conn.commit()
        lib._analyze_denylist.discard(tid)

        lib.conclude_kept_prior(tid)
        t = lib.get(tid)
        assert t is not None
        assert t.analyzed is True
        assert abs(t.valence - 0.62) < 1e-9
        assert abs(t.energy - 0.71) < 1e-9
        assert t.brightness == 0.5
        assert "kept prior" in (t.confidence_note or "")
        assert tid not in lib.unanalyzed_ids()
        # Not counted as "needs another listen" deferred.
        pcm_ok, deferred = lib.analyze_session_stats()
        assert deferred == 0
    finally:
        lib.close()


def test_heal_kept_prior_pending_clears_mass_backlog(tmp_path: Path) -> None:
    from meridian.library import Library

    lib = Library(tmp_path / "heal.sqlite")
    try:
        stuck = []
        for i in range(3):
            tid = _track(
                lib,
                tmp_path / f"s{i}.flac",
                analyzed=0,
                note=f"tag:game · PCM · multi-window · pcm pending · kept prior",
            )
            lib.conn.execute(
                "UPDATE tracks SET analyzed = 0, brightness = 0.5, acoustic_flux = 0.3, "
                "valence = 0.55, energy = 0.66, confidence_note = ? WHERE id = ?",
                (f"tag:game · PCM · pcm pending · kept prior", tid),
            )
            stuck.append(tid)
        # Plain pcm-pending without prior acoustics must stay pending.
        bare = _track(
            lib,
            tmp_path / "bare.flac",
            analyzed=0,
            note="pcm pending",
        )
        lib.conn.execute(
            "UPDATE tracks SET analyzed = 0, brightness = NULL, acoustic_flux = NULL, "
            "confidence_note = 'pcm pending' WHERE id = ?",
            (bare,),
        )
        lib.conn.commit()

        n = lib.heal_kept_prior_pending()
        assert n == 3
        for tid in stuck:
            t = lib.get(tid)
            assert t is not None and t.analyzed is True
            assert abs(t.valence - 0.55) < 1e-9
        assert bare in lib.unanalyzed_ids()
        assert lib.unanalyzed_ids() == [bare]
    finally:
        lib.close()


def test_rescan_can_retry_after_kept_prior_conclude(tmp_path: Path) -> None:
    from meridian.library import Library

    lib = Library(tmp_path / "rescan.sqlite")
    try:
        tid = _track(lib, tmp_path / "r.flac", analyzed=0)
        lib.set_analyzed_mood(
            tid,
            0.5,
            0.5,
            None,
            confidence=0.8,
            confidence_note="PCM",
            brightness=0.4,
            acoustic_flux=0.2,
            from_pcm=True,
        )
        lib.conclude_kept_prior(tid)
        assert lib.get(tid).analyzed is True

        pending = lib.mark_all_pending_analysis()
        assert pending >= 1
        assert tid in lib.unanalyzed_ids()
        # Heal must not immediately undo a mid-session Rescan when called again
        # only on fresh process — calling heal here mimics a mistaken second heal.
        # After Rescan the note still says kept prior; heal WOULD clear it.
        # Product relies on once-per-process flag in start_analyze, not on heal
        # being unsafe to call — document that Rescan retry needs no heal after.
        assert "kept prior" in (lib.get(tid).confidence_note or "")
    finally:
        lib.close()
