"""Expanded acoustic profile from one PCM buffer (section decode is orchestrated upstream).

Items 2–11: multi-band energy, spectral flux, RMS dynamics, onset stats,
brightness normalization, confidence, local-window aggregation — still projecting
onto Shadow↔Glow / Still↔Kinetic only. Intro/mid/late ~12s seeks (~36s budget on
long tracks) are orchestrated in features; this module profiles one buffer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

SAMPLERATE = 11025


@dataclass
class AcousticProfile:
    valence: float
    energy: float
    bpm: float | None
    unstable: bool
    brightness: float
    flux: float
    band_energy: dict[str, float] = field(default_factory=dict)
    energy_mean: float = 0.5
    energy_std: float = 0.0
    energy_peak: float = 0.5
    energy_range: float = 0.0
    energy_trend: float = 0.0
    onset_rate: float = 0.0
    onset_burstiness: float = 0.0
    onset_consistency: float = 0.5
    variation: float = 0.0
    window_count: int = 0
    pcm_samples: int = 0
    # Timbre leftovers (persisted upstream; not a map axis).
    tonal: float = 0.5
    timbre_source: str | None = None
    # Multi-section shape note set by features merge (steady|build|drop|bookend).
    shape_note: str | None = None
    # Placement-honesty leftovers (not drawn axes).
    # Higher = clearer/flatter spectrum; lower = muffled steep falloff.
    spectral_slope: float = 0.5
    # Energy share under ~80 Hz (one light Shadow vote upstream).
    sub_bass: float = 0.0
    zcr: float = 0.0


# Target RMS for analysis-only loudness normalization (fixes hot-master Kinetic leak).
ANALYSIS_TARGET_RMS = 0.10
# Cap boost so near-floor hiss does not explode into a fake body.
ANALYSIS_MAX_GAIN = 40.0


def normalize_analysis_loudness(
    pcm: np.ndarray, *, target_rms: float = ANALYSIS_TARGET_RMS
) -> tuple[np.ndarray, float]:
    """Uniform gain to a shared RMS target; returns ``(normalized, raw_rms)``.

    Relative dynamics (crest, hop std/mean, onset timing) are preserved under a
    single scale factor. ReplayGain stays a weak tag prior elsewhere — this is
    waveform-local only, before energy/brightness cues.
    """
    mono = np.ascontiguousarray(pcm, dtype=np.float32)
    if mono.size < 64:
        return mono, 0.0
    raw_rms = float(np.sqrt(np.mean(np.square(mono))) + 1e-12)
    if raw_rms < 1e-8:
        return mono, raw_rms
    gain = float(target_rms) / raw_rms
    gain = float(min(max(gain, 1e-3), ANALYSIS_MAX_GAIN))
    out = mono * gain
    peak = float(np.max(np.abs(out)) + 1e-12)
    # Soft ceiling only after a boost — never re-inflate a hot master.
    if peak > 1.0 and gain > 1.0:
        out = out * (1.0 / peak)
    return np.ascontiguousarray(out, dtype=np.float32), raw_rms


def infer_timbre_source(
    bands: dict[str, float],
    *,
    flatness: float,
    zcr: float,
    tonal: float,
) -> str | None:
    """Honest vocal/instrumental guess from spectrum — None when unsure."""
    mid = float(bands.get("mid", 0.2)) + float(bands.get("high_mid", 0.2))
    bass = float(bands.get("bass", 0.2)) + float(bands.get("low_mid", 0.2))
    high = float(bands.get("high", 0.2))
    flat = float(flatness)
    # Noise-like / FX: never claim vocal.
    if flat > 0.55 or tonal < 0.40:
        return "instrumental" if flat > 0.60 or tonal < 0.35 else None
    # Vocal-ish: midband presence, moderate ZCR, not bass-dominated pad.
    if (
        tonal >= 0.55
        and mid >= 0.36
        and mid >= bass * 0.80
        and flat < 0.42
        and 0.04 <= float(zcr) <= 0.28
    ):
        return "vocal"
    # Clear instrumental / harmonic body.
    if tonal >= 0.58 and flat < 0.40 and (bass >= 0.32 or high >= 0.22 or mid >= 0.30):
        return "instrumental"
    return None


def trim_silence(pcm: np.ndarray, floor: float = 0.012) -> np.ndarray:
    if pcm.size < 4096:
        return pcm
    abs_p = np.abs(pcm)
    peak = float(np.max(abs_p) + 1e-12)
    thr = max(floor * peak, 1e-4)
    idx = np.where(abs_p >= thr)[0]
    if idx.size < 2048:
        return pcm
    return pcm[int(idx[0]) : int(idx[-1]) + 1]


def spectral_bands(spec: np.ndarray, freqs: np.ndarray) -> dict[str, float]:
    """Broad band energy shares from an existing magnitude spectrum.

    Partition only (bass/low_mid/mid/high_mid/high). Sub-bass (&lt;~80 Hz) is a
    parallel leftover computed beside this dict — not a second map axis.
    """
    denom = float(spec.sum()) + 1e-12
    nyq = float(freqs[-1]) if freqs.size else 5500.0
    edges = (
        ("bass", 20.0, 250.0),
        ("low_mid", 250.0, 500.0),
        ("mid", 500.0, 2000.0),
        ("high_mid", 2000.0, min(4000.0, nyq)),
        ("high", min(4000.0, nyq), nyq + 1.0),
    )
    out: dict[str, float] = {}
    for name, lo, hi in edges:
        if hi <= lo:
            out[name] = 0.0
            continue
        mask = (freqs >= lo) & (freqs < hi)
        out[name] = float(np.clip(spec[mask].sum() / denom, 0.0, 1.0))
    return out


def sub_bass_share(spec: np.ndarray, freqs: np.ndarray) -> float:
    """Energy share under ~80 Hz — parallel leftover, not a partition band."""
    denom = float(spec.sum()) + 1e-12
    mask = (freqs >= 20.0) & (freqs < 80.0)
    if not np.any(mask):
        return 0.0
    return float(np.clip(spec[mask].sum() / denom, 0.0, 1.0))


def normalize_brightness(centroid_hz: float) -> float:
    """Bounded nonlinear map — less brittle than centroid/4200 alone."""
    c = max(0.0, float(centroid_hz))
    mapped = np.log1p(c / 700.0) / np.log1p(4500.0 / 700.0)
    return float(np.clip(mapped, 0.05, 0.95))


def spectral_slope_clarity(spec: np.ndarray, freqs: np.ndarray) -> float:
    """Map spectral tilt to a 0..1 clarity score (higher = clearer / less muffled).

    Steep negative log-power vs log-freq ⇒ muffled. Flatter ⇒ clearer. Used as a
    permission/confidence check upstream — not a new map axis.
    """
    if spec.size < 8 or freqs.size < 8:
        return 0.5
    hi = min(5000.0, float(freqs[-1]))
    mask = (freqs >= 120.0) & (freqs <= hi)
    if int(np.count_nonzero(mask)) < 8:
        return 0.5
    f = np.asarray(freqs[mask], dtype=np.float64)
    p = np.asarray(spec[mask], dtype=np.float64) + 1e-12
    x = np.log(np.maximum(f, 1.0))
    y = np.log(p)
    # One-shot least squares slope.
    x_m = float(np.mean(x))
    y_m = float(np.mean(y))
    var_x = float(np.sum((x - x_m) ** 2)) + 1e-12
    slope = float(np.sum((x - x_m) * (y - y_m)) / var_x)
    # Typical musical slopes ~−2.5 (muffled) … ~−0.4 (clear/bright).
    return float(np.clip((slope + 2.6) / 2.4, 0.0, 1.0))


def frame_zcr(frame: np.ndarray) -> float:
    """Zero-crossing rate for one short frame."""
    if frame.size < 4:
        return 0.0
    return float(np.mean(np.abs(np.diff(np.sign(frame)))) / 2.0)


def frame_harmonicity(spec: np.ndarray, freqs: np.ndarray) -> float:
    """0..1 peakiness in the voiced band — high for sung/tones, low for noise/speech.

    Decoupled from spectral flatness so dialogue gating is not deadlocked when
    ``tonal = 1 - 0.85·flat``. Prefer ``frame_periodicity`` for dialogue keep/drop.
    """
    if spec.size < 8 or freqs.size < 8:
        return 0.5
    mask = (freqs >= 80.0) & (freqs <= 4000.0)
    if int(np.count_nonzero(mask)) < 8:
        return 0.5
    mag = np.asarray(spec[mask], dtype=np.float64)
    peak = float(np.max(mag))
    med = float(np.median(mag)) + 1e-12
    # Crest ~1 (noise) … tens (pure tone). Map loosely into 0..1.
    crest = peak / med
    return float(np.clip(np.log1p(crest) / np.log1p(25.0), 0.0, 1.0))


def frame_periodicity(frame: np.ndarray, samplerate: int = SAMPLERATE) -> float:
    """0..1 clear-period strength from autocorrelation (sung/tonal ≫ speech/noise)."""
    mono = np.ascontiguousarray(frame, dtype=np.float64)
    if mono.size < 64:
        return 0.0
    mono = mono - float(np.mean(mono))
    rms = float(np.sqrt(np.mean(mono * mono)) + 1e-12)
    if rms < 1e-8:
        return 0.0
    mono = mono / rms
    fmin, fmax = 80.0, 1000.0
    lag_min = max(1, int(samplerate / fmax))
    lag_max = min(mono.size // 2, int(samplerate / fmin))
    if lag_max <= lag_min + 2:
        return 0.0
    n_fft = 1
    while n_fft < 2 * mono.size:
        n_fft <<= 1
    spec = np.fft.rfft(mono, n=n_fft)
    ac = np.fft.irfft(spec * np.conj(spec), n=n_fft)[: mono.size]
    if ac[0] <= 1e-12:
        return 0.0
    ac = ac / ac[0]
    region = ac[lag_min : lag_max + 1]
    if region.size < 3:
        return 0.0
    peak_val = float(np.max(region))
    return float(np.clip(peak_val, 0.0, 1.0))


def estimate_pitch_hz(
    frame: np.ndarray, samplerate: int = SAMPLERATE, *, fmin: float = 80.0, fmax: float = 1000.0
) -> float | None:
    """Lightweight autocorrelation F0 proxy — None when no clear period."""
    mono = np.ascontiguousarray(frame, dtype=np.float64)
    if mono.size < int(samplerate / fmin) + 16:
        return None
    mono = mono - float(np.mean(mono))
    rms = float(np.sqrt(np.mean(mono * mono)) + 1e-12)
    if rms < 1e-8:
        return None
    mono = mono / rms
    lag_min = max(1, int(samplerate / fmax))
    lag_max = min(mono.size // 2, int(samplerate / fmin))
    if lag_max <= lag_min + 2:
        return None
    # Biased autocorr via FFT (same length).
    n_fft = 1
    while n_fft < 2 * mono.size:
        n_fft <<= 1
    spec = np.fft.rfft(mono, n=n_fft)
    ac = np.fft.irfft(spec * np.conj(spec), n=n_fft)[: mono.size]
    if ac[0] <= 1e-12:
        return None
    ac = ac / ac[0]
    region = ac[lag_min : lag_max + 1]
    if region.size < 3:
        return None
    peak_i = int(np.argmax(region))
    peak_val = float(region[peak_i])
    # Require a clear periodic peak (sung/tonal); reject noise.
    if peak_val < 0.35:
        return None
    lag = lag_min + peak_i
    if lag <= 0:
        return None
    hz = float(samplerate) / float(lag)
    if not (fmin <= hz <= fmax) or not np.isfinite(hz):
        return None
    return hz


def normalize_pitch_glow(pitch_hz: float) -> float:
    """Map F0 into Glow contribution — higher pitch → more Glow."""
    hz = max(80.0, float(pitch_hz))
    mapped = np.log1p((hz - 80.0) / 120.0) / np.log1p((800.0 - 80.0) / 120.0)
    return float(np.clip(mapped, 0.05, 0.95))


def is_dialogue_frame(
    *,
    periodicity: float,
    zcr: float,
    bands: dict[str, float],
    flatness: float | None = None,
    harmonicity: float | None = None,
) -> bool:
    """True only when a local frame is clearly spoken dialogue, not music.

    Keep when a clear period is present (sung/tonal). Bias toward keeping when
    unsure. ``harmonicity`` is accepted for back-compat but periodicity wins.
    """
    period = float(periodicity)
    # Sung / clear pitched body → keep (never treat sung as speech).
    if period >= 0.35:
        return False
    # Optional: very smooth + peaky spectrum without a strong period still keep.
    if harmonicity is not None and float(harmonicity) >= 0.72 and period >= 0.22:
        return False
    if flatness is not None and float(flatness) < 0.18 and period >= 0.25:
        return False
    mid = float(bands.get("mid", 0.2)) + float(bands.get("high_mid", 0.2))
    bass = float(bands.get("bass", 0.2)) + float(bands.get("low_mid", 0.2))
    z = float(zcr)
    # Spoken-like: mid presence, speechy ZCR, weak period.
    if mid >= 0.36 and mid >= bass * 0.85 and period < 0.28 and 0.06 <= z <= 0.42:
        return True
    return False


def tonal_onset_score(profile: object) -> float:
    """0..1 quality: real tonality × real onset structure (section blend weight)."""
    tonal_raw = getattr(profile, "tonal", None)
    tonal = float(tonal_raw) if tonal_raw is not None else 0.5
    if not np.isfinite(tonal):
        tonal = 0.5
    oc_raw = getattr(profile, "onset_consistency", None)
    onset_c = float(oc_raw) if oc_raw is not None else 0.5
    if not np.isfinite(onset_c):
        onset_c = 0.5
    or_raw = getattr(profile, "onset_rate", None)
    onset_r = float(or_raw) if or_raw is not None else 0.0
    if not np.isfinite(onset_r):
        onset_r = 0.0
    tonal_q = float(np.clip((tonal - 0.32) / 0.50, 0.0, 1.0))
    onset_q = float(
        np.clip(0.55 * onset_c + 0.45 * float(np.clip(onset_r / 2.8, 0.0, 1.0)), 0.0, 1.0)
    )
    return float(np.clip(tonal_q * onset_q, 0.0, 1.0))


def _empty_partition_bands() -> dict[str, float]:
    return {k: 0.2 for k in ("bass", "low_mid", "mid", "high_mid", "high")}


def spectral_slice_ex(
    pcm: np.ndarray, start: int, n: int, samplerate: int = SAMPLERATE
) -> tuple[float, float, float, dict[str, float], float, float, float, float, float, float]:
    """bright, bass, flatness, bands, centroid, slope, zcr, sub_bass, harmonicity, periodicity."""
    end = min(start + n, pcm.size)
    if end - start < n // 2:
        start = max(0, pcm.size - n)
        end = pcm.size
    frame = pcm[start:end]
    if frame.size < 64:
        return 0.5, 0.35, 0.3, _empty_partition_bands(), 1500.0, 0.5, 0.0, 0.05, 0.5, 0.0
    if frame.size < n:
        pad = np.zeros(n, dtype=np.float32)
        pad[: frame.size] = frame
        frame = pad
    else:
        frame = frame[:n]
    windowed = frame * np.hanning(frame.size)
    spec = np.abs(np.fft.rfft(windowed)) + 1e-12
    freqs = np.fft.rfftfreq(frame.size, 1.0 / samplerate)
    denom = float(spec.sum()) + 1e-12
    centroid = float((freqs * spec).sum() / denom)
    bright = normalize_brightness(centroid)
    bass = float(np.clip(spec[freqs < 250.0].sum() / denom, 0.0, 1.0))
    power = np.square(spec)
    flatness = float(np.exp(np.mean(np.log(power))) / (float(np.mean(power)) + 1e-12))
    flatness = float(np.clip(flatness, 0.0, 1.0))
    bands = spectral_bands(spec, freqs)
    slope = spectral_slope_clarity(spec, freqs)
    zcr = frame_zcr(frame)
    sub = sub_bass_share(spec, freqs)
    harm = frame_harmonicity(spec, freqs)
    period = frame_periodicity(frame, samplerate)
    return bright, bass, flatness, bands, centroid, slope, zcr, sub, harm, period


def spectral_slice(
    pcm: np.ndarray, start: int, n: int, samplerate: int = SAMPLERATE
) -> tuple[float, float, float, dict[str, float], float]:
    """bright, bass_share, flatness, bands, centroid_hz for one window."""
    bright, bass, flatness, bands, centroid, *_rest = spectral_slice_ex(
        pcm, start, n, samplerate
    )
    return bright, bass, flatness, bands, centroid


def spectral_flux(pcm: np.ndarray, samplerate: int = SAMPLERATE) -> float:
    """Mean positive spectral flux over hops, normalized by prior frame energy.

    Relative flux keeps steady tones low and avoids absolute-sum saturation on
    loud or broadband material (chirp/noise no longer both pin near 0.95).
    """
    win_s = 512
    hop_s = 256
    if pcm.size < win_s * 2:
        return 0.35
    mono = np.ascontiguousarray(pcm, dtype=np.float32)
    window = np.hanning(win_s).astype(np.float32)
    prev: np.ndarray | None = None
    fluxes: list[float] = []
    for i in range(0, mono.size - win_s + 1, hop_s):
        frame = mono[i : i + win_s] * window
        spec = np.abs(np.fft.rfft(frame))
        if prev is not None:
            diff = spec - prev
            pos = float(np.sum(diff[diff > 0.0]))
            denom = float(np.sum(prev)) + 1e-12
            fluxes.append(pos / denom)
        prev = spec
    if not fluxes:
        return 0.35
    raw = float(np.mean(fluxes))
    # raw ~0 for steady tones, ~0.1 musical change, ~0.3 broadband noise
    mapped = np.log1p(raw * 10.0) / np.log1p(10.0 * 0.55)
    return float(np.clip(mapped, 0.05, 0.95))


def energy_stats(pcm: np.ndarray) -> dict[str, float]:
    """RMS-derived dynamics from existing PCM (no extra decode)."""
    mono = np.ascontiguousarray(pcm, dtype=np.float32)
    if mono.size < 512:
        return {
            "mean": 0.4,
            "std": 0.0,
            "peak": 0.4,
            "range": 0.0,
            "trend": 0.0,
            "consistency": 0.5,
        }
    hop = max(256, int(SAMPLERATE * 0.2))
    win = hop * 2
    vals: list[float] = []
    for i in range(0, mono.size - win + 1, hop):
        chunk = mono[i : i + win]
        vals.append(float(np.sqrt(np.mean(np.square(chunk)) + 1e-12)))
    if not vals:
        vals = [float(np.sqrt(np.mean(np.square(mono)) + 1e-12))]
    arr = np.asarray(vals, dtype=np.float64)
    mean = float(np.mean(arr))
    std = float(np.std(arr))
    peak = float(np.max(arr))
    lo = float(np.min(arr))
    rng = float(peak - lo)
    consistency = float(np.clip(1.0 - (std / (mean + 1e-6)), 0.0, 1.0))
    mid = len(arr) // 2
    trend = 0.0
    if mid > 0:
        trend = float(
            np.clip(
                (float(np.mean(arr[mid:])) - float(np.mean(arr[:mid]))) / (mean + 1e-6),
                -1.0,
                1.0,
            )
        )
    mean_n = float(np.clip(np.log10(mean * 40 + 1e-6) / 1.6 + 0.55, 0.04, 0.96))
    peak_n = float(np.clip(np.log10(peak * 40 + 1e-6) / 1.6 + 0.55, 0.04, 0.96))
    return {
        "mean": mean_n,
        "std": float(np.clip(std / (mean + 1e-6), 0.0, 1.5)),
        "peak": peak_n,
        "range": float(np.clip(rng / (mean + 1e-6), 0.0, 2.0)),
        "trend": trend,
        "consistency": consistency,
    }


def onset_stats(pcm: np.ndarray, samplerate: int = SAMPLERATE) -> tuple[float | None, dict[str, float]]:
    """aubio onset/tempo pass — keep timing stats from the existing hop walk."""
    try:
        import aubio
    except ImportError:
        return None, {
            "kinetic": 0.5,
            "rate": 0.0,
            "burstiness": 0.0,
            "consistency": 0.5,
            "density": 0.0,
        }

    win_s = 512
    hop_s = 256
    if pcm.size < hop_s * 4:
        return None, {
            "kinetic": 0.5,
            "rate": 0.0,
            "burstiness": 0.0,
            "consistency": 0.5,
            "density": 0.0,
        }

    mono = np.ascontiguousarray(pcm, dtype=np.float32)
    tempo_o = aubio.tempo("default", win_s, hop_s, samplerate)
    onset_o = aubio.onset("default", win_s, hop_s, samplerate)
    onset_times: list[float] = []
    for i in range(0, mono.size - hop_s + 1, hop_s):
        frame = mono[i : i + hop_s]
        if onset_o(frame):
            onset_times.append(i / float(samplerate))
        tempo_o(frame)

    duration_s = max(mono.size / float(samplerate), 1e-3)
    rate = len(onset_times) / duration_s
    kinetic = float(np.clip((rate - 0.35) / 3.4, 0.05, 0.95))

    burstiness = 0.0
    consistency = 0.5
    if len(onset_times) >= 3:
        intervals = np.diff(np.asarray(onset_times, dtype=np.float64))
        intervals = intervals[intervals > 1e-4]
        if intervals.size >= 2:
            mu = float(np.mean(intervals))
            sig = float(np.std(intervals))
            burstiness = float(np.clip(sig / (mu + 1e-6), 0.0, 2.0) / 2.0)
            consistency = float(np.clip(1.0 - burstiness, 0.0, 1.0))

    density = 0.0
    if onset_times:
        bins = max(1, int(np.ceil(duration_s)))
        counts = np.zeros(bins, dtype=np.float64)
        for t in onset_times:
            counts[min(bins - 1, int(t))] += 1.0
        density = float(np.clip(np.mean(counts) / 4.0, 0.0, 1.0))

    bpm = float(tempo_o.get_bpm())
    bpm_out: float | None
    # Require real onset evidence — aubio often invents ~60–90 BPM on silence.
    if (
        len(onset_times) < 3
        or rate < 0.25
        or not np.isfinite(bpm)
        or bpm < 40.0
        or bpm > 220.0
    ):
        bpm_out = None
    else:
        bpm_out = bpm

    return bpm_out, {
        "kinetic": kinetic,
        "rate": float(rate),
        "burstiness": float(np.clip(burstiness, 0.0, 1.0)),
        "consistency": consistency,
        "density": density,
    }


def _mean_bands(band_list: list[dict[str, float]]) -> dict[str, float]:
    if not band_list:
        return _empty_partition_bands()
    keys = ("bass", "low_mid", "mid", "high_mid", "high")
    return {k: float(np.mean([float(b.get(k, 0.2)) for b in band_list])) for k in keys}


def aggregate_window_metrics(
    bright: list[float],
    bass: list[float],
    flat: list[float],
    bands: list[dict[str, float]],
) -> tuple[dict[str, float], dict[str, float]]:
    """Robust overall + variation across local windows inside one decode."""
    if not bright:
        return (
            {
                "bright": 0.5,
                "bass": 0.35,
                "flat": 0.3,
                "bright_std": 0.0,
                "bass_std": 0.0,
                "variation": 0.0,
                "unstable": 0.0,
            },
            _empty_partition_bands(),
        )
    b = np.asarray(bright, dtype=np.float64)
    ba = np.asarray(bass, dtype=np.float64)
    fl = np.asarray(flat, dtype=np.float64)
    bright_m = float(np.median(b))
    bass_m = float(np.median(ba))
    flat_m = float(np.median(fl))
    bright_std = float(np.std(b))
    bass_std = float(np.std(ba))
    variation = float(np.clip(0.55 * bright_std + 0.45 * bass_std, 0.0, 1.0))
    unstable = 1.0 if (bright_std > 0.22 or bass_std > 0.24 or abs(float(b[0] - b[-1])) > 0.28) else 0.0
    return (
        {
            "bright": bright_m,
            "bass": bass_m,
            "flat": flat_m,
            "bright_std": bright_std,
            "bass_std": bass_std,
            "variation": variation,
            "unstable": unstable,
        },
        _mean_bands(bands),
    )


def local_analysis_windows(pcm_size: int, n: int) -> list[int]:
    """Several FFT starts along the already-decoded buffer (not track timeline %)."""
    max_start = max(0, int(pcm_size) - n)
    if max_start <= 0:
        return [0]
    fracs = (0.08, 0.28, 0.50, 0.72, 0.90)
    starts = sorted({int(frac * max_start) for frac in fracs})
    return starts if starts else [0]


def build_profile(pcm: np.ndarray, samplerate: int = SAMPLERATE) -> AcousticProfile:
    """Expanded acoustic profile → still projects to valence/energy only."""
    pcm = trim_silence(np.ascontiguousarray(pcm, dtype=np.float32))
    # Loudness-normalize before energy/brightness so hot masters (OC ReMix-style)
    # do not leak into Kinetic via absolute RMS. Audibility still uses raw level.
    pcm_n, raw_rms = normalize_analysis_loudness(pcm)
    n = min(4096, int(pcm_n.size))
    brights: list[float] = []
    basses: list[float] = []
    flats: list[float] = []
    band_list: list[dict[str, float]] = []
    centroids: list[float] = []
    slopes: list[float] = []
    zcrs_w: list[float] = []
    subs: list[float] = []
    frame_starts: list[int] = []
    keep_mask: list[bool] = []
    for start in local_analysis_windows(pcm_n.size, n):
        bright, bass, flat, bands, centroid, slope, zcr_w, sub, harm, period = spectral_slice_ex(
            pcm_n, start, n, samplerate
        )
        dialogue = is_dialogue_frame(
            periodicity=period,
            zcr=zcr_w,
            bands=bands,
            flatness=flat,
            harmonicity=harm,
        )
        brights.append(bright)
        basses.append(bass)
        flats.append(flat)
        band_list.append(bands)
        centroids.append(centroid)
        slopes.append(slope)
        zcrs_w.append(zcr_w)
        subs.append(sub)
        frame_starts.append(start)
        keep_mask.append(not dialogue)

    # Drop dialogue frames when at least one musical frame survives; else keep all.
    if any(keep_mask) and not all(keep_mask):
        brights = [v for v, k in zip(brights, keep_mask) if k]
        basses = [v for v, k in zip(basses, keep_mask) if k]
        flats = [v for v, k in zip(flats, keep_mask) if k]
        band_list = [v for v, k in zip(band_list, keep_mask) if k]
        centroids = [v for v, k in zip(centroids, keep_mask) if k]
        slopes = [v for v, k in zip(slopes, keep_mask) if k]
        zcrs_w = [v for v, k in zip(zcrs_w, keep_mask) if k]
        subs = [v for v, k in zip(subs, keep_mask) if k]
        frame_starts = [v for v, k in zip(frame_starts, keep_mask) if k]

    agg, bands_m = aggregate_window_metrics(brights, basses, flats, band_list)
    bright = float(agg["bright"])
    flatness = float(agg["flat"])
    variation = float(agg["variation"])
    unstable = bool(agg["unstable"] > 0.5)
    slope_clarity = float(np.median(slopes)) if slopes else 0.5
    sub_bass = float(np.median(subs)) if subs else 0.0

    flux = spectral_flux(pcm_n, samplerate)
    est = energy_stats(pcm_n)
    detected_bpm, ostats = onset_stats(pcm_n, samplerate)

    # Whole-buffer ZCR for Kinetic + timbre (window ZCR is dialogue-only).
    zcr = (
        float(np.mean(np.abs(np.diff(np.sign(pcm_n)))) / 2.0) if pcm_n.size > 2 else 0.0
    )
    kinetic_zcr = float(np.clip(zcr * 3.2, 0.05, 0.95))

    peak = float(np.max(np.abs(pcm_n)) + 1e-12) if pcm_n.size else 1e-12
    rms = float(np.sqrt(np.mean(np.square(pcm_n))) + 1e-12) if pcm_n.size else 1e-12
    crest = peak / rms
    crest_n = float(np.clip((np.log10(crest) - 0.25) / 1.15, 0.05, 0.95))

    # Quiet / near-floor material: broadband hiss looks "bright" but shouldn't Glow.
    # audibility→0 collapses brightness and band_glow toward neutral (from raw RMS).
    audibility = float(np.clip(raw_rms / 0.045, 0.0, 1.0))
    bright_v = 0.50 + (bright - 0.50) * audibility

    warm = float(bands_m.get("bass", 0.2) + bands_m.get("low_mid", 0.2))
    present = float(bands_m.get("high_mid", 0.2) + bands_m.get("high", 0.2))
    mid = float(bands_m.get("mid", 0.2))
    band_glow = float(np.clip(0.5 + 0.55 * (present - warm), 0.05, 0.95))
    band_glow_v = 0.50 + (band_glow - 0.50) * audibility

    # Tonal vs noisy: high flatness (noise-like) pulls Glow down; clear tones keep Glow.
    tonal = float(np.clip(1.0 - 0.85 * flatness, 0.05, 0.95))
    noise_pull = float(np.clip((flatness - 0.35) / 0.45, 0.0, 1.0))
    timbre_source = infer_timbre_source(
        bands_m, flatness=flatness, zcr=zcr, tonal=tonal
    )

    # Pitch for Glow only when the frame is tonal enough; else brightness path.
    # Real autocorrelation F0 proxy — not centroid-as-pitch.
    if tonal >= 0.55:
        pitch_vals: list[float] = []
        for start in frame_starts or local_analysis_windows(pcm_n.size, n):
            end = min(start + n, pcm_n.size)
            chunk = pcm_n[start:end]
            hz = estimate_pitch_hz(chunk, samplerate)
            if hz is not None:
                pitch_vals.append(hz)
        if pitch_vals:
            pitch_v = normalize_pitch_glow(float(np.median(pitch_vals)))
            pitch_v = 0.50 + (pitch_v - 0.50) * audibility
            bright_term = pitch_v
        else:
            # Tonal but no clear F0 — keep brightness path rather than inventing pitch.
            bright_term = bright_v
    else:
        bright_term = bright_v

    # Sub-bass (<~80 Hz): one light Shadow vote — bright lead over heavy sub
    # must not land fully Glow. No new axis.
    sub_shadow = float(np.clip(sub_bass / 0.35, 0.0, 1.0))

    # band_glow already encodes bass/low-mid darkness — do not also use (1-bass).
    valence = float(
        np.clip(
            0.40 * bright_term
            + 0.28 * band_glow_v
            + 0.24 * tonal
            + 0.08 * float(np.clip(0.5 + 0.4 * (mid - warm), 0.05, 0.95))
            - 0.06 * noise_pull * audibility
            - 0.07 * sub_shadow * audibility,
            0.03,
            0.97,
        )
    )

    # Base Kinetic from rate-like cues — burstiness is texture, not a primary driver.
    # Keep ZCR light: high-frequency tones cross zero often without being more Kinetic.
    kinetic_base = float(
        np.clip(
            0.12 * kinetic_zcr
            + 0.40 * float(ostats["kinetic"])
            + 0.28 * flux
            + 0.14 * float(np.clip(ostats["density"], 0.05, 0.95))
            + 0.06 * float(np.clip(est["std"], 0.0, 1.0)),
            0.05,
            0.95,
        )
    )
    # Burstiness modulates Kinetic only when onset structure is trustworthy.
    # Unstable / high-variation windows (intro/drop, shaky PCM) get almost no burst lift.
    burst = float(np.clip(ostats["burstiness"], 0.0, 1.0))
    onset_c_local = float(np.clip(ostats["consistency"], 0.0, 1.0))
    burst_gate = onset_c_local
    if unstable or variation > 0.28:
        burst_gate *= 0.25
    elif variation > 0.18:
        burst_gate *= 0.55
    # ~±12% texture around the base when gated; irregular ambient won't fake energy.
    burst_mod = 1.0 + 0.12 * burst_gate * (burst - 0.35)
    kinetic = float(np.clip(kinetic_base * burst_mod, 0.05, 0.95))
    # Near-floor hiss: loudness-norm can invent mid Kinetic — pull motion toward
    # Still using the same raw-RMS audibility gate that hushes Glow.
    motion_scale = float(np.clip(0.15 + 0.85 * audibility, 0.15, 1.0))
    kinetic = float(np.clip(0.12 + (kinetic - 0.12) * motion_scale, 0.05, 0.95))
    # Brightness belongs on Shadow↔Glow only — keep energy Still↔Kinetic.
    # Motion/activity dominates Y; loudness is supporting texture (not "loud = Kinetic").
    # After loudness-norm, est mean/peak are relative — still keep them light vs onset/flux.
    # High onset consistency + high RMS consistency = controlled Kinetic: dampen
    # irregular/dynamic loudness cues without pushing a steady loop toward Still.
    dyn_from_consistency = float(np.clip(1.0 - float(est["consistency"]), 0.0, 1.0))
    peak_n = float(np.clip(est["peak"], 0.05, 0.95))
    rms_c = float(np.clip(est["consistency"], 0.0, 1.0))
    control = float(np.clip(onset_c_local * rms_c, 0.0, 1.0))
    loud_scale = 1.0 - 0.40 * control
    dyn_scale = 1.0 - 0.55 * control
    energy = float(
        np.clip(
            0.60 * kinetic
            + 0.10 * loud_scale * float(est["mean"])
            + 0.08 * loud_scale * crest_n
            + 0.06 * dyn_scale * float(np.clip(est["range"] / 2.0, 0.0, 1.0))
            + 0.06 * float(np.clip(0.5 + 0.35 * est["trend"], 0.05, 0.95))
            + 0.06 * dyn_scale * dyn_from_consistency
            + 0.04 * loud_scale * peak_n,
            0.03,
            0.97,
        )
    )
    energy = float(np.clip(0.12 + (energy - 0.12) * motion_scale, 0.03, 0.97))

    # np.clip does not scrub NaN — never leak non-finite moods.
    if not np.isfinite(valence):
        valence = 0.5
    if not np.isfinite(energy):
        energy = 0.5
    valence = float(np.clip(valence, 0.03, 0.97))
    energy = float(np.clip(energy, 0.03, 0.97))

    return AcousticProfile(
        valence=valence,
        energy=energy,
        bpm=detected_bpm,
        unstable=unstable or variation > 0.35,
        brightness=bright,
        flux=flux,
        band_energy={k: float(v) for k, v in bands_m.items()},
        energy_mean=float(est["mean"]),
        energy_std=float(est["std"]),
        energy_peak=float(est["peak"]),
        energy_range=float(est["range"]),
        energy_trend=float(est["trend"]),
        onset_rate=float(ostats["rate"]),
        onset_burstiness=float(ostats["burstiness"]),
        onset_consistency=float(ostats["consistency"]),
        variation=variation,
        window_count=len(brights),
        pcm_samples=int(pcm_n.size),
        tonal=tonal,
        timbre_source=timbre_source,
        spectral_slope=float(np.clip(slope_clarity, 0.0, 1.0)),
        sub_bass=float(np.clip(sub_bass, 0.0, 1.0)),
        zcr=float(zcr),
    )


def confidence_from_evidence(
    *,
    tag_key: str | None = None,
    path_key: str | None = None,
    pcm_ok: bool = False,
    pcm_fallback: bool = False,
    pcm_unstable: bool = False,
    bpm_ok: bool = False,
    bpm_conflict: bool = False,
    replaygain: bool = False,
    keyword_hit: bool = False,
    weak_tags: bool = False,
    jitter: bool = False,
    variation: float = 0.0,
    flux: float | None = None,
    onset_consistency: float | None = None,
    genre_conflict: bool = False,
    multi_window: bool = False,
) -> tuple[float, str]:
    """Graduated confidence: consistent evidence up, conflict/unstable down."""
    score = 0.0
    reasons: list[str] = []
    if tag_key:
        score += 0.40 if pcm_ok else 0.30
        reasons.append(f"tag:{tag_key}")
    if path_key:
        if tag_key:
            if genre_conflict:
                score -= 0.10
                reasons.append("path conflict")
            else:
                score += 0.08
                reasons.append("path agrees")
        else:
            score += 0.12 if pcm_ok else 0.15
            reasons.append(f"path:{path_key}")
    if pcm_ok:
        if pcm_fallback or pcm_unstable:
            score += 0.12
            reasons.append("PCM weak" if pcm_unstable else "PCM fallback")
        elif tag_key or path_key:
            score += 0.30
            reasons.append("PCM")
        else:
            score += 0.50
            reasons.append("PCM only")
        if multi_window:
            # Note only — no score jitter (tidy/spread thresholds must not move on the flag).
            reasons.append("multi-window")
        if variation > 0.28:
            score -= float(np.clip((variation - 0.28) * 0.35, 0.0, 0.12))
            reasons.append("unstable spectrum")
        elif variation < 0.10 and not pcm_unstable:
            score += 0.03
            reasons.append("stable spectrum")
        if onset_consistency is not None:
            if onset_consistency > 0.7:
                score += 0.03
            elif onset_consistency < 0.35:
                score -= 0.03
                reasons.append("uneven rhythm")
        if flux is not None and flux > 0.85:
            score -= 0.02
    if bpm_conflict:
        score -= 0.04
        reasons.append("BPM conflict")
    elif bpm_ok:
        score += 0.08
        reasons.append("BPM")
    if replaygain:
        score += 0.04
        reasons.append("ReplayGain")
    if keyword_hit:
        score += 0.03
        reasons.append("keywords")
    if weak_tags:
        score -= 0.08
        reasons.append("weak-tag format")
    if jitter or (not pcm_ok and not tag_key and not path_key):
        score = min(score, 0.20)
        if jitter:
            reasons.append("jitter")
        elif not reasons:
            reasons.append("no evidence")
    score = float(np.clip(score, 0.0, 1.0))
    return score, " · ".join(reasons)
