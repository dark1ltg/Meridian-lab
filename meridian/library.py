from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QStandardPaths


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS folders (
    path TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    title TEXT,
    artist TEXT,
    album TEXT,
    albumartist TEXT,
    genre TEXT,
    duration_ms INTEGER DEFAULT 0,
    year INTEGER,
    bpm REAL,
    valence REAL NOT NULL DEFAULT 0.5,
    energy REAL NOT NULL DEFAULT 0.5,
    mood_confidence REAL NOT NULL DEFAULT 0.5,
    confidence_note TEXT,
    low_trust INTEGER NOT NULL DEFAULT 0,
    pinned INTEGER NOT NULL DEFAULT 0,
    loved INTEGER NOT NULL DEFAULT 0,
    play_count INTEGER NOT NULL DEFAULT 0,
    skip_count INTEGER NOT NULL DEFAULT 0,
    last_played REAL,
    added_at REAL,
    mtime REAL,
    analyzed INTEGER NOT NULL DEFAULT 0,
    acoustic_flux REAL,
    onset_consistency REAL,
    brightness REAL,
    shape_note TEXT,
    timbre_flag TEXT,
    timbre_source TEXT,
    steer_weight REAL,
    playback_denied INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_tracks_mood ON tracks(valence, energy);
"""


@dataclass(slots=True)
class Track:
    id: int
    path: str
    title: str
    artist: str
    album: str
    albumartist: str
    genre: str
    duration_ms: int
    year: int | None
    bpm: float | None
    valence: float
    energy: float
    mood_confidence: float
    confidence_note: str
    low_trust: bool
    pinned: bool
    loved: bool
    play_count: int
    skip_count: int
    last_played: float | None
    added_at: float | None
    mtime: float | None
    analyzed: bool
    acoustic_flux: float | None = None
    onset_consistency: float | None = None
    brightness: float | None = None
    shape_note: str | None = None
    timbre_flag: str | None = None
    timbre_source: str | None = None
    steer_weight: float | None = None

    @property
    def label(self) -> str:
        artist = self.artist or self.albumartist or "Unknown"
        title = self.title or Path(self.path).stem
        return f"{artist} — {title}"

    @property
    def short_title(self) -> str:
        return self.title or Path(self.path).stem


def data_dir() -> Path:
    root = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    root.mkdir(parents=True, exist_ok=True)
    return root


class Library:
    def __init__(self, db_path: Path | None = None) -> None:
        self.path = db_path or (data_dir() / "library.sqlite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()
        self._closed = False
        # Process-local: keeps analyze from looping when DB mark_analyze_failed fails.
        self._analyze_denylist: set[int] = set()
        # Process-local: corrupt/unsupported files that failed playback stay out of plans.
        self._playback_denylist: set[int] = set()
        # Per analyze-worker run: how many tracks got real PCM vs deferred.
        self._analyze_pcm_ok_count = 0
        self._analyze_deferred_count = 0
        self._migrate()

    def _migrate(self) -> None:
        with self.lock:
            cols = {str(r[1]) for r in self.conn.execute("PRAGMA table_info(tracks)")}
            if "low_trust" not in cols:
                self.conn.execute(
                    "ALTER TABLE tracks ADD COLUMN low_trust INTEGER NOT NULL DEFAULT 0"
                )
            if "albumartist" not in cols:
                self.conn.execute("ALTER TABLE tracks ADD COLUMN albumartist TEXT")
            if "mood_confidence" not in cols:
                self.conn.execute(
                    "ALTER TABLE tracks ADD COLUMN mood_confidence REAL NOT NULL DEFAULT 0.5"
                )
                # Approximate from existing binary flag until the next analyze.
                self.conn.execute(
                    "UPDATE tracks SET mood_confidence = CASE WHEN low_trust = 1 THEN 0.30 ELSE 0.80 END"
                )
            if "confidence_note" not in cols:
                self.conn.execute("ALTER TABLE tracks ADD COLUMN confidence_note TEXT")
            if "acoustic_flux" not in cols:
                self.conn.execute("ALTER TABLE tracks ADD COLUMN acoustic_flux REAL")
            if "onset_consistency" not in cols:
                self.conn.execute("ALTER TABLE tracks ADD COLUMN onset_consistency REAL")
            if "brightness" not in cols:
                self.conn.execute("ALTER TABLE tracks ADD COLUMN brightness REAL")
            if "shape_note" not in cols:
                self.conn.execute("ALTER TABLE tracks ADD COLUMN shape_note TEXT")
            if "timbre_flag" not in cols:
                self.conn.execute("ALTER TABLE tracks ADD COLUMN timbre_flag TEXT")
            if "timbre_source" not in cols:
                self.conn.execute("ALTER TABLE tracks ADD COLUMN timbre_source TEXT")
            if "steer_weight" not in cols:
                self.conn.execute("ALTER TABLE tracks ADD COLUMN steer_weight REAL")
            if "playback_denied" not in cols:
                self.conn.execute(
                    "ALTER TABLE tracks ADD COLUMN playback_denied INTEGER NOT NULL DEFAULT 0"
                )
            self.conn.commit()
        # Reload durable playback denylist into the process-local set.
        with self.lock:
            denied = self.conn.execute(
                "SELECT id FROM tracks WHERE playback_denied = 1"
            ).fetchall()
        self._playback_denylist = {int(r["id"]) for r in denied}

    def close(self) -> None:
        with self.lock:
            if getattr(self, "_closed", False):
                return
            self._closed = True
            try:
                self.conn.close()
            except Exception:
                pass

    @property
    def closed(self) -> bool:
        return bool(getattr(self, "_closed", False))

    def folders(self) -> list[str]:
        with self.lock:
            if self._closed:
                return []
            rows = self.conn.execute("SELECT path FROM folders ORDER BY path").fetchall()
        return [r["path"] for r in rows]

    def add_folder(self, path: str) -> None:
        with self.lock:
            self.conn.execute("INSERT OR IGNORE INTO folders(path) VALUES (?)", (path,))
            self.conn.commit()

    def remove_folder(self, path: str) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM folders WHERE path = ?", (path,))
            self.conn.commit()

    def upsert_track(self, values: dict) -> int:
        path = values["path"]
        with self.lock:
            existing = self.conn.execute(
                "SELECT id, pinned, analyzed, valence, energy, mood_confidence, "
                "low_trust, confidence_note, brightness, acoustic_flux "
                "FROM tracks WHERE path = ?",
                (path,),
            ).fetchone()
            if existing:
                payload = dict(values)
                # Preserve original import time on updates.
                payload.pop("added_at", None)
                if int(existing["pinned"] or 0):
                    # Pins keep mood + trust metadata; still allow analyzed=0 so
                    # acoustics/BPM refresh on mtime/tag scan (set_analyzed_mood
                    # protects pinned coords).
                    for key in (
                        "valence",
                        "energy",
                        "bpm",
                        "mood_confidence",
                        "low_trust",
                        "confidence_note",
                    ):
                        payload.pop(key, None)
                elif (
                    "analyzed" in payload
                    and int(payload.get("analyzed") or 0) == 0
                    and (
                        # Normal mtime refresh while still marked analyzed.
                        int(existing["analyzed"] or 0) == 1
                        # Rescan zeros analyzed first; prior PCM still leaves
                        # brightness/flux — keep placement until a new decode.
                        or existing["brightness"] is not None
                        or existing["acoustic_flux"] is not None
                    )
                ):
                    # Re-queue analyze but keep PCM placement until a successful
                    # decode lands (avoids seed flash / sticky seed / Rescan wipe).
                    for key in (
                        "valence",
                        "energy",
                        "mood_confidence",
                        "low_trust",
                        "confidence_note",
                    ):
                        payload.pop(key, None)
                fields = [k for k in payload if k != "path"]
                if fields:
                    assignments = ", ".join(f"{k} = ?" for k in fields)
                    params = [payload[k] for k in fields] + [path]
                    self.conn.execute(f"UPDATE tracks SET {assignments} WHERE path = ?", params)
                tid = int(existing["id"])
                # Any scan refresh of an existing file clears sticky playback denial
                # (fixed/replaced media should get another chance without full Rescan).
                self._playback_denylist.discard(tid)
                self.conn.execute(
                    "UPDATE tracks SET playback_denied = 0 WHERE id = ?", (tid,)
                )
                # Re-queue for analysis must clear sticky analyze denylist.
                if "analyzed" in payload and int(payload.get("analyzed") or 0) == 0:
                    self._analyze_denylist.discard(tid)
                self.conn.commit()
                return tid
            cols = ", ".join(values)
            placeholders = ", ".join("?" for _ in values)
            cur = self.conn.execute(
                f"INSERT INTO tracks ({cols}) VALUES ({placeholders})",
                list(values.values()),
            )
            self.conn.commit()
            tid = int(cur.lastrowid)
            if int(values.get("analyzed") or 0) == 0:
                self._analyze_denylist.discard(tid)
                self._playback_denylist.discard(tid)
            return tid

    def set_mood(self, track_id: int, valence: float, energy: float, pinned: bool = True) -> None:
        with self.lock:
            # User pin ⇒ full confidence; clear low-trust dimming.
            self.conn.execute(
                """
                UPDATE tracks
                SET valence = ?, energy = ?, pinned = ?,
                    mood_confidence = 1.0, low_trust = 0, confidence_note = 'pinned'
                WHERE id = ?
                """,
                (valence, energy, int(pinned), track_id),
            )
            self.conn.commit()

    @staticmethod
    def _append_note(existing: str | None, addition: str) -> str:
        base = (existing or "").strip()
        add = addition.strip()
        if not add:
            return base
        if not base:
            return add
        if add in base:
            return base
        return f"{base} · {add}"

    def nudge_mood_from_listen(
        self,
        track_id: int,
        *,
        lens_x: float,
        lens_y: float,
        skipped: bool,
        amount: float = 0.018,
        max_step: float = 0.032,
    ) -> bool:
        """Offline personalization: tiny unpinned drift from skip vs finish under the lens."""
        from meridian.features import confidence_low_trust

        with self.lock:
            row = self.conn.execute(
                """
                SELECT valence, energy, pinned, mood_confidence, confidence_note
                FROM tracks WHERE id = ?
                """,
                (track_id,),
            ).fetchone()
            if not row or int(row["pinned"]):
                return False
            v = float(row["valence"])
            e = float(row["energy"])
            conf = float(row["mood_confidence"] if row["mood_confidence"] is not None else 0.5)
            dx = float(lens_x) - v
            dy = float(lens_y) - e
            if skipped:
                dx = -dx
                dy = -dy
            step = float(amount)
            dv = max(-max_step, min(max_step, step * dx))
            de = max(-max_step, min(max_step, step * dy))
            if abs(dv) < 1e-6 and abs(de) < 1e-6:
                # If already on the lens, push skips slightly toward higher energy variance.
                if skipped:
                    de = max_step * 0.35
                else:
                    return False
            nv = max(0.03, min(0.97, v + dv))
            ne = max(0.03, min(0.97, e + de))
            if skipped:
                nconf = max(0.12, conf - 0.05)
                note = self._append_note(row["confidence_note"], "listen skip")
            else:
                nconf = min(0.95, conf + 0.06)
                note = self._append_note(row["confidence_note"], "listen finish")
            self.conn.execute(
                """
                UPDATE tracks
                SET valence = ?, energy = ?, mood_confidence = ?, low_trust = ?, confidence_note = ?
                WHERE id = ? AND pinned = 0
                """,
                (nv, ne, nconf, int(confidence_low_trust(nconf)), note, track_id),
            )
            self.conn.commit()
            return True

    def reset_analyze_session_stats(self) -> None:
        """Clear per-run PCM/defer counters before an AnalyzeWorker pass."""
        self._analyze_pcm_ok_count = 0
        self._analyze_deferred_count = 0

    def analyze_session_stats(self) -> tuple[int, int]:
        """Return ``(pcm_ok_count, deferred_count)`` for the current analyze run."""
        return int(self._analyze_pcm_ok_count), int(self._analyze_deferred_count)

    def set_analyzed_mood(
        self,
        track_id: int,
        valence: float,
        energy: float,
        bpm: float | None,
        *,
        confidence: float = 0.5,
        low_trust: bool = False,
        confidence_note: str = "",
        onset_consistency: float | None = None,
        acoustic_flux: float | None = None,
        brightness: float | None = None,
        shape_note: str | None = None,
        timbre_flag: str | None = None,
        timbre_source: str | None = None,
        steer_weight: float | None = None,
        from_pcm: bool = True,
    ) -> None:
        with self.lock:
            if from_pcm:
                self.conn.execute(
                    """
                    UPDATE tracks
                    SET valence = CASE WHEN pinned = 1 THEN valence ELSE ? END,
                        energy = CASE WHEN pinned = 1 THEN energy ELSE ? END,
                        bpm = CASE WHEN pinned = 1 THEN bpm ELSE ? END,
                        mood_confidence = CASE WHEN pinned = 1 THEN mood_confidence ELSE ? END,
                        low_trust = CASE WHEN pinned = 1 THEN low_trust ELSE ? END,
                        confidence_note = CASE WHEN pinned = 1 THEN confidence_note ELSE ? END,
                        onset_consistency = ?,
                        acoustic_flux = ?,
                        brightness = ?,
                        shape_note = COALESCE(?, shape_note),
                        timbre_flag = ?,
                        timbre_source = ?,
                        steer_weight = ?,
                        analyzed = 1
                    WHERE id = ?
                    """,
                    (
                        valence,
                        energy,
                        bpm,
                        float(confidence),
                        int(low_trust),
                        confidence_note or "",
                        onset_consistency,
                        acoustic_flux,
                        brightness,
                        shape_note,
                        timbre_flag,
                        timbre_source,
                        steer_weight,
                        track_id,
                    ),
                )
            else:
                # Seed-only conclude: never NULL out prior waveform acoustics, and do
                # not overwrite a prior PCM placement with a weaker tag/path guess.
                self.conn.execute(
                    """
                    UPDATE tracks
                    SET valence = CASE
                            WHEN pinned = 1 THEN valence
                            WHEN brightness IS NOT NULL OR acoustic_flux IS NOT NULL THEN valence
                            ELSE ? END,
                        energy = CASE
                            WHEN pinned = 1 THEN energy
                            WHEN brightness IS NOT NULL OR acoustic_flux IS NOT NULL THEN energy
                            ELSE ? END,
                        bpm = CASE
                            WHEN pinned = 1 THEN bpm
                            WHEN brightness IS NOT NULL OR acoustic_flux IS NOT NULL THEN bpm
                            ELSE ? END,
                        mood_confidence = CASE
                            WHEN pinned = 1 THEN mood_confidence
                            WHEN brightness IS NOT NULL OR acoustic_flux IS NOT NULL THEN mood_confidence
                            ELSE ? END,
                        low_trust = CASE
                            WHEN pinned = 1 THEN low_trust
                            WHEN brightness IS NOT NULL OR acoustic_flux IS NOT NULL THEN low_trust
                            ELSE ? END,
                        confidence_note = CASE
                            WHEN pinned = 1 THEN confidence_note
                            WHEN brightness IS NOT NULL OR acoustic_flux IS NOT NULL THEN confidence_note
                            ELSE ? END,
                        onset_consistency = COALESCE(?, onset_consistency),
                        acoustic_flux = COALESCE(?, acoustic_flux),
                        brightness = COALESCE(?, brightness),
                        shape_note = COALESCE(?, shape_note),
                        timbre_flag = COALESCE(?, timbre_flag),
                        timbre_source = COALESCE(?, timbre_source),
                        steer_weight = CASE
                            WHEN brightness IS NOT NULL OR acoustic_flux IS NOT NULL THEN steer_weight
                            ELSE COALESCE(?, steer_weight) END,
                        analyzed = 1
                    WHERE id = ?
                    """,
                    (
                        valence,
                        energy,
                        bpm,
                        float(confidence),
                        int(low_trust),
                        confidence_note or "",
                        onset_consistency,
                        acoustic_flux,
                        brightness,
                        shape_note,
                        timbre_flag,
                        timbre_source,
                        steer_weight,
                        track_id,
                    ),
                )
            if from_pcm:
                self._analyze_pcm_ok_count += 1
            self.conn.commit()

    def had_pcm_acoustics(self, track_id: int) -> bool:
        """True when a prior waveform pass left brightness/flux (keep over seed wipe)."""
        with self.lock:
            row = self.conn.execute(
                "SELECT brightness, acoustic_flux FROM tracks WHERE id = ?",
                (int(track_id),),
            ).fetchone()
        if not row:
            return False
        return row["brightness"] is not None or row["acoustic_flux"] is not None

    def requeue_seed_only_without_pcm(self) -> int:
        """One-shot: re-queue seed-only rows that never got waveform (ffmpeg may be back)."""
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT id FROM tracks
                WHERE analyzed = 1 AND pinned = 0
                  AND brightness IS NULL AND acoustic_flux IS NULL
                  AND instr(lower(COALESCE(confidence_note, '')), 'seed only') > 0
                """
            ).fetchall()
            ids = [int(r["id"]) for r in rows]
            if not ids:
                return 0
            self.conn.executemany(
                "UPDATE tracks SET analyzed = 0 WHERE id = ? AND pinned = 0",
                [(i,) for i in ids],
            )
            self.conn.commit()
        for tid in ids:
            self._analyze_denylist.discard(tid)
        return len(ids)

    def mark_analyze_failed(self, track_id: int) -> None:
        """Mark a track analyzed so a poison file cannot loop the analyze worker forever.

        Always denylists in-process first so a failed DB write cannot restart the loop.
        """
        from meridian.features import confidence_low_trust

        self._analyze_denylist.add(int(track_id))
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                with self.lock:
                    row = self.conn.execute(
                        "SELECT pinned, mood_confidence, confidence_note FROM tracks WHERE id = ?",
                        (track_id,),
                    ).fetchone()
                    if not row:
                        return
                    if int(row["pinned"] or 0):
                        # Pins keep mood/trust; only clear the pending-analyze flag.
                        self.conn.execute(
                            "UPDATE tracks SET analyzed = 1 WHERE id = ?", (track_id,)
                        )
                    else:
                        conf = float(
                            row["mood_confidence"]
                            if row["mood_confidence"] is not None
                            else 0.25
                        )
                        conf = min(conf, 0.28)
                        note = self._append_note(row["confidence_note"], "analyze failed")
                        self.conn.execute(
                            """
                            UPDATE tracks
                            SET analyzed = 1, mood_confidence = ?, low_trust = ?, confidence_note = ?
                            WHERE id = ? AND pinned = 0
                            """,
                            (conf, int(confidence_low_trust(conf)), note, track_id),
                        )
                    self.conn.commit()
                return
            except sqlite3.Error as exc:
                last_err = exc
                time.sleep(0.05 * (attempt + 1))
        # Denylist already prevents the session poison loop.
        _ = last_err

    def smooth_album_moods(self, max_shift: float = 0.08, blend: float = 0.30) -> int:
        """Gently pull unpinned tracks toward their album median mood (no ffmpeg).

        Groups by albumartist when present (VA/OST/classical-safe), else artist.
        """
        return self._smooth_group_moods(
            group_sql="""
                SELECT id, artist, albumartist, album, valence, energy, pinned,
                       low_trust, mood_confidence, confidence_note
                FROM tracks
                WHERE analyzed = 1
                  AND album IS NOT NULL AND TRIM(album) != ''
                  AND (
                    (albumartist IS NOT NULL AND TRIM(albumartist) != '')
                    OR (artist IS NOT NULL AND TRIM(artist) != '')
                  )
                """,
            key_fn=lambda row: (
                (
                    (lambda aa, ar: aa if aa and aa.lower() not in {"various artists", "various", "va"} else ar)(
                        str(row["albumartist"] or "").strip(),
                        str(row["artist"] or "").strip(),
                    )
                ).lower(),
                str(row["album"] or "").lower().strip(),
            ),
            min_group=4,
            max_shift=max_shift,
            blend=blend,
            note_tag="album smooth",
        )

    def smooth_artist_moods(self, max_shift: float = 0.06, blend: float = 0.22) -> int:
        """Gently pull unpinned tracks toward their artist median mood (no ffmpeg)."""
        return self._smooth_group_moods(
            group_sql="""
                SELECT id, artist, valence, energy, pinned,
                       low_trust, mood_confidence, confidence_note
                FROM tracks
                WHERE analyzed = 1
                  AND artist IS NOT NULL AND TRIM(artist) != ''
                """,
            key_fn=lambda row: str(row["artist"]).lower(),
            min_group=6,
            max_shift=max_shift,
            blend=blend,
            note_tag="artist smooth",
        )

    def _smooth_group_moods(
        self,
        *,
        group_sql: str,
        key_fn,
        min_group: int,
        max_shift: float,
        blend: float,
        note_tag: str = "group smooth",
    ) -> int:
        from statistics import median

        from meridian.features import CONFIDENCE_LOW, confidence_low_trust

        with self.lock:
            rows = self.conn.execute(group_sql).fetchall()

        groups: dict = {}
        for row in rows:
            groups.setdefault(key_fn(row), []).append(row)

        # valence, energy, confidence, low_trust, note, id
        updates: list[tuple[float, float, float, int, str, int]] = []
        for items in groups.values():
            if len(items) < min_group:
                continue
            # Prefer high-trust median when available so low-trust tracks snap toward
            # confident neighbors; fall back to full-group median.
            trusted = [i for i in items if not int(i["low_trust"] or 0)]
            med_src = trusted if len(trusted) >= 2 else items
            med_v = float(median(float(i["valence"]) for i in med_src))
            med_e = float(median(float(i["energy"]) for i in med_src))
            for item in items:
                if int(item["pinned"]):
                    continue
                # Only nudge low-trust placements — keep confident stars put (#6).
                if not int(item["low_trust"] or 0):
                    continue
                v0 = float(item["valence"])
                e0 = float(item["energy"])
                v = (1.0 - blend) * v0 + blend * med_v
                e = (1.0 - blend) * e0 + blend * med_e
                v = v0 + max(-max_shift, min(max_shift, v - v0))
                e = e0 + max(-max_shift, min(max_shift, e - e0))
                v = max(0.03, min(0.97, v))
                e = max(0.03, min(0.97, e))
                if abs(v - v0) <= 1e-9 and abs(e - e0) <= 1e-9:
                    continue
                conf0 = float(item["mood_confidence"] if item["mood_confidence"] is not None else 0.3)
                # Tidy may bump confidence a bit when snapping toward neighbors — but
                # never promote a sub-0.45 rough place (seed/edge salvage) into mid-trust.
                # Position still clusters; the star stays dim / low-trust.
                bumped = min(0.62, max(conf0, 0.42) + 0.12)
                if conf0 < CONFIDENCE_LOW:
                    conf = min(bumped, CONFIDENCE_LOW - 0.01)
                else:
                    conf = bumped
                note = self._append_note(item["confidence_note"] if "confidence_note" in item.keys() else "", note_tag)
                updates.append((v, e, conf, int(confidence_low_trust(conf)), note, int(item["id"])))

        if not updates:
            return 0
        with self.lock:
            self.conn.executemany(
                """
                UPDATE tracks
                SET valence = ?, energy = ?, mood_confidence = ?, low_trust = ?, confidence_note = ?
                WHERE id = ? AND pinned = 0
                """,
                updates,
            )
            self.conn.commit()
        return len(updates)

    def spread_album_acoustics(self, max_shift: float = 0.07) -> int:
        """Unstick same-album clones using persisted brightness/flux (no ffmpeg).

        After album smooth, low/medium-confidence unpinned tracks get a bounded
        offset from the album *median* brightness/flux. High-confidence stars
        stay put so trusted placements are not nudged into clones.
        """
        import math
        from statistics import median

        from meridian.features import CONFIDENCE_HIGH

        with self.lock:
            rows = self.conn.execute(
                """
                SELECT id, artist, albumartist, album, valence, energy, pinned,
                       brightness, acoustic_flux, confidence_note, mood_confidence
                FROM tracks
                WHERE analyzed = 1
                  AND album IS NOT NULL AND TRIM(album) != ''
                  AND (
                    (albumartist IS NOT NULL AND TRIM(albumartist) != '')
                    OR (artist IS NOT NULL AND TRIM(artist) != '')
                  )
                """
            ).fetchall()

        def album_key(row) -> tuple[str, str]:
            aa = str(row["albumartist"] or "").strip()
            ar = str(row["artist"] or "").strip()
            credit = aa if aa and aa.lower() not in {"various artists", "various", "va"} else ar
            return (credit.lower(), str(row["album"] or "").lower().strip())

        def _finite(value) -> bool:
            try:
                return math.isfinite(float(value))
            except (TypeError, ValueError):
                return False

        groups: dict[tuple[str, str], list] = {}
        for row in rows:
            groups.setdefault(album_key(row), []).append(row)

        updates: list[tuple[float, float, str, int]] = []
        for items in groups.values():
            if len(items) < 4:
                continue
            bright_vals = [
                float(i["brightness"])
                for i in items
                if i["brightness"] is not None and _finite(i["brightness"])
            ]
            flux_vals = [
                float(i["acoustic_flux"])
                for i in items
                if i["acoustic_flux"] is not None and _finite(i["acoustic_flux"])
            ]
            if len(bright_vals) < 2 and len(flux_vals) < 2:
                continue
            med_b = float(median(bright_vals)) if bright_vals else 0.5
            med_f = float(median(flux_vals)) if flux_vals else 0.5
            for item in items:
                if int(item["pinned"]):
                    continue
                try:
                    conf = float(item["mood_confidence"] or 0.0)
                except (TypeError, ValueError):
                    conf = 0.0
                if conf >= CONFIDENCE_HIGH:
                    continue
                note_raw = item["confidence_note"] if "confidence_note" in item.keys() else ""
                # Idempotent: do not re-apply additive offsets on later analyze passes.
                if "album spread" in (note_raw or ""):
                    continue
                b = float(item["brightness"]) if item["brightness"] is not None and _finite(item["brightness"]) else med_b
                f = (
                    float(item["acoustic_flux"])
                    if item["acoustic_flux"] is not None and _finite(item["acoustic_flux"])
                    else med_f
                )
                dv = max(-max_shift, min(max_shift, 0.55 * (b - med_b)))
                de = max(-max_shift, min(max_shift, 0.45 * (f - med_f)))
                if abs(dv) < 1e-4 and abs(de) < 1e-4:
                    continue
                v0 = float(item["valence"])
                e0 = float(item["energy"])
                v = max(0.03, min(0.97, v0 + dv))
                e = max(0.03, min(0.97, e0 + de))
                note = self._append_note(note_raw, "album spread")
                updates.append((v, e, note, int(item["id"])))

        if not updates:
            return 0
        with self.lock:
            self.conn.executemany(
                """
                UPDATE tracks
                SET valence = ?, energy = ?, confidence_note = ?
                WHERE id = ? AND pinned = 0
                """,
                updates,
            )
            self.conn.commit()
        return len(updates)

    def finish_listen_points(self, max_shift: float = 0.055) -> int:
        """Let stored brightness and flux finish the spot, including trusted songs.

        Brighter audio nudges Glow. Busier flux nudges Kinetic. The cap keeps
        the song inside the mood the lens already means. Idempotent.
        """
        import math

        with self.lock:
            rows = self.conn.execute(
                """
                SELECT id, valence, energy, pinned, brightness, acoustic_flux,
                       confidence_note
                FROM tracks
                WHERE analyzed = 1 AND pinned = 0
                """
            ).fetchall()

        def _finite(value) -> float | None:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(number):
                return None
            return number

        updates: list[tuple[float, float, str, int]] = []
        for row in rows:
            note_raw = row["confidence_note"] if "confidence_note" in row.keys() else ""
            # "listen finish" is the play-through note. Do not treat it as this pass.
            if "acoustic point" in (note_raw or "") or "kept prior" in (note_raw or ""):
                continue
            bright = _finite(row["brightness"])
            flux = _finite(row["acoustic_flux"])
            if bright is None and flux is None:
                continue
            dv = 0.0
            de = 0.0
            if bright is not None:
                dv = max(-max_shift, min(max_shift, 0.22 * (bright - 0.5)))
            if flux is not None:
                de = max(-max_shift, min(max_shift, 0.20 * (flux - 0.5)))
            if abs(dv) < 1e-4 and abs(de) < 1e-4:
                continue
            v0 = float(row["valence"])
            e0 = float(row["energy"])
            v = max(0.03, min(0.97, v0 + dv))
            e = max(0.03, min(0.97, e0 + de))
            if abs(v - v0) < 1e-4 and abs(e - e0) < 1e-4:
                continue
            note = self._append_note(note_raw, "acoustic point")
            updates.append((v, e, note, int(row["id"])))

        if not updates:
            return 0
        with self.lock:
            self.conn.executemany(
                """
                UPDATE tracks
                SET valence = ?, energy = ?, confidence_note = ?
                WHERE id = ? AND pinned = 0
                """,
                updates,
            )
            self.conn.commit()
        return len(updates)

    def rescale_moods_by_percentile(self, min_group: int = 12) -> int:
        """P4: library/genre percentile rescale for relative placement (no ffmpeg).

        Pins are never moved. High-confidence stars get a tiny blend; low-confidence
        stars get a stronger pull so neighbors rank better inside the library.
        """
        from meridian.features import (
            CONFIDENCE_HIGH,
            CONFIDENCE_LOW,
            genre_match,
        )

        with self.lock:
            rows = self.conn.execute(
                """
                SELECT id, genre, valence, energy, pinned, mood_confidence, confidence_note
                FROM tracks
                WHERE analyzed = 1
                """
            ).fetchall()

        if len(rows) < min_group:
            return 0

        groups: dict[str, list] = {}
        for row in rows:
            key = genre_match(row["genre"] or "") or "_global_"
            groups.setdefault(key, []).append(row)

        # Fold undersized genre buckets into the global pool for stable ranks.
        global_rows = list(groups.pop("_global_", []))
        for key, items in list(groups.items()):
            if len(items) < min_group:
                global_rows.extend(items)
                del groups[key]
        if len(global_rows) >= min_group:
            groups["_global_"] = global_rows

        updates: list[tuple[float, float, str, int]] = []
        for items in groups.values():
            n = len(items)
            if n < min_group:
                continue
            # Rank within group (average ranks for ties via sorted index).
            order_v = sorted(range(n), key=lambda i: float(items[i]["valence"]))
            order_e = sorted(range(n), key=lambda i: float(items[i]["energy"]))
            pct_v = [0.0] * n
            pct_e = [0.0] * n
            denom = max(n - 1, 1)
            for rank, idx in enumerate(order_v):
                pct_v[idx] = rank / denom
            for rank, idx in enumerate(order_e):
                pct_e[idx] = rank / denom

            for i, item in enumerate(items):
                if int(item["pinned"]):
                    continue
                conf = float(item["mood_confidence"] if item["mood_confidence"] is not None else 0.5)
                if conf >= CONFIDENCE_HIGH:
                    blend, max_shift = 0.08, 0.03
                elif conf >= CONFIDENCE_LOW:
                    blend, max_shift = 0.25, 0.07
                else:
                    blend, max_shift = 0.45, 0.12
                v0 = float(item["valence"])
                e0 = float(item["energy"])
                target_v = 0.10 + 0.80 * pct_v[i]
                target_e = 0.10 + 0.80 * pct_e[i]
                v = (1.0 - blend) * v0 + blend * target_v
                e = (1.0 - blend) * e0 + blend * target_e
                v = v0 + max(-max_shift, min(max_shift, v - v0))
                e = e0 + max(-max_shift, min(max_shift, e - e0))
                v = max(0.03, min(0.97, v))
                e = max(0.03, min(0.97, e))
                if abs(v - v0) < 1e-9 and abs(e - e0) < 1e-9:
                    continue
                note_raw = item["confidence_note"] if "confidence_note" in item.keys() else ""
                # Idempotent: one relative rescale until a fresh analyze replaces the note.
                if "relative rescale" in (note_raw or ""):
                    continue
                note = self._append_note(note_raw, "relative rescale")
                updates.append((v, e, note, int(item["id"])))

        if not updates:
            return 0
        with self.lock:
            self.conn.executemany(
                """
                UPDATE tracks
                SET valence = ?, energy = ?, confidence_note = ?
                WHERE id = ? AND pinned = 0
                """,
                updates,
            )
            self.conn.commit()
        return len(updates)

    def toggle_loved(self, track_id: int) -> bool:
        with self.lock:
            row = self.conn.execute("SELECT loved FROM tracks WHERE id = ?", (track_id,)).fetchone()
            if not row:
                return False
            loved = 0 if row["loved"] else 1
            self.conn.execute("UPDATE tracks SET loved = ? WHERE id = ?", (loved, track_id))
            self.conn.commit()
            return bool(loved)

    def record_play(self, track_id: int, timestamp: float) -> None:
        with self.lock:
            self.conn.execute(
                """
                UPDATE tracks
                SET play_count = play_count + 1, last_played = ?
                WHERE id = ?
                """,
                (timestamp, track_id),
            )
            self.conn.commit()

    def unrecord_play(self, track_id: int) -> None:
        """Undo a play credit when hard-cut media fails before any real listen."""
        with self.lock:
            self.conn.execute(
                """
                UPDATE tracks
                SET play_count = CASE WHEN play_count > 0 THEN play_count - 1 ELSE 0 END,
                    last_played = CASE WHEN play_count <= 1 THEN NULL ELSE last_played END
                WHERE id = ?
                """,
                (track_id,),
            )
            self.conn.commit()

    def mark_playback_failed(self, track_id: int) -> None:
        """Keep a corrupt/unsupported file out of plans (persists across relaunch)."""
        tid = int(track_id)
        self._playback_denylist.add(tid)
        with self.lock:
            self.conn.execute(
                "UPDATE tracks SET playback_denied = 1 WHERE id = ?", (tid,)
            )
            self.conn.commit()

    def is_playback_denied(self, track_id: int) -> bool:
        return int(track_id) in self._playback_denylist

    def defer_analyze(self, track_id: int, *, note: str = "pcm pending") -> None:
        """Leave analyzed=0 but denylist this session so silent PCM miss can retry later.

        Use only when there is no prior PCM placement to keep. If acoustics already
        exist, call :meth:`conclude_kept_prior` instead — leaving analyzed=0 forever
        re-queues the whole library on every launch.
        """
        tid = int(track_id)
        self._analyze_denylist.add(tid)
        with self.lock:
            self._analyze_deferred_count += 1
            row = self.conn.execute(
                "SELECT pinned, confidence_note FROM tracks WHERE id = ?", (tid,)
            ).fetchone()
            if not row or int(row["pinned"] or 0):
                return
            merged = self._append_note(row["confidence_note"], note)
            self.conn.execute(
                "UPDATE tracks SET confidence_note = ?, analyzed = 0 WHERE id = ? AND pinned = 0",
                (merged, tid),
            )
            self.conn.commit()

    def conclude_kept_prior(
        self, track_id: int, *, note: str = "pcm pending · kept prior"
    ) -> None:
        """Honest conclude when this listen failed but a prior waveform placement stands.

        Marks analyzed=1 without touching valence/energy/acoustics so the track is
        not eternally re-queued. Explicit Rescan still zeros analyzed for a retry.
        """
        tid = int(track_id)
        # Session denylist: avoid a same-run claim if a dual worker races.
        self._analyze_denylist.add(tid)
        with self.lock:
            row = self.conn.execute(
                "SELECT pinned, confidence_note FROM tracks WHERE id = ?", (tid,)
            ).fetchone()
            if not row:
                return
            if int(row["pinned"] or 0):
                # Pins already keep mood; just clear the pending-analyze flag.
                self.conn.execute(
                    "UPDATE tracks SET analyzed = 1 WHERE id = ?", (tid,)
                )
            else:
                merged = self._append_note(row["confidence_note"], note)
                self.conn.execute(
                    "UPDATE tracks SET confidence_note = ?, analyzed = 1 WHERE id = ?",
                    (merged, tid),
                )
            self.conn.commit()

    def heal_kept_prior_pending(self) -> int:
        """One-shot: mark stuck 'kept prior' rows analyzed so launches stop re-listening.

        Matches the live mass-defer backlog (analyzed=0 + prior acoustics + kept prior
        note). Safe without shape/steer columns. Rescan still zeros analyzed afterward.
        """
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT id FROM tracks
                WHERE analyzed = 0
                  AND (brightness IS NOT NULL OR acoustic_flux IS NOT NULL)
                  AND instr(lower(COALESCE(confidence_note, '')), 'kept prior') > 0
                """
            ).fetchall()
            ids = [int(r["id"]) for r in rows]
            if not ids:
                return 0
            self.conn.executemany(
                "UPDATE tracks SET analyzed = 1 WHERE id = ?",
                [(i,) for i in ids],
            )
            self.conn.commit()
        for tid in ids:
            self._analyze_denylist.discard(tid)
        return len(ids)

    def count_tracks_under(self, root: Path | str) -> int:
        """How many DB tracks resolve under this library root (for sparse-mount guards)."""
        return len(self.paths_under(root))

    def paths_under(self, root: Path | str) -> list[str]:
        """DB track paths whose resolved location sits under this library root."""
        try:
            root_r = Path(root).resolve()
        except OSError:
            return []
        with self.lock:
            rows = self.conn.execute("SELECT path FROM tracks").fetchall()
        out: list[str] = []
        for row in rows:
            try:
                Path(row["path"]).resolve().relative_to(root_r)
            except (OSError, ValueError):
                continue
            out.append(str(row["path"]))
        return out

    def record_skip(self, track_id: int) -> None:
        with self.lock:
            self.conn.execute(
                "UPDATE tracks SET skip_count = skip_count + 1 WHERE id = ?",
                (track_id,),
            )
            self.conn.commit()

    def delete_missing(
        self,
        existing_paths: Iterable[str],
        *,
        only_under: Iterable[Path | str] | None = None,
    ) -> None:
        """Remove DB rows for files not seen in this scan.

        When only_under is set, only delete tracks whose resolved path sits under
        one of those fully-walked roots (partial/unreadable trees stay intact).
        """
        keep = set(existing_paths)
        roots: list[Path] = []
        for raw in only_under or ():
            try:
                roots.append(Path(raw).resolve())
            except OSError:
                continue

        def _under_scanned_root(path: str) -> bool:
            if not roots:
                return True
            try:
                resolved = Path(path).resolve()
            except OSError:
                return False
            for root in roots:
                try:
                    resolved.relative_to(root)
                    return True
                except ValueError:
                    continue
            return False

        with self.lock:
            rows = self.conn.execute("SELECT id, path FROM tracks").fetchall()
            dead = [
                r["id"]
                for r in rows
                if r["path"] not in keep and _under_scanned_root(r["path"])
            ]
            if dead:
                self.conn.executemany("DELETE FROM tracks WHERE id = ?", [(i,) for i in dead])
                self.conn.commit()

    def delete_paths(self, paths: Iterable[str]) -> int:
        """Remove specific DB rows by path (e.g. deleted files under skipped dirs)."""
        victims = {str(p) for p in paths if p}
        if not victims:
            return 0
        with self.lock:
            rows = self.conn.execute("SELECT id, path FROM tracks").fetchall()
            dead = [int(r["id"]) for r in rows if str(r["path"]) in victims]
            if dead:
                self.conn.executemany("DELETE FROM tracks WHERE id = ?", [(i,) for i in dead])
                self.conn.commit()
            return len(dead)

    def all_tracks(self) -> list[Track]:
        with self.lock:
            rows = self.conn.execute("SELECT * FROM tracks ORDER BY artist, album, title").fetchall()
        return [self._track(r) for r in rows]

    def get(self, track_id: int) -> Track | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        return self._track(row) if row else None

    def search(self, query: str, limit: int = 40) -> list[Track]:
        needle = query.strip()
        if not needle:
            return []
        like = f"%{needle}%"
        prefix = f"{needle}%"
        with self.lock:
            rows = self.conn.execute(
                """
                SELECT * FROM tracks
                WHERE title LIKE ? COLLATE NOCASE
                   OR artist LIKE ? COLLATE NOCASE
                   OR album LIKE ? COLLATE NOCASE
                   OR path LIKE ? COLLATE NOCASE
                ORDER BY
                    CASE
                        WHEN title LIKE ? COLLATE NOCASE THEN 0
                        WHEN artist LIKE ? COLLATE NOCASE THEN 1
                        ELSE 2
                    END,
                    artist, title
                LIMIT ?
                """,
                (like, like, like, like, prefix, prefix, limit),
            ).fetchall()
        return [self._track(r) for r in rows]

    def unanalyzed_ids(self) -> list[int]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT id FROM tracks WHERE analyzed = 0"
            ).fetchall()
            deny = set(self._analyze_denylist)
        return [int(r["id"]) for r in rows if int(r["id"]) not in deny]

    def mark_all_pending_analysis(self) -> int:
        """Mark every track for re-analysis (Rescan). Pinned moods stay protected in set_analyzed_mood."""
        self._analyze_denylist.clear()
        self._playback_denylist.clear()
        with self.lock:
            cur = self.conn.execute(
                "UPDATE tracks SET analyzed = 0, playback_denied = 0"
            )
            self.conn.commit()
            return int(cur.rowcount)

    def existing_mtime(self, path: str) -> float | None:
        with self.lock:
            row = self.conn.execute("SELECT mtime FROM tracks WHERE path = ?", (path,)).fetchone()
        return float(row["mtime"]) if row and row["mtime"] is not None else None

    @staticmethod
    def _track(row: sqlite3.Row) -> Track:
        return Track(
            id=int(row["id"]),
            path=row["path"],
            title=row["title"] or "",
            artist=row["artist"] or "",
            album=row["album"] or "",
            albumartist=(row["albumartist"] or "") if "albumartist" in row.keys() else "",
            genre=row["genre"] or "",
            duration_ms=int(row["duration_ms"] or 0),
            year=row["year"],
            bpm=row["bpm"],
            valence=float(row["valence"]),
            energy=float(row["energy"]),
            mood_confidence=(
                float(row["mood_confidence"])
                if "mood_confidence" in row.keys() and row["mood_confidence"] is not None
                else (0.30 if (row["low_trust"] if "low_trust" in row.keys() else 0) else 0.80)
            ),
            confidence_note=(
                (row["confidence_note"] or "")
                if "confidence_note" in row.keys()
                else ""
            ),
            low_trust=bool(row["low_trust"]) if "low_trust" in row.keys() else False,
            pinned=bool(row["pinned"]),
            loved=bool(row["loved"]),
            play_count=int(row["play_count"]),
            skip_count=int(row["skip_count"]),
            last_played=row["last_played"],
            added_at=row["added_at"],
            mtime=row["mtime"],
            analyzed=bool(row["analyzed"]),
            acoustic_flux=(
                float(row["acoustic_flux"])
                if "acoustic_flux" in row.keys() and row["acoustic_flux"] is not None
                else None
            ),
            onset_consistency=(
                float(row["onset_consistency"])
                if "onset_consistency" in row.keys() and row["onset_consistency"] is not None
                else None
            ),
            brightness=(
                float(row["brightness"])
                if "brightness" in row.keys() and row["brightness"] is not None
                else None
            ),
            shape_note=(
                (row["shape_note"] or None)
                if "shape_note" in row.keys() and row["shape_note"]
                else None
            ),
            timbre_flag=(
                (row["timbre_flag"] or None)
                if "timbre_flag" in row.keys() and row["timbre_flag"]
                else None
            ),
            timbre_source=(
                (row["timbre_source"] or None)
                if "timbre_source" in row.keys() and row["timbre_source"]
                else None
            ),
            steer_weight=(
                float(row["steer_weight"])
                if "steer_weight" in row.keys() and row["steer_weight"] is not None
                else None
            ),
        )
