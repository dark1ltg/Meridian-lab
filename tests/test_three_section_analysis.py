"""Three-section (intro / mid / late) analyze path — merge, seeks, short, partial, abort."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np

from meridian.acoustic import AcousticProfile, confidence_from_evidence


def _prof(
    *,
    valence: float,
    energy: float,
    brightness: float = 0.5,
    flux: float = 0.4,
    onset_rate: float = 0.5,
    onset_consistency: float = 0.7,
    variation: float = 0.1,
    unstable: bool = False,
    energy_mean: float = 0.55,
    bpm: float | None = 120.0,
) -> AcousticProfile:
    return AcousticProfile(
        valence=valence,
        energy=energy,
        bpm=bpm,
        unstable=unstable,
        brightness=brightness,
        flux=flux,
        onset_rate=onset_rate,
        onset_consistency=onset_consistency,
        onset_burstiness=0.2,
        variation=variation,
        energy_mean=energy_mean,
        energy_std=0.1,
        energy_peak=0.6,
        energy_range=0.2,
        energy_trend=0.0,
        window_count=3,
        pcm_samples=1000,
    )


def test_analysis_window_plan_short_unknown_long() -> None:
    from meridian.features import SECTION_MIN_GAP_S, _analysis_window_plan

    # Unknown / very short → single listen from 0.
    assert _analysis_window_plan(0) == [("mid", 0.0)]
    assert _analysis_window_plan(10_000) == [("mid", 0.0)]

    # Short but long enough for two separated windows.
    short_two = _analysis_window_plan(35_000)
    assert len(short_two) == 2
    assert short_two[0][0] == "intro"
    assert short_two[1][0] == "late"
    assert short_two[1][1] - short_two[0][1] >= SECTION_MIN_GAP_S

    # Medium → intro + mid (not three).
    medium = _analysis_window_plan(50_000)
    assert [r for r, _ in medium] == ["intro", "mid"]

    # Long → intro / mid / late with safe EOF clamps and separation.
    long = _analysis_window_plan(210_000)
    assert [r for r, _ in long] == ["intro", "mid", "late"]
    starts = [s for _r, s in long]
    assert starts[0] == 0.0
    assert starts[1] > starts[0] + SECTION_MIN_GAP_S - 1e-6
    assert starts[2] > starts[1] + SECTION_MIN_GAP_S - 1e-6
    # Late seek must leave room for a 12s window inside 210s.
    assert starts[2] + 12.0 <= 210.0 + 1e-6


def test_analysis_window_plan_never_seeks_past_eof() -> None:
    from meridian.features import SECTION_WINDOW_S, _analysis_window_plan, _clamp_seek_s

    for ms in (0, 5_000, 25_000, 45_000, 90_000, 180_000):
        dur_s = ms / 1000.0
        for _role, start in _analysis_window_plan(ms):
            if dur_s > 0:
                assert start >= 0.0
                assert start <= _clamp_seek_s(start, dur_s, SECTION_WINDOW_S) + 1e-9
                assert start < dur_s


def test_merge_dual_compat_and_mid_heavy_three() -> None:
    from meridian.features import SECTION_BLEND_WEIGHTS, _merge_pcm_profiles

    intro = _prof(valence=0.44, energy=0.46, onset_consistency=0.8)
    mid = _prof(valence=0.50, energy=0.50, onset_consistency=0.85)
    # Spread mid↔late just outside body-close (0.12) so default 20/50/30 applies.
    late = _prof(valence=0.60, energy=0.58, onset_consistency=0.75)

    # Dual legacy path still works.
    dual = _merge_pcm_profiles(intro, mid)
    assert 0.03 <= dual.valence <= 0.97

    merged = _merge_pcm_profiles(
        None, role_profiles={"intro": intro, "mid": mid, "late": late}
    )
    # Mid-heavy (~20/50/30): deterministic weighted blend.
    expected = (
        SECTION_BLEND_WEIGHTS["intro"] * intro.valence
        + SECTION_BLEND_WEIGHTS["mid"] * mid.valence
        + SECTION_BLEND_WEIGHTS["late"] * late.valence
    )
    assert abs(merged.valence - expected) < 0.02
    assert abs(merged.valence - mid.valence) < abs(merged.valence - intro.valence)


def test_mid_late_close_agreement_trusts_body() -> None:
    """Path-to-9: mid+late close + meaningfully offset intro → soft body bias."""
    from meridian.features import (
        SECTION_BLEND_WEIGHTS,
        SECTION_BODY_AGREE_WEIGHTS,
        _merge_pcm_profiles,
    )

    # Intro offset enough to matter, but not outlier/cold-open; mid+late sit close.
    intro = _prof(valence=0.42, energy=0.40, onset_consistency=0.75)
    mid = _prof(valence=0.55, energy=0.55, onset_consistency=0.88)
    late = _prof(valence=0.58, energy=0.57, onset_consistency=0.84)

    merged = _merge_pcm_profiles(
        None, role_profiles={"intro": intro, "mid": mid, "late": late}
    )
    expected = (
        SECTION_BODY_AGREE_WEIGHTS["intro"] * intro.valence
        + SECTION_BODY_AGREE_WEIGHTS["mid"] * mid.valence
        + SECTION_BODY_AGREE_WEIGHTS["late"] * late.valence
    )
    default = (
        SECTION_BLEND_WEIGHTS["intro"] * intro.valence
        + SECTION_BLEND_WEIGHTS["mid"] * mid.valence
        + SECTION_BLEND_WEIGHTS["late"] * late.valence
    )
    assert abs(merged.valence - expected) < 0.02
    # Soft body bias still sits closer to mid+late than default 20/50/30.
    body_mid = 0.5 * (mid.valence + late.valence)
    assert abs(merged.valence - body_mid) < abs(default - body_mid) + 1e-9
    assert abs(merged.valence - body_mid) < abs(merged.valence - intro.valence)
    # Intro keeps more say than the old half-weight (0.10) cut.
    assert SECTION_BODY_AGREE_WEIGHTS["intro"] >= 0.15


def test_normal_mild_intro_keeps_default_blend_when_body_close() -> None:
    """Hunt #3: normal build-up intros keep 20/50/30 when not pathological."""
    from meridian.features import SECTION_BLEND_WEIGHTS, _merge_pcm_profiles

    # Mild intro offset (below BODY_AGREE_INTRO_MIN); mid+late still close.
    intro = _prof(valence=0.50, energy=0.48, onset_consistency=0.80)
    mid = _prof(valence=0.55, energy=0.55, onset_consistency=0.88)
    late = _prof(valence=0.56, energy=0.54, onset_consistency=0.84)

    merged = _merge_pcm_profiles(
        None, role_profiles={"intro": intro, "mid": mid, "late": late}
    )
    expected = (
        SECTION_BLEND_WEIGHTS["intro"] * intro.valence
        + SECTION_BLEND_WEIGHTS["mid"] * mid.valence
        + SECTION_BLEND_WEIGHTS["late"] * late.valence
    )
    assert abs(merged.valence - expected) < 0.02


def test_cold_open_intro_does_not_veto_mid_late() -> None:
    from meridian.features import _merge_pcm_profiles

    cold = _prof(
        valence=0.15,
        energy=0.12,
        flux=0.10,
        onset_rate=0.05,
        onset_consistency=0.2,
        energy_mean=0.20,
        unstable=True,
        variation=0.4,
    )
    mid = _prof(
        valence=0.62,
        energy=0.70,
        flux=0.55,
        onset_rate=0.9,
        onset_consistency=0.85,
        variation=0.08,
        unstable=False,
    )
    late = _prof(
        valence=0.60,
        energy=0.68,
        flux=0.52,
        onset_rate=0.85,
        onset_consistency=0.8,
        variation=0.10,
        unstable=False,
    )

    merged = _merge_pcm_profiles(
        None, role_profiles={"intro": cold, "mid": mid, "late": late}
    )
    # Body agreement + cold intro → result near mid/late, not dragged to silence.
    assert merged.energy > 0.55
    assert merged.valence > 0.50
    assert abs(merged.energy - mid.energy) < abs(merged.energy - cold.energy)
    # Score 7: zero-weight cold intro must not poison merge health stats.
    assert merged.unstable is False
    assert merged.variation <= max(mid.variation, late.variation) + 1e-9


def test_enough_section_evidence_partial_rules() -> None:
    from meridian.features import _enough_section_evidence

    plan3 = [("intro", 0.0), ("mid", 40.0), ("late", 80.0)]
    assert _enough_section_evidence(plan3, {"mid"})
    assert _enough_section_evidence(plan3, {"mid", "late"})
    assert not _enough_section_evidence(plan3, {"intro", "late"})  # rehunt 4: ends-only
    assert not _enough_section_evidence(plan3, {"late"})  # score 4: late-only defers
    assert not _enough_section_evidence(plan3, {"intro"})
    assert not _enough_section_evidence(plan3, set())

    plan2 = [("intro", 0.0), ("mid", 20.0)]
    assert not _enough_section_evidence(plan2, {"intro"})  # score 6: dual intro-only
    assert _enough_section_evidence(plan2, {"mid"})
    assert _enough_section_evidence(plan2, {"intro", "mid"})

    plan_short = [("intro", 0.0), ("late", 20.0)]
    assert not _enough_section_evidence(plan_short, {"late"})  # rehunt 3: dual late-only
    assert not _enough_section_evidence(plan_short, {"intro"})
    assert _enough_section_evidence(plan_short, {"intro", "late"})


def test_decode_partial_intro_only_salvages_on_long_plan() -> None:
    """Long-plan intro-only tries ~28s salvage but stays intro-only low-trust."""
    from meridian import features

    features.clear_decode_abort()
    calls: list[tuple[float, float]] = []

    def fake_decode(path: str, *, start_s: float = 0.0, duration_s: float = 12.0):
        calls.append((float(start_s), float(duration_s)))
        # Section intro @ 0 works; longer salvage at 0 also works (dual-style).
        if abs(start_s) < 0.01:
            return np.ones(11025 * int(max(4.0, duration_s)), dtype=np.float32) * 0.05
        return None

    with patch.object(features, "_decode_pcm", side_effect=fake_decode):
        with patch.object(features, "_pcm_signal_ok", side_effect=lambda pcm: pcm is not None):
            pcm, fallback, merged, edge_salvage = features._decode_pcm_with_fallback(
                "/long.wav", duration_ms=210_000
            )
            result = features.analyze_audio(
                "/long.wav",
                "game",
                "pad track",
                "",
                None,
                duration_ms=210_000,
            )
    assert pcm is not None
    assert merged is None
    assert fallback is True
    assert edge_salvage == "intro"
    # Same ~28s salvage path as dual-plan intro-only.
    assert any(
        abs(dur - features.LONG_SINGLE_WINDOW_S) < 1e-6 and abs(ss) < 0.01
        for ss, dur in calls
    )
    assert len(calls) >= 3  # attempted intro/mid/late (+ salvage)
    assert result.pcm_ok is True
    assert result.low_trust is True
    assert result.confidence <= features.CONFIDENCE_LOW - 0.08
    assert "intro only" in (result.confidence_note or "")
    assert "PCM fallback" in (result.confidence_note or "")


def test_decode_long_intro_only_falls_back_to_stub_when_salvage_fails() -> None:
    """If ~28s salvage finds nothing, keep the 12s intro stub as weak place."""
    from meridian import features

    features.clear_decode_abort()

    def fake_decode(path: str, *, start_s: float = 0.0, duration_s: float = 12.0):
        # Only the short section intro works; longer salvage at 0 fails.
        if abs(start_s) < 0.01 and abs(duration_s - features.SECTION_WINDOW_S) < 0.5:
            return np.ones(11025 * 4, dtype=np.float32) * 0.05
        return None

    with patch.object(features, "_decode_pcm", side_effect=fake_decode):
        with patch.object(features, "_pcm_signal_ok", side_effect=lambda pcm: pcm is not None):
            pcm, fallback, merged, edge_salvage = features._decode_pcm_with_fallback(
                "/long.wav", duration_ms=210_000
            )
            result = features.analyze_audio(
                "/long.wav",
                "game",
                "pad track",
                "",
                None,
                duration_ms=210_000,
            )
    assert pcm is not None
    assert fallback is True
    assert edge_salvage == "intro"
    assert result.low_trust is True
    assert result.confidence <= features.CONFIDENCE_LOW - 0.08
    assert "intro only" in (result.confidence_note or "")


def test_decode_short_track_does_not_triple_sample() -> None:
    from meridian import features

    features.clear_decode_abort()
    starts: list[float] = []

    def fake_decode(path: str, *, start_s: float = 0.0, duration_s: float = 12.0):
        starts.append(float(start_s))
        return np.ones(11025 * 4, dtype=np.float32) * 0.05

    with patch.object(features, "_decode_pcm", side_effect=fake_decode):
        with patch.object(features, "_pcm_signal_ok", return_value=True):
            with patch("meridian.acoustic.build_profile", return_value=_prof(valence=0.5, energy=0.5)):
                pcm, _fb, merged, edge_salvage = features._decode_pcm_with_fallback(
                    "/short.wav", duration_ms=15_000
                )
    assert pcm is not None
    # Single-window plan for ~15s — at most one section decode (plus possible silence FB).
    section_starts = starts[:1]
    assert len(section_starts) == 1
    assert len(set(round(s, 2) for s in starts)) <= 2


def test_decode_long_track_three_windows_mid_heavy_merge() -> None:
    from meridian import features

    features.clear_decode_abort()
    starts: list[float] = []
    durations: list[float] = []

    def fake_decode(path: str, *, start_s: float = 0.0, duration_s: float = 12.0):
        starts.append(float(start_s))
        durations.append(float(duration_s))
        return np.ones(11025 * 4, dtype=np.float32) * 0.05

    built = [
        _prof(valence=0.2, energy=0.2, onset_consistency=0.8),
        _prof(valence=0.5, energy=0.5, onset_consistency=0.85),
        _prof(valence=0.8, energy=0.8, onset_consistency=0.75),
    ]

    with patch.object(features, "_decode_pcm", side_effect=fake_decode):
        with patch.object(features, "_pcm_signal_ok", return_value=True):
            with patch("meridian.acoustic.build_profile", side_effect=built):
                pcm, partial, merged, edge_salvage = features._decode_pcm_with_fallback(
                    "/long.wav", duration_ms=210_000
                )
    assert pcm is not None
    assert merged is not None
    assert partial is False
    # Short head probe at 0, then three 12s section windows.
    assert len(starts) == 4
    assert abs(durations[0] - features.PROBE_HEAD_S) < 1e-6
    assert all(abs(d - 12.0) < 1e-6 for d in durations[1:])
    assert abs(merged.valence - 0.5) < abs(merged.valence - 0.2)
    assert abs(merged.valence - 0.5) < abs(merged.valence - 0.8)


def test_abort_kills_active_proc_across_seeks() -> None:
    """Severity 9: abort clears the active slot and kills; later seeks do not stick."""
    from meridian import features

    features.clear_decode_abort()
    killed: list[object] = []

    class FakeProc:
        def __init__(self) -> None:
            self.stdout = MagicMock()
            self.stderr = MagicMock()
            self.stdout.closed = False
            self.stderr.closed = False
            self.returncode = 0
            self._blocked = True

        def communicate(self, timeout=None):
            # First call blocks until abort kills / flag flips.
            if features.decode_abort_requested():
                self.returncode = -9
                self.stdout.closed = True
                self.stderr.closed = True
                return b"", b""
            # Simulate hanging decode.
            raise TimeoutError("should have been aborted")

        def kill(self) -> None:
            killed.append(self)

        def wait(self, timeout=None) -> int:
            return -9

    proc = FakeProc()
    assert features._register_decode_proc(proc) is True
    features.request_decode_abort()
    assert proc not in features._decode_procs
    assert not features._decode_procs
    assert killed == [proc]
    # A subsequent decode attempt must refuse while abort is set.
    with patch("meridian.features.shutil.which", return_value="/usr/bin/ffmpeg"):
        out = features._decode_pcm("/x.wav", start_s=40.0, duration_s=12.0)
    assert out is None
    features.clear_decode_abort()


def test_multi_window_confidence_note_stable() -> None:
    score, note = confidence_from_evidence(
        tag_key="rock",
        pcm_ok=True,
        multi_window=True,
        variation=0.05,
        onset_consistency=0.8,
    )
    assert "multi-window" in note
    score2, note2 = confidence_from_evidence(
        tag_key="rock",
        pcm_ok=True,
        multi_window=True,
        variation=0.05,
        onset_consistency=0.8,
    )
    assert score == score2
    assert note == note2
    # Appwide score 2: flag alone must not nudge confidence thresholds.
    score_plain, note_plain = confidence_from_evidence(
        tag_key="rock",
        pcm_ok=True,
        multi_window=False,
        variation=0.05,
        onset_consistency=0.8,
    )
    assert score == score_plain
    assert "multi-window" not in note_plain


def test_cold_open_does_not_poison_confidence() -> None:
    """Score 7/2: zero-weight cold intro keeps body confidence (no weak/unstable tax)."""
    from meridian.features import _merge_pcm_profiles

    cold = _prof(
        valence=0.12,
        energy=0.10,
        flux=0.08,
        onset_rate=0.04,
        onset_consistency=0.15,
        energy_mean=0.18,
        unstable=True,
        variation=0.45,
    )
    mid = _prof(
        valence=0.55,
        energy=0.60,
        flux=0.50,
        onset_rate=0.8,
        onset_consistency=0.85,
        variation=0.08,
        unstable=False,
    )
    late = _prof(
        valence=0.58,
        energy=0.62,
        flux=0.48,
        onset_rate=0.75,
        onset_consistency=0.82,
        variation=0.09,
        unstable=False,
    )
    body = _merge_pcm_profiles(None, role_profiles={"mid": mid, "late": late})
    with_cold = _merge_pcm_profiles(
        None, role_profiles={"intro": cold, "mid": mid, "late": late}
    )
    assert with_cold.unstable is False
    assert with_cold.variation <= max(mid.variation, late.variation) + 1e-9

    conf_body, _ = confidence_from_evidence(
        tag_key="rock",
        pcm_ok=True,
        pcm_fallback=False,
        pcm_unstable=body.unstable,
        variation=body.variation,
        onset_consistency=body.onset_consistency,
        multi_window=True,
    )
    conf_cold, note = confidence_from_evidence(
        tag_key="rock",
        pcm_ok=True,
        pcm_fallback=False,
        pcm_unstable=with_cold.unstable,
        variation=with_cold.variation,
        onset_consistency=with_cold.onset_consistency,
        multi_window=True,
    )
    assert conf_cold == conf_body
    assert "PCM weak" not in note
    assert "unstable spectrum" not in note


def test_dual_plan_intro_only_salvages_long_single() -> None:
    """Appwide score 6: dual intro-only salvages ~28s from 0 (no hard-defer / no short park)."""
    from meridian import features

    features.clear_decode_abort()
    calls: list[tuple[float, float]] = []

    def fake_decode(path: str, *, start_s: float = 0.0, duration_s: float = 12.0):
        calls.append((float(start_s), float(duration_s)))
        if abs(start_s) < 0.01:
            return np.ones(11025 * int(max(4.0, duration_s)), dtype=np.float32) * 0.05
        return None

    with patch.object(features, "_decode_pcm", side_effect=fake_decode):
        with patch.object(features, "_pcm_signal_ok", side_effect=lambda pcm: pcm is not None):
            pcm, fallback, merged, edge_salvage = features._decode_pcm_with_fallback(
                "/medium.wav", duration_ms=45_000
            )
    assert pcm is not None
    assert merged is None  # salvage is single-window, not multi-window merge
    assert fallback is True
    assert edge_salvage == "intro"  # keep low-trust even after ~28s salvage
    # Section attempts (12s) then a real longer salvage (~28s) from primary/0.
    assert any(abs(dur - features.LONG_SINGLE_WINDOW_S) < 1e-6 and abs(ss) < 0.01 for ss, dur in calls)
    assert any(abs(dur - features.SECTION_WINDOW_S) < 1e-6 for _ss, dur in calls)


def test_silent_intro_mid_late_not_weak_fallback() -> None:
    """Score 5/2: silent intro + solid mid/late is full PCM, not pcm_fallback tax."""
    from meridian import features

    features.clear_decode_abort()
    built = [
        _prof(valence=0.55, energy=0.60, onset_consistency=0.85, variation=0.08),
        _prof(valence=0.58, energy=0.62, onset_consistency=0.82, variation=0.09),
    ]
    plan = features._analysis_window_plan(210_000)
    mid_start = plan[1][1]
    late_start = plan[2][1]

    def fake_decode(path: str, *, start_s: float = 0.0, duration_s: float = 12.0):
        # Head / intro region is silence (probe + intro@0); mid/late return audio.
        # Keep early probes quiet so audible-span does not rebase the whole plan.
        if abs(start_s - mid_start) < 0.5 or abs(start_s - late_start) < 0.5:
            return np.ones(11025 * 4, dtype=np.float32) * 0.05
        return np.zeros(11025 * 4, dtype=np.float32)

    def fake_signal_ok(pcm: np.ndarray) -> bool:
        return float(np.sqrt(np.mean(np.square(pcm)))) > 1e-4

    with patch.object(features, "_decode_pcm", side_effect=fake_decode):
        with patch.object(features, "_pcm_signal_ok", side_effect=fake_signal_ok):
            with patch("meridian.acoustic.build_profile", side_effect=built):
                pcm, partial, merged, edge_salvage = features._decode_pcm_with_fallback(
                    "/long.wav", duration_ms=210_000
                )
    assert pcm is not None
    assert merged is not None
    assert partial is False  # not weak-fallback

    conf_full, note_full = confidence_from_evidence(
        tag_key="rock",
        pcm_ok=True,
        pcm_fallback=False,
        multi_window=True,
        variation=0.08,
        onset_consistency=0.85,
    )
    conf_taxed, note_taxed = confidence_from_evidence(
        tag_key="rock",
        pcm_ok=True,
        pcm_fallback=True,
        multi_window=True,
        variation=0.08,
        onset_consistency=0.85,
    )
    assert conf_full > conf_taxed
    assert "PCM fallback" not in note_full
    assert "PCM fallback" in note_taxed


def test_late_only_long_plan_salvages() -> None:
    """Long-plan end-only (intro/mid dead) salvages tail PCM as weak placement."""
    from meridian import features

    features.clear_decode_abort()
    plan = features._analysis_window_plan(210_000)
    assert [r for r, _ in plan] == ["intro", "mid", "late"]
    late_start = plan[2][1]

    def fake_decode(path: str, *, start_s: float = 0.0, duration_s: float = 12.0):
        if abs(start_s - late_start) < 0.05:
            return np.ones(11025 * 4, dtype=np.float32) * 0.05
        return None

    with patch.object(features, "_decode_pcm", side_effect=fake_decode):
        with patch.object(features, "_pcm_signal_ok", side_effect=lambda pcm: pcm is not None):
            pcm, fallback, merged, edge_salvage = features._decode_pcm_with_fallback(
                "/long.wav", duration_ms=210_000
            )
            result = features.analyze_audio(
                "/long.wav",
                "game",
                "outro track",
                "",
                None,
                duration_ms=210_000,
            )
    assert pcm is not None
    assert merged is None
    assert fallback is True
    assert edge_salvage == "late"
    assert result.pcm_ok is True
    assert result.low_trust is True
    assert result.confidence < features.CONFIDENCE_LOW
    assert "end only" in (result.confidence_note or "")
    assert "PCM fallback" in (result.confidence_note or "")


def test_outlier_intro_does_not_poison_merge_health() -> None:
    """Rehunt score 5: shrunk flashy intro keeps tiny coord nudge, not health poison."""
    from meridian.features import _merge_pcm_profiles

    flashy = _prof(
        valence=0.95,
        energy=0.92,
        flux=0.80,
        onset_rate=0.95,
        onset_consistency=0.3,
        energy_mean=0.85,
        unstable=True,
        variation=0.50,
    )
    mid = _prof(
        valence=0.52,
        energy=0.55,
        flux=0.45,
        onset_rate=0.7,
        onset_consistency=0.85,
        variation=0.08,
        unstable=False,
    )
    late = _prof(
        valence=0.54,
        energy=0.57,
        flux=0.44,
        onset_rate=0.68,
        onset_consistency=0.82,
        variation=0.09,
        unstable=False,
    )
    body = _merge_pcm_profiles(None, role_profiles={"mid": mid, "late": late})
    with_flash = _merge_pcm_profiles(
        None, role_profiles={"intro": flashy, "mid": mid, "late": late}
    )
    # Coords stay near the body (tiny intro nudge allowed).
    assert abs(with_flash.energy - body.energy) < 0.08
    assert with_flash.unstable is False
    assert with_flash.variation <= max(mid.variation, late.variation) + 1e-9

    conf_body, _ = confidence_from_evidence(
        tag_key="rock",
        pcm_ok=True,
        pcm_fallback=False,
        pcm_unstable=body.unstable,
        variation=body.variation,
        onset_consistency=body.onset_consistency,
        multi_window=True,
    )
    conf_flash, note = confidence_from_evidence(
        tag_key="rock",
        pcm_ok=True,
        pcm_fallback=False,
        pcm_unstable=with_flash.unstable,
        variation=with_flash.variation,
        onset_consistency=with_flash.onset_consistency,
        multi_window=True,
    )
    assert conf_flash == conf_body
    assert "unstable spectrum" not in note
    assert "PCM weak" not in note


def test_ends_only_intro_late_salvages_on_long_plan() -> None:
    """Start+end with dead middle: weak place from both edges (no genre defer)."""
    from meridian import features

    features.clear_decode_abort()
    plan = features._analysis_window_plan(210_000)
    assert [r for r, _ in plan] == ["intro", "mid", "late"]
    intro_start, late_start = plan[0][1], plan[2][1]

    def fake_decode(path: str, *, start_s: float = 0.0, duration_s: float = 12.0):
        if abs(start_s - intro_start) < 0.05 or abs(start_s - late_start) < 0.05:
            return np.ones(11025 * 4, dtype=np.float32) * 0.05
        return None

    with patch.object(features, "_decode_pcm", side_effect=fake_decode):
        with patch.object(features, "_pcm_signal_ok", side_effect=lambda pcm: pcm is not None):
            pcm, fallback, merged, edge_salvage = features._decode_pcm_with_fallback(
                "/long.wav", duration_ms=210_000
            )
            result = features.analyze_audio(
                "/long.wav",
                "game",
                "ends track",
                "",
                None,
                duration_ms=210_000,
            )
    assert pcm is not None
    assert merged is not None
    assert fallback is True
    assert edge_salvage == "ends"
    assert result.pcm_ok is True
    assert result.low_trust is True
    assert result.confidence < features.CONFIDENCE_LOW
    assert "ends only" in (result.confidence_note or "")


def test_dual_plan_late_only_salvages() -> None:
    """Short intro+late plan with late-only audio: weak end place, not defer."""
    from meridian import features

    features.clear_decode_abort()
    plan = features._analysis_window_plan(35_000)
    assert [r for r, _ in plan] == ["intro", "late"]
    late_start = plan[1][1]

    def fake_decode(path: str, *, start_s: float = 0.0, duration_s: float = 12.0):
        if abs(start_s - late_start) < 0.05:
            return np.ones(11025 * 4, dtype=np.float32) * 0.05
        return None

    with patch.object(features, "_decode_pcm", side_effect=fake_decode):
        with patch.object(features, "_pcm_signal_ok", side_effect=lambda pcm: pcm is not None):
            pcm, fallback, merged, edge_salvage = features._decode_pcm_with_fallback(
                "/short-dual.wav", duration_ms=35_000
            )
            result = features.analyze_audio(
                "/short-dual.wav",
                "game",
                "outro only",
                "",
                None,
                duration_ms=35_000,
            )
    assert pcm is not None
    assert merged is None
    assert fallback is True
    assert edge_salvage == "late"
    assert result.pcm_ok is True
    assert result.low_trust is True
    assert result.confidence <= features.CONFIDENCE_LOW - 0.08
    assert "end only" in (result.confidence_note or "")


def test_hard_decode_miss_skips_same_seek_reffmpeg() -> None:
    """Rehunt score 2: hard decode misses must not re-ffmpeg the same section seeks."""
    from meridian import features

    features.clear_decode_abort()
    calls: list[float] = []

    def fake_decode(path: str, *, start_s: float = 0.0, duration_s: float = 12.0):
        calls.append(float(start_s))
        return None  # hard miss every seek

    with patch.object(features, "_decode_pcm", side_effect=fake_decode):
        with patch.object(features, "_pcm_signal_ok", return_value=True):
            pcm, _fb, merged, edge_salvage = features._decode_pcm_with_fallback(
                "/broken.wav", duration_ms=210_000
            )
    assert pcm is None
    assert merged is None
    # Head probe miss at 0 + 3 section seeks + optional mid_fb. Intro@0 may
    # retry after a short-probe miss (full 12s) — still no silence-fallback loop.
    assert len(calls) <= 5
    # Section starts appear once each (0 may also appear as the probe).
    plan = features._analysis_window_plan(210_000)
    for _role, ss in plan:
        if abs(float(ss)) < 0.01:
            assert calls.count(float(ss)) <= 2
        else:
            assert calls.count(float(ss)) == 1


def test_unknown_duration_decodes_long_single_window() -> None:
    """Appwide score 4: duration_ms==0 listens ~28s from 0, not a lone 12s stub."""
    from meridian import features

    features.clear_decode_abort()
    calls: list[tuple[float, float]] = []

    def fake_decode(path: str, *, start_s: float = 0.0, duration_s: float = 12.0):
        calls.append((float(start_s), float(duration_s)))
        return np.ones(11025 * int(max(4.0, duration_s)), dtype=np.float32) * 0.05

    with patch.object(features, "_decode_pcm", side_effect=fake_decode):
        with patch.object(features, "_pcm_signal_ok", return_value=True):
            pcm, fallback, merged, edge_salvage = features._decode_pcm_with_fallback(
                "/unknown.wav", duration_ms=0
            )
    assert pcm is not None
    assert fallback is False
    assert merged is None
    assert len(calls) == 1
    assert abs(calls[0][0]) < 0.01
    assert abs(calls[0][1] - features.LONG_SINGLE_WINDOW_S) < 1e-6


def test_dual_plan_short_intro_only_salvages() -> None:
    """Appwide score 6: short intro+late plan with intro-only also salvages ~28s."""
    from meridian import features

    features.clear_decode_abort()
    calls: list[tuple[float, float]] = []
    plan = features._analysis_window_plan(35_000)
    assert [r for r, _ in plan] == ["intro", "late"]

    def fake_decode(path: str, *, start_s: float = 0.0, duration_s: float = 12.0):
        calls.append((float(start_s), float(duration_s)))
        if abs(start_s) < 0.01:
            return np.ones(11025 * int(max(4.0, duration_s)), dtype=np.float32) * 0.05
        return None

    with patch.object(features, "_decode_pcm", side_effect=fake_decode):
        with patch.object(features, "_pcm_signal_ok", side_effect=lambda pcm: pcm is not None):
            pcm, fallback, merged, edge_salvage = features._decode_pcm_with_fallback(
                "/short-dual.wav", duration_ms=35_000
            )
    assert pcm is not None
    assert merged is None
    assert fallback is True
    assert edge_salvage == "intro"
    assert any(abs(dur - features.LONG_SINGLE_WINDOW_S) < 1e-6 and abs(ss) < 0.01 for ss, dur in calls)


def test_mid_only_full_pcm_not_multi_window() -> None:
    """Mid-only body: full PCM credit, not fallback+multi-window double signal."""
    from meridian import features

    features.clear_decode_abort()
    plan = features._analysis_window_plan(210_000)
    mid_start = plan[1][1]

    def fake_decode(path: str, *, start_s: float = 0.0, duration_s: float = 12.0):
        if abs(start_s - mid_start) < 0.05:
            return np.ones(11025 * 4, dtype=np.float32) * 0.05
        return None

    with patch.object(features, "_decode_pcm", side_effect=fake_decode):
        with patch.object(features, "_pcm_signal_ok", side_effect=lambda pcm: pcm is not None):
            pcm, partial, merged, edge_salvage = features._decode_pcm_with_fallback(
                "/long.wav", duration_ms=210_000
            )
            result = features.analyze_audio(
                "/music/Rock/mid.mp3",
                "rock",
                "Mid Only",
                "Band",
                120,
                duration_ms=210_000,
            )
    assert pcm is not None
    assert partial is False
    assert merged is None
    assert edge_salvage is None
    assert result.pcm_ok is True
    assert result.low_trust is False
    assert "PCM fallback" not in (result.confidence_note or "")
    assert "multi-window" not in (result.confidence_note or "")
    assert "PCM" in (result.confidence_note or "")


def test_audible_span_shifts_plan_off_silent_head() -> None:
    """Upgrade 2: quiet intro probe rebases intro/mid/late onto the audible span."""
    from meridian import features

    features.clear_decode_abort()
    starts: list[tuple[float, float]] = []

    def fake_decode(path: str, *, start_s: float = 0.0, duration_s: float = 12.0):
        starts.append((float(start_s), float(duration_s)))
        # Silence for the first ~8s; audio afterward.
        if start_s < 8.0:
            return np.zeros(11025 * 4, dtype=np.float32)
        return np.ones(11025 * 4, dtype=np.float32) * 0.05

    def fake_signal_ok(pcm: np.ndarray) -> bool:
        return float(np.sqrt(np.mean(np.square(pcm)))) > 1e-4

    with patch.object(features, "_decode_pcm", side_effect=fake_decode):
        with patch.object(features, "_pcm_signal_ok", side_effect=fake_signal_ok):
            with patch(
                "meridian.acoustic.build_profile",
                side_effect=lambda pcm: _prof(valence=0.5, energy=0.55),
            ):
                pcm, partial, merged, edge = features._decode_pcm_with_fallback(
                    "/game-cue.wav", duration_ms=210_000
                )
    assert pcm is not None
    assert merged is not None
    assert edge is None
    # Section windows should start on/after the first audible probe (~9s step).
    section = [(s, d) for s, d in starts if abs(d - 12.0) < 0.5]
    assert len(section) == 3
    assert section[0][0] >= 8.0 - 1e-6
    assert section[1][0] > section[0][0]
    assert section[2][0] > section[1][0]


def test_shape_note_from_roles_vocabulary() -> None:
    """Upgrade 1: clear arcs → build/drop/bookend; mild → steady."""
    from meridian.features import shape_note_from_roles

    assert (
        shape_note_from_roles(
            {
                "intro": _prof(valence=0.4, energy=0.30),
                "mid": _prof(valence=0.5, energy=0.70),
                "late": _prof(valence=0.5, energy=0.65),
            }
        )
        == "drop"
    )
    assert (
        shape_note_from_roles(
            {
                "intro": _prof(valence=0.4, energy=0.30),
                "mid": _prof(valence=0.5, energy=0.50),
                "late": _prof(valence=0.55, energy=0.75),
            }
        )
        == "build"
    )
    assert (
        shape_note_from_roles(
            {
                "intro": _prof(valence=0.5, energy=0.70),
                "mid": _prof(valence=0.5, energy=0.35),
                "late": _prof(valence=0.5, energy=0.72),
            }
        )
        == "bookend"
    )
    assert (
        shape_note_from_roles(
            {
                "intro": _prof(valence=0.50, energy=0.50),
                "mid": _prof(valence=0.51, energy=0.51),
                "late": _prof(valence=0.52, energy=0.52),
            }
        )
        == "steady"
    )
    assert shape_note_from_roles({"mid": _prof(valence=0.5, energy=0.5)}) is None


def test_steer_weight_seed_and_intro_near_zero() -> None:
    """Upgrade 5: seed-only / intro-only are not safe to steer."""
    from meridian.features import steer_weight_from_listen

    assert (
        steer_weight_from_listen(
            pcm_ok=False,
            multi_window=False,
            pcm_fallback=False,
            pcm_unstable=False,
            edge_salvage=None,
            seed_evidence=True,
        )
        <= 0.08
    )
    assert (
        steer_weight_from_listen(
            pcm_ok=True,
            multi_window=False,
            pcm_fallback=True,
            pcm_unstable=False,
            edge_salvage="intro",
            seed_evidence=True,
        )
        <= 0.08
    )
    assert (
        steer_weight_from_listen(
            pcm_ok=True,
            multi_window=True,
            pcm_fallback=False,
            pcm_unstable=False,
            edge_salvage=None,
            seed_evidence=True,
            variation=0.1,
        )
        >= 0.9
    )


def test_merge_attaches_shape_note() -> None:
    from meridian.features import _merge_pcm_profiles

    intro = _prof(valence=0.4, energy=0.30)
    mid = _prof(valence=0.5, energy=0.70)
    late = _prof(valence=0.5, energy=0.66)
    merged = _merge_pcm_profiles(None, role_profiles={"intro": intro, "mid": mid, "late": late})
    assert merged.shape_note == "drop"


def test_unknown_duration_silence_reseek_not_fallback() -> None:
    """Hunt #6: successful leading-silence re-seek is a full listen, not PCM fallback."""
    from meridian import features

    features.clear_decode_abort()
    sr = 11025
    tone = (0.2 * np.sin(2 * np.pi * 440 * np.arange(sr * 24) / sr)).astype(np.float32)
    buf = np.concatenate([np.zeros(sr * 4, dtype=np.float32), tone])
    calls: list[tuple[float, float]] = []

    def fake_decode(path: str, *, start_s: float = 0.0, duration_s: float = 12.0):
        calls.append((float(start_s), float(duration_s)))
        if abs(start_s) < 0.01:
            return buf.copy()
        return tone.copy()

    with patch.object(features, "_decode_pcm", side_effect=fake_decode):
        with patch.object(
            features, "_pcm_signal_ok", side_effect=lambda p: float(np.sqrt(np.mean(np.square(p)))) > 1e-4
        ):
            pcm, fallback, merged, edge = features._decode_pcm_with_fallback(
                "/cue.wav", duration_ms=0
            )
            with patch.object(
                features,
                "_decode_pcm_with_fallback",
                return_value=(pcm, fallback, merged, edge),
            ):
                result = features.analyze_audio(
                    "/cue.wav", "", "cue", "a", None, duration_ms=0
                )
    assert pcm is not None
    assert fallback is False
    assert len(calls) == 2
    assert "PCM fallback" not in (result.confidence_note or "")
    assert result.steer_weight is not None and result.steer_weight >= 0.65


def test_partial_multi_window_caps_steer() -> None:
    """Hunt #4: multi-window + pcm_fallback must not keep max steer."""
    from meridian.features import steer_weight_from_listen

    full = steer_weight_from_listen(
        pcm_ok=True,
        multi_window=True,
        pcm_fallback=False,
        pcm_unstable=False,
        edge_salvage=None,
        seed_evidence=True,
        variation=0.1,
    )
    partial = steer_weight_from_listen(
        pcm_ok=True,
        multi_window=True,
        pcm_fallback=True,
        pcm_unstable=False,
        edge_salvage=None,
        seed_evidence=True,
        variation=0.1,
    )
    assert full >= 0.9
    assert partial <= 0.70
    assert partial < full


def test_three_tastes_that_agree_brighten_inside_the_band() -> None:
    from meridian.features import (
        CONFIDENCE_HIGH,
        CONFIDENCE_LOW,
        _merge_pcm_profiles,
        apply_taste_agreement,
        lift_confidence_inside_band,
    )

    close = _merge_pcm_profiles(
        None,
        role_profiles={
            "intro": _prof(valence=0.50, energy=0.50),
            "mid": _prof(valence=0.51, energy=0.51),
            "late": _prof(valence=0.52, energy=0.52),
        },
    )
    assert close.shape_note == "steady"
    assert close.tastes_agree is True
    lifted, note = apply_taste_agreement(
        0.70,
        "PCM",
        tastes_agree=True,
        multi_window=True,
        pcm_fallback=False,
        pcm_unstable=False,
        variation=0.05,
        shape_note="steady",
    )
    assert 0.70 < lifted < CONFIDENCE_HIGH
    assert "tastes agree" in note
    assert lift_confidence_inside_band(0.74) < CONFIDENCE_HIGH
    assert lift_confidence_inside_band(0.44) < CONFIDENCE_LOW
    assert lift_confidence_inside_band(0.80) > 0.80

    drop = _merge_pcm_profiles(
        None,
        role_profiles={
            "intro": _prof(valence=0.40, energy=0.30),
            "mid": _prof(valence=0.50, energy=0.70),
            "late": _prof(valence=0.50, energy=0.66),
        },
    )
    assert drop.tastes_agree is False
    stayed, stayed_note = apply_taste_agreement(
        0.70,
        "PCM",
        tastes_agree=False,
        multi_window=True,
        pcm_fallback=False,
        pcm_unstable=False,
        variation=0.05,
        shape_note=drop.shape_note,
    )
    assert stayed == 0.70
    assert stayed_note == "PCM"


def test_unsure_confidence_keeps_steer_under_the_now_gate() -> None:
    from meridian.features import UNSURE_STEER_MAX, steer_weight_from_listen

    unsure = steer_weight_from_listen(
        pcm_ok=True,
        multi_window=True,
        pcm_fallback=False,
        pcm_unstable=False,
        edge_salvage=None,
        seed_evidence=True,
        variation=0.1,
        confidence=0.40,
    )
    sure = steer_weight_from_listen(
        pcm_ok=True,
        multi_window=True,
        pcm_fallback=False,
        pcm_unstable=False,
        edge_salvage=None,
        seed_evidence=True,
        variation=0.1,
        confidence=0.80,
    )
    assert unsure <= UNSURE_STEER_MAX
    assert sure >= 0.9
