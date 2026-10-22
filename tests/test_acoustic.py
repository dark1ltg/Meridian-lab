"""Tests for acoustic profile helpers (items 2–11; no distributed decode)."""

from __future__ import annotations

import numpy as np

from meridian.acoustic import (
    SAMPLERATE,
    aggregate_window_metrics,
    build_profile,
    confidence_from_evidence,
    energy_stats,
    local_analysis_windows,
    normalize_brightness,
    onset_stats,
    spectral_flux,
    spectral_slice,
)
from meridian.features import mood_confidence


def test_local_windows_inside_buffer() -> None:
    starts = local_analysis_windows(SAMPLERATE * 20, 4096)
    assert len(starts) >= 2
    assert starts[0] >= 0
    assert starts[-1] <= SAMPLERATE * 20 - 4096


def test_spectral_bands_flux_energy() -> None:
    sr = SAMPLERATE
    t = np.arange(sr * 3, dtype=np.float32) / sr
    steady = (0.4 * np.sin(2 * np.pi * 110 * t)).astype(np.float32)
    bright, bass, flat, bands, _c = spectral_slice(steady, 0, 4096, sr)
    assert set(bands) >= {"bass", "low_mid", "mid", "high_mid", "high"}
    assert bass >= 0.15
    flux_steady = spectral_flux(steady, sr)
    assert 0.05 <= flux_steady <= 0.25
    rng = np.random.default_rng(0)
    noisy = rng.standard_normal(sr * 3).astype(np.float32) * 0.25
    flux_noisy = spectral_flux(noisy, sr)
    assert flux_noisy > flux_steady + 0.15
    assert flux_noisy < 0.95
    chirp = (0.4 * np.sin(2 * np.pi * (110 + 400 * t / t[-1]) * t)).astype(np.float32)
    flux_chirp = spectral_flux(chirp, sr)
    assert flux_steady < flux_chirp < flux_noisy
    est = energy_stats(steady)
    assert 0.0 <= est["mean"] <= 1.0
    ramp = (steady * np.linspace(0.15, 1.0, steady.size)).astype(np.float32)
    assert energy_stats(ramp)["trend"] > 0.0


def test_quiet_noise_not_glowing() -> None:
    sr = SAMPLERATE
    rng = np.random.default_rng(1)
    quiet = (rng.standard_normal(sr * 3) * 0.002).astype(np.float32)
    loud_bright = (0.35 * np.sin(2 * np.pi * 3200 * np.arange(sr * 3) / sr)).astype(np.float32)
    pq = build_profile(quiet)
    pb = build_profile(loud_bright)
    assert pq.valence < 0.55
    assert pb.valence > pq.valence + 0.08


def test_bass_not_double_counted() -> None:
    sr = SAMPLERATE
    t = np.arange(sr * 4, dtype=np.float32) / sr
    bass_heavy = (0.45 * np.sin(2 * np.pi * 80 * t)).astype(np.float32)
    mid = (0.45 * np.sin(2 * np.pi * 900 * t)).astype(np.float32)
    pb = build_profile(bass_heavy)
    pm = build_profile(mid)
    assert pb.valence < pm.valence
    assert pb.band_energy["bass"] > pm.band_energy["bass"]


def test_brightness_not_in_energy_axis() -> None:
    sr = SAMPLERATE
    t = np.arange(sr * 4, dtype=np.float32) / sr
    dark = (0.35 * np.sin(2 * np.pi * 120 * t)).astype(np.float32)
    bright = (0.35 * np.sin(2 * np.pi * 2800 * t)).astype(np.float32)
    pd = build_profile(dark)
    pb = build_profile(bright)
    assert abs(pb.energy - pd.energy) < 0.12
    assert pb.valence > pd.valence + 0.05


def test_brightness_and_aggregate() -> None:
    assert normalize_brightness(3000.0) > normalize_brightness(400.0)
    bands = [{"bass": 0.3, "low_mid": 0.2, "mid": 0.2, "high_mid": 0.15, "high": 0.15}] * 4
    agg_s, _ = aggregate_window_metrics([0.5, 0.51, 0.49, 0.5], [0.3, 0.31, 0.29, 0.3], [0.2] * 4, bands)
    agg_c, _ = aggregate_window_metrics([0.15, 0.35, 0.7, 0.85], [0.6, 0.45, 0.2, 0.15], [0.2] * 4, bands)
    assert agg_s["variation"] < agg_c["variation"]
    assert 0.05 <= agg_c["bright"] <= 0.95


def test_build_profile_bounded() -> None:
    sr = SAMPLERATE
    t = np.arange(sr * 4, dtype=np.float32) / sr
    pcm = (0.35 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    profile = build_profile(pcm)
    assert 0.03 <= profile.valence <= 0.97
    assert 0.03 <= profile.energy <= 0.97
    assert 0.05 <= profile.flux <= 0.95
    assert profile.window_count >= 2
    _bpm, ostats = onset_stats(pcm)
    assert "burstiness" in ostats and "consistency" in ostats


def test_soft_pcm_bpm_stays_clamped() -> None:
    """Tagged BPM must not pull energy outside the soft / evidence envelope."""
    from unittest.mock import patch

    from meridian.features import EVIDENCE_SOFT_ENERGY_MAX, analyze_audio, genre_seed

    sr = SAMPLERATE
    t = np.arange(sr * 3, dtype=np.float32) / sr
    pcm = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    seed = genre_seed("metal", "x", "y", path="/tmp/x.flac")
    assert seed.clamp_match
    with patch("meridian.features._decode_pcm_with_fallback", return_value=(pcm, False, None, None)):
        result = analyze_audio("/tmp/x.flac", "metal", "x", "y", 180.0)
    assert abs(result.energy - seed.energy) <= EVIDENCE_SOFT_ENERGY_MAX + 1e-9


def test_onset_stats_keeps_a_landed_pulse_and_drops_a_fake_one() -> None:
    """A grid the hits land on stays. A shifted or unsure tempo is refused."""
    pytest = __import__("pytest")
    aubio = pytest.importorskip("aubio")
    del aubio
    sr = SAMPLERATE

    def _bursts(bpm: float, *, regular: bool) -> np.ndarray:
        seconds = 8.0
        n = int(sr * seconds)
        y = np.zeros(n, dtype=np.float32)
        rng = np.random.default_rng(9)
        length = int(0.05 * sr)
        t = 0.15
        tt = np.arange(n) / sr
        y += (0.12 * np.sin(2 * np.pi * 98.0 * tt)).astype(np.float32)
        while t < seconds - 0.15:
            i = int(t * sr)
            env = np.exp(-np.arange(length) / (0.01 * sr)).astype(np.float32)
            burst = (0.9 * env * rng.standard_normal(length)).astype(np.float32)
            if i + length <= n:
                y[i : i + length] += burst
            t += (60.0 / bpm) if regular else float(rng.uniform(0.17, 0.73))
        return y

    bpm, stats = onset_stats(_bursts(90.0, regular=True))
    assert bpm is not None
    assert stats["pulse_rejected"] == 0.0
    assert stats["beat_alignment"] >= 0.55

    fake_bpm, fake_stats = onset_stats(_bursts(90.0, regular=False))
    assert fake_bpm is None
    assert fake_stats["pulse_rejected"] == 1.0


def test_onset_stats_rejects_spurious_bpm() -> None:
    """Silence / no-onset PCM must not invent a BPM."""
    sr = SAMPLERATE
    silence = np.zeros(sr * 4, dtype=np.float32)
    bpm, _ostats = onset_stats(silence)
    assert bpm is None


def test_pcm_signal_rejects_nonfinite() -> None:
    from meridian.features import _pcm_signal_ok

    sr = SAMPLERATE
    t = np.arange(sr * 3, dtype=np.float32) / sr
    pcm = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    assert _pcm_signal_ok(pcm)
    bad = pcm.copy()
    bad[1000] = np.inf
    assert not _pcm_signal_ok(bad)
    bad2 = pcm.copy()
    bad2[1000] = np.nan
    assert not _pcm_signal_ok(bad2)


def test_zero_tag_bpm_not_trusted() -> None:
    """BPM 0 / NaN must not enable soft-PCM or claim BPM in confidence."""
    from unittest.mock import patch

    from meridian.features import SOFT_PCM_MAX_SHIFT, _coerce_bpm, analyze_audio, genre_seed

    assert _coerce_bpm(0.0) is None
    assert _coerce_bpm(float("nan")) is None
    assert _coerce_bpm(128.0) == 128.0

    sr = SAMPLERATE
    t = np.arange(sr * 3, dtype=np.float32) / sr
    pcm = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    seed = genre_seed("metal", "x", "y", path="/tmp/x.flac")
    assert seed.clamp_match
    # Extreme PCM energy — soft envelope would hold within ±SOFT; zero BPM must not.
    cues = (0.05, 0.05, None, False, None)
    with patch("meridian.features._decode_pcm_with_fallback", return_value=(pcm, False, None, None)):
        with patch("meridian.features._pcm_mood_cues", return_value=cues):
            soft = analyze_audio("/tmp/x.flac", "metal", "x", "y", 180.0)
            zero = analyze_audio("/tmp/x.flac", "metal", "x", "y", 0.0)
    assert abs(soft.energy - seed.energy) <= SOFT_PCM_MAX_SHIFT + 1e-9
    assert abs(zero.energy - seed.energy) > SOFT_PCM_MAX_SHIFT
    assert zero.bpm is None
    assert "BPM" not in (zero.confidence_note or "")
    assert "BPM" in (soft.confidence_note or "")


def test_out_bpm_prefers_detected_over_zero_tag() -> None:
    from unittest.mock import patch

    from meridian.features import analyze_audio

    sr = SAMPLERATE
    t = np.arange(sr * 3, dtype=np.float32) / sr
    pcm = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    with patch("meridian.features._decode_pcm_with_fallback", return_value=(pcm, False, None, None)):
        with patch(
            "meridian.features._pcm_mood_cues",
            return_value=(0.5, 0.5, 118.0, False, None),
        ):
            result = analyze_audio("/tmp/x.flac", "metal", "x", "y", 0.0)
    assert result.bpm == 118.0
    assert "BPM" in (result.confidence_note or "")


def test_merge_dual_window_profiles() -> None:
    from meridian.acoustic import AcousticProfile
    from meridian.features import _merge_pcm_profiles

    a = AcousticProfile(
        valence=0.3,
        energy=0.4,
        bpm=120.0,
        unstable=False,
        brightness=0.4,
        flux=0.3,
        onset_consistency=0.8,
        variation=0.1,
        window_count=3,
        pcm_samples=1000,
    )
    b = AcousticProfile(
        valence=0.5,
        energy=0.6,
        bpm=124.0,
        unstable=True,
        brightness=0.5,
        flux=0.4,
        onset_consistency=0.4,
        variation=0.2,
        window_count=3,
        pcm_samples=1000,
    )
    # Mild disagreement → weighted toward stabler (a).
    m = _merge_pcm_profiles(a, b)
    assert m.valence < 0.45
    assert m.onset_consistency == 0.8
    assert m.window_count == 6

    # Strong disagreement → stabler base + Kinetic injection from active loser (not a false middle).
    far = AcousticProfile(
        valence=0.85,
        energy=0.9,
        bpm=140.0,
        unstable=True,
        brightness=0.8,
        flux=0.7,
        onset_rate=0.8,
        onset_consistency=0.2,
        variation=0.5,
        window_count=3,
        pcm_samples=1000,
    )
    hard = _merge_pcm_profiles(a, far)
    assert hard.energy > a.energy + 0.05
    assert hard.energy < far.energy - 0.05
    assert abs(hard.valence - a.valence) < abs(hard.valence - far.valence)
    assert hard.onset_consistency == 0.8


def test_energy_bpm_arbitration() -> None:
    from meridian.features import _energy_bpm_for_nudge

    assert _energy_bpm_for_nudge(120.0, 160.0, onset_consistency=0.85, unstable=False) == 160.0
    assert _energy_bpm_for_nudge(120.0, 160.0, onset_consistency=0.2, unstable=True) == 120.0
    assert _energy_bpm_for_nudge(120.0, 125.0, onset_consistency=0.9, unstable=False) == 120.0


def test_genre_dictionary_has_contemporary_seeds() -> None:
    from meridian.features import GENRE_MOOD, genre_match

    for key in ("phonk", "hyperpop", "drill", "vaporwave", "amapiano", "city pop"):
        assert key in GENRE_MOOD
    assert genre_match("Drift Phonk") == "drift phonk" or genre_match("phonk") == "phonk"

    strong, _ = confidence_from_evidence(
        tag_key="metal",
        pcm_ok=True,
        bpm_ok=True,
        variation=0.05,
        onset_consistency=0.85,
    )
    weak, note_w = confidence_from_evidence(pcm_ok=True, pcm_unstable=True, variation=0.45)
    conflict, note_c = confidence_from_evidence(tag_key="metal", pcm_ok=True, bpm_conflict=True, variation=0.4)
    assert strong > weak
    assert strong > conflict
    assert "PCM weak" in note_w or "unstable" in note_w
    assert "BPM conflict" in note_c
    c, note = mood_confidence(tag_key="metal", pcm_ok=True, bpm_ok=True)
    assert c > 0.7 and "tag:metal" in note


def test_rms_consistency_and_peak_affect_energy() -> None:
    """Uneven dynamics may raise Kinetic slightly; motion still dominates over loudness."""
    sr = SAMPLERATE
    n = sr * 4
    t = np.arange(n, dtype=np.float32) / sr
    tone = np.sin(2 * np.pi * 220 * t).astype(np.float32)
    steady = (0.35 * tone).astype(np.float32)
    # Long quiet gaps with short loud hits → low RMS consistency, high peak.
    dynamic = (0.04 * tone).astype(np.float32)
    for i in range(0, n, sr):
        dynamic[i : i + sr // 8] = (0.85 * tone[i : i + sr // 8]).astype(np.float32)
    assert energy_stats(steady)["consistency"] > energy_stats(dynamic)["consistency"] + 0.2
    assert energy_stats(dynamic)["range"] > energy_stats(steady)["range"]
    ps = build_profile(steady)
    pd = build_profile(dynamic)
    # Loudness/dynamics still contribute, but must not dominate Still↔Kinetic.
    assert pd.energy > ps.energy + 0.015


def test_loud_static_not_forced_kinetic() -> None:
    """High RMS with little onset/flux activity should stay less Kinetic than busy quiet material."""
    sr = SAMPLERATE
    n = sr * 4
    t = np.arange(n, dtype=np.float32) / sr
    loud_static = (0.75 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    quiet_busy = np.zeros(n, dtype=np.float32)
    for i in range(0, n, sr // 8):
        quiet_busy[i : i + 64] = 0.22
    pl = build_profile(loud_static)
    pq = build_profile(quiet_busy)
    assert pq.energy > pl.energy + 0.04


def test_container_seed_skips_soft_pcm_lock() -> None:
    """OST/game catalog labels stay neighborhoods but do not soft-lock PCM like metal+BPM."""
    from unittest.mock import patch

    from meridian.acoustic import AcousticProfile
    from meridian.features import analyze_audio, genre_seed

    sr = SAMPLERATE
    t = np.arange(sr * 3, dtype=np.float32) / sr
    # Bright / active-ish tone far from the mid OST seed.
    pcm = (0.55 * np.sin(2 * np.pi * 1800 * t)).astype(np.float32)
    seed = genre_seed("soundtrack", "Boss Theme", "Composer", path="/music/OST/game/x.flac")
    assert seed.container_only
    assert seed.clamp_match
    far = AcousticProfile(
        valence=0.85,
        energy=0.80,
        bpm=120.0,
        unstable=False,
        brightness=0.8,
        flux=0.55,
        onset_consistency=0.75,
        variation=0.08,
        tonal=0.7,
        spectral_slope=0.65,
        window_count=3,
        pcm_samples=pcm.size,
    )
    with patch("meridian.features._decode_pcm_with_fallback", return_value=(pcm, False, None, None)):
        with patch(
            "meridian.features._pcm_mood_cues",
            return_value=(0.85, 0.80, 120.0, False, far),
        ):
            result = analyze_audio(
                "/music/OST/game/x.flac", "soundtrack", "Boss Theme", "Composer", 120.0
            )
        # Soft-PCM would keep within SOFT_PCM_MAX_SHIFT; container clamp may move farther.
        from meridian.features import SOFT_PCM_MAX_SHIFT

        assert (
            abs(result.energy - seed.energy) > SOFT_PCM_MAX_SHIFT + 1e-6
            or abs(result.valence - seed.valence) > SOFT_PCM_MAX_SHIFT + 1e-6
        )

    metal = genre_seed("metal", "x", "y", path="/tmp/x.flac")
    assert not metal.container_only


def test_burstiness_modulates_not_drives_kinetic() -> None:
    """Burstiness may texture Kinetic slightly; unstable material should not fake energy."""
    sr = SAMPLERATE
    n = sr * 4
    regular = np.zeros(n, dtype=np.float32)
    bursty = np.zeros(n, dtype=np.float32)
    for i in range(0, n, sr // 4):
        regular[i : i + 48] = 0.9
    pos = 0
    while pos < n - 200:
        bursty[pos : pos + 48] = 0.9
        bursty[pos + 80 : pos + 128] = 0.9
        pos += sr // 2
    pr = build_profile(regular)
    pb = build_profile(bursty)
    # Texture only — not a large Kinetic jump from burstiness alone.
    assert abs(pb.energy - pr.energy) < 0.12
    # High-variation / shaky buffer: burst gate should not invent Kinetic.
    shaky = bursty.copy()
    shaky[n // 2 :] *= 0.05
    ps = build_profile(shaky)
    assert ps.energy < 0.85


def test_evidence_soft_widens_valence() -> None:
    """Steady PCM far from a soft genre seed may move Glow beyond the tiny soft envelope."""
    from unittest.mock import patch

    from meridian.acoustic import AcousticProfile
    from meridian.features import (
        EVIDENCE_SOFT_VALENCE_MAX,
        SOFT_PCM_MAX_SHIFT,
        analyze_audio,
        genre_seed,
    )

    seed = genre_seed("metal", "x", "y", path="/tmp/x.flac")
    profile = AcousticProfile(
        valence=0.88,
        energy=0.30,
        bpm=120.0,
        unstable=False,
        brightness=0.85,
        flux=0.25,
        onset_consistency=0.88,
        variation=0.06,
        window_count=3,
        pcm_samples=8000,
    )
    pcm = np.zeros(SAMPLERATE, dtype=np.float32)
    with patch(
        "meridian.features._decode_pcm_with_fallback",
        return_value=(pcm, False, profile, None),
    ):
        result = analyze_audio("/tmp/x.flac", "metal", "x", "y", 180.0)
    moved = abs(result.valence - seed.valence)
    assert moved > SOFT_PCM_MAX_SHIFT + 0.02
    assert moved <= EVIDENCE_SOFT_VALENCE_MAX + 1e-9


def test_genre_conflict_reduces_metadata_not_max_pcm() -> None:
    """Conflict frees seed authority for PCM; unstable PCM claims little of that weight."""
    from unittest.mock import patch

    from meridian.acoustic import AcousticProfile
    from meridian.features import (
        EVIDENCE_SOFT_ENERGY_MAX,
        SOFT_PCM_MAX_SHIFT,
        _genre_pair_conflict,
        analyze_audio,
        genre_seed,
    )

    path_conflict = "/music/Ambient/Album/track.flac"
    path_aligned = "/music/Metal/Album/track.flac"
    seed_c = genre_seed("metal", "x", "y", path=path_conflict)
    seed_a = genre_seed("metal", "x", "y", path=path_aligned)
    assert _genre_pair_conflict(seed_c.tag_key, seed_c.path_key)
    assert not _genre_pair_conflict(seed_a.tag_key, seed_a.path_key)

    stable = AcousticProfile(
        valence=0.55,
        energy=0.25,
        bpm=90.0,
        unstable=False,
        brightness=0.5,
        flux=0.2,
        # Below evidence-widen gate (0.70) so conflict transfer is the only soft open.
        onset_consistency=0.60,
        variation=0.08,
        window_count=3,
        pcm_samples=8000,
    )
    unstable_p = AcousticProfile(
        valence=0.55,
        energy=0.25,
        bpm=90.0,
        unstable=True,
        brightness=0.5,
        flux=0.2,
        onset_consistency=0.25,
        variation=0.40,
        window_count=3,
        pcm_samples=8000,
    )
    pcm = np.zeros(SAMPLERATE, dtype=np.float32)

    with patch(
        "meridian.features._decode_pcm_with_fallback",
        return_value=(pcm, False, stable, None),
    ):
        conflicted = analyze_audio(path_conflict, "metal", "x", "y", 120.0)
        aligned = analyze_audio(path_aligned, "metal", "x", "y", 120.0)
    moved_c = abs(conflicted.energy - seed_c.energy)
    moved_a = abs(aligned.energy - seed_a.energy)
    # Freed metadata → a bit more PCM residual than aligned soft, up to evidence-soft max.
    assert moved_c > moved_a + 0.005
    assert moved_c <= EVIDENCE_SOFT_ENERGY_MAX + 1e-9
    assert moved_c > SOFT_PCM_MAX_SHIFT - 1e-9

    with patch(
        "meridian.features._decode_pcm_with_fallback",
        return_value=(pcm, False, unstable_p, None),
    ):
        conflicted_weak = analyze_audio(path_conflict, "metal", "x", "y", 120.0)
    # Unstable PCM must not inherit most of the freed metadata authority.
    assert abs(conflicted_weak.energy - seed_c.energy) < moved_c - 0.005


def test_path_to_9_clamp_constants() -> None:
    """Path-to-9: slightly looser soft/clamp envelopes; energy widen ≤ valence."""
    from meridian.features import (
        EVIDENCE_SOFT_ENERGY_MAX,
        EVIDENCE_SOFT_VALENCE_MAX,
        PCM_MAX_SHIFT,
        PCM_STRONG_ONSET_MAX,
        SOFT_PCM_MAX_SHIFT,
    )

    assert SOFT_PCM_MAX_SHIFT == 0.09
    assert EVIDENCE_SOFT_VALENCE_MAX == 0.14
    assert EVIDENCE_SOFT_ENERGY_MAX == 0.12
    assert EVIDENCE_SOFT_ENERGY_MAX <= EVIDENCE_SOFT_VALENCE_MAX
    assert PCM_MAX_SHIFT == 0.16
    assert PCM_STRONG_ONSET_MAX == 0.18
    assert min(PCM_STRONG_ONSET_MAX, PCM_MAX_SHIFT * 1.25) == 0.18


def test_meta_accurate_vs_weak_kinetic_pull() -> None:
    """Good metadata + strong onset fine-tunes Kinetic; weak metadata allows more pull."""
    from unittest.mock import patch

    from meridian.acoustic import AcousticProfile
    from meridian.features import SOFT_PCM_MAX_SHIFT, analyze_audio, genre_seed

    seed = genre_seed("metal", "x", "y", path="/music/Metal/Album/track.flac")
    # Far-calm PCM with steady onsets + real detected BPM.
    profile = AcousticProfile(
        valence=0.40,
        energy=0.25,
        bpm=96.0,
        unstable=False,
        brightness=0.45,
        flux=0.22,
        onset_consistency=0.88,
        variation=0.06,
        window_count=3,
        pcm_samples=8000,
    )
    pcm = np.zeros(SAMPLERATE, dtype=np.float32)
    with patch(
        "meridian.features._decode_pcm_with_fallback",
        return_value=(pcm, False, profile, None),
    ):
        good = analyze_audio(
            "/music/Metal/Album/track.flac", "metal", "x", "y", 180.0
        )
        # Missing tag BPM → not soft-locked; stronger Kinetic pull inside clamp.
        weak = analyze_audio(
            "/music/Metal/Album/track.flac", "metal", "x", "y", None
        )

    moved_good = abs(good.energy - seed.energy)
    moved_weak = abs(weak.energy - seed.energy)
    assert moved_good <= SOFT_PCM_MAX_SHIFT + 1e-9
    assert moved_weak > moved_good + 0.03


def test_good_meta_mid_shaky_not_wider_kinetic_than_solid() -> None:
    """Hunt #5: mid-shaky onset must not out-shove Kinetic vs a solid listen on good tags."""
    from unittest.mock import patch

    from meridian.acoustic import AcousticProfile
    from meridian.features import SOFT_PCM_MAX_SHIFT, analyze_audio, genre_seed

    seed = genre_seed("metal", "x", "y", path="/music/Metal/Album/track.flac")
    path = "/music/Metal/Album/track.flac"
    pcm = np.zeros(SAMPLERATE, dtype=np.float32)

    def _run(onset: float, variation: float):
        profile = AcousticProfile(
            valence=0.40,
            energy=0.25,
            bpm=96.0,
            unstable=False,
            brightness=0.45,
            flux=0.22,
            onset_consistency=onset,
            variation=variation,
            window_count=3,
            pcm_samples=8000,
        )
        with patch(
            "meridian.features._decode_pcm_with_fallback",
            return_value=(pcm, False, profile, None),
        ):
            return analyze_audio(path, "metal", "x", "y", 180.0)

    solid = _run(0.88, 0.06)
    shaky = _run(0.72, 0.24)
    moved_solid = abs(solid.energy - seed.energy)
    moved_shaky = abs(shaky.energy - seed.energy)
    assert moved_solid <= SOFT_PCM_MAX_SHIFT + 1e-9
    # Mid-shaky must not get a wider Kinetic shove than the solid fine-tune.
    assert moved_shaky <= moved_solid + 1e-9
    assert moved_shaky <= SOFT_PCM_MAX_SHIFT + 1e-9


def test_conflict_soft_keeps_evidence_kinetic_room() -> None:
    """Hunt #3: tag↔path conflict must not claw back evidence-opened Kinetic room."""
    from unittest.mock import patch

    from meridian.acoustic import AcousticProfile
    from meridian.features import (
        EVIDENCE_SOFT_ENERGY_MAX,
        EVIDENCE_SOFT_VALENCE_MAX,
        SOFT_PCM_MAX_SHIFT,
        analyze_audio,
        genre_seed,
    )

    path = "/music/Ambient/Album/track.flac"
    seed = genre_seed("metal", "x", "y", path=path)
    # Strong calm PCM: evidence-soft + conflict path (not meta_accurate).
    profile = AcousticProfile(
        valence=0.55,
        energy=0.20,
        bpm=90.0,
        unstable=False,
        brightness=0.5,
        flux=0.2,
        onset_consistency=0.88,
        variation=0.06,
        window_count=3,
        pcm_samples=8000,
    )
    pcm = np.zeros(SAMPLERATE, dtype=np.float32)
    with patch(
        "meridian.features._decode_pcm_with_fallback",
        return_value=(pcm, False, profile, None),
    ):
        result = analyze_audio(path, "metal", "x", "y", 120.0)

    moved_e = abs(result.energy - seed.energy)
    moved_v = abs(result.valence - seed.valence)
    # Conflicted soft path keeps at least the evidence-soft Kinetic ceiling room.
    assert moved_e >= EVIDENCE_SOFT_ENERGY_MAX - 1e-9
    assert moved_e <= max(EVIDENCE_SOFT_ENERGY_MAX, min(0.13, SOFT_PCM_MAX_SHIFT * 1.45)) + 1e-9
    assert moved_v >= min(EVIDENCE_SOFT_VALENCE_MAX, abs(profile.valence - seed.valence)) - 1e-6
    assert moved_v <= EVIDENCE_SOFT_VALENCE_MAX + 1e-9


def test_loudness_normalization_equalizes_static_masters() -> None:
    """Upgrade 3: hot vs quiet static tones must not diverge on Kinetic via RMS."""
    from meridian.acoustic import build_profile, normalize_analysis_loudness

    sr = SAMPLERATE
    t = np.arange(sr * 4, dtype=np.float32) / sr
    tone = np.sin(2 * np.pi * 220 * t).astype(np.float32)
    quiet = (0.05 * tone).astype(np.float32)
    hot = (0.85 * tone).astype(np.float32)
    nq, rq = normalize_analysis_loudness(quiet)
    nh, rh = normalize_analysis_loudness(hot)
    assert rh > rq * 5
    # After norm, RMS lands near the shared target.
    rms_q = float(np.sqrt(np.mean(np.square(nq))))
    rms_h = float(np.sqrt(np.mean(np.square(nh))))
    assert abs(rms_q - rms_h) < 0.02
    pq = build_profile(quiet)
    ph = build_profile(hot)
    assert abs(pq.energy - ph.energy) < 0.05
    assert pq.tonal > 0.5 and ph.tonal > 0.5


def test_timbre_flag_persists_tonal_vs_noisy() -> None:
    """Upgrade 4: tonal tone vs noise get distinct timbre leftovers."""
    from meridian.acoustic import build_profile
    from meridian.features import _timbre_flag_from_tonal

    sr = SAMPLERATE
    t = np.arange(sr * 4, dtype=np.float32) / sr
    tonal_pcm = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    rng = np.random.default_rng(0)
    noisy_pcm = (rng.standard_normal(sr * 4).astype(np.float32) * 0.35)
    pt = build_profile(tonal_pcm)
    pn = build_profile(noisy_pcm)
    assert pt.tonal > pn.tonal + 0.12
    assert _timbre_flag_from_tonal(pt.tonal) == "tonal"
    # White noise may land mid-band after norm; flag only when clearly noisy.
    flag_n = _timbre_flag_from_tonal(pn.tonal)
    assert flag_n in (None, "noisy")
    assert flag_n != "tonal"


def test_near_floor_hiss_stays_still_after_loudness_norm() -> None:
    """Hunt #3: boosted near-floor hiss must not invent mid Kinetic."""
    sr = SAMPLERATE
    rng = np.random.default_rng(2)
    hiss = (rng.standard_normal(sr * 4).astype(np.float32) * 0.002)
    profile = build_profile(hiss)
    assert profile.energy < 0.30
    assert abs(profile.valence - 0.5) < 0.12
