"""Placement-honesty package — dialogue drop, blend floor, pitch Glow, slope,
pulse Kinetic gate, keyword undo, sub-bass Shadow vote + hunt 3–8 fixes."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
from numpy.fft import irfft, rfft, rfftfreq

from meridian.acoustic import (
    SAMPLERATE,
    AcousticProfile,
    build_profile,
    estimate_pitch_hz,
    frame_periodicity,
    is_dialogue_frame,
    local_analysis_windows,
    normalize_pitch_glow,
    spectral_bands,
    spectral_slice_ex,
    spectral_slope_clarity,
    tonal_onset_score,
)


def _tone(hz: float, seconds: float = 3.0, amp: float = 0.4) -> np.ndarray:
    t = np.arange(int(SAMPLERATE * seconds), dtype=np.float32) / SAMPLERATE
    return (amp * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def _soft_mid_noise(seconds: float = 4.0, seed: int = 1) -> np.ndarray:
    """Speech-like mid-emphasized noise without brick-wall zeros."""
    rng = np.random.default_rng(seed)
    sr = SAMPLERATE
    x = rng.standard_normal(int(sr * seconds)).astype(np.float32)
    spec = rfft(x)
    freqs = rfftfreq(x.size, 1.0 / sr)
    weight = 0.12 + 0.88 * np.exp(-0.5 * ((freqs - 1100.0) / 800.0) ** 2)
    y = irfft(spec * weight, n=x.size).astype(np.float32)
    t = np.arange(y.size) / sr
    y = (
        y
        * (0.55 + 0.45 * np.sin(2 * np.pi * 4.0 * t))
        * (0.6 + 0.4 * np.sin(2 * np.pi * 1.1 * t))
    ).astype(np.float32)
    y *= 0.22 / (float(np.sqrt(np.mean(np.square(y)))) + 1e-12)
    return y


def _prof(**kwargs) -> AcousticProfile:
    base = dict(
        valence=0.5,
        energy=0.5,
        bpm=120.0,
        unstable=False,
        brightness=0.5,
        flux=0.4,
        onset_rate=1.0,
        onset_consistency=0.8,
        onset_burstiness=0.2,
        variation=0.1,
        energy_mean=0.55,
        tonal=0.7,
        spectral_slope=0.6,
        sub_bass=0.05,
        window_count=3,
        pcm_samples=1000,
    )
    base.update(kwargs)
    return AcousticProfile(**base)


def test_dialogue_frame_keeps_sung_and_drops_speechy() -> None:
    # Clear period → never dialogue (sung bias).
    assert not is_dialogue_frame(
        periodicity=0.80,
        zcr=0.12,
        bands={"mid": 0.45, "high_mid": 0.2, "bass": 0.1, "low_mid": 0.1, "high": 0.1},
    )
    # Mid-heavy, speechy ZCR, weak period → dialogue.
    assert is_dialogue_frame(
        periodicity=0.12,
        zcr=0.18,
        bands={"mid": 0.40, "high_mid": 0.22, "bass": 0.08, "low_mid": 0.10, "high": 0.12},
    )
    # Unsure → keep.
    assert not is_dialogue_frame(
        periodicity=0.32,
        zcr=0.10,
        bands={"mid": 0.30, "high_mid": 0.20, "bass": 0.25, "low_mid": 0.15, "high": 0.10},
    )


def test_build_profile_drops_dialogue_frames_keeps_music() -> None:
    """Integration: speechy head + tonal tail drops some frames; all-speech keeps all."""
    speech = _soft_mid_noise(4.0, seed=2)
    music = _tone(330.0, seconds=4.0)
    mixed = np.concatenate([speech, music]).astype(np.float32)
    n = 4096
    drops = 0
    total = 0
    for start in local_analysis_windows(mixed.size, n):
        _b, _ba, flat, bands, _c, _s, zcr, _sub, harm, period = spectral_slice_ex(
            mixed, start, n
        )
        if is_dialogue_frame(
            periodicity=period, zcr=zcr, bands=bands, flatness=flat, harmonicity=harm
        ):
            drops += 1
        total += 1
    assert drops >= 1
    assert drops < total
    mixed_p = build_profile(mixed)
    assert mixed_p.window_count == total - drops
    # All-fail keep: pure speechy buffer does not shrink to zero windows.
    speech_p = build_profile(speech)
    assert speech_p.window_count == len(local_analysis_windows(speech.size, n))
    # Sung / clear tone never treated as dialogue.
    tone = _tone(220.0, seconds=3.0)
    assert frame_periodicity(tone[:4096]) >= 0.35
    assert build_profile(tone).window_count == len(local_analysis_windows(tone.size, n))


def test_sub_bass_parallel_leftover_not_in_partition() -> None:
    sr = SAMPLERATE
    sub = _tone(50.0, seconds=4.0, amp=0.45)
    mid = _tone(1200.0, seconds=4.0, amp=0.45)
    mixed = (0.55 * sub + 0.35 * _tone(2800.0, seconds=4.0, amp=1.0)).astype(np.float32)
    ps = build_profile(sub)
    pm = build_profile(mid)
    px = build_profile(mixed)
    assert "sub_bass" not in ps.band_energy
    assert set(ps.band_energy) == {"bass", "low_mid", "mid", "high_mid", "high"}
    assert abs(sum(ps.band_energy.values()) - 1.0) < 0.08
    assert ps.sub_bass > pm.sub_bass + 0.05
    assert ps.valence < pm.valence
    # Heavy sub on profile is the Shadow vote; lead+sub must carry real sub_bass.
    assert px.sub_bass > pm.sub_bass + 0.08


def test_pitch_glow_uses_f0_not_centroid_when_tonal() -> None:
    low = _tone(120.0, seconds=4.0)
    high = _tone(660.0, seconds=4.0)
    pl = build_profile(low)
    ph = build_profile(high)
    assert pl.tonal >= 0.55 and ph.tonal >= 0.55
    assert estimate_pitch_hz(high[:4096]) is not None
    assert normalize_pitch_glow(660.0) > normalize_pitch_glow(120.0)
    # Higher F0 should Glow more than low F0 when tonal enough.
    assert ph.valence > pl.valence + 0.03
    # Noisy (not tonal enough) keeps brightness path — no F0 requirement.
    rng = np.random.default_rng(3)
    noisy = (rng.standard_normal(SAMPLERATE * 4) * 0.28).astype(np.float32)
    pn = build_profile(noisy)
    assert pn.tonal < 0.55


def test_spectral_slope_zero_not_coerced() -> None:
    from meridian.features import _avg_attr, _finite_attr

    p0 = _prof(spectral_slope=0.0)
    assert _finite_attr(p0, "spectral_slope", 0.5) == 0.0
    assert _avg_attr([p0, p0], "spectral_slope", 0.5) == 0.0
    n = 4096
    freqs = np.fft.rfftfreq(n, 1.0 / SAMPLERATE)
    muffled = np.exp(-freqs / 400.0)
    clear = np.exp(-freqs / 2500.0) + 0.15
    assert spectral_slope_clarity(muffled, freqs) < spectral_slope_clarity(clear, freqs)


def test_slope_zero_muffled_stays_nearer_sticker() -> None:
    from meridian.features import analyze_audio, genre_seed

    seed = genre_seed("ambient", "pad", "x", path="/tmp/slope0.flac")
    pcm = _tone(200.0, seconds=3.0)

    def _run(slope: float):
        profile = _prof(
            valence=0.85,
            energy=0.70,
            bpm=100.0,
            onset_consistency=0.75,
            variation=0.10,
            unstable=False,
            tonal=0.55,
            spectral_slope=slope,
            brightness=0.70,
            sub_bass=0.05,
        )
        with patch(
            "meridian.features._decode_pcm_with_fallback",
            return_value=(pcm, False, None, None),
        ):
            with patch(
                "meridian.features._pcm_mood_cues",
                return_value=(0.85, 0.70, 100.0, False, profile),
            ):
                return analyze_audio("/tmp/slope0.flac", "ambient", "pad", "x", 100.0)

    move0 = abs(_run(0.0).valence - seed.valence)
    move_half = abs(_run(0.50).valence - seed.valence)
    move_soft = abs(_run(0.01).valence - seed.valence)
    # Exact 0.0 must muff (not behave like missing 0.5).
    assert move0 < move_half - 0.02
    assert abs(move0 - move_soft) < 0.03


def test_tonal_onset_blend_floor_caps_sting() -> None:
    from meridian.features import (
        SECTION_TONAL_ONSET_CAP,
        SECTION_TONAL_ONSET_FLOOR,
        _reweight_tonal_onset,
    )

    intro = _prof(tonal=0.95, onset_consistency=0.95, onset_rate=3.0, energy=0.9, valence=0.9)
    mid = _prof(tonal=0.55, onset_consistency=0.6, onset_rate=0.8, energy=0.45, valence=0.4)
    late = _prof(tonal=0.50, onset_consistency=0.55, onset_rate=0.7, energy=0.42, valence=0.38)
    base = {"intro": 0.20, "mid": 0.50, "late": 0.30}
    out = _reweight_tonal_onset(base, {"intro": intro, "mid": mid, "late": late})
    assert abs(sum(out.values()) - 1.0) < 1e-6
    assert out["intro"] <= SECTION_TONAL_ONSET_CAP + 1e-9
    assert out["mid"] >= SECTION_TONAL_ONSET_FLOOR - 1e-9
    assert out["late"] >= SECTION_TONAL_ONSET_FLOOR - 1e-9
    assert out["mid"] + out["late"] >= 0.45

    # Cap redistribute must not break the floor.
    extreme = {"intro": 0.05, "mid": 0.05, "late": 0.90}
    ex_p = {
        "intro": _prof(tonal=0.2, onset_consistency=0.2, onset_rate=0.1),
        "mid": _prof(tonal=0.2, onset_consistency=0.2, onset_rate=0.1),
        "late": _prof(tonal=0.99, onset_consistency=0.99, onset_rate=4.0),
    }
    out_x = _reweight_tonal_onset(extreme, ex_p)
    assert all(v + 1e-6 >= min(SECTION_TONAL_ONSET_FLOOR, 1.0 / 3) for v in out_x.values())
    assert out_x["late"] <= SECTION_TONAL_ONSET_CAP + 1e-9


def test_dual_window_applies_tonal_onset_floor() -> None:
    from meridian.features import _merge_pcm_profiles

    intro = _prof(
        valence=0.85, energy=0.85, tonal=0.95, onset_consistency=0.95, onset_rate=3.0, flux=0.7
    )
    mid = _prof(
        valence=0.45, energy=0.45, tonal=0.5, onset_consistency=0.55, onset_rate=0.7
    )
    dual = _merge_pcm_profiles(None, role_profiles={"intro": intro, "mid": mid})
    triple = _merge_pcm_profiles(
        None,
        role_profiles={
            "intro": intro,
            "mid": mid,
            "late": _prof(valence=0.47, energy=0.46, tonal=0.48, onset_consistency=0.52),
        },
    )
    # Dual must not crown the sting the way pre-fix merge_two did (~0.82).
    assert dual.valence < 0.70
    assert dual.energy < 0.70
    assert abs(dual.valence - triple.valence) < 0.25


def test_body_agree_intro_preserved() -> None:
    from meridian.features import SECTION_BODY_AGREE_WEIGHTS, _reweight_tonal_onset
    from meridian.features import _preserve_shrunk_intro

    intro = _prof(valence=0.85, energy=0.85, tonal=0.95, onset_consistency=0.95, onset_rate=3.0)
    mid = _prof(valence=0.45, energy=0.45, tonal=0.5, onset_consistency=0.55, onset_rate=0.7)
    late = _prof(valence=0.47, energy=0.46, tonal=0.48, onset_consistency=0.52, onset_rate=0.65)
    base = dict(SECTION_BODY_AGREE_WEIGHTS)
    boosted = _reweight_tonal_onset(base, {"intro": intro, "mid": mid, "late": late})
    preserved = _preserve_shrunk_intro(
        boosted, intro_cap=base["intro"], role_profiles={"intro": intro, "mid": mid, "late": late}
    )
    assert abs(preserved["intro"] - 0.15) < 1e-9


def test_pulse_clarity_gates_bpm_kinetic() -> None:
    from meridian.features import analyze_audio

    pcm = _tone(220.0, seconds=3.0)
    muddy = _prof(
        valence=0.45,
        energy=0.40,
        bpm=None,
        onset_consistency=0.25,
        variation=0.12,
        unstable=False,
        tonal=0.6,
        spectral_slope=0.6,
    )
    clear = _prof(
        valence=0.45,
        energy=0.40,
        bpm=160.0,
        onset_consistency=0.88,
        variation=0.08,
        unstable=False,
        tonal=0.6,
        spectral_slope=0.6,
    )

    def _run(profile: AcousticProfile):
        with patch(
            "meridian.features._decode_pcm_with_fallback",
            return_value=(pcm, False, None, None),
        ):
            with patch(
                "meridian.features._pcm_mood_cues",
                return_value=(
                    float(profile.valence),
                    float(profile.energy),
                    profile.bpm,
                    False,
                    profile,
                ),
            ):
                return analyze_audio("/tmp/honest_pulse.flac", "", "x", "y", 170.0)

    muddy_r = _run(muddy)
    clear_r = _run(clear)
    assert muddy_r.energy < clear_r.energy + 0.02
    assert clear_r.energy > muddy_r.energy + 0.02

    # No PCM: tag BPM nudge still applies (gate only when pulse was measured).
    with patch(
        "meridian.features._decode_pcm_with_fallback",
        return_value=(None, False, None, None),
    ):
        with_bpm = analyze_audio("/tmp/nopcm_pulse.flac", "", "x", "y", 170.0)
        no_bpm = analyze_audio("/tmp/nopcm_pulse.flac", "", "x", "y", None)
    assert with_bpm.energy > no_bpm.energy + 0.03


def test_keyword_nudge_zeroed_on_pcm_contradiction() -> None:
    from meridian.features import (
        _keyword_contradiction_axes,
        analyze_audio,
        genre_seed,
    )

    seed = genre_seed("", "Happy Party Joy", "Artist", path="/tmp/kw_honest.flac")
    assert seed.keyword_hit
    assert seed.keyword_base_valence is not None

    # Clip-safe: hyperpop + happy words applied contribution does not overshoot genre.
    clipped = genre_seed(
        "hyperpop", "Happy Joy Party Banger Fire Fast", "x", path="/tmp/clip_kw.flac"
    )
    undo_e = clipped.energy - clipped.keyword_de
    assert undo_e + 1e-9 >= float(clipped.keyword_base_energy) - 0.08

    cv, ce = _keyword_contradiction_axes(
        keyword_dv=float(seed.keyword_dv),
        keyword_de=float(seed.keyword_de),
        seed_valence=float(seed.valence),
        seed_energy=float(seed.energy),
        pcm_valence=0.22,
        pcm_energy=0.20,
        onset_consistency=0.80,
        unstable=False,
        variation=0.08,
        keyword_base_valence=seed.keyword_base_valence,
        keyword_base_energy=seed.keyword_base_energy,
    )
    assert cv or ce

    # Per-axis: energy-only contradiction does not strip valence words.
    cv2, ce2 = _keyword_contradiction_axes(
        keyword_dv=0.18,
        keyword_de=0.18,
        seed_valence=0.70,
        seed_energy=0.70,
        pcm_valence=0.72,  # agrees with Glow words
        pcm_energy=0.20,  # contradicts Kinetic words
        onset_consistency=0.80,
        unstable=False,
        variation=0.08,
        keyword_base_valence=0.52,
        keyword_base_energy=0.52,
    )
    assert not cv2 and ce2

    dark = _prof(
        valence=0.20,
        energy=0.18,
        bpm=90.0,
        onset_consistency=0.82,
        variation=0.08,
        unstable=False,
        tonal=0.65,
        spectral_slope=0.62,
        brightness=0.30,
    )
    pcm = _tone(110.0, seconds=3.0)
    with patch(
        "meridian.features._decode_pcm_with_fallback",
        return_value=(pcm, False, None, None),
    ):
        with patch(
            "meridian.features._pcm_mood_cues",
            return_value=(0.20, 0.18, 90.0, False, dark),
        ):
            result = analyze_audio(
                "/tmp/kw_honest.flac",
                "",
                "Happy Party Joy",
                "Artist",
                None,
            )
    assert result.valence < seed.valence - 0.05


def test_band_energy_blend_matches_sub_bass_weights() -> None:
    from meridian.features import _merge_two_pcm_profiles

    a = _prof(sub_bass=0.40, band_energy={"bass": 0.5, "low_mid": 0.2, "mid": 0.1, "high_mid": 0.1, "high": 0.1})
    b = _prof(sub_bass=0.10, band_energy={"bass": 0.1, "low_mid": 0.2, "mid": 0.3, "high_mid": 0.2, "high": 0.2})
    # Force mild path by close coords.
    a = _prof(
        valence=0.50,
        energy=0.50,
        sub_bass=0.40,
        onset_consistency=0.9,
        variation=0.05,
        band_energy={"bass": 0.5, "low_mid": 0.2, "mid": 0.1, "high_mid": 0.1, "high": 0.1},
    )
    b = _prof(
        valence=0.52,
        energy=0.51,
        sub_bass=0.10,
        onset_consistency=0.4,
        variation=0.05,
        band_energy={"bass": 0.1, "low_mid": 0.2, "mid": 0.3, "high_mid": 0.2, "high": 0.2},
    )
    m = _merge_two_pcm_profiles(a, b)
    assert "sub_bass" not in m.band_energy
    # Weighted toward stabler (a): bass share and sub_bass both lean high.
    assert m.band_energy["bass"] > 0.30
    assert m.sub_bass > 0.25


def test_tonal_onset_score_monotonic() -> None:
    weak = _prof(tonal=0.30, onset_consistency=0.2, onset_rate=0.1)
    strong = _prof(tonal=0.85, onset_consistency=0.9, onset_rate=2.5)
    assert tonal_onset_score(strong) > tonal_onset_score(weak) + 0.2


def test_pitch_holds_a_melody_and_refuses_one_weak_window() -> None:
    from meridian.acoustic import held_pitch_hz, pitch_holds

    assert pitch_holds([220.0, 222.0, 218.0], [0.72, 0.70, 0.74])
    assert pitch_holds([196.0, 247.0, 330.0, 294.0, 220.0], [0.70] * 5)
    assert pitch_holds([220.0, 440.0], [0.80, 0.80])
    assert not pitch_holds([440.0], [0.66])
    assert not pitch_holds([220.0, 224.0], [0.40, 0.42])
    assert not pitch_holds([], [])
    folded = held_pitch_hz([220.0, 440.0], [0.80, 0.80])
    assert folded is not None and abs(folded - 440.0) < 1.0


def test_beat_grid_refuses_a_shifted_pulse() -> None:
    from meridian.acoustic import BEAT_ALIGN_MIN, beat_hit_alignment

    bpm = 90.0
    period = 60.0 / bpm
    beats = [i * period for i in range(8)]
    on_grid = [b + 0.02 for b in beats]
    shifted = [b + 0.45 * period for b in beats]
    hop = 256 / SAMPLERATE
    aligned = beat_hit_alignment(on_grid, beats, bpm, hop_s=hop)
    missed = beat_hit_alignment(shifted, beats, bpm, hop_s=hop)
    assert aligned is not None and aligned >= BEAT_ALIGN_MIN
    assert missed is not None and missed < BEAT_ALIGN_MIN
    assert beat_hit_alignment(on_grid[:2], beats, bpm, hop_s=hop) is None


def test_spoken_and_faint_hits_dim_the_score_only() -> None:
    from meridian.acoustic import confidence_from_evidence

    base, note_base = confidence_from_evidence(
        tag_key="metal",
        pcm_ok=True,
        bpm_ok=True,
        variation=0.05,
        onset_consistency=0.85,
    )
    spoken, note_spoken = confidence_from_evidence(
        tag_key="metal",
        pcm_ok=True,
        bpm_ok=True,
        variation=0.05,
        onset_consistency=0.85,
        speech_share=0.8,
    )
    partial, note_partial = confidence_from_evidence(
        tag_key="metal",
        pcm_ok=True,
        bpm_ok=True,
        variation=0.05,
        onset_consistency=0.85,
        speech_share=0.2,
    )
    faint, note_faint = confidence_from_evidence(
        tag_key="metal",
        pcm_ok=True,
        bpm_ok=True,
        variation=0.05,
        onset_consistency=0.85,
        onset_strength=0.05,
    )
    strong_hits, note_strong = confidence_from_evidence(
        tag_key="metal",
        pcm_ok=True,
        bpm_ok=True,
        variation=0.05,
        onset_consistency=0.85,
        onset_strength=0.80,
    )
    assert spoken < base - 0.08
    assert "spoken" in note_spoken
    assert partial == base
    assert "spoken" not in note_partial
    assert 0.0 < base - faint <= 0.03
    assert "faint hits" in note_faint
    assert strong_hits == base
    assert "faint hits" not in note_strong
    assert "spoken" not in note_base


def test_refused_pulse_skips_the_kinetic_step_and_the_bpm_bonus() -> None:
    from unittest.mock import patch

    from meridian.features import analyze_audio

    trusted = _prof(
        valence=0.42,
        energy=0.40,
        bpm=160.0,
        onset_consistency=0.86,
        variation=0.05,
        unstable=False,
        pulse_rejected=False,
    )
    refused = _prof(
        valence=0.42,
        energy=0.40,
        bpm=160.0,
        onset_consistency=0.86,
        variation=0.05,
        unstable=False,
        pulse_rejected=True,
    )
    pcm = _tone(220.0, seconds=3.0)

    def _cues(profile):
        return (profile.valence, profile.energy, profile.bpm, profile.unstable, profile)

    with patch("meridian.features._decode_pcm_with_fallback", return_value=(pcm, False, None, None)):
        with patch("meridian.features._pcm_mood_cues", return_value=_cues(trusted)):
            kept = analyze_audio("/tmp/pulse-kept.flac", "", "t", "a", None)
        with patch("meridian.features._pcm_mood_cues", return_value=_cues(refused)):
            dropped = analyze_audio("/tmp/pulse-dropped.flac", "", "t", "a", None)
    assert kept.bpm == 160.0
    assert "BPM" in (kept.confidence_note or "")
    assert dropped.bpm is None
    assert "BPM" not in (dropped.confidence_note or "")
    assert "pulse miss" in (dropped.confidence_note or "")
    assert abs(dropped.energy - 0.40) < abs(kept.energy - 0.40)

    tagged = _prof(
        valence=0.42,
        energy=0.40,
        bpm=160.0,
        onset_consistency=0.86,
        variation=0.05,
        unstable=False,
        pulse_rejected=True,
    )
    with patch("meridian.features._decode_pcm_with_fallback", return_value=(pcm, False, None, None)):
        with patch("meridian.features._pcm_mood_cues", return_value=_cues(tagged)):
            kept_tag = analyze_audio("/tmp/pulse-tag.flac", "", "t", "a", 128.0)
    assert kept_tag.bpm == 128.0
    assert "BPM" in (kept_tag.confidence_note or "")
    assert "pulse miss" in (kept_tag.confidence_note or "")
    assert abs(kept_tag.energy - 0.40) > abs(dropped.energy - 0.40)


def test_section_merge_keeps_speech_share_and_a_refused_pulse() -> None:
    from meridian.features import _merge_two_pcm_profiles

    body = _prof(speech_share=0.0, window_count=5, bpm=100.0, pulse_rejected=False)
    intro = _prof(
        valence=0.52,
        energy=0.52,
        speech_share=1.0,
        window_count=5,
        bpm=None,
        pulse_rejected=True,
        onset_consistency=0.4,
    )
    mixed = _merge_two_pcm_profiles(body, intro)
    assert mixed.bpm == 100.0
    assert mixed.pulse_rejected is False
    assert abs(mixed.speech_share - 0.5) < 1e-6

    refused_a = _prof(bpm=None, pulse_rejected=True, window_count=3, speech_share=0.0)
    refused_b = _prof(
        valence=0.55,
        energy=0.55,
        bpm=None,
        pulse_rejected=True,
        window_count=3,
        speech_share=0.0,
        onset_consistency=0.4,
    )
    both = _merge_two_pcm_profiles(refused_a, refused_b)
    assert both.bpm is None
    assert both.pulse_rejected is True

    spoken_heavy = _prof(
        speech_share=0.8,
        heard_windows=5,
        window_count=1,
        bpm=None,
        pulse_rejected=False,
    )
    musical = _prof(
        valence=0.52,
        energy=0.52,
        speech_share=0.0,
        heard_windows=5,
        window_count=5,
        bpm=100.0,
        onset_consistency=0.4,
        onset_strength=0.9,
    )
    spoken_heavy.onset_strength = 0.05
    weighted = _merge_two_pcm_profiles(spoken_heavy, musical)
    assert abs(weighted.speech_share - 0.4) < 1e-6
    assert weighted.onset_strength is not None
    assert abs(weighted.onset_strength - 0.475) < 1e-6


def test_spectral_bands_partition_only() -> None:
    n = 4096
    frame = _tone(55.0)[:n]
    windowed = frame * np.hanning(frame.size)
    spec = np.abs(np.fft.rfft(windowed)) + 1e-12
    freqs = np.fft.rfftfreq(frame.size, 1.0 / SAMPLERATE)
    bands = spectral_bands(spec, freqs)
    assert "sub_bass" not in bands
    assert bands["bass"] > 0.5
