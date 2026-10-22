from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from mutagen import File as MutagenFile
from mutagen.id3 import ID3NoHeaderError

AUDIO_EXTS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".wav", ".aac", ".wma", ".aiff"}

GENRE_MOOD: dict[str, tuple[float, float]] = {
    "ambient": (0.42, 0.18),
    "classical": (0.48, 0.28),
    "jazz": (0.55, 0.38),
    "blues": (0.32, 0.36),
    "soul": (0.58, 0.45),
    "r&b": (0.60, 0.48),
    "hip hop": (0.52, 0.62),
    "rap": (0.48, 0.68),
    "rock": (0.46, 0.72),
    "metal": (0.28, 0.88),
    "punk": (0.34, 0.86),
    "electronic": (0.56, 0.74),
    "edm": (0.62, 0.86),
    "techno": (0.40, 0.84),
    "house": (0.66, 0.78),
    "pop": (0.72, 0.58),
    "indie": (0.54, 0.50),
    "folk": (0.50, 0.34),
    "country": (0.58, 0.44),
    "reggae": (0.64, 0.46),
    "lofi": (0.46, 0.22),
    "lo-fi": (0.46, 0.22),
    "soundtrack": (0.50, 0.40),
    # Container / catalog labels (not acoustic identities) — see CONTAINER_SEED_KEYS.
    "video game": (0.52, 0.46),
    "game": (0.52, 0.46),
    "score": (0.50, 0.40),
    "ost": (0.50, 0.40),
    "vgm": (0.52, 0.46),
    # Aliases → same coordinates as a nearby canonical genre (longest match wins).
    "alt rock": (0.50, 0.64),
    "alternative rock": (0.50, 0.64),
    "alt-rock": (0.50, 0.64),
    "alternative": (0.52, 0.56),
    "indie rock": (0.52, 0.58),
    "indie-rock": (0.52, 0.58),
    "indie pop": (0.62, 0.52),
    "indie-pop": (0.62, 0.52),
    "post-rock": (0.44, 0.42),
    "post rock": (0.44, 0.42),
    "postrock": (0.44, 0.42),
    "emo": (0.38, 0.58),
    "shoegaze": (0.40, 0.48),
    "grunge": (0.34, 0.70),
    "hard rock": (0.42, 0.80),
    "hard-rock": (0.42, 0.80),
    "progressive rock": (0.48, 0.62),
    "prog": (0.48, 0.62),
    "prog-rock": (0.48, 0.62),
    "prog rock": (0.48, 0.62),
    "death metal": (0.22, 0.92),
    "black metal": (0.20, 0.90),
    "heavy metal": (0.30, 0.88),
    "metalcore": (0.30, 0.90),
    "hardcore": (0.32, 0.88),
    "drum and bass": (0.48, 0.88),
    "dnb": (0.48, 0.88),
    "jungle": (0.46, 0.86),
    "dubstep": (0.40, 0.84),
    "trap": (0.50, 0.76),
    "synthwave": (0.58, 0.60),
    "synth-wave": (0.58, 0.60),
    "synthpop": (0.66, 0.62),
    "electro": (0.58, 0.76),
    "trance": (0.60, 0.82),
    "dub": (0.52, 0.40),
    "dancehall": (0.62, 0.70),
    "reggaeton": (0.64, 0.72),
    "funk": (0.68, 0.66),
    "disco": (0.74, 0.70),
    "gospel": (0.70, 0.50),
    "rnb": (0.60, 0.48),
    "rhythm and blues": (0.60, 0.48),
    "hip-hop": (0.52, 0.62),
    "hiphop": (0.52, 0.62),
    "k-pop": (0.74, 0.68),
    "kpop": (0.74, 0.68),
    "j-pop": (0.72, 0.60),
    "anime": (0.62, 0.58),
    "acoustic": (0.54, 0.30),
    "singer-songwriter": (0.52, 0.32),
    "americana": (0.56, 0.40),
    "bluegrass": (0.58, 0.48),
    "new age": (0.50, 0.20),
    "downtempo": (0.48, 0.28),
    "chillout": (0.56, 0.26),
    "idm": (0.44, 0.58),
    "industrial": (0.28, 0.82),
    "gothic": (0.30, 0.55),
    "goth": (0.30, 0.55),
    "ska": (0.66, 0.68),
    "latin": (0.66, 0.58),
    "afrobeats": (0.68, 0.72),
    "afrobeat": (0.68, 0.72),
    # Contemporary / long-tail tags (local seeds only — longest match wins).
    "phonk": (0.36, 0.78),
    "drift phonk": (0.34, 0.80),
    "hyperpop": (0.78, 0.82),
    "digicore": (0.72, 0.76),
    "drill": (0.34, 0.80),
    "uk drill": (0.32, 0.78),
    "jersey club": (0.70, 0.84),
    "baile funk": (0.72, 0.86),
    "funk carioca": (0.72, 0.86),
    "breakcore": (0.42, 0.92),
    "jungle terror": (0.48, 0.88),
    "vaporwave": (0.48, 0.24),
    "future funk": (0.70, 0.58),
    "city pop": (0.72, 0.52),
    "citypop": (0.72, 0.52),
    "pluggnb": (0.56, 0.48),
    "plugg": (0.54, 0.50),
    "rage": (0.44, 0.88),
    "opium": (0.40, 0.82),
    "dream pop": (0.58, 0.36),
    "dreampop": (0.58, 0.36),
    "math rock": (0.50, 0.66),
    "post-punk": (0.36, 0.62),
    "post punk": (0.36, 0.62),
    "garage rock": (0.44, 0.74),
    "psychedelic": (0.48, 0.52),
    "psych": (0.48, 0.52),
    "boom bap": (0.46, 0.58),
    "cloud rap": (0.50, 0.42),
    "detroit techno": (0.38, 0.86),
    "minimal techno": (0.36, 0.78),
    "deep house": (0.62, 0.68),
    "afro house": (0.66, 0.74),
    "amapiano": (0.64, 0.70),
    "kwaito": (0.62, 0.66),
    "soca": (0.74, 0.78),
    "compas": (0.68, 0.60),
    "highlife": (0.70, 0.58),
    "bossa nova": (0.62, 0.32),
    "bossa": (0.62, 0.32),
    "mpb": (0.60, 0.42),
    "cumbia": (0.66, 0.62),
    "tango": (0.44, 0.48),
    "flamenco": (0.52, 0.56),
    "grime": (0.40, 0.82),
    "garage": (0.52, 0.76),
    "uk garage": (0.54, 0.78),
    "2-step": (0.56, 0.74),
    "footwork": (0.48, 0.90),
    "juke": (0.50, 0.88),
}

# Lowercase alias table once (match path always lowercases haystacks).
GENRE_MOOD = {k.lower(): v for k, v in GENRE_MOOD.items()}

# Collision tokens (blue/black/light/sun) intentionally omitted.
WORD_VALENCE = {
    "sad": -0.28,
    "dark": -0.22,
    "night": -0.10,
    "rain": -0.12,
    "lonely": -0.24,
    "grief": -0.30,
    "love": 0.22,
    "happy": 0.26,
    "joy": 0.24,
    "dream": 0.08,
    "hope": 0.16,
    "war": -0.16,
    "rage": -0.12,
    "party": 0.20,
    "chill": 0.04,
}

WORD_ENERGY = {
    "slow": -0.22,
    "chill": -0.18,
    "sleep": -0.28,
    "ambient": -0.20,
    "ballad": -0.16,
    "fast": 0.22,
    "run": 0.18,
    "fire": 0.16,
    "rage": 0.24,
    "war": 0.18,
    "club": 0.22,
    "banger": 0.26,
    # "live" omitted — too many calm live albums got a false kinetic bump.
    "acoustic": -0.14,
    "lofi": -0.18,
    "lo-fi": -0.18,
}

PCM_MAX_SHIFT = 0.16
# Tiny residual when genre+BPM are already strong — spreads neighbors without leaving the cluster.
SOFT_PCM_MAX_SHIFT = 0.09
# Evidence-gated soft widen: steady PCM that clearly disagrees with the seed.
EVIDENCE_SOFT_VALENCE_MAX = 0.14
EVIDENCE_SOFT_ENERGY_MAX = 0.12
# Strong-onset clamp widen — stays above base, below ~continent hop.
PCM_STRONG_ONSET_MAX = 0.18
KEYWORD_SHIFT_CAP = 0.18
REPLAYGAIN_ENERGY_CAP = 0.06
DEFAULT_VALENCE = 0.5
DEFAULT_ENERGY = 0.48


def _note_tokens(note: str) -> list[str]:
    return [part.strip() for part in str(note or "").split("·") if part.strip()]


def track_is_unplaced(track: object) -> bool:
    """Unheard, with no real mood sticker. Container labels are not a mood.

    A filename keyword counts. A soundtrack / game / score label does not.
    Missing ``analyzed`` means the caller already decided this row is on the map.
    """
    if getattr(track, "pinned", False):
        return False
    analyzed = getattr(track, "analyzed", None)
    if analyzed is None or bool(analyzed):
        return False
    tokens = _note_tokens(str(getattr(track, "confidence_note", "") or ""))
    if any(token == "keywords" or token.startswith("keywords ") for token in tokens):
        return False
    stickers: list[str] = []
    for token in tokens:
        for prefix in ("tag:", "path:"):
            if token.startswith(prefix):
                stickers.append(token[len(prefix) :].strip().lower())
    if any(label and label not in CONTAINER_SEED_KEYS for label in stickers):
        return False
    return True


# Dump / raw formats often have empty or junk tags — favor PCM more.
WEAK_TAG_EXTS = {".wav", ".aiff", ".aif"}
# Contextual catalog labels: useful neighborhood priors, not soft-PCM locks.
CONTAINER_SEED_KEYS = frozenset(
    {
        "soundtrack",
        "ost",
        "score",
        "game",
        "video game",
        "vgm",
    }
)
# Graduated confidence bands (UI + low_trust compat).
CONFIDENCE_LOW = 0.45
CONFIDENCE_HIGH = 0.75


def read_mood_confidence(track: object) -> float:
    """Neutral 0.5 only when confidence is missing. Exact 0 stays rock-bottom."""
    raw = getattr(track, "mood_confidence", None)
    if raw is None:
        return 0.5
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.5
    if not np.isfinite(val):
        return 0.5
    return val

# Test hook: increments whenever ffmpeg decode is attempted.
_decode_pcm_calls = 0
# Cooperative abort for AnalyzeWorker.stop / quit (kills in-flight ffmpeg).
# Set of procs — dual analyze workers may each own an ffmpeg at once.
_decode_abort = False
_decode_procs: set[subprocess.Popen] = set()
_decode_proc_lock = threading.Lock()


def request_decode_abort() -> None:
    """Stop any in-flight ffmpeg decode ASAP (analyze abort / quit).

    Clears every active-proc slot under the lock so a sequential multi-seek
    ``finally`` cannot resurrect a pointer to a process we already killed.
    """
    global _decode_abort
    _decode_abort = True
    with _decode_proc_lock:
        procs = list(_decode_procs)
        _decode_procs.clear()
    for proc in procs:
        try:
            proc.kill()
        except OSError:
            pass


def clear_decode_abort() -> None:
    global _decode_abort
    _decode_abort = False


def decode_abort_requested() -> bool:
    return bool(_decode_abort)


def _register_decode_proc(proc: subprocess.Popen) -> bool:
    """Publish *proc* as a kill target. Return False if abort already won."""
    with _decode_proc_lock:
        if _decode_abort:
            return False
        _decode_procs.add(proc)
        return True


def _clear_decode_proc(proc: subprocess.Popen) -> None:
    """Drop *proc* from the active set when its decode finishes."""
    with _decode_proc_lock:
        _decode_procs.discard(proc)


@dataclass(frozen=True, slots=True)
class MoodSeed:
    valence: float
    energy: float
    tag_key: str | None = None
    path_key: str | None = None
    keyword_hit: bool = False
    # Applied keyword contribution (after clip) so undo cannot eat genre.
    keyword_dv: float = 0.0
    keyword_de: float = 0.0
    # Pre-keyword coords (post genre/year/RG) for clip-safe undo.
    keyword_base_valence: float | None = None
    keyword_base_energy: float | None = None

    @property
    def clamp_match(self) -> bool:
        """Tag or path genre hit — used to limit PCM drag."""
        return self.tag_key is not None or self.path_key is not None

    @property
    def container_only(self) -> bool:
        """True when every matched key is a catalog/container label (OST/game/…)."""
        keys = {k for k in (self.tag_key, self.path_key) if k}
        return bool(keys) and keys <= CONTAINER_SEED_KEYS


@dataclass(frozen=True, slots=True)
class MoodResult:
    valence: float
    energy: float
    bpm: float | None
    confidence: float
    low_trust: bool
    confidence_note: str = ""
    # Optional acoustic cues persisted for ranking / album spread (no re-decode).
    onset_consistency: float | None = None
    acoustic_flux: float | None = None
    brightness: float | None = None
    # Shape / timbre / steer leftovers — queue hints, not map axes.
    shape_note: str | None = None
    timbre_flag: str | None = None  # "tonal" | "noisy"
    timbre_source: str | None = None  # "vocal" | "instrumental"
    steer_weight: float | None = None
    pcm_ok: bool = False
    # Genre/path/keyword evidence usable when waveform decode finds nothing.
    seed_evidence: bool = False

def _text(tag) -> str:
    if tag is None:
        return ""
    if isinstance(tag, (list, tuple)):
        return str(tag[0]) if tag else ""
    return str(tag)


def _text_join(tag) -> str:
    """Join multi-value tags (genre) so every entry reaches genre matching."""
    if tag is None:
        return ""
    if isinstance(tag, (list, tuple)):
        return " ".join(str(part) for part in tag if part is not None and str(part).strip())
    return str(tag)


def _parse_gain_db(raw: str, *, r128: bool = False) -> float | None:
    """Parse ReplayGain / R128 style strings into dB (negative = louder master)."""
    if not raw:
        return None
    text = str(raw).strip()
    # Opus/R128 gain is Q7.8 integer (divide by 256). Never treat bare ints as ReplayGain dB.
    if re.fullmatch(r"[+-]?\d+", text):
        try:
            q = int(text)
        except ValueError:
            return None
        if r128:
            return float(q) / 256.0
        return None
    match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*dB?", text, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        val = float(match.group(1))
    except ValueError:
        return None
    # Explicit R128 with a unit still sometimes stored as Q7.8-sized ints.
    if r128 and abs(val) > 64 and "db" not in text.lower():
        return val / 256.0
    return val


def _read_replaygain_db(audio) -> float | None:
    """Prefer track gain; fall back to album / R128. Free loudness prior when present."""
    easy_rg = ("replaygain_track_gain", "replaygain_album_gain")
    easy_r128 = ("r128_track_gain", "r128_album_gain")
    for key in easy_rg:
        parsed = _parse_gain_db(_text(audio.get(key)), r128=False)
        if parsed is not None:
            return parsed
    for key in easy_r128:
        parsed = _parse_gain_db(_text(audio.get(key)), r128=True)
        if parsed is not None:
            return parsed
    try:
        raw = getattr(audio, "tags", None)
        if raw is None:
            return None
        for key in ("REPLAYGAIN_TRACK_GAIN", "REPLAYGAIN_ALBUM_GAIN"):
            if key in raw:
                parsed = _parse_gain_db(_text_join(raw.get(key)), r128=False)
                if parsed is not None:
                    return parsed
        for key in ("R128_TRACK_GAIN", "R128_ALBUM_GAIN"):
            if key in raw:
                parsed = _parse_gain_db(_text_join(raw.get(key)), r128=True)
                if parsed is not None:
                    return parsed
        # ID3 TXXX frames
        for frame in raw.getall("TXXX") if hasattr(raw, "getall") else []:
            desc = str(getattr(frame, "desc", "") or "").lower()
            text = _text_join(frame.text if hasattr(frame, "text") else frame)
            if "replaygain_track_gain" in desc or "replaygain_album_gain" in desc:
                parsed = _parse_gain_db(text, r128=False)
                if parsed is not None:
                    return parsed
        for frame in raw.getall("TXXX") if hasattr(raw, "getall") else []:
            desc = str(getattr(frame, "desc", "") or "").lower()
            text = _text_join(frame.text if hasattr(frame, "text") else frame)
            if desc in {"r128_track_gain", "r128_album_gain"} or "r128_track_gain" in desc or "r128_album_gain" in desc:
                parsed = _parse_gain_db(text, r128=True)
                if parsed is not None:
                    return parsed
    except Exception:
        return None
    return None


def read_tags(path: str) -> dict:
    info = {
        "title": Path(path).stem,
        "artist": "",
        "album": "",
        "albumartist": "",
        "composer": "",
        "genre": "",
        "year": None,
        "bpm": None,
        "duration_ms": 0,
        "replaygain_db": None,
        "extra_text": "",
    }
    try:
        audio = MutagenFile(path, easy=True)
    except (ID3NoHeaderError, Exception):
        info["extra_text"] = _filename_mood_text(path)
        return info
    if audio is None:
        info["extra_text"] = _filename_mood_text(path)
        return info
    info["title"] = _text(audio.get("title")) or info["title"]
    info["artist"] = _text(audio.get("artist"))
    info["album"] = _text(audio.get("album"))
    info["albumartist"] = _text(
        audio.get("albumartist") or audio.get("album artist") or audio.get("performer")
    )
    info["composer"] = _text(audio.get("composer"))
    info["genre"] = _text_join(audio.get("genre"))
    date = _text(audio.get("date") or audio.get("year"))
    if date:
        match = re.search(r"(\d{4})", date)
        if match:
            info["year"] = int(match.group(1))
    bpm = _text(audio.get("bpm"))
    if bpm:
        try:
            info["bpm"] = float(str(bpm).split()[0])
        except ValueError:
            pass
    length = getattr(audio.info, "length", None)
    if length:
        info["duration_ms"] = int(length * 1000)
    info["replaygain_db"] = _read_replaygain_db(audio)

    extras: list[str] = []
    for key in ("comment", "grouping", "mood", "description", "lyrics"):
        val = _text_join(audio.get(key))
        if val:
            extras.append(val)
    # Non-easy ID3 mood / grouping frames when present.
    try:
        raw = getattr(audio, "tags", None)
        if raw is not None:
            for frame_id in ("TMOO", "GRP1", "TIT1"):
                if frame_id in raw:
                    extras.append(_text_join(raw.get(frame_id)))
            if not info["composer"] and "TCOM" in raw:
                info["composer"] = _text_join(raw.get("TCOM"))
            if not info["albumartist"] and "TPE2" in raw:
                info["albumartist"] = _text_join(raw.get("TPE2"))
    except Exception:
        pass
    extras.append(_filename_mood_text(path))
    info["extra_text"] = " ".join(x for x in extras if x).strip()
    return info


def _weak_tag_ext(path: str | None) -> bool:
    if not path:
        return False
    return Path(path).suffix.lower() in WEAK_TAG_EXTS


def _replaygain_energy_prior(gain_db: float | None) -> float:
    """Negative track gain ⇒ louder master ⇒ slight kinetic nudge."""
    if gain_db is None or not np.isfinite(float(gain_db)):
        return 0.0
    return float(np.clip(-float(gain_db) * 0.007, -REPLAYGAIN_ENERGY_CAP, REPLAYGAIN_ENERGY_CAP))


def _credit_text(albumartist: str = "", composer: str = "") -> str:
    """Composer / album artist — often cleaner than track artist (classical, VA, OST)."""
    parts: list[str] = []
    aa = (albumartist or "").strip()
    if aa and aa.lower() not in {"various artists", "various", "va", "unknown"}:
        parts.append(aa)
    comp = (composer or "").strip()
    if comp:
        parts.append(comp)
    return " ".join(parts)


def _filename_mood_text(path: str | None) -> str:
    if not path:
        return ""
    stem = Path(path).stem
    stem = re.sub(r"^\d{1,3}[\s.\-_]+", "", stem)
    stem = stem.replace("_", " ").replace(".", " ")
    return stem.strip()


def _year_prior(year: int | None) -> tuple[float, float]:
    """Older releases → slightly calmer/darker; recent → slight glow/kinetic."""
    if year is None or year < 1920 or year > 2035:
        return 0.0, 0.0
    # 0 = 1960-era, 1 = 2020-era
    t = float(np.clip((year - 1960) / 60.0, 0.0, 1.0))
    dv = (t - 0.5) * 0.06
    de = (t - 0.5) * 0.08
    return float(dv), float(de)


def _keyword_shift(text: str, skip_words: set[str] | None = None) -> tuple[float, float]:
    """Apply title/artist mood words; skip tokens already used as genre seeds."""
    blob = text.lower()
    skip = {w.lower() for w in (skip_words or ()) if w}
    expanded = set(skip)
    for w in list(skip):
        expanded.add(w.replace("-", " "))
        expanded.add(w.replace(" ", "-"))
        expanded.add(w.replace(" ", ""))
    skip = expanded
    dv = de = 0.0
    for word, delta in WORD_VALENCE.items():
        if word in skip:
            continue
        if re.search(rf"\b{re.escape(word)}\b", blob, flags=re.IGNORECASE):
            dv += delta
    for word, delta in WORD_ENERGY.items():
        if word in skip:
            continue
        if re.search(rf"\b{re.escape(word)}\b", blob, flags=re.IGNORECASE):
            de += delta
    dv = float(np.clip(dv, -KEYWORD_SHIFT_CAP, KEYWORD_SHIFT_CAP))
    de = float(np.clip(de, -KEYWORD_SHIFT_CAP, KEYWORD_SHIFT_CAP))
    return dv, de


def _match_genre_keys(haystack: str) -> list[str]:
    """All matching GENRE_MOOD keys, longest first (most specific).

    Keys must match as whole tokens (word boundaries) so short aliases like
    ost/prog/game/house do not fire inside host/program/gameplay/warehouse.
    """
    g = (haystack or "").lower()
    if not g.strip():
        return []
    hits: list[str] = []
    for key in GENRE_MOOD:
        # (?<![a-z0-9])…(?![a-z0-9]) ≈ word boundary for genre tokens incl. &/-.
        pattern = rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])"
        if re.search(pattern, g, flags=re.IGNORECASE):
            hits.append(key)
    hits.sort(key=len, reverse=True)
    # Drop shorter keys fully contained in a longer hit ("rock" under "indie rock").
    filtered: list[str] = []
    for key in hits:
        if any(key != other and key in other for other in filtered):
            continue
        filtered.append(key)
    return filtered


def _match_genre_key(haystack: str) -> str | None:
    keys = _match_genre_keys(haystack)
    return keys[0] if keys else None


def _blend_genre_pairs(keys: list[str]) -> tuple[float, float] | None:
    """Weight longest key 0.7 and next 0.3 when multiple genres match."""
    if not keys:
        return None
    if len(keys) == 1:
        return GENRE_MOOD[keys[0]]
    v0, e0 = GENRE_MOOD[keys[0]]
    v1, e1 = GENRE_MOOD[keys[1]]
    return (0.70 * v0 + 0.30 * v1, 0.70 * e0 + 0.30 * e1)


def _best_genre_keys_from_parts(parts: list[str]) -> list[str]:
    """Prefer the deepest path segment that is a genre folder, not the longest key."""
    for part in reversed(parts):
        part_l = (part or "").lower().strip()
        if not part_l or part_l in {".", "/"}:
            continue
        stem = Path(part_l).stem if "." in part_l else part_l
        for candidate in (part_l, stem):
            keys = _path_segment_genre_keys(candidate)
            if keys:
                return keys
    return []


def _path_segment_genre_keys(segment: str) -> list[str]:
    """Genre keys for one folder/file stem — avoid 'rock' in 'rock-drive'."""
    raw = (segment or "").lower().strip()
    if not raw:
        return []
    normalized = re.sub(
        r"[\s_\-]+",
        " ",
        raw.replace(".", " ").replace("&", " and "),
    ).strip()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return []
    # Whole segment is a known genre (e.g. "indie rock", "drum and bass").
    if normalized in GENRE_MOOD:
        return [normalized]
    keys = _match_genre_keys(normalized)
    if not keys:
        return []
    # Residual after removing matched keys must be empty (or harmless stopwords).
    # Reject compound device/volume names like "rock drive" / "jazz usb".
    residual = normalized
    for key in sorted(keys, key=len, reverse=True):
        residual = re.sub(
            rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])",
            " ",
            residual,
            flags=re.IGNORECASE,
        )
    residual = re.sub(r"\s+", " ", residual).strip()
    noise = {"the", "a", "an", "and", "n", "music", "songs", "genre", "mix", "vol", "volume"}
    leftover = [w for w in residual.split() if w and w not in noise]
    if leftover:
        return []
    return keys


def _path_genre_keys(path: str | None) -> list[str]:
    """Genre from path — deepest matching folder, then filename stem."""
    if not path:
        return []
    p = Path(path)
    parts = list(p.parts)
    if not parts:
        return []
    dir_parts = parts[:-1]
    file_stem = p.stem
    dir_keys = _best_genre_keys_from_parts(dir_parts)
    if dir_keys:
        return dir_keys
    return _path_segment_genre_keys(file_stem)


def _path_genre_key(path: str | None) -> str | None:
    keys = _path_genre_keys(path)
    return keys[0] if keys else None


# Full range for seed-local scatter (± half of this around the genre neighborhood).
# ±0.07 keeps OC Remix / OST / game cohorts readable as clouds without leaving the pocket.
SEED_JITTER_SPREAD = 0.14


def _path_hash_units(path: str) -> tuple[float, float]:
    """Stable [0, 1) pair from path via blake2b (process/rescan stable)."""
    digest = hashlib.blake2b(path.encode("utf-8", errors="replace"), digest_size=8).digest()
    h_v = int.from_bytes(digest[:4], "little")
    h_e = int.from_bytes(digest[4:], "little")
    return (h_v % 1000) / 1000.0, (h_e % 1000) / 1000.0


def _path_jitter_deltas(path: str, *, spread: float = SEED_JITTER_SPREAD) -> tuple[float, float]:
    """Return (d_valence, d_energy) in roughly ±spread/2 from a stable path hash."""
    u_v, u_e = _path_hash_units(path)
    return (u_v - 0.5) * spread, (u_e - 0.5) * spread


def genre_seed(
    genre: str,
    title: str,
    artist: str,
    path: str | None = None,
    *,
    year: int | None = None,
    extra_text: str = "",
    albumartist: str = "",
    composer: str = "",
    replaygain_db: float | None = None,
) -> MoodSeed:
    tag_keys = _match_genre_keys(genre or "")
    path_keys = _path_genre_keys(path)
    tag_key = tag_keys[0] if tag_keys else None
    path_key = path_keys[0] if path_keys else None

    tag_pair = _blend_genre_pairs(tag_keys)
    path_pair = _blend_genre_pairs(path_keys)

    if tag_pair and path_pair:
        valence = 0.75 * tag_pair[0] + 0.25 * path_pair[0]
        energy = 0.75 * tag_pair[1] + 0.25 * path_pair[1]
    elif tag_pair:
        valence, energy = tag_pair
    elif path_pair:
        valence, energy = path_pair
    else:
        valence, energy = DEFAULT_VALENCE, DEFAULT_ENERGY

    # Year prior: stronger when genre tags are missing.
    yv, ye = _year_prior(year)
    if tag_key is None:
        valence += yv
        energy += ye
    else:
        valence += 0.35 * yv
        energy += 0.35 * ye

    energy += _replaygain_energy_prior(replaygain_db)

    skip_words = set(tag_keys) | set(path_keys)
    credit = _credit_text(albumartist, composer)
    # Filename is already folded into extra_text by read_tags — only add if missing.
    fn = _filename_mood_text(path)
    blob = f"{title} {artist} {credit} {genre} {extra_text}"
    if fn and fn.lower() not in blob.lower():
        blob = f"{blob} {fn}"
    dv, de = _keyword_shift(blob, skip_words=skip_words)
    # Persist pre-keyword coords so undo restores genre, not an overshoot past clip.
    base_v, base_e = float(valence), float(energy)
    post_kw_v = float(np.clip(base_v + dv, 0.03, 0.97))
    post_kw_e = float(np.clip(base_e + de, 0.03, 0.97))
    applied_dv = post_kw_v - base_v
    applied_de = post_kw_e - base_e
    valence, energy = post_kw_v, post_kw_e
    # Stable per-path scatter so same-genre cohorts form clouds, not single pixels.
    # Applied at seed time (scan + analyze base) so large imports read as a night sky
    # before PCM tidy finishes.
    if path:
        jv, je = _path_jitter_deltas(path, spread=SEED_JITTER_SPREAD)
        valence = float(np.clip(valence + jv, 0.03, 0.97))
        energy = float(np.clip(energy + je, 0.03, 0.97))
    return MoodSeed(
        valence=valence,
        energy=energy,
        tag_key=tag_key,
        path_key=path_key,
        keyword_hit=bool(abs(applied_dv) > 1e-9 or abs(applied_de) > 1e-9),
        keyword_dv=float(applied_dv),
        keyword_de=float(applied_de),
        keyword_base_valence=float(base_v),
        keyword_base_energy=float(base_e),
    )

def genre_match(genre: str) -> str | None:
    return _match_genre_key(genre or "")


def _stable_jitter(path: str) -> tuple[float, float]:
    """Tiny stable scatter so unknown tracks do not stack on one point.

    Uses blake2b (not Python hash) so positions stay fixed across rescans/processes.
    """
    dv, de = _path_jitter_deltas(path, spread=0.06)
    valence = DEFAULT_VALENCE + dv
    energy = DEFAULT_ENERGY + de
    return (
        float(np.clip(valence, 0.03, 0.97)),
        float(np.clip(energy, 0.03, 0.97)),
    )


def _close_decode_pipes(proc: subprocess.Popen) -> None:
    """Close stdout/stderr PIPEs so timeout/abort cannot leak FDs."""
    for stream in (proc.stdout, proc.stderr):
        if stream is None:
            continue
        try:
            if getattr(stream, "closed", False):
                continue
            stream.close()
        except (OSError, ValueError):
            pass


def _decode_pcm(path: str, *, start_s: float = 0.0, duration_s: float = 12.0) -> np.ndarray | None:
    global _decode_pcm_calls
    _decode_pcm_calls += 1
    if _decode_abort:
        return None
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    # Never ask ffmpeg to seek to a negative offset.
    seek = max(0.0, float(start_s))
    dur = max(2.0, float(duration_s))
    # Wall clock must cover the requested window plus IO/codec slack (M1).
    wall_timeout = max(dur + 10.0, 30.0)
    cmd = [
        ffmpeg,
        "-v",
        "error",
        "-ss",
        f"{seek:.3f}",
        "-t",
        f"{dur:.3f}",
        "-i",
        path,
        "-ac",
        "1",
        "-ar",
        "11025",
        "-f",
        "f32le",
        "pipe:1",
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError:
        return None
    if not _register_decode_proc(proc):
        # Abort won the race between Popen and registration — kill immediately.
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.communicate(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            _close_decode_pipes(proc)
            try:
                proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        return None
    stdout: bytes | None = None
    try:
        if _decode_abort:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                # Drain + close PIPEs after kill (AB1/H3).
                proc.communicate(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                _close_decode_pipes(proc)
                try:
                    proc.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            return None
        stdout, _stderr = proc.communicate(timeout=wall_timeout)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            # communicate after kill reaps and closes stdout/stderr PIPEs.
            proc.communicate(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            _close_decode_pipes(proc)
            try:
                proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        return None
    except OSError:
        _close_decode_pipes(proc)
        return None
    finally:
        _clear_decode_proc(proc)
        # Belt-and-suspenders: any path that skipped communicate still closes FDs.
        _close_decode_pipes(proc)
    if _decode_abort:
        return None
    if proc.returncode != 0 or not stdout:
        return None
    pcm = np.frombuffer(stdout, dtype=np.float32)
    if pcm.size < 2048:
        return None
    return pcm


def _pcm_signal_ok(pcm: np.ndarray) -> bool:
    """True when the buffer still has usable energy after silence trim."""
    trimmed = _trim_silence(np.ascontiguousarray(pcm, dtype=np.float32))
    if trimmed.size < 4096:
        return False
    if not np.isfinite(trimmed).all():
        return False
    rms = float(np.sqrt(np.mean(np.square(trimmed))) + 1e-12)
    return rms > 1e-4


def _coerce_bpm(bpm: float | None) -> float | None:
    """Return a usable BPM, or None for missing / non-finite / non-positive tags."""
    if bpm is None:
        return None
    try:
        value = float(bpm)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(value) or value <= 0.0:
        return None
    return value


# Per-window listen length for multi-section analysis.
SECTION_WINDOW_S = 12.0
# Prior single-window listen length (unknown duration + dual-plan salvage).
LONG_SINGLE_WINDOW_S = 28.0
# Minimum separation between section starts so we do not triple-sample the same audio.
SECTION_MIN_GAP_S = 8.0
# Mid-heavy blend for intro / mid / late when all three are usable.
SECTION_BLEND_WEIGHTS = {"intro": 0.20, "mid": 0.50, "late": 0.30}
# When mid+late agree closely *and* intro is meaningfully offset (not a mild
# build-up), soft-bias the body — still leave intro more say than cold-open/outlier.
SECTION_BODY_AGREE_WEIGHTS = {"intro": 0.15, "mid": 0.53, "late": 0.32}
SECTION_BODY_AGREE_MAX = 0.12
# Below this, a normal intro keeps default 20/50/30 even if mid+late sit close.
SECTION_BODY_AGREE_INTRO_MIN = 0.16
# Shrunk outlier intro keeps a tiny coord nudge but must not poison merge health
# (unstable / variation) — same spirit as zero-weight cold-open (rehunt score 5).
SECTION_OUTLIER_INTRO_WEIGHT = 0.08
SECTION_HEALTH_WEIGHT_MIN = 0.10
# Audible-span probe: short head check so silent intros do not burn a full 12s.
PROBE_HEAD_S = 1.5
PROBE_STEP_S = 3.0
# Cap how far we hunt for the first audible sample (keeps total listen budget small).
AUDIBLE_SEARCH_MAX_S = 21.0
# Shape note: only label when windows disagree / agree clearly enough.
SHAPE_DISAGREE_CLEAR = 0.14
SHAPE_ENERGY_SPAN_CLEAR = 0.10
# Timbre flag thresholds on profile.tonal.
TIMBRE_TONAL_MIN = 0.58
TIMBRE_NOISY_MAX = 0.42
# Section blend: tonal×onset reweight floor so a sting cannot become the song.
SECTION_TONAL_ONSET_FLOOR = 0.18
SECTION_TONAL_ONSET_CAP = 0.70
# Pulse clarity must permit BPM→Kinetic (muddy pulse skips the nudge).
PULSE_CLARITY_BPM_MIN = 0.58
# Spectral slope clarity: muffled stays nearer sticker; clear may move.
SLOPE_MUFFLED_MAX = 0.38
SLOPE_CLEAR_MIN = 0.55


def _duration_s(duration_ms: int) -> float:
    return max(0.0, float(duration_ms) / 1000.0) if duration_ms else 0.0


def _clamp_seek_s(start_s: float, duration_s: float, window_s: float = SECTION_WINDOW_S) -> float:
    """Clamp a seek so the window stays inside the known file (never past EOF)."""
    seek = max(0.0, float(start_s))
    if duration_s <= 0.0:
        return 0.0
    # Leave a tiny tail so -t still has samples when duration is exact.
    max_start = max(0.0, float(duration_s) - max(2.0, float(window_s) * 0.5))
    return float(min(seek, max_start))


def _analysis_window_plan(
    duration_ms: int, *, audible_start_s: float = 0.0
) -> list[tuple[str, float]]:
    """Return ordered ``(role, start_s)`` windows — 1, 2, or 3 distinct tastes.

    Long tracks: intro / mid / late at 12s each (~36s budget).
    Short / unknown: never triple-sample the same audio.
    When ``audible_start_s`` > 0 (silent/front-pad intro), windows sit on the
    audible span so budget is not spent on near-silence — mid/late stay relative
    to that span. Flashy first-hit poison is still handled by cold-open / outlier
    intro merge weights, not by parking the sting as the sole mid window.
    """
    dur_s = _duration_s(duration_ms)
    win = SECTION_WINDOW_S
    gap = SECTION_MIN_GAP_S
    offset = max(0.0, float(audible_start_s))

    if dur_s <= 0.0:
        # Unknown duration: single listen — from audible start when probed.
        return [("mid", offset if offset > 0.0 else 0.0)]

    # Effective timeline for planning: [offset, dur_s].
    if offset > 0.0:
        if offset >= dur_s - 2.0:
            return [("mid", _clamp_seek_s(max(0.0, dur_s - win), dur_s, win))]
        # Plan as if the audible span were the whole track, then shift seeks.
        span_ms = max(1, int(round((dur_s - offset) * 1000.0)))
        inner = _analysis_window_plan(span_ms, audible_start_s=0.0)
        return [
            (role, _clamp_seek_s(offset + start, dur_s, win)) for role, start in inner
        ]

    if dur_s < 18.0:
        return [("mid", 0.0)]

    if dur_s < 40.0:
        # Short: one window, or two only when starts are well separated.
        late = _clamp_seek_s(dur_s - win - 1.0, dur_s, win)
        if late >= gap:
            return [("intro", 0.0), ("late", late)]
        return [("mid", 0.0)]

    if dur_s < 60.0:
        # Medium: intro + mid (not three overlapping listens).
        mid = _clamp_seek_s(dur_s * 0.42, dur_s, win)
        if mid >= gap:
            return [("intro", 0.0), ("mid", mid)]
        return [("mid", 0.0)]

    # Long enough for three distinct 12s windows.
    intro = 0.0
    mid = _clamp_seek_s(float(np.clip(dur_s * 0.45, 20.0, dur_s - win - 2.0)), dur_s, win)
    late = _clamp_seek_s(dur_s - win - 2.0, dur_s, win)
    # Enforce separation; collapse roles rather than sampling the same region thrice.
    if mid - intro < gap:
        mid = _clamp_seek_s(intro + gap, dur_s, win)
    if late - mid < gap:
        late = _clamp_seek_s(mid + gap, dur_s, win)
    if late - mid < gap or mid - intro < gap:
        # Still too tight (weird/wrong duration) — fall back to two or one.
        if mid >= gap:
            return [("intro", intro), ("mid", mid)]
        return [("mid", 0.0)]
    return [("intro", intro), ("mid", mid), ("late", late)]


def _probe_head_status(
    path: str, start_s: float, *, duration_s: float = PROBE_HEAD_S
) -> str:
    """Return ``ok`` / ``quiet`` / ``miss`` for a short head probe."""
    if decode_abort_requested():
        return "miss"
    pcm = _decode_pcm(path, start_s=float(start_s), duration_s=float(duration_s))
    if pcm is None:
        return "miss"
    return "ok" if _pcm_signal_ok(pcm) else "quiet"


def _probe_signal_at(path: str, start_s: float, *, duration_s: float = PROBE_HEAD_S) -> bool:
    """True when a short decode at ``start_s`` still has usable energy."""
    return _probe_head_status(path, start_s, duration_s=duration_s) == "ok"


def _find_audible_start(
    path: str,
    duration_ms: int,
    *,
    from_s: float = 0.0,
    max_search_s: float = AUDIBLE_SEARCH_MAX_S,
) -> float | None:
    """First seek with audible content within a capped search, or None."""
    dur_s = _duration_s(duration_ms)
    start = max(0.0, float(from_s))
    limit = float(max_search_s)
    if dur_s > 0.0:
        limit = min(limit, max(0.0, dur_s - 2.0))
    t = start
    while t <= start + limit + 1e-9:
        if decode_abort_requested():
            return None
        status = _probe_head_status(path, t)
        if status == "ok":
            return float(t)
        if status == "miss":
            # Hard miss mid-search: stop burning ffmpeg on a broken file.
            return None
        t += PROBE_STEP_S
    return None


def _audible_span_offset(path: str, duration_ms: int) -> tuple[float, str]:
    """Probe the head; if near-silent, return ``(first_audible_seek, head_status)``.

    Spends only short probes — never a full 12s on a silent game-cue intro.
    Hard miss at 0 does not scan forward (section path handles the file).
    """
    if decode_abort_requested():
        return 0.0, "miss"
    head = _probe_head_status(path, 0.0)
    if head == "ok":
        return 0.0, head
    if head == "miss":
        return 0.0, head
    found = _find_audible_start(
        path, duration_ms, from_s=PROBE_STEP_S, max_search_s=AUDIBLE_SEARCH_MAX_S
    )
    return (float(found), head) if found is not None else (0.0, head)


def _leading_silence_s(pcm: np.ndarray, samplerate: int = 11025, floor: float = 0.012) -> float:
    """Seconds of leading near-silence inside an already-decoded buffer."""
    if pcm is None or pcm.size < 4096:
        return 0.0
    abs_p = np.abs(np.ascontiguousarray(pcm, dtype=np.float32))
    peak = float(np.max(abs_p) + 1e-12)
    thr = max(floor * peak, 1e-4)
    idx = np.where(abs_p >= thr)[0]
    if idx.size < 2048:
        return 0.0
    return float(idx[0]) / float(samplerate)


def _primary_seek_s(duration_ms: int) -> float:
    """Compat: first planned window start (0 for short/unknown)."""
    plan = _analysis_window_plan(duration_ms)
    return float(plan[0][1]) if plan else 0.0


def _secondary_seek_s(duration_ms: int) -> float | None:
    """Compat: second planned window start, or None when only one listen."""
    plan = _analysis_window_plan(duration_ms)
    if len(plan) < 2:
        return None
    return float(plan[1][1])


def _timbre_flag_from_tonal(tonal: float | None) -> str | None:
    """Map continuous tonal score to a tiny persisted vocabulary."""
    if tonal is None:
        return None
    t = float(tonal)
    if not np.isfinite(t):
        return None
    if t >= TIMBRE_TONAL_MIN:
        return "tonal"
    if t <= TIMBRE_NOISY_MAX:
        return "noisy"
    return None


def shape_note_from_roles(role_profiles: dict[str, object]) -> str | None:
    """Tiny shape vocabulary from intro/mid/late disagreement — or None.

    ``steady`` when multi-window and mild; ``build`` / ``drop`` / ``bookend``
    only when the energy arc is clear. Not a map axis — queue + stored note.
    """
    if not role_profiles or len(role_profiles) < 2:
        return None
    roles = [r for r in ("intro", "mid", "late") if r in role_profiles]
    if len(roles) < 2:
        return None

    def _e(role: str) -> float:
        return float(getattr(role_profiles[role], "energy", 0.5) or 0.5)

    def _v(role: str) -> float:
        return float(getattr(role_profiles[role], "valence", 0.5) or 0.5)

    max_disagree = 0.0
    for i, ra in enumerate(roles):
        for rb in roles[i + 1 :]:
            d = float(np.hypot(_v(ra) - _v(rb), _e(ra) - _e(rb)))
            if d > max_disagree:
                max_disagree = d
    energies = [_e(r) for r in roles]
    span = float(max(energies) - min(energies))

    intro_e = _e("intro") if "intro" in role_profiles else None
    mid_e = _e("mid") if "mid" in role_profiles else None
    late_e = _e("late") if "late" in role_profiles else None

    clear = max_disagree >= SHAPE_DISAGREE_CLEAR or span >= SHAPE_ENERGY_SPAN_CLEAR
    if not clear:
        # Multi-window mild agreement → steady (still useful for queue).
        return "steady"

    # bookend: hot edges, calm mid
    if intro_e is not None and mid_e is not None and late_e is not None:
        if (
            intro_e > mid_e + 0.08
            and late_e > mid_e + 0.08
            and 0.5 * (intro_e + late_e) > mid_e + 0.10
        ):
            return "bookend"
        # drop: quiet/soft open into a hotter body (or falling after a hot open)
        if mid_e > intro_e + 0.10 and mid_e >= late_e - 0.05:
            return "drop"
        if intro_e > mid_e + 0.10 and (late_e is None or mid_e >= late_e - 0.04):
            return "drop"
        # build: rising arc
        if mid_e >= intro_e - 0.02 and late_e > mid_e + 0.06 and late_e > intro_e + 0.10:
            return "build"
        if late_e > intro_e + 0.12 and mid_e >= intro_e - 0.02:
            return "build"

    if intro_e is not None and late_e is not None and mid_e is None:
        if late_e > intro_e + 0.12:
            return "build"
        if intro_e > late_e + 0.12:
            return "drop"

    if intro_e is not None and mid_e is not None and late_e is None:
        if mid_e > intro_e + 0.12:
            return "drop"
        if mid_e > intro_e + 0.08:
            return "build"

    if mid_e is not None and late_e is not None and intro_e is None:
        if late_e > mid_e + 0.12:
            return "build"
        if mid_e > late_e + 0.12:
            return "drop"

    # Clear disagreement without a clean archetype — still mark steady vs inventing.
    return "steady"


def steer_weight_from_listen(
    *,
    pcm_ok: bool,
    multi_window: bool,
    pcm_fallback: bool,
    pcm_unstable: bool,
    edge_salvage: str | None,
    seed_evidence: bool,
    variation: float = 0.0,
) -> float:
    """How safe this placement is to steer the queue (0 ≈ seed/intro-only)."""
    if not pcm_ok:
        return 0.05 if seed_evidence else 0.0
    if edge_salvage == "intro":
        return 0.05
    if edge_salvage in ("late", "ends"):
        return 0.22
    if edge_salvage == "mid":
        return 0.35
    if multi_window and not pcm_unstable and variation < 0.28:
        steer = 0.95
    elif multi_window:
        steer = 0.78
    elif pcm_fallback or pcm_unstable:
        return 0.32
    else:
        return 0.70
    # Partial multi-window (missing a section) still takes the fallback tax on
    # confidence — keep steer from looking fully safe.
    if pcm_fallback:
        steer = min(steer, 0.70)
    return float(steer)


def _profile_stability(p) -> float:
    """Higher is better: consistent onsets, low variation, not marked unstable."""
    return (
        float(p.onset_consistency)
        + (0.0 if p.unstable else 0.25)
        + float(np.clip(1.0 - float(p.variation), 0.0, 1.0)) * 0.35
    )


def _is_cold_open_profile(p) -> bool:
    """True when a window looks like silence / cold-open pad, not the song body."""
    return (
        float(p.energy) < 0.30
        and float(p.flux) < 0.28
        and float(getattr(p, "onset_rate", 0.0) or 0.0) < 0.45
        and float(p.energy_mean) < 0.42
    )


def _avg_tonal(profiles: list, weights: list[float] | None = None) -> float:
    if not profiles:
        return 0.5
    if weights is None:
        return float(np.mean([float(getattr(p, "tonal", 0.5) or 0.5) for p in profiles]))
    total = sum(max(0.0, float(w)) for w in weights)
    if total <= 1e-12:
        return float(getattr(profiles[0], "tonal", 0.5) or 0.5)
    return float(
        sum(float(getattr(p, "tonal", 0.5) or 0.5) * max(0.0, float(w)) for p, w in zip(profiles, weights))
        / total
    )


def _finite_attr(profile: object, attr: str, default: float) -> float:
    """Read a numeric profile field without treating 0.0 as missing."""
    raw = getattr(profile, attr, None)
    if raw is None:
        return float(default)
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return float(default)
    if not np.isfinite(val):
        return float(default)
    return val


def _avg_attr(profiles: list, attr: str, default: float, weights: list[float] | None = None) -> float:
    if not profiles:
        return default
    if weights is None:
        return float(np.mean([_finite_attr(p, attr, default) for p in profiles]))
    total = sum(max(0.0, float(w)) for w in weights)
    if total <= 1e-12:
        return _finite_attr(profiles[0], attr, default)
    return float(
        sum(_finite_attr(p, attr, default) * max(0.0, float(w)) for p, w in zip(profiles, weights))
        / total
    )


def _optional_profile_float(profile: object, attr: str) -> float | None:
    raw = getattr(profile, attr, None)
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(val):
        return None
    return val


def _speech_weight(profile: object) -> int:
    """Windows heard, before speech frames were dropped. Falls back to windows kept."""
    heard = int(getattr(profile, "heard_windows", 0) or 0)
    if heard > 0:
        return heard
    return max(int(getattr(profile, "window_count", 0) or 0), 0)


def _blend_speech_share(profiles: list) -> float:
    """Share of clearly spoken windows, weighted by windows heard.

    A section that kept one musical frame after dropping speech still counts
    every window it heard. A spoken intro must not dim a musical body.
    """
    if not profiles:
        return 0.0
    counts = [_speech_weight(p) for p in profiles]
    if sum(counts) <= 0:
        counts = [1] * len(profiles)
    total = float(sum(counts))
    acc = 0.0
    for profile, count in zip(profiles, counts):
        acc += float(getattr(profile, "speech_share", 0.0) or 0.0) * count
    return float(acc / total)


def _blend_onset_strength(profiles: list) -> float | None:
    """Window-weighted hit-strength ratio. Skip sections that were not judged."""
    vals: list[float] = []
    weights: list[int] = []
    for profile in profiles:
        strength = _optional_profile_float(profile, "onset_strength")
        if strength is None:
            continue
        vals.append(strength)
        weights.append(max(_speech_weight(profile), 1))
    if not vals:
        return None
    total = float(sum(weights))
    return float(sum(v * w for v, w in zip(vals, weights)) / total)


def _merged_pulse_rejected(profiles: list, bpm: float | None) -> bool:
    """A trusted BPM from any section keeps the pulse. Refusal sticks only when none survived."""
    if bpm is not None:
        return False
    return any(bool(getattr(p, "pulse_rejected", False)) for p in profiles)


def _blend_band_energy(profiles: list, weights: list[float]) -> dict[str, float]:
    """Weight-average partition bands (no sub_bass key — parallel leftover only)."""
    keys = ("bass", "low_mid", "mid", "high_mid", "high")
    total = sum(max(0.0, float(w)) for w in weights)
    if total <= 1e-12 or not profiles:
        return {k: 0.2 for k in keys}
    out: dict[str, float] = {}
    for k in keys:
        out[k] = float(
            sum(
                float((getattr(p, "band_energy", None) or {}).get(k, 0.2)) * max(0.0, float(w))
                for p, w in zip(profiles, weights)
            )
            / total
        )
    return out


def _pick_timbre_source(profiles: list, weights: list[float] | None = None) -> str | None:
    """Prefer the heaviest-weighted non-None timbre_source."""
    best: str | None = None
    best_w = -1.0
    for i, p in enumerate(profiles):
        src = getattr(p, "timbre_source", None)
        if not src:
            continue
        w = float(weights[i]) if weights is not None else 1.0
        if w > best_w:
            best_w = w
            best = str(src)
    return best


def _reweight_tonal_onset(
    role_weights: dict[str, float],
    role_profiles: dict[str, object],
) -> dict[str, float]:
    """Bias section blend toward tonal + real-onset windows; keep a share floor.

    A single stingy/flashy window cannot monopolize the song body. Zero-weight
    roles stay zero. If every survivor scores near zero, leave base weights.
    Floor is re-enforced after cap redistribute so the sting floor cannot break.
    """
    from meridian.acoustic import tonal_onset_score

    active = {r: float(w) for r, w in role_weights.items() if float(w) > 1e-12 and r in role_profiles}
    if len(active) < 2:
        return dict(role_weights)
    scores = {r: float(tonal_onset_score(role_profiles[r])) for r in active}
    if max(scores.values()) < 0.08:
        return dict(role_weights)
    boosted = {r: active[r] * (0.40 + 0.60 * scores[r]) for r in active}
    total = sum(boosted.values())
    if total <= 1e-12:
        return dict(role_weights)
    normed = {r: boosted[r] / total for r in boosted}
    n = len(normed)
    floor = min(SECTION_TONAL_ONSET_FLOOR, 1.0 / n)
    # Iterate floor + cap to a fixed point (cap excess only onto above-floor peers).
    for _ in range(6):
        for r in list(normed):
            normed[r] = max(normed[r], floor)
        total = sum(normed.values())
        normed = {r: normed[r] / total for r in normed}
        capped = False
        for r in list(normed):
            if normed[r] > SECTION_TONAL_ONSET_CAP + 1e-12:
                excess = normed[r] - SECTION_TONAL_ONSET_CAP
                normed[r] = SECTION_TONAL_ONSET_CAP
                peers = [o for o in normed if o != r and normed[o] + 1e-12 < SECTION_TONAL_ONSET_CAP]
                if not peers:
                    peers = [o for o in normed if o != r]
                if peers:
                    share = excess / len(peers)
                    for o in peers:
                        normed[o] += share
                capped = True
        total = sum(normed.values())
        normed = {r: normed[r] / total for r in normed}
        if not capped and all(normed[r] + 1e-12 >= floor for r in normed):
            break
    # Final snap: renorm can leave values a hair under floor.
    for r in list(normed):
        if normed[r] < floor:
            need = floor - normed[r]
            donor = max(normed, key=lambda k: normed[k])
            take = min(need, max(0.0, normed[donor] - floor))
            normed[r] += take
            normed[donor] -= take
    total = sum(normed.values())
    normed = {r: normed[r] / total for r in normed}
    out = dict(role_weights)
    for r, w in role_weights.items():
        if float(w) <= 1e-12:
            out[r] = 0.0
        elif r in normed:
            out[r] = float(normed[r])
    return out


def _keyword_contradiction_axes(
    *,
    keyword_dv: float,
    keyword_de: float,
    seed_valence: float,
    seed_energy: float,
    pcm_valence: float,
    pcm_energy: float,
    onset_consistency: float,
    unstable: bool,
    variation: float,
    keyword_base_valence: float | None = None,
    keyword_base_energy: float | None = None,
) -> tuple[bool, bool]:
    """Per-axis: True when a steady listen clearly opposes that keyword nudge."""
    dv = float(keyword_dv)
    de = float(keyword_de)
    if abs(dv) < 1e-9 and abs(de) < 1e-9:
        return False, False
    if unstable or float(onset_consistency) < 0.55 or float(variation) > 0.28:
        return False, False
    base_v = (
        float(keyword_base_valence)
        if keyword_base_valence is not None
        else float(seed_valence) - dv
    )
    base_e = (
        float(keyword_base_energy)
        if keyword_base_energy is not None
        else float(seed_energy) - de
    )
    contra_v = (
        abs(dv) >= 0.04
        and (float(pcm_valence) - base_v) * float(np.sign(dv) or 1.0) < -0.10
    )
    contra_e = (
        abs(de) >= 0.04
        and (float(pcm_energy) - base_e) * float(np.sign(de) or 1.0) < -0.10
    )
    return bool(contra_v), bool(contra_e)


def _keyword_contradicted_by_pcm(
    *,
    keyword_dv: float,
    keyword_de: float,
    seed_valence: float,
    seed_energy: float,
    pcm_valence: float,
    pcm_energy: float,
    onset_consistency: float,
    unstable: bool,
    variation: float,
    keyword_base_valence: float | None = None,
    keyword_base_energy: float | None = None,
) -> bool:
    """True when any axis is clearly contradicted (compat wrapper)."""
    cv, ce = _keyword_contradiction_axes(
        keyword_dv=keyword_dv,
        keyword_de=keyword_de,
        seed_valence=seed_valence,
        seed_energy=seed_energy,
        pcm_valence=pcm_valence,
        pcm_energy=pcm_energy,
        onset_consistency=onset_consistency,
        unstable=unstable,
        variation=variation,
        keyword_base_valence=keyword_base_valence,
        keyword_base_energy=keyword_base_energy,
    )
    return bool(cv or ce)


def _merge_two_pcm_profiles(primary, secondary):
    """Combine two window profiles; on strong disagreement prefer the stabler song-like window."""
    from meridian.acoustic import AcousticProfile

    disagree = float(
        np.hypot(primary.valence - secondary.valence, primary.energy - secondary.energy)
    )
    s1, s2 = _profile_stability(primary), _profile_stability(secondary)
    if s1 >= s2:
        winner, loser = primary, secondary
    else:
        winner, loser = secondary, primary

    if disagree > 0.18:
        # Intro vs drop (etc.): keep the stabler window as the base, but do not discard
        # a musically active disagreeing window — inject contrast (Kinetic freer than Glow).
        loser_more_active = (
            float(loser.flux) > float(winner.flux) + 0.04
            or float(loser.onset_rate) > float(winner.onset_rate) + 0.02
            or float(loser.energy) > float(winner.energy) + 0.06
        )
        inj_e = 0.32 if loser_more_active else 0.18
        inj_v = 0.14 if loser_more_active else 0.08
        valence = float((1.0 - inj_v) * winner.valence + inj_v * loser.valence)
        energy = float((1.0 - inj_e) * winner.energy + inj_e * loser.energy)
        brightness = float((1.0 - inj_v) * winner.brightness + inj_v * loser.brightness)
        flux = float(max(winner.flux, (1.0 - inj_e) * winner.flux + inj_e * loser.flux))
        trend = float((1.0 - inj_e) * winner.energy_trend + inj_e * loser.energy_trend)
        bands = _blend_band_energy([winner, loser], [1.0 - inj_v, inj_v])
        bpm = winner.bpm if winner.bpm is not None else loser.bpm
        onset_consistency = float(winner.onset_consistency)
        variation = float(
            max(winner.variation, (1.0 - inj_e) * winner.variation + inj_e * loser.variation)
        )
        unstable = bool(winner.unstable)
        ostats_src = winner
        if loser_more_active:
            # Persist the active window's motion cues for Focus / album spread.
            ostats_src = loser if float(loser.onset_rate) >= float(winner.onset_rate) else winner
        tonal = _avg_tonal([winner, loser], [1.0 - inj_v, inj_v])
        timbre_source = _pick_timbre_source([winner, loser], [1.0 - inj_v, inj_v])
        slope_profiles = [winner, loser]
        slope_weights = [1.0 - inj_v, inj_v]
    else:
        # Mild disagreement: lean toward the stabler window.
        wp = 0.62 if primary is winner else 0.38
        ws = 1.0 - wp
        valence = float(wp * primary.valence + ws * secondary.valence)
        energy = float(wp * primary.energy + ws * secondary.energy)
        brightness = float(wp * primary.brightness + ws * secondary.brightness)
        flux = float(wp * primary.flux + ws * secondary.flux)
        trend = float(wp * primary.energy_trend + ws * secondary.energy_trend)
        bands = _blend_band_energy([primary, secondary], [wp, ws])
        bpms = [b for b in (primary.bpm, secondary.bpm) if b is not None]
        bpm = float(np.median(bpms)) if bpms else None
        onset_consistency = float(winner.onset_consistency)
        variation = float(wp * primary.variation + ws * secondary.variation)
        unstable = bool(primary.unstable or secondary.unstable)
        ostats_src = winner
        tonal = _avg_tonal([primary, secondary], [wp, ws])
        timbre_source = _pick_timbre_source([primary, secondary], [wp, ws])
        slope_profiles = [primary, secondary]
        slope_weights = [wp, ws]

    return AcousticProfile(
        valence=float(np.clip(valence, 0.03, 0.97)),
        energy=float(np.clip(energy, 0.03, 0.97)),
        bpm=bpm,
        unstable=unstable,
        brightness=brightness,
        flux=flux,
        band_energy=bands,
        energy_mean=float(np.median([primary.energy_mean, secondary.energy_mean])),
        energy_std=float(np.median([primary.energy_std, secondary.energy_std])),
        energy_peak=float(max(primary.energy_peak, secondary.energy_peak)),
        energy_range=float(np.median([primary.energy_range, secondary.energy_range])),
        energy_trend=trend,
        onset_rate=float(ostats_src.onset_rate),
        onset_burstiness=float(ostats_src.onset_burstiness),
        onset_consistency=onset_consistency,
        variation=variation,
        window_count=int(primary.window_count + secondary.window_count),
        pcm_samples=int(primary.pcm_samples + secondary.pcm_samples),
        tonal=float(np.clip(tonal, 0.05, 0.95)),
        timbre_source=timbre_source,
        shape_note=None,
        spectral_slope=float(
            _avg_attr(slope_profiles, "spectral_slope", 0.5, slope_weights)
        ),
        sub_bass=float(_avg_attr(slope_profiles, "sub_bass", 0.0, slope_weights)),
        zcr=float(_avg_attr(slope_profiles, "zcr", 0.0, slope_weights)),
        speech_share=_blend_speech_share([primary, secondary]),
        heard_windows=int(_speech_weight(primary) + _speech_weight(secondary)),
        onset_strength=_blend_onset_strength([primary, secondary]),
        tempo_confidence=_optional_profile_float(ostats_src, "tempo_confidence"),
        beat_alignment=_optional_profile_float(ostats_src, "beat_alignment"),
        pulse_rejected=_merged_pulse_rejected([primary, secondary], bpm),
    )


def _weighted_profile_blend(items: list[tuple[object, float]]):
    """Deterministic weighted mood blend (weights renormalized over survivors).

    Zero-weight sections are dropped entirely so cold-open / vetoed intros cannot
    poison merge health (``unstable`` / ``variation``) while coords stay mid-heavy.
    Tiny-weight sections (shrunk outlier intro) still nudge coords but are excluded
    from health aggregates — same spirit as zero-weight cold-open (rehunt score 5).
    """
    from meridian.acoustic import AcousticProfile

    # Exclude zero-weight items before any health/aggregate stats (score-7 cold open).
    positive = [(p, max(0.0, float(w))) for p, w in items if float(w) > 1e-12]
    if not positive:
        return items[0][0]
    total_w = sum(w for _p, w in positive)
    if total_w <= 1e-12:
        return positive[0][0]
    normed = [(p, w / total_w) for p, w in positive]

    # Health from meaningful-weight survivors only (drop shrunk outlier intro @ 0.08).
    health_src = [(p, w) for p, w in positive if w >= SECTION_HEALTH_WEIGHT_MIN]
    if not health_src:
        health_src = positive
    health_total = sum(w for _p, w in health_src)
    health_normed = [(p, w / health_total) for p, w in health_src]

    def _avg(attr: str) -> float:
        return float(sum(float(getattr(p, attr)) * w for p, w in normed))

    # Prefer BPM / onset cues from the heaviest (or stabler-on-tie) survivor.
    anchor = max(normed, key=lambda pw: (pw[1], _profile_stability(pw[0])))[0]
    bpms = [float(p.bpm) for p, _w in normed if getattr(p, "bpm", None) is not None]
    bpm = float(np.median(bpms)) if bpms else None
    profiles = [p for p, _w in normed]
    weights = [w for _p, w in normed]
    bands = _blend_band_energy(profiles, weights)
    return AcousticProfile(
        valence=float(np.clip(_avg("valence"), 0.03, 0.97)),
        energy=float(np.clip(_avg("energy"), 0.03, 0.97)),
        bpm=bpm,
        unstable=bool(any(bool(p.unstable) for p, _w in health_normed)),
        brightness=float(_avg("brightness")),
        flux=float(_avg("flux")),
        band_energy=bands,
        energy_mean=float(np.median([float(p.energy_mean) for p, _w in normed])),
        energy_std=float(np.median([float(p.energy_std) for p, _w in normed])),
        energy_peak=float(max(float(p.energy_peak) for p, _w in normed)),
        energy_range=float(np.median([float(p.energy_range) for p, _w in normed])),
        energy_trend=float(_avg("energy_trend")),
        onset_rate=float(anchor.onset_rate),
        onset_burstiness=float(anchor.onset_burstiness),
        onset_consistency=float(anchor.onset_consistency),
        variation=float(max(float(p.variation) for p, _w in health_normed)),
        window_count=int(sum(int(p.window_count) for p, _w in normed)),
        pcm_samples=int(sum(int(p.pcm_samples) for p, _w in normed)),
        tonal=float(np.clip(_avg_tonal(profiles, weights), 0.05, 0.95)),
        timbre_source=_pick_timbre_source(profiles, weights),
        shape_note=None,
        spectral_slope=float(_avg_attr(profiles, "spectral_slope", 0.5, weights)),
        sub_bass=float(_avg_attr(profiles, "sub_bass", 0.0, weights)),
        zcr=float(_avg_attr(profiles, "zcr", 0.0, weights)),
        speech_share=_blend_speech_share(profiles),
        heard_windows=int(sum(_speech_weight(p) for p in profiles)),
        onset_strength=_blend_onset_strength(profiles),
        tempo_confidence=_optional_profile_float(anchor, "tempo_confidence"),
        beat_alignment=_optional_profile_float(anchor, "beat_alignment"),
        pulse_rejected=_merged_pulse_rejected(profiles, bpm),
    )


def _attach_shape(profile, role_profiles: dict[str, object]):
    """Stamp shape_note onto a merged profile from role-tagged windows."""
    note = shape_note_from_roles(role_profiles)
    try:
        profile.shape_note = note
    except Exception:
        pass
    return profile


def _preserve_shrunk_intro(
    weights: dict[str, float],
    *,
    intro_cap: float,
    role_profiles: dict[str, object],
) -> dict[str, float]:
    """Restore intentional intro weight below the default 0.20 blend share."""
    if "intro" not in weights:
        return weights
    default_intro = float(SECTION_BLEND_WEIGHTS["intro"])
    if intro_cap + 1e-12 >= default_intro:
        return weights
    out = dict(weights)
    body = {r: float(out[r]) for r in ("mid", "late") if r in out and r in role_profiles}
    # Dual paths may use late-only / mid-only companions.
    if not body:
        body = {r: float(out[r]) for r in out if r != "intro"}
    body_total = sum(body.values())
    remain = max(0.0, 1.0 - intro_cap)
    out["intro"] = float(intro_cap)
    if body_total > 1e-12 and remain > 0.0:
        for r in body:
            out[r] = remain * (body[r] / body_total)
    return out


def _merge_section_profiles(role_profiles: dict[str, object]):
    """Mid-heavy intro/mid/late merge; cold-open intro cannot veto mid+late."""
    if not role_profiles:
        raise ValueError("role_profiles must not be empty")
    if len(role_profiles) == 1:
        sole = next(iter(role_profiles.values()))
        return _attach_shape(sole, role_profiles)

    roles = [r for r in ("intro", "mid", "late") if r in role_profiles]

    # Dual-window path: tonal×onset reweight + sting floor (same honesty as three-section).
    if len(roles) == 2:
        a, b = roles[0], roles[1]
        pa, pb = role_profiles[a], role_profiles[b]
        sa, sb = _profile_stability(pa), _profile_stability(pb)
        # Mild stability prior, then honesty reweight.
        if sa + sb > 1e-12:
            wa = 0.38 + 0.24 * (sa / (sa + sb))
        else:
            wa = 0.5
        weights = {a: float(wa), b: float(1.0 - wa)}
        # Cold-open / outlier intro on dual plans.
        if a == "intro":
            span = float(np.hypot(pa.valence - pb.valence, pa.energy - pb.energy))
            if _is_cold_open_profile(pa) and span > 0.10:
                weights["intro"] = 0.0
                weights[b] = 1.0
            elif span > 0.22:
                weights["intro"] = SECTION_OUTLIER_INTRO_WEIGHT
                weights[b] = 1.0 - SECTION_OUTLIER_INTRO_WEIGHT
        intro_cap = float(weights.get("intro", 1.0))
        weights = _reweight_tonal_onset(weights, role_profiles)
        weights = _preserve_shrunk_intro(weights, intro_cap=intro_cap, role_profiles=role_profiles)
        merged = _weighted_profile_blend([(role_profiles[r], weights[r]) for r in roles])
        return _attach_shape(merged, role_profiles)

    intro = role_profiles.get("intro")
    mid = role_profiles.get("mid")
    late = role_profiles.get("late")
    if intro is None or mid is None or late is None:
        # Should be unreachable (handled by dual), but keep a safe fallback.
        ordered = [role_profiles[k] for k in roles]
        merged = _merge_two_pcm_profiles(ordered[0], ordered[1])
        return _attach_shape(merged, role_profiles)

    weights = dict(SECTION_BLEND_WEIGHTS)
    # Cold-open / silent intro must not veto a clear mid+late body.
    body_span = float(np.hypot(mid.valence - late.valence, mid.energy - late.energy))
    mid_late_agree = body_span <= 0.18
    mid_late_close = body_span <= SECTION_BODY_AGREE_MAX
    intro_vs_body = float(
        np.hypot(
            intro.valence - 0.5 * (mid.valence + late.valence),
            intro.energy - 0.5 * (mid.energy + late.energy),
        )
    )
    if _is_cold_open_profile(intro) and (mid_late_agree or intro_vs_body > 0.16):
        weights["intro"] = 0.0
        # Renormalize mid-heavy among body windows.
        weights["mid"] = 0.62
        weights["late"] = 0.38
    elif intro_vs_body > 0.22 and mid_late_agree:
        # Active-but-outlier intro (false cold? drama open): tiny coord nudge only.
        # Health excludes this weight via SECTION_HEALTH_WEIGHT_MIN (rehunt score 5).
        # Also covers flashy first-hit after audible-span shift — do not poison body.
        weights["intro"] = SECTION_OUTLIER_INTRO_WEIGHT
        weights["mid"] = 0.55
        weights["late"] = 0.37
    elif mid_late_close and intro_vs_body > SECTION_BODY_AGREE_INTRO_MIN:
        # Path-to-9: mid+late agree closely + intro clearly offset → soft body bias.
        # Mild/normal intros keep default blend so build-ups aren't half-muted.
        weights = dict(SECTION_BODY_AGREE_WEIGHTS)

    # Honesty: lean toward windows with real tonality + real onsets; floor so a
    # sting cannot become the song. Preserve any intentional intro below default 0.20
    # (cold-open 0, outlier 0.08, body-agree 0.15).
    intro_cap = float(weights.get("intro", 0.0))
    weights = _reweight_tonal_onset(weights, role_profiles)
    weights = _preserve_shrunk_intro(weights, intro_cap=intro_cap, role_profiles=role_profiles)

    # Strong mid vs late disagreement: keep tonal×onset mid/late shares (do not
    # throw them away for a stability-only merge_two).
    body_disagree = body_span
    if body_disagree > 0.18:
        mid_w = float(weights.get("mid", 0.0))
        late_w = float(weights.get("late", 0.0))
        body_total = mid_w + late_w
        if body_total <= 1e-12:
            body = _merge_two_pcm_profiles(mid, late)
        else:
            body = _weighted_profile_blend([(mid, mid_w), (late, late_w)])
        if weights["intro"] <= 1e-9:
            return _attach_shape(body, role_profiles)
        merged = _weighted_profile_blend([(intro, weights["intro"]), (body, 1.0 - weights["intro"])])
        return _attach_shape(merged, role_profiles)

    merged = _weighted_profile_blend(
        [
            (intro, weights["intro"]),
            (mid, weights["mid"]),
            (late, weights["late"]),
        ]
    )
    return _attach_shape(merged, role_profiles)


def _merge_pcm_profiles(primary, secondary=None, *, role_profiles: dict[str, object] | None = None):
    """Combine window profiles (dual legacy args, or role-tagged intro/mid/late)."""
    if role_profiles is not None:
        return _merge_section_profiles(role_profiles)
    if secondary is None:
        return primary
    return _merge_two_pcm_profiles(primary, secondary)


def _enough_section_evidence(
    planned: list[tuple[str, float]],
    ok_roles: set[str],
) -> bool:
    """Partial decode is not success when it would park a lying edge-only mood."""
    if not ok_roles:
        return False
    planned_roles = {role for role, _ss in planned}
    # Long-track plan (3 windows): require mid/body — not edges-only (pad+fade).
    if len(planned) >= 3:
        # Mid planned and missing → reject intro+late ends-only (rehunt score 4).
        # Also rejects intro-only / late-only (prior score 4/6 honesty).
        return "mid" in ok_roles
    # Dual plans (short/medium): intro-only is a lie; late-only outro also defers
    # (rehunt score 3 — prefer defer for consistency with long-plan late-only).
    if len(planned) == 2:
        if not (ok_roles - {"intro"}):
            return False
        if ok_roles == {"late"} and "mid" not in planned_roles:
            return False
        return True
    # Single-window plan: any usable decode is enough.
    if planned_roles and ok_roles:
        return True
    return False


def _decode_pcm_with_fallback(
    path: str, duration_ms: int = 0
) -> tuple[np.ndarray | None, bool, object | None, str | None]:
    """Decode intro/mid/late 12s windows (~36s) when the track is long enough.

    Returns
    ``(pcm_or_None, used_fallback_or_partial, merged_profile_or_None, edge_salvage)``.
    ``edge_salvage`` is ``"intro"`` / ``"late"`` / ``"ends"`` for weak placement when
    body evidence is missing, else ``None``. Multi-window success returns a
    representative PCM buffer plus a merged profile. Any decode with real audio
    places (weak/low-trust if not a full honest body listen) — no defer-on-seed
    when music was heard. Dual-plan intro-only falls through to a ~28s
    single-window salvage from 0 / primary. Unknown duration uses that same ~28s
    listen from the first audible sample (short head probe skips front-loaded
    silence / game cues). True silence / total decode failure still returns no PCM.
    """
    from meridian.acoustic import build_profile

    # Audible-span: for known durations, probe a short head; if near-silent, shift
    # the plan onto the first window that has sound (mid/late stay on the audible
    # span). Unknown duration skips the upfront probe — it listens ~28s once and
    # may re-seek once if the buffer itself is front-loaded silence (game cues).
    # Flashy first-hit poison stays handled by cold-open / outlier intro weights.
    dur_s = _duration_s(duration_ms)
    audible_off = 0.0
    head_status = "ok"
    skip_reseek_starts: set[float] = set()
    if dur_s > 0.0:
        audible_off, head_status = _audible_span_offset(path, duration_ms)
        # Hard miss on the short probe does not blacklist the full section seek —
        # only a failed SECTION_WINDOW decode spends that offset.
        _ = head_status
    plan = _analysis_window_plan(duration_ms, audible_start_s=audible_off)
    if not plan:
        return None, False, None, None

    # Seeks already tried: no-signal (energy gate) or hard decode miss — do not
    # blindly re-ffmpeg the same offsets in the silence-fallback pass (rehunt score 2).
    # Dual/long-plan salvage: section evidence failed → ~28s single listen.
    dual_salvage = False
    # If longer salvage fails, weak-place this stashed intro stub.
    edge_fallback: tuple[np.ndarray, str] | None = None

    # Multi-section path (2 or 3 planned windows).
    if len(plan) >= 2:
        role_pcm: dict[str, np.ndarray] = {}
        for role, start_s in plan:
            if decode_abort_requested():
                return None, False, None, None
            pcm = _decode_pcm(path, start_s=float(start_s), duration_s=SECTION_WINDOW_S)
            key = round(float(start_s), 2)
            if pcm is not None and _pcm_signal_ok(pcm):
                role_pcm[role] = pcm
            else:
                # Hard miss (None) or quiet/no-signal audio — both are spent seeks.
                skip_reseek_starts.add(key)

        ok_roles = set(role_pcm)
        if decode_abort_requested():
            return None, False, None, None

        if _enough_section_evidence(plan, ok_roles):
            role_profiles = {role: build_profile(pcm) for role, pcm in role_pcm.items()}
            # Prefer mid buffer for callers that still want a PCM handle.
            rep_role = (
                "mid"
                if "mid" in role_pcm
                else ("late" if "late" in role_pcm else next(iter(role_pcm)))
            )
            planned_roles = {role for role, _ in plan}
            partial = ok_roles != planned_roles
            # Silent/missing intro with solid mid+late body is full PCM credit,
            # not a weak-fallback tax (score-5 asymmetry vs cold-open pad that passes signal).
            if "mid" in ok_roles and "late" in ok_roles:
                partial = False
            # Mid-only body is honest placement: full PCM credit, but not multi-window
            # (single section — rebuild cues from PCM so we do not claim a merge).
            if len(role_profiles) == 1 and "mid" in ok_roles:
                return role_pcm[rep_role], False, None, None
            # Other single survivors keep the built profile with a weak-fallback tax.
            if len(role_profiles) == 1:
                sole = next(iter(role_profiles.values()))
                return role_pcm[rep_role], True, sole, None
            merged = _merge_pcm_profiles(None, role_profiles=role_profiles)
            return role_pcm[rep_role], partial, merged, None

        # Not enough honest multi-window evidence — still place if we heard music.
        if ok_roles:
            first_role = plan[0][0]
            # Dual plan + usable first window (intro-only etc.): long single-window
            # salvage (~28s) instead of parking the short intro alone.
            if len(plan) == 2 and first_role in ok_roles:
                dual_salvage = True
                # Allow a longer re-listen at the primary start (prior 12s stub is spent
                # as a section window, not as the salvage length).
                skip_reseek_starts.discard(round(float(plan[0][1]), 2))
                if first_role == "intro" and "intro" in role_pcm:
                    edge_fallback = (role_pcm["intro"], "intro")
            elif ok_roles == {"intro"} and "intro" in role_pcm:
                # Long-plan intro-only: same ~28s salvage as dual, then weak intro stub.
                dual_salvage = True
                skip_reseek_starts.discard(round(float(plan[0][1]), 2))
                edge_fallback = (role_pcm["intro"], "intro")
            elif ok_roles == {"late"} and "late" in role_pcm:
                return role_pcm["late"], True, None, "late"
            elif ok_roles == {"intro", "late"} and "intro" in role_pcm and "late" in role_pcm:
                # Start+end, dead middle: weak place from both edges (not genre defer).
                role_profiles = {
                    role: build_profile(role_pcm[role]) for role in ("intro", "late")
                }
                merged = _merge_pcm_profiles(None, role_profiles=role_profiles)
                return role_pcm["intro"], True, merged, "ends"
            else:
                # Any other audible partial: weak place from what we got.
                rep = (
                    "mid"
                    if "mid" in role_pcm
                    else ("intro" if "intro" in role_pcm else next(iter(role_pcm)))
                )
                role_profiles = {role: build_profile(pcm) for role, pcm in role_pcm.items()}
                if len(role_profiles) == 1:
                    return role_pcm[rep], True, next(iter(role_profiles.values())), rep
                merged = _merge_pcm_profiles(None, role_profiles=role_profiles)
                tag = "ends" if ok_roles == {"intro", "late"} else rep
                return role_pcm[rep], True, merged, tag

    # Single-window / silence-fallback / dual-salvage path.
    if dur_s <= 0.0 or dual_salvage:
        # Unknown duration or intro-only salvage: prior ~28s single listen.
        win_s = LONG_SINGLE_WINDOW_S
    elif dur_s < 40.0:
        win_s = min(LONG_SINGLE_WINDOW_S, max(SECTION_WINDOW_S, dur_s))
    else:
        win_s = LONG_SINGLE_WINDOW_S
    starts = [float(ss) for _role, ss in plan]
    if dual_salvage:
        # Prefer primary / audible-start first for the long salvage listen.
        primary = float(plan[0][1])
        starts = [primary] + [s for s in starts if abs(s - primary) > 1e-9]
    if dur_s > 0.0:
        mid_fb = _clamp_seek_s(
            (audible_off if audible_off > 0.0 else 0.0) + max(0.0, dur_s - audible_off) * 0.35,
            dur_s,
            win_s,
        )
        if mid_fb not in starts:
            starts.append(mid_fb)
    head = float(audible_off) if audible_off > 0.0 else 0.0
    if head not in starts:
        starts.append(head)
    if 0.0 not in starts and audible_off <= 0.0:
        starts.append(0.0)

    seen: set[float] = set()
    for index, ss in enumerate(starts):
        if decode_abort_requested():
            return None, False, None, None
        key = round(float(ss), 2)
        if key in seen or key in skip_reseek_starts:
            continue
        seen.add(key)
        pcm = _decode_pcm(path, start_s=float(ss), duration_s=win_s)
        if pcm is not None and _pcm_signal_ok(pcm):
            # Unknown-duration / single listen: if the buffer itself has a long
            # silent head (game cue), spend one more listen from the first sound.
            if dur_s <= 0.0 and not dual_salvage and index == 0 and abs(float(ss)) < 0.01:
                lead = _leading_silence_s(pcm)
                if lead >= 2.5:
                    pcm2 = _decode_pcm(path, start_s=float(lead), duration_s=win_s)
                    if pcm2 is not None and _pcm_signal_ok(pcm2):
                        # Successful audible re-seek is a full single listen — not
                        # a weak fallback tax (game-cue / front-loaded silence).
                        return pcm2, False, None, None
            # Longer salvage after multi-window honesty failed — still fallback-taxed.
            # If we only ever had intro evidence, keep the intro edge tag so a pad
            # head cannot look mid-trust after a ~28s re-listen of the same region.
            if edge_fallback is not None:
                return pcm, True, None, edge_fallback[1]
            return pcm, bool(index > 0 or dual_salvage), None, None
    # Longer salvage found nothing — weak-place the stashed intro stub if we have it.
    if edge_fallback is not None:
        pcm_fb, tag_fb = edge_fallback
        return pcm_fb, True, None, tag_fb
    return None, False, None, None


def _genre_pair_conflict(tag_key: str | None, path_key: str | None) -> bool:
    """True when tag and path genre seeds disagree strongly on the map."""
    if not tag_key or not path_key or tag_key == path_key:
        return False
    tv, te = GENRE_MOOD[tag_key]
    pv, pe = GENRE_MOOD[path_key]
    return float(np.hypot(tv - pv, te - pe)) > 0.35


def mood_confidence(
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
    multi_window: bool = False,
    speech_share: float = 0.0,
    onset_strength: float | None = None,
    pulse_rejected: bool = False,
) -> tuple[float, str]:
    """Graduated placement confidence in 0..1 plus a short evidence note for tooltips."""
    from meridian.acoustic import confidence_from_evidence

    return confidence_from_evidence(
        tag_key=tag_key,
        path_key=path_key,
        pcm_ok=pcm_ok,
        pcm_fallback=pcm_fallback,
        pcm_unstable=pcm_unstable,
        bpm_ok=bpm_ok,
        bpm_conflict=bpm_conflict,
        replaygain=replaygain,
        keyword_hit=keyword_hit,
        weak_tags=weak_tags,
        jitter=jitter,
        variation=variation,
        flux=flux,
        onset_consistency=onset_consistency,
        genre_conflict=_genre_pair_conflict(tag_key, path_key),
        multi_window=multi_window,
        speech_share=speech_share,
        onset_strength=onset_strength,
        pulse_rejected=pulse_rejected,
    )


def confidence_low_trust(confidence: float) -> bool:
    """Compat flag: dimmest band / smooth target."""
    return float(confidence) < CONFIDENCE_LOW


def _aubio_rhythm(pcm: np.ndarray, samplerate: int = 11025) -> tuple[float | None, float]:
    """Return (bpm_or_None, kinetic_from_onsets in 0..1). Soft-fails if aubio is missing."""
    from meridian.acoustic import onset_stats

    bpm, stats = onset_stats(pcm, samplerate)
    return bpm, float(stats["kinetic"])


def _bpm_nudge(energy: float, out_bpm: float | None, *, soft: bool = False) -> float:
    if out_bpm is None or not np.isfinite(out_bpm) or out_bpm <= 0.0:
        return energy
    pace = float(np.clip((out_bpm - 70) / 110, 0, 1))
    w = 0.18 if soft else 0.30
    return float(np.clip((1.0 - w) * energy + w * pace, 0.03, 0.97))


def _energy_bpm_for_nudge(
    tag_bpm: float | None,
    detected_bpm: float | None,
    *,
    onset_consistency: float,
    unstable: bool,
) -> float | None:
    """Pick which BPM drives the energy nudge when tag and waveform disagree."""
    if tag_bpm is None:
        return detected_bpm
    if detected_bpm is None:
        return tag_bpm
    if abs(float(tag_bpm) - float(detected_bpm)) <= 18.0:
        return tag_bpm
    # Conflict: steady rhythm → trust detected for pace; messy audio → keep tag.
    if onset_consistency > 0.70 and not unstable:
        return detected_bpm
    return tag_bpm


def _trim_silence(pcm: np.ndarray, floor: float = 0.012) -> np.ndarray:
    """Drop leading/trailing near-silence so RMS/crest aren't skewed."""
    from meridian.acoustic import trim_silence

    return trim_silence(pcm, floor=floor)


def _spectral_slice(
    pcm: np.ndarray, start: int, n: int, samplerate: int
) -> tuple[float, float, float]:
    """Return (bright, bass_share, flatness) for one window."""
    from meridian.acoustic import spectral_slice

    bright, bass, flatness, _bands, _c = spectral_slice(pcm, start, n, samplerate)
    return bright, bass, flatness


def _pcm_mood_cues(
    pcm: np.ndarray, samplerate: int = 11025
) -> tuple[float, float, float | None, bool, object]:
    """Derive valence/energy cues from one already-decoded PCM buffer (no extra I/O).

    Returns (valence, energy, bpm, unstable, profile). Uses local windows inside
    the existing decode only — no distributed track-wide sampling.
    """
    from meridian.acoustic import build_profile

    profile = build_profile(pcm, samplerate=samplerate)
    return profile.valence, profile.energy, profile.bpm, profile.unstable, profile


def _cue_supports_move(cue: float, move: float) -> bool:
    """A cue in the muddy middle does not count as agreement."""
    if abs(move) <= 0.12:
        return True
    cue_delta = float(cue) - 0.5
    if abs(cue_delta) < 0.08:
        return False
    return move * cue_delta > 0


def _listen_cues_agree(
    *,
    bright: float,
    flux: float,
    valence_pcm: float,
    energy_pcm: float,
    seed_v: float,
    seed_e: float,
) -> bool:
    """True when brightness and motion point the same way as the listen's move."""
    e_delta = float(energy_pcm) - float(seed_e)
    v_delta = float(valence_pcm) - float(seed_v)
    if abs(e_delta) <= 0.12 and abs(v_delta) <= 0.12:
        return False
    return _cue_supports_move(flux, e_delta) and _cue_supports_move(bright, v_delta)


def _retain_clean_cue(
    value: float,
    seed: float,
    cue: float,
    pcm: float,
    ceiling: float,
) -> float:
    """Keep a slice of a strong clean cue that the genre clamp would otherwise drop."""
    if abs(float(cue) - 0.5) < 0.16:
        return float(value)
    cue_delta = float(cue) - 0.5
    move = float(pcm) - float(seed)
    if abs(move) > 0.05 and move * cue_delta <= 0:
        return float(value)
    step = max(-ceiling, min(ceiling, cue_delta * 0.40))
    target = float(seed) + step
    pulled = float(value) + 0.55 * (target - float(value))
    kept = float(seed) + max(-ceiling, min(ceiling, pulled - float(seed)))
    return float(np.clip(kept, 0.03, 0.97))


def analyze_audio(
    path: str,
    genre: str,
    title: str,
    artist: str,
    bpm: float | None,
    *,
    year: int | None = None,
    extra_text: str = "",
    albumartist: str = "",
    composer: str = "",
    replaygain_db: float | None = None,
    duration_ms: int = 0,
) -> MoodResult:
    seed = genre_seed(
        genre,
        title,
        artist,
        path=path,
        year=year,
        extra_text=extra_text,
        albumartist=albumartist,
        composer=composer,
        replaygain_db=replaygain_db,
    )
    valence, energy = seed.valence, seed.energy
    # Working seed coords — keyword may be zeroed when PCM clearly contradicts.
    seed_v, seed_e = float(seed.valence), float(seed.energy)
    keyword_hit = bool(seed.keyword_hit)
    tag_bpm = _coerce_bpm(bpm)
    tag_bpm_ok = tag_bpm is not None
    weak_tags = _weak_tag_ext(path)
    # Strong acoustic genre (tag or path) + BPM: tiny PCM residual only.
    # Container labels (OST/game/…) stay clamp neighborhoods but do not soft-lock PCM.
    soft_pcm_only = (
        seed.clamp_match and tag_bpm_ok and not weak_tags and not seed.container_only
    )

    detected_bpm: float | None = None
    pcm_ok = False
    pcm_fallback = False
    pcm_unstable = False
    profile = None
    soft_shift = SOFT_PCM_MAX_SHIFT

    pcm, pcm_fallback, merged_profile, edge_salvage = _decode_pcm_with_fallback(
        path, duration_ms=duration_ms
    )
    multi_window = False
    if pcm is not None:
        pcm_ok = True
        # Weak edge/ends salvage — always tax as PCM fallback (not full multi-window credit).
        if edge_salvage:
            pcm_fallback = True
        if merged_profile is not None:
            profile = merged_profile
            valence_pcm = float(profile.valence)
            energy_pcm = float(profile.energy)
            detected_bpm = profile.bpm
            pcm_unstable = bool(profile.unstable)
            multi_window = True
        else:
            valence_pcm, energy_pcm, detected_bpm, pcm_unstable, profile = _pcm_mood_cues(pcm)
        if edge_salvage:
            multi_window = False
        detected_bpm = _coerce_bpm(detected_bpm)
        # A refused pulse must not widen Kinetic or confirm a tag BPM.
        pulse_rejected = bool(profile is not None and getattr(profile, "pulse_rejected", False))
        if pulse_rejected:
            detected_bpm = None

        onset_c = _finite_attr(profile, "onset_consistency", 0.5) if profile else 0.5
        variation = _finite_attr(profile, "variation", 0.0) if profile else 0.0
        unstable = bool(pcm_unstable or variation > 0.28)
        # Zero keyword nudge per axis when the listen clearly contradicts it —
        # leave genre alone (undo uses applied deltas / keyword_base, not raw pre-clip).
        if keyword_hit:
            contra_v, contra_e = _keyword_contradiction_axes(
                keyword_dv=float(seed.keyword_dv),
                keyword_de=float(seed.keyword_de),
                seed_valence=float(seed.valence),
                seed_energy=float(seed.energy),
                pcm_valence=valence_pcm,
                pcm_energy=energy_pcm,
                onset_consistency=onset_c,
                unstable=unstable,
                variation=variation,
                keyword_base_valence=seed.keyword_base_valence,
                keyword_base_energy=seed.keyword_base_energy,
            )
            if contra_v:
                seed_v = float(np.clip(seed.valence - float(seed.keyword_dv), 0.03, 0.97))
            if contra_e:
                seed_e = float(np.clip(seed.energy - float(seed.keyword_de), 0.03, 0.97))
            if contra_v or contra_e:
                # Keep keyword_hit only if an uncontradicted axis still applied.
                remain_v = abs(float(seed.keyword_dv)) > 1e-9 and not contra_v
                remain_e = abs(float(seed.keyword_de)) > 1e-9 and not contra_e
                keyword_hit = bool(remain_v or remain_e)
        disagree = float(np.hypot(valence_pcm - seed_v, energy_pcm - seed_e))
        genre_conflict = _genre_pair_conflict(seed.tag_key, seed.path_key)
        # Good metadata: real genre, sane tag BPM, not container/weak/conflict.
        # Strong onset evidence: steady rhythm + real BPM/onset cues (not unstable).
        meta_accurate = bool(soft_pcm_only and not genre_conflict)
        strong_onset_ev = bool(
            onset_c > 0.70
            and not unstable
            and variation < 0.22
            and (detected_bpm is not None or onset_c > 0.78)
        )
        # Structure-aware clamps: steady rhythm + genre disagreement → trust PCM more;
        # unstable / uneven onsets → hug the genre seed tighter (keep this branch tight).
        soft_shift_v = SOFT_PCM_MAX_SHIFT
        soft_shift_e = SOFT_PCM_MAX_SHIFT
        pcm_w_v, pcm_w_e = 0.40, 0.48
        clamp_shift = PCM_MAX_SHIFT
        if onset_c > 0.70 and disagree > 0.12:
            widened = min(0.13, SOFT_PCM_MAX_SHIFT * 1.45)
            soft_shift_v = widened
            # Kinetic widen needs a truly solid onset — mid-shaky must not out-shove
            # a clean listen on good tags (path-to-9 hunt score 5).
            if variation < 0.22 and not unstable:
                soft_shift_e = widened
            pcm_w_v, pcm_w_e = 0.55, 0.62
            clamp_shift = min(PCM_STRONG_ONSET_MAX, PCM_MAX_SHIFT * 1.25)
        elif unstable or onset_c < 0.35:
            soft_shift_v = SOFT_PCM_MAX_SHIFT * 0.55
            soft_shift_e = SOFT_PCM_MAX_SHIFT * 0.55
            pcm_w_v, pcm_w_e = 0.28, 0.32
            clamp_shift = PCM_MAX_SHIFT * 0.70

        # OST/game/score: keep a seed neighborhood but do not hug it like acoustic genres.
        if seed.container_only:
            pcm_w_v = max(pcm_w_v, 0.68)
            pcm_w_e = max(pcm_w_e, 0.75)
            clamp_shift = max(clamp_shift, 0.20)

        # Evidence-gated soft widen: stable PCM that clearly disagrees with the seed.
        # Glow may move farther than Kinetic when metadata is accurate (pace often in tags).
        if (
            soft_pcm_only
            and onset_c > 0.70
            and not unstable
            and variation < 0.22
            and disagree > 0.18
        ):
            soft_shift_v = max(soft_shift_v, EVIDENCE_SOFT_VALENCE_MAX)
            if meta_accurate:
                # Good metadata + strong onset: fine-tune Kinetic only (stay near seed).
                soft_shift_e = min(soft_shift_e, SOFT_PCM_MAX_SHIFT)
            else:
                soft_shift_e = max(
                    soft_shift_e, min(EVIDENCE_SOFT_ENERGY_MAX, EVIDENCE_SOFT_VALENCE_MAX)
                )

        # Metadata-gated Kinetic: less accurate labels + strong onset → stronger PCM pull
        # inside the loosened clamp band (aubio/PCM may correct pace).
        if strong_onset_ev and not meta_accurate:
            pcm_w_e = max(pcm_w_e, 0.70)
            if not soft_pcm_only:
                clamp_shift = max(clamp_shift, min(PCM_STRONG_ONSET_MAX, PCM_MAX_SHIFT * 1.25))
            else:
                # Soft path but conflict/container edge: open Kinetic residual a bit.
                soft_shift_e = max(soft_shift_e, min(EVIDENCE_SOFT_ENERGY_MAX, SOFT_PCM_MAX_SHIFT * 1.35))
        elif strong_onset_ev and meta_accurate:
            # Accurate genre+BPM: keep Kinetic near seed even if Glow widened.
            soft_shift_e = min(soft_shift_e, SOFT_PCM_MAX_SHIFT)

        # Tag vs path conflict: metadata is unreliable — reduce seed authority.
        # Freed weight goes to PCM only in proportion to how trustworthy PCM is
        # (conflict alone must not crank waveform authority to maximum).
        if genre_conflict and seed.tag_key and seed.path_key:
            tv, te = GENRE_MOOD[seed.tag_key]
            pv, pe = GENRE_MOOD[seed.path_key]
            conflict_d = float(np.hypot(tv - pv, te - pe))
            conflict_amt = float(np.clip((conflict_d - 0.35) / 0.40, 0.0, 1.0))
            if unstable or onset_c < 0.35:
                pcm_claim = 0.20
            elif variation > 0.22 or onset_c < 0.55:
                pcm_claim = 0.50
            else:
                pcm_claim = 0.85
            transfer = conflict_amt * pcm_claim
            # Soft residual: open the envelope with freed metadata weight.
            # Never claw back room evidence/structure already opened; only widen
            # up to the evidence-soft ceilings.
            soft_shift_v = max(
                soft_shift_v,
                min(EVIDENCE_SOFT_VALENCE_MAX, soft_shift_v + transfer * 0.045),
            )
            soft_shift_e = max(
                soft_shift_e,
                min(EVIDENCE_SOFT_ENERGY_MAX, soft_shift_e + transfer * 0.035),
            )
            # Clamp blend: move weight from seed → PCM (capped; not untagged-level).
            pcm_w_v = min(0.58, pcm_w_v + transfer * 0.18)
            pcm_w_e = min(0.72, pcm_w_e + transfer * 0.18)
            clamp_shift = min(PCM_STRONG_ONSET_MAX, clamp_shift + transfer * 0.025)

        # Spectral slope as confidence/permission: muffled → stay nearer sticker;
        # clear dark low end may still move Shadow (Glow dampened when muffled).
        # None-aware reads — exact 0.0 must not become the 0.5 default.
        slope = _finite_attr(profile, "spectral_slope", 0.5) if profile else 0.5
        sub = _finite_attr(profile, "sub_bass", 0.0) if profile else 0.0
        bright_pcm = _finite_attr(profile, "brightness", 0.5) if profile else 0.5
        if slope < SLOPE_MUFFLED_MAX:
            muff = float(np.clip((SLOPE_MUFFLED_MAX - slope) / max(SLOPE_MUFFLED_MAX, 1e-6), 0.0, 1.0))
            soft_shift_v *= 1.0 - 0.45 * muff
            soft_shift_e *= 1.0 - 0.35 * muff
            pcm_w_v *= 1.0 - 0.35 * muff
            pcm_w_e *= 1.0 - 0.25 * muff
            clamp_shift *= 1.0 - 0.25 * muff
        elif slope >= SLOPE_CLEAR_MIN and bright_pcm < 0.42 and sub >= 0.12:
            # Clear dark low end: allow Shadow room (do not shrink valence pull).
            soft_shift_v = max(soft_shift_v, min(EVIDENCE_SOFT_VALENCE_MAX, soft_shift_v * 1.15))
            pcm_w_v = min(0.62, pcm_w_v + 0.06)

        # Clear listen that agrees with itself and disagrees with the sticker
        # keeps the evidence widen. Good tags no longer pull that move back.
        flux_pcm = _finite_attr(profile, "flux", 0.5) if profile else 0.5
        if (
            soft_pcm_only
            and onset_c > 0.70
            and not unstable
            and variation < 0.22
            and disagree > 0.18
            and slope >= SLOPE_CLEAR_MIN
            and _listen_cues_agree(
                bright=bright_pcm,
                flux=flux_pcm,
                valence_pcm=valence_pcm,
                energy_pcm=energy_pcm,
                seed_v=seed_v,
                seed_e=seed_e,
            )
        ):
            soft_shift_v = max(soft_shift_v, EVIDENCE_SOFT_VALENCE_MAX)
            soft_shift_e = max(soft_shift_e, EVIDENCE_SOFT_ENERGY_MAX)

        soft_shift = soft_shift_e  # post-BPM energy reclamp uses Kinetic envelope

        if weak_tags:
            # Dump formats: distrust tags; lean hard on waveform.
            valence = float(np.clip(0.22 * seed_v + 0.78 * valence_pcm, 0.03, 0.97))
            energy = float(np.clip(0.18 * seed_e + 0.82 * energy_pcm, 0.03, 0.97))
        elif soft_pcm_only:
            # Genre+BPM soft residual. Glow may evidence-widen; Kinetic stays a
            # fine-tune when metadata is accurate + onset is strong (see gates above).
            valence = float(
                np.clip(
                    seed_v
                    + float(np.clip(valence_pcm - seed_v, -soft_shift_v, soft_shift_v)),
                    0.03,
                    0.97,
                )
            )
            energy = float(
                np.clip(
                    seed_e
                    + float(np.clip(energy_pcm - seed_e, -soft_shift_e, soft_shift_e)),
                    0.03,
                    0.97,
                )
            )
        elif seed.clamp_match:
            valence = float(np.clip((1.0 - pcm_w_v) * seed_v + pcm_w_v * valence_pcm, 0.03, 0.97))
            energy = float(np.clip((1.0 - pcm_w_e) * seed_e + pcm_w_e * energy_pcm, 0.03, 0.97))
            valence = float(
                np.clip(
                    seed_v
                    + float(np.clip(valence - seed_v, -clamp_shift, clamp_shift)),
                    0.03,
                    0.97,
                )
            )
            energy = float(
                np.clip(
                    seed_e
                    + float(np.clip(energy - seed_e, -clamp_shift, clamp_shift)),
                    0.03,
                    0.97,
                )
            )
        else:
            # Messy / untagged: trust waveform cues heavily.
            valence = float(np.clip(0.15 * seed_v + 0.85 * valence_pcm, 0.03, 0.97))
            energy = float(np.clip(0.12 * seed_e + 0.88 * energy_pcm, 0.03, 0.97))

    # Prefer a real tag BPM for storage; energy nudge may use detected when rhythm is steady.
    out_bpm = tag_bpm if tag_bpm is not None else detected_bpm
    onset_for_bpm = _finite_attr(profile, "onset_consistency", 0.5) if profile else 0.5
    variation_for_bpm = _finite_attr(profile, "variation", 0.0) if profile else 0.0
    # Pulse clarity — not the BPM number alone — permits a Kinetic step when pulse
    # was measured. A refused pulse skips the step entirely. Without PCM, keep the
    # prior tag-BPM nudge.
    pulse_rejected = bool(profile is not None and getattr(profile, "pulse_rejected", False))
    if pcm_ok:
        # A refused listen drops the detected BPM above. A tag pace may still step.
        pulse_clear = bool(
            onset_for_bpm >= PULSE_CLARITY_BPM_MIN
            and not bool(pcm_unstable)
            and variation_for_bpm < 0.28
        )
        if pulse_clear:
            nudge_bpm = _energy_bpm_for_nudge(
                tag_bpm,
                detected_bpm,
                onset_consistency=onset_for_bpm,
                unstable=bool(pcm_unstable),
            )
            energy = _bpm_nudge(
                energy,
                nudge_bpm,
                soft=soft_pcm_only or (tag_bpm is None and detected_bpm is not None),
            )
    else:
        energy = _bpm_nudge(energy, tag_bpm, soft=soft_pcm_only)
    if soft_pcm_only and pcm_ok:
        energy = float(
            np.clip(
                seed_e
                + float(
                    np.clip(
                        energy - seed_e,
                        -soft_shift,
                        soft_shift,
                    )
                ),
                0.03,
                0.97,
            )
        )
    if soft_pcm_only and pcm_ok and profile is not None:
        slope_now = _finite_attr(profile, "spectral_slope", 0.5)
        if (
            slope_now >= SLOPE_CLEAR_MIN
            and not bool(pcm_unstable)
            and _finite_attr(profile, "variation", 0.0) < 0.22
        ):
            valence = _retain_clean_cue(
                valence,
                seed_v,
                _finite_attr(profile, "brightness", 0.5),
                valence_pcm,
                EVIDENCE_SOFT_VALENCE_MAX,
            )
            energy = _retain_clean_cue(
                energy,
                seed_e,
                _finite_attr(profile, "flux", 0.5),
                energy_pcm,
                EVIDENCE_SOFT_ENERGY_MAX,
            )

    used_jitter = False
    if (
        not seed.clamp_match
        and not pcm_ok
        and abs(valence - DEFAULT_VALENCE) < 1e-6
        and abs(energy - DEFAULT_ENERGY) < 1e-6
    ):
        valence, energy = _stable_jitter(path)
        used_jitter = True

    bpm_conflict = False
    bpm_ok = False
    # A refused listen does not fight the tag, and the tag pace still counts.
    if tag_bpm is not None and detected_bpm is not None and not pulse_rejected:
        if abs(float(tag_bpm) - float(detected_bpm)) > 18.0:
            bpm_conflict = True
        else:
            bpm_ok = True
    elif tag_bpm is not None or (detected_bpm is not None and not pulse_rejected):
        # Only credit BPM when we have a coerced (finite, positive) value.
        bpm_ok = True

    if not np.isfinite(valence):
        valence = float(seed_v)
    if not np.isfinite(energy):
        energy = float(seed_e)
    valence = float(np.clip(valence, 0.03, 0.97))
    energy = float(np.clip(energy, 0.03, 0.97))

    confidence, note = mood_confidence(
        tag_key=seed.tag_key,
        path_key=seed.path_key,
        pcm_ok=pcm_ok,
        pcm_fallback=pcm_fallback,
        pcm_unstable=pcm_unstable,
        bpm_ok=bpm_ok,
        bpm_conflict=bpm_conflict,
        replaygain=replaygain_db is not None and np.isfinite(float(replaygain_db)),
        keyword_hit=keyword_hit,
        weak_tags=weak_tags,
        jitter=used_jitter,
        variation=float(getattr(profile, "variation", 0.0) or 0.0) if profile else 0.0,
        flux=float(getattr(profile, "flux", 0.0)) if profile else None,
        onset_consistency=float(getattr(profile, "onset_consistency", 0.5)) if profile else None,
        multi_window=multi_window,
        speech_share=float(getattr(profile, "speech_share", 0.0) or 0.0) if profile else 0.0,
        onset_strength=_optional_profile_float(profile, "onset_strength") if profile else None,
        pulse_rejected=pulse_rejected,
    )
    if edge_salvage:
        # Keep edge salvage clearly weaker than seed-only / mid-body (dimmer on map).
        # Still places so the collection is complete — just obviously unsure.
        confidence = min(float(confidence), CONFIDENCE_LOW - 0.08)
        edge_note = {
            "intro": "intro only",
            "late": "end only",
            "ends": "ends only",
            "mid": "mid only",
        }.get(edge_salvage, "partial listen")
        if edge_note not in note:
            note = f"{note} · {edge_note}" if note else edge_note
    # Tag/path/keyword evidence — enough to conclude without waveform.
    # Original seed keyword still counts as seed evidence even if PCM undid the nudge.
    seed_evidence = bool(seed.clamp_match or seed.keyword_hit)
    if not pcm_ok and seed_evidence:
        confidence = min(float(confidence), CONFIDENCE_LOW - 0.01)
        if "seed only" not in note:
            note = f"{note} · seed only" if note else "seed only"

    tonal = float(getattr(profile, "tonal", 0.5) or 0.5) if profile else None
    timbre_flag = _timbre_flag_from_tonal(tonal) if pcm_ok else None
    timbre_source = (
        str(getattr(profile, "timbre_source", None) or "") or None if profile and pcm_ok else None
    )
    shape_note = (
        str(getattr(profile, "shape_note", None) or "") or None if profile and pcm_ok else None
    )
    # Single-window listens do not get a multi-section shape.
    # Ends salvage is dual-edge and may carry a shape; everything else clears.
    if not multi_window and edge_salvage != "ends":
        shape_note = None
    steer = steer_weight_from_listen(
        pcm_ok=pcm_ok,
        multi_window=multi_window,
        pcm_fallback=pcm_fallback,
        pcm_unstable=pcm_unstable,
        edge_salvage=edge_salvage,
        seed_evidence=seed_evidence,
        variation=float(getattr(profile, "variation", 0.0) or 0.0) if profile else 0.0,
    )

    return MoodResult(
        valence=float(valence),
        energy=float(energy),
        bpm=out_bpm,
        confidence=float(confidence),
        low_trust=confidence_low_trust(confidence),
        confidence_note=note,
        onset_consistency=(
            float(getattr(profile, "onset_consistency", 0.5)) if profile else None
        ),
        acoustic_flux=float(getattr(profile, "flux", 0.0)) if profile else None,
        brightness=float(getattr(profile, "brightness", 0.5)) if profile else None,
        shape_note=shape_note,
        timbre_flag=timbre_flag,
        timbre_source=timbre_source,
        steer_weight=float(steer),
        pcm_ok=bool(pcm_ok),
        seed_evidence=seed_evidence,
    )
