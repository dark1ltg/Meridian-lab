from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal, QThread

from meridian.features import (
    AUDIO_EXTS,
    analyze_audio,
    confidence_low_trust,
    genre_seed,
    mood_confidence,
    read_tags,
    _coerce_bpm,
)
from meridian.library import Library


class AnalyzeTrackQueue:
    """Shared FIFO of track ids for one or more AnalyzeWorkers (SSD dual listen)."""

    def __init__(self, track_ids: list[int]) -> None:
        self._ids = [int(i) for i in track_ids]
        self._next = 0
        self._done = 0
        self._lock = threading.Lock()
        self.total = len(self._ids)

    def claim(self) -> int | None:
        with self._lock:
            if self._next >= len(self._ids):
                return None
            tid = self._ids[self._next]
            self._next += 1
            return tid

    def mark_done(self) -> tuple[int, int]:
        """Return ``(completed, total)`` after one track finishes."""
        with self._lock:
            self._done += 1
            return self._done, self.total


def _resolve(path: Path) -> Path | None:
    try:
        return path.resolve()
    except OSError:
        return None


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class ScanWorker(QObject):
    # Display-ready progress line (folder + file/counts). UI treats as sticky job text.
    progress = Signal(str)
    finished = Signal(int)
    failed = Signal(str)

    def __init__(self, library: Library, *, force: bool = False) -> None:
        super().__init__()
        self.library = library
        self.force = force
        self._abort = False

    def abort(self) -> None:
        self._abort = True

    def run(self) -> None:
        try:
            count = self._scan()
            self.finished.emit(count)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
            # Always close the finished channel so the UI can quit the QThread.
            # Negative count = failure (UI must not treat this as a successful scan).
            self.finished.emit(-1)

    def _scan(self) -> int:
        folders = self.library.folders()
        found: list[str] = []
        prune_roots: list[Path] = []
        added = 0
        now = time.time()
        for folder in folders:
            if self._abort:
                break
            root = Path(folder)
            if not root.is_dir():
                continue
            root_resolved = _resolve(root)
            if root_resolved is None:
                continue
            root_name = root.name or str(root)
            self.progress.emit(f"Scanning {root_name}…")
            root_found: list[str] = []
            walk_ok = True
            skipped_dirs: list[Path] = []
            seen_in_root = 0
            skipped_mtime = 0

            def on_walk_error(_err: OSError) -> None:
                nonlocal walk_ok
                walk_ok = False

            for dirpath, dirnames, filenames in os.walk(
                root,
                onerror=on_walk_error,
                followlinks=False,
            ):
                if self._abort:
                    return added
                # Do not descend into hidden or symlink directories.
                keep_dirs: list[str] = []
                for name in dirnames:
                    child = Path(dirpath) / name
                    if name.startswith(".") or child.is_symlink():
                        skipped_dirs.append(child)
                        continue
                    keep_dirs.append(name)
                dirnames[:] = keep_dirs
                for name in filenames:
                    path = Path(dirpath) / name
                    if path.suffix.lower() not in AUDIO_EXTS:
                        continue
                    # Skip file symlinks that escape the library root.
                    if path.is_symlink():
                        resolved = _resolve(path)
                        if resolved is None or not _is_under(resolved, root_resolved):
                            continue
                    path_str = str(path)
                    root_found.append(path_str)
                    seen_in_root += 1
                    try:
                        st = os.stat(path_str)
                    except OSError:
                        continue
                    mtime = self.library.existing_mtime(path_str)
                    if (
                        not self.force
                        and mtime is not None
                        and abs(mtime - st.st_mtime) < 0.5
                    ):
                        skipped_mtime += 1
                        # Heartbeat on mtime-skip walks so Rescan/Add never looks stuck.
                        if skipped_mtime == 1 or skipped_mtime % 75 == 0:
                            self.progress.emit(
                                f"Scanning {root_name}… ({seen_in_root} files seen)"
                            )
                        continue
                    try:
                        rel = str(Path(path_str).relative_to(root))
                    except ValueError:
                        rel = name
                    self.progress.emit(
                        f"Scanning {root_name}: {rel} · indexed {added + 1}"
                    )
                    tags = read_tags(path_str)
                    seed = genre_seed(
                        tags["genre"],
                        tags["title"],
                        tags["artist"],
                        path=path_str,
                        year=tags.get("year"),
                        extra_text=tags.get("extra_text") or "",
                        albumartist=tags.get("albumartist") or "",
                        composer=tags.get("composer") or "",
                        replaygain_db=tags.get("replaygain_db"),
                    )
                    # Provisional score (no PCM yet) — same weights as analyze, pcm_ok=False.
                    # Coerce BPM so tag "0" / empty does not falsely credit confidence.
                    bpm = _coerce_bpm(tags.get("bpm"))
                    conf, note = mood_confidence(
                        tag_key=seed.tag_key,
                        path_key=seed.path_key,
                        pcm_ok=False,
                        bpm_ok=bpm is not None,
                        replaygain=tags.get("replaygain_db") is not None,
                        keyword_hit=seed.keyword_hit,
                        weak_tags=path.suffix.lower() in {".wav", ".aiff", ".aif"},
                    )
                    self.library.upsert_track(
                        {
                            "path": path_str,
                            "title": tags["title"],
                            "artist": tags["artist"],
                            "album": tags["album"],
                            "albumartist": tags.get("albumartist") or "",
                            "genre": tags["genre"],
                            "duration_ms": tags["duration_ms"],
                            "year": tags["year"],
                            "bpm": tags["bpm"],
                            "valence": seed.valence,
                            "energy": seed.energy,
                            "mood_confidence": conf,
                            "confidence_note": note or "pre-analyze",
                            "low_trust": int(confidence_low_trust(conf)),
                            "added_at": now,
                            "mtime": st.st_mtime,
                            "analyzed": 0,
                        }
                    )
                    added += 1
            # Keep DB rows under dirs we deliberately skipped (symlink / .hidden)
            # so prune cannot wipe them when the rest of the root is ≥85% visible.
            # Missing files under those dirs are dropped here (not via mass prune),
            # so sparse-mount guards stay intact.
            # Use lexical path checks: resolve() can escape the root via symlink targets.
            if skipped_dirs and walk_ok and not self._abort:
                root_path = Path(root)

                def _under_root(path_str: str) -> bool:
                    try:
                        Path(path_str).relative_to(root_path)
                        return True
                    except ValueError:
                        return False

                def _under_skipped(path_str: str) -> bool:
                    raw = Path(path_str)
                    for d in skipped_dirs:
                        try:
                            raw.relative_to(d)
                            return True
                        except ValueError:
                            continue
                    return False

                gone_skipped: list[str] = []
                with self.library.lock:
                    all_paths = [
                        str(r["path"])
                        for r in self.library.conn.execute("SELECT path FROM tracks")
                    ]
                for known in all_paths:
                    if known in root_found or not _under_root(known):
                        continue
                    if not _under_skipped(known):
                        continue
                    if not Path(known).exists():
                        gone_skipped.append(known)
                        continue
                    root_found.append(known)
                if gone_skipped:
                    self.library.delete_paths(gone_skipped)
            found.extend(root_found)
            # Only prune under roots we fully walked AND actually saw audio.
            # An empty successful walk (unmounted drive, empty mountpoint) must not
            # delete every DB row under that root when another root still has files.
            # Sparse/wrong mounts (one leftover file on an empty volume) also skip prune
            # when this walk sees less than half of the tracks the DB already knows.
            if walk_ok and not self._abort and root_found:
                known = self.library.count_tracks_under(root_resolved)
                # Require ~85% of known tracks before pruning — half-visible mounts
                # must not delete the unseen half.
                if known == 0 or len(root_found) * 20 >= known * 17:
                    prune_roots.append(root_resolved)
        # Never wipe when nothing was kept, walk aborted, or a root was incomplete.
        if found and prune_roots and not self._abort:
            self.library.delete_missing(found, only_under=prune_roots)
        return added


class AnalyzeWorker(QObject):
    progress = Signal(str, int, int)
    finished = Signal()

    def __init__(
        self,
        library: Library,
        *,
        queue: AnalyzeTrackQueue | None = None,
        run_tidy: bool = True,
        reset_session: bool = True,
        clear_abort: bool = True,
    ) -> None:
        super().__init__()
        self.library = library
        self._queue = queue
        self._run_tidy = bool(run_tidy)
        self._reset_session = bool(reset_session)
        self._clear_abort = bool(clear_abort)
        self._abort = False

    def abort(self) -> None:
        self._abort = True
        from meridian.features import request_decode_abort

        request_decode_abort()

    def run(self) -> None:
        from meridian.features import clear_decode_abort

        if self._clear_abort:
            clear_decode_abort()
        try:
            if self._reset_session:
                self.library.reset_analyze_session_stats()
            if self._queue is not None:
                queue = self._queue
            else:
                queue = AnalyzeTrackQueue(self.library.unanalyzed_ids())
            while not self._abort:
                track_id = queue.claim()
                if track_id is None:
                    break
                track = self.library.get(track_id)
                if not track:
                    completed, total = queue.mark_done()
                    continue
                try:
                    tags = read_tags(track.path)
                    result = analyze_audio(
                        track.path,
                        track.genre or tags.get("genre") or "",
                        track.title,
                        track.artist,
                        track.bpm if track.bpm is not None else tags.get("bpm"),
                        year=track.year if track.year is not None else tags.get("year"),
                        extra_text=tags.get("extra_text") or "",
                        albumartist=track.albumartist or tags.get("albumartist") or "",
                        composer=tags.get("composer") or "",
                        replaygain_db=tags.get("replaygain_db"),
                        duration_ms=track.duration_ms or int(tags.get("duration_ms") or 0),
                    )
                    if self._abort:
                        queue.mark_done()
                        break
                    if not result.pcm_ok:
                        # No waveform this pass.
                        # Prior PCM acoustics → keep that placement and conclude
                        # analyzed=1 (do not leave analyzed=0 or every launch
                        # re-queues the whole library). Explicit Rescan retries.
                        if self.library.had_pcm_acoustics(track.id):
                            self.library.conclude_kept_prior(track.id)
                        elif result.seed_evidence:
                            # First-time / never-heard: conclude on tags so the map
                            # still shows the full collection.
                            self.library.set_analyzed_mood(
                                track.id,
                                result.valence,
                                result.energy,
                                result.bpm,
                                confidence=result.confidence,
                                low_trust=result.low_trust,
                                confidence_note=result.confidence_note,
                                onset_consistency=result.onset_consistency,
                                acoustic_flux=result.acoustic_flux,
                                brightness=result.brightness,
                                shape_note=result.shape_note,
                                timbre_flag=result.timbre_flag,
                                timbre_source=result.timbre_source,
                                # Seed conclude: only stamp steer when there is no
                                # prior PCM (library CASE also guards). Never pass a
                                # weak 0.05 over a real listen via COALESCE alone.
                                steer_weight=result.steer_weight,
                                from_pcm=False,
                            )
                        else:
                            self.library.defer_analyze(track.id)
                    else:
                        self.library.set_analyzed_mood(
                            track.id,
                            result.valence,
                            result.energy,
                            result.bpm,
                            confidence=result.confidence,
                            low_trust=result.low_trust,
                            confidence_note=result.confidence_note,
                            onset_consistency=result.onset_consistency,
                            acoustic_flux=result.acoustic_flux,
                            brightness=result.brightness,
                            shape_note=result.shape_note,
                            timbre_flag=result.timbre_flag,
                            timbre_source=result.timbre_source,
                            steer_weight=result.steer_weight,
                        )
                except Exception:
                    # Always denylist in-process; DB mark may fail under lock contention.
                    self.library.mark_analyze_failed(track.id)
                completed, total = queue.mark_done()
                self.progress.emit(track.short_title, completed, total)
            if self._run_tidy and not self._abort:
                run_library_tidy(self.library)
        finally:
            self.finished.emit()


def run_library_tidy(library: Library) -> None:
    """Album/artist smooth + acoustic spread + percentile rescale (no ffmpeg)."""
    library.smooth_album_moods()
    library.smooth_artist_moods()
    library.spread_album_acoustics()
    library.rescale_moods_by_percentile()
    library.finish_listen_points()


class TidyWorker(QObject):
    """Background tidy after the analyze pool finishes (keeps the GUI responsive)."""

    finished = Signal(str)  # empty ok, else error message

    def __init__(self, library: Library) -> None:
        super().__init__()
        self.library = library

    def run(self) -> None:
        try:
            run_library_tidy(self.library)
            self.finished.emit("")
        except Exception as exc:
            self.finished.emit(str(exc) or exc.__class__.__name__)


def start_worker(worker: QObject, fn_name: str = "run") -> QThread:
    thread = QThread()
    worker.moveToThread(thread)
    thread.started.connect(getattr(worker, fn_name))
    # When run() returns via finished, leave the event loop so wait() can complete.
    # Use a lambda so Signal(int) workers (scan) do not pass args into quit().
    # Quit uses a Direct lambda on the worker thread (safe: only thread.quit()).
    # UI handlers must use QueuedConnection to real @Slot methods/bridges —
    # PySide drops QueuedConnection to lambdas/partials (scan→analyze handoff).
    if hasattr(worker, "finished"):
        worker.finished.connect(lambda *_a: thread.quit())
    thread.start()
    return thread
