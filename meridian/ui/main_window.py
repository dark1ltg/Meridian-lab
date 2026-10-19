from __future__ import annotations

from pathlib import Path
from time import time

from PySide6.QtCore import QObject, QSettings, Qt, QThread, QTimer, Slot
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from meridian import __version__
from meridian.features import CONFIDENCE_HIGH, CONFIDENCE_LOW
from meridian.context import (
    LENS_RADIUS_DEFAULT,
    LENS_RADIUS_MAX,
    LENS_RADIUS_MIN,
    MODE_HINTS,
    MODE_LABELS,
    Mode,
    make_context,
    mode_bias,
)
from meridian.host_deps import (
    ffmpeg_missing_message,
    ffmpeg_missing_status,
    libx264_missing_message,
    libx264_missing_status,
    should_warn_missing_ffmpeg,
    should_warn_missing_libx264,
)
from meridian.library import Library
from meridian.player import Player
from meridian.queue_engine import Quadrant, QueuePlan, RenewalContext, build_plan
from meridian.scanner import (
    AnalyzeTrackQueue,
    AnalyzeWorker,
    ScanWorker,
    TidyWorker,
    start_worker,
)
from meridian.ui.search import TrackSearch
from meridian.ui.fonts import condensed
from meridian.ui.palette import PLAYLIST_HEX
from meridian.ui.matrix import (
    STAR_COLOR_ROLE,
    STAR_CURRENT_ROLE,
    STAR_LINK_ROLE,
    STAR_LOVED_ROLE,
    EisenhowerMatrix,
    StarList,
)
from meridian.ui.mood_map import MoodMap
from meridian.ui.transport import TransportBar


class _ScanFinishBridge(QObject):
    """Queued scan finished/failed must be real Slots — PySide drops lambda queues."""

    def __init__(self, handler, gen: int, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._handler = handler
        self._gen = gen

    @Slot(int)
    def on_finished(self, added: int) -> None:
        handler = self._handler
        if handler is not None:
            handler(added, self._gen)


class _AnalyzeFinishBridge(QObject):
    """Capture gen/thread/worker for analyze finished without a Queued lambda."""

    def __init__(
        self,
        window: "MainWindow",
        gen: int,
        thread,
        worker,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = window
        self._gen = gen
        self._thread = thread
        self._worker = worker

    @Slot()
    def on_finished(self) -> None:
        self._window._analyze_done(self._gen, self._thread, self._worker)


class _TidyFinishBridge(QObject):
    """Queued tidy finished must be a real Slot — PySide drops Queued lambdas."""

    def __init__(
        self,
        window: "MainWindow",
        gen: int,
        thread,
        worker,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = window
        self._gen = gen
        self._thread = thread
        self._worker = worker

    @Slot(str)
    def on_finished(self, error: str) -> None:
        self._window._analyze_tidy_done(self._gen, error, self._thread, self._worker)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Meridian {__version__}")
        self.resize(1440, 900)
        self.settings = QSettings()
        self.library = Library()
        self.player = Player(self)
        raw_mode = self.settings.value("mode", Mode.WANDER.value)
        try:
            self.mode = Mode(raw_mode)
        except (ValueError, TypeError):
            self.mode = Mode.WANDER
            self.settings.setValue("mode", self.mode.value)
        self.explicit: list[int] = []
        self.plan: QueuePlan | None = None
        self.session_queue: list[int] = []
        self.ephemeral: set[int] = set()
        self.played_history: list[int] = []
        self.queue_index = 0
        self.skips_window: list[float] = []
        self._scan_thread = None
        self._scan_worker = None
        self._scan_gen = 0
        self._scan_finish_bridge: _ScanFinishBridge | None = None
        self._analyze_thread = None
        self._analyze_worker = None
        # Active analyze pool (1 on HDD, up to 2 on SSD/NVMe). Primary pair also
        # mirrored in `_analyze_thread` / `_analyze_worker` for legacy stop/reap.
        self._analyze_pool: list[tuple] = []
        self._analyze_finish_bridges: list[_AnalyzeFinishBridge] = []
        self._analyze_remaining = 0
        self._analyze_run_tidy = False
        self._analyze_progress_shown = 0
        self._tidy_thread = None
        self._tidy_worker = None
        self._tidy_finish_bridge: _TidyFinishBridge | None = None
        self._analyze_gen = 0
        self._analyze_finish_bridge: _AnalyzeFinishBridge | None = None
        # Timed-out workers kept alive (signals disconnected) until QThread ends.
        self._lingering_workers: list[tuple] = []
        self._close_library_when_idle = False
        self._closing = False
        self._pending_play_credit: int | None = None
        # Outgoing track of the active crossfade (for Next/Prev / settle credit).
        self._crossfade_outgoing_id: int | None = None
        # When True, natural fade settle finish-nudges `_crossfade_outgoing_id`.
        # User jumps credit immediately instead (skip vs finish from position).
        self._outgoing_settle_finish: bool = False
        self._expect_natural_advance: bool = False
        self._host_codec_sticky = False
        self._host_ffmpeg_sticky = False
        self._job_status: str | None = None
        self._analyze_map_tick = 0
        self._last_analyze_map_refresh = 0.0
        self._duration = 0
        self._rebuild_lock = False
        # Listen-nudge / lens refresh requested while play_id holds the rebuild lock.
        self._pending_plan_refresh = False
        # Consecutive Renew Queue presses since last lens/mode change (capped in engine).
        self._renew_streak = 0
        # Restart analyze once lingering timed-out workers fully exit.
        self._pending_analyze_after_linger = False
        # Keep process alive after window close until lingering workers finish.
        self._quit_when_idle = False
        self._handling_playback_error = False
        # Hard-cut play_id credits immediately; cleared/undone if media errors.
        self._last_hard_play_credit: int | None = None
        # play_next already credited this id — play_id must not credit it again.
        self._abandon_credited_id: int | None = None
        self._lens_timer = QTimer(self)
        self._lens_timer.setSingleShot(True)
        self._lens_timer.setInterval(180)
        self._lens_timer.timeout.connect(lambda: self.refresh_plan(rebuild_queue=False))

        self._build()
        self._bind()
        self._restore_lens()
        if not self.library.folders():
            music = Path.home() / "Music"
            if music.is_dir():
                self.library.add_folder(str(music))
        self.refresh_plan()
        QTimer.singleShot(400, self.start_scan)
        QTimer.singleShot(900, self._maybe_warn_libx264)
        QTimer.singleShot(1100, self._maybe_warn_ffmpeg)

    def _paint_status(self, message: str) -> None:
        """Write the status strip, keeping sticky host tips when dependencies are missing."""
        text = message or ""
        tips: list[str] = []
        if self._host_codec_sticky and should_warn_missing_libx264():
            tips.append(libx264_missing_status())
        else:
            self._host_codec_sticky = False
        if self._host_ffmpeg_sticky and should_warn_missing_ffmpeg():
            tips.append(ffmpeg_missing_status())
        else:
            self._host_ffmpeg_sticky = False
        for tip in tips:
            if tip and tip not in text:
                text = f"{text} · {tip}" if text else tip
        self.status_label.setText(text)
        if self._job_status:
            self.status_label.setStyleSheet("color: #c5cde0; font-weight: 500;")
        else:
            self.status_label.setStyleSheet("")

    def _set_job_status(self, message: str) -> None:
        """Sticky scan/analyze line — owns the strip until cleared."""
        self._job_status = message or ""
        self._paint_status(self._job_status)

    def _clear_job_status(self) -> None:
        self._job_status = None
        self.status_label.setStyleSheet("")

    def _set_status(self, message: str) -> None:
        """Ephemeral tips (hover, lens, queue). Does not overwrite an active job line."""
        if self._job_status:
            return
        self._paint_status(message)

    def _maybe_warn_libx264(self) -> None:
        """AppImage keeps libx264 on the host — tell the user if playback may be broken."""
        if not should_warn_missing_libx264():
            self._host_codec_sticky = False
            return
        self._host_codec_sticky = True
        self._set_status(libx264_missing_status())
        if self.settings.value("host/skip_libx264_warning", False, type=bool):
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("System library needed for playback")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(libx264_missing_message())
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        dont = box.addButton("Don’t show again", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        if box.clickedButton() is dont:
            self.settings.setValue("host/skip_libx264_warning", True)
        # Re-assert after the dialog so scan/analyze progress cannot bury the tip forever.
        self._set_status(libx264_missing_status())

    def _maybe_warn_ffmpeg(self) -> None:
        """Mood analyze needs host ffmpeg — warn when PATH has no binary."""
        if not should_warn_missing_ffmpeg():
            self._host_ffmpeg_sticky = False
            return
        self._host_ffmpeg_sticky = True
        # Next successful ffmpeg session may re-queue seed-only never-heard rows.
        self.settings.setValue("analyze/retry_seed_after_ffmpeg", True)
        self._set_status(ffmpeg_missing_status())
        if self.settings.value("host/skip_ffmpeg_warning", False, type=bool):
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("ffmpeg needed for mood analysis")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(ffmpeg_missing_message())
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        dont = box.addButton("Don’t show again", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        if box.clickedButton() is dont:
            self.settings.setValue("host/skip_ffmpeg_warning", True)
        self._set_status(ffmpeg_missing_status())
    def _build(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 24, 14, 8)
        layout.setSpacing(20)

        header = QHBoxLayout()
        header.setContentsMargins(0, 4, 0, 8)
        brand = QLabel("MERIDIAN")
        brand.setObjectName("brand")
        self.band_chip = QLabel("Night")
        self.band_chip.setObjectName("chip")
        self.mode_box = QComboBox()
        for mode in Mode:
            self.mode_box.addItem(MODE_LABELS[mode], mode.value)
        idx = self.mode_box.findData(self.mode.value)
        if idx >= 0:
            self.mode_box.setCurrentIndex(idx)
        self.hint = QLabel(MODE_HINTS[self.mode])
        self.hint.setObjectName("hint")
        self.search = TrackSearch(self.library)
        self.search.setMinimumWidth(260)
        add_btn = QPushButton("Add library folder")
        add_btn.setObjectName("ghostBtn")
        scan_btn = QPushButton("Rescan")
        scan_btn.setObjectName("ghostBtn")
        add_btn.clicked.connect(self.add_folder)
        scan_btn.clicked.connect(self.start_rescan)
        header.addWidget(brand)
        header.addSpacing(12)
        header.addWidget(self.band_chip)
        header.addWidget(self.mode_box)
        header.addWidget(self.hint, 1)
        header.addWidget(self.search)
        header.addWidget(add_btn)
        header.addWidget(scan_btn)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        map_label = QLabel(
            "MOOD MAP  ·  drag a star to pin  ·  scroll = lens  ·  pinch / Ctrl+scroll = dive  ·  drag empty = pan  ·  double-click empty = night sky"
        )
        map_label.setObjectName("section")
        self.map = MoodMap()
        left_l.addWidget(map_label)
        left_l.addWidget(self.map, 1)

        right = QSplitter(Qt.Orientation.Vertical)
        right.setHandleWidth(18)
        right.setChildrenCollapsible(False)
        self.matrix = EisenhowerMatrix()
        queue_wrap = QFrame()
        queue_wrap.setObjectName("queuePanel")
        q_l = QVBoxLayout(queue_wrap)
        q_l.setContentsMargins(10, 10, 10, 10)
        q_l.setSpacing(8)
        q_head_row = QHBoxLayout()
        q_head_row.setContentsMargins(0, 0, 0, 0)
        q_head_row.setSpacing(8)
        q_head = QLabel("CONTEXT QUEUE  ·  replenishes from lens, clock, and matrix when empty")
        q_head.setObjectName("section")
        renew_btn = QPushButton("Renew queue")
        renew_btn.setObjectName("ghostBtn")
        renew_btn.setToolTip(
            "Pick a fresh set of songs from the mood neighborhood you’re looking at "
            "(the listen matrix).\n\n"
            "Not shuffle. Not skips. What’s already playing stays.\n"
            "Wandering the map alone doesn’t change this list — Renew is how you "
            "commit a new one."
        )
        renew_btn.setToolTipDuration(12000)
        renew_btn.clicked.connect(self._renew_queue)
        q_head_row.addWidget(q_head, 1)
        q_head_row.addWidget(renew_btn, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.queue_list = StarList()
        self.queue_list.setObjectName("queueList")
        self.queue_list.setFont(condensed(12))
        q_l.addLayout(q_head_row)
        q_l.addWidget(self.queue_list, 1)
        right.addWidget(self.matrix)
        right.addWidget(queue_wrap)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 2)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self.transport = TransportBar()
        layout.addLayout(header)
        layout.addWidget(splitter, 1)
        layout.addWidget(self.transport)

        status = QStatusBar()
        self.setStatusBar(status)
        self.status_label = QLabel("Local, offline, map-first.")
        status.addWidget(self.status_label)

        play_act = QAction("Play/Pause", self)
        play_act.setShortcut(QKeySequence(Qt.Key.Key_Space))
        play_act.triggered.connect(self.toggle_play)
        self.addAction(play_act)
        next_act = QAction("Next", self)
        next_act.setShortcut(QKeySequence("Ctrl+Right"))
        next_act.triggered.connect(self.play_next)
        self.addAction(next_act)
        prev_act = QAction("Previous", self)
        prev_act.setShortcut(QKeySequence("Ctrl+Left"))
        prev_act.triggered.connect(self.play_prev)
        self.addAction(prev_act)
        find_act = QAction("Search", self)
        find_act.setShortcut(QKeySequence.StandardKey.Find)
        find_act.triggered.connect(self.search.setFocus)
        self.addAction(find_act)

    def _bind(self) -> None:
        self.mode_box.currentIndexChanged.connect(self._mode_changed)
        self.map.lens_changed.connect(self._lens_changed)
        self.map.track_pinned.connect(self._pin_track)
        self.map.track_activated.connect(self._map_activated)
        self.map.track_hovered.connect(self._set_status)
        self.matrix.track_activated.connect(self._pull_and_play)
        self.queue_list.itemDoubleClicked.connect(self._queue_activated)
        self.search.track_chosen.connect(self._search_picked)
        self.search.query_changed.connect(self.map.set_search_query)
        self.transport.play_toggled.connect(self.toggle_play)
        self.transport.previous.connect(self.play_prev)
        self.transport.next.connect(self.play_next)
        self.transport.seeked.connect(self.player.seek)
        self.transport.volume_changed.connect(self.player.set_volume)
        self.transport.love_toggled.connect(self._love)
        self.player.position_changed.connect(self._pos)
        self.player.duration_changed.connect(self._dur)
        self.player.state_changed.connect(self.transport.set_playing)
        self.player.state_changed.connect(self.map.set_playing)
        self.map.set_playing(self.player.is_playing())
        self.player.track_nearly_finished.connect(self._nearly_finished)
        self.player.track_finished.connect(self._track_finished_hard)
        self.player.crossfade_settled.connect(self._crossfade_settled)
        self.player.error_occurred.connect(self._playback_error)

    def _restore_lens(self) -> None:
        try:
            x = float(self.settings.value("lens_x", 0.52))
            y = float(self.settings.value("lens_y", 0.48))
            r = float(self.settings.value("lens_r", LENS_RADIUS_DEFAULT))
        except (TypeError, ValueError):
            x, y, r = 0.52, 0.48, LENS_RADIUS_DEFAULT
        x = max(0.0, min(1.0, x))
        y = max(0.0, min(1.0, y))
        r = max(LENS_RADIUS_MIN, min(LENS_RADIUS_MAX, r))
        self._apply_mode_lens_scale()
        self.map.set_lens(x, y, r)

    def skip_pressure(self) -> float:
        cutoff = time() - 900
        self.skips_window = [t for t in self.skips_window if t > cutoff]
        return min(1.0, len(self.skips_window) / 6)

    def current_context(self):
        x, y, r = self.map.lens_mood()
        _, _, scale = mode_bias(self.mode)
        return make_context(self.mode, x, y, r * scale, self.skip_pressure())

    def _apply_mode_lens_scale(self) -> None:
        _, _, scale = mode_bias(self.mode)
        self.map.set_radius_scale(scale)

    def _playable_tracks(self):
        """Library rows whose files still exist and are not playback-denylisted."""
        return [
            t
            for t in self.library.all_tracks()
            if Path(t.path).exists() and not self.library.is_playback_denied(t.id)
        ]

    def _flush_pending_plan_refresh(self) -> None:
        if self._closing:
            self._pending_plan_refresh = False
            return
        if not self._pending_plan_refresh:
            return
        self._pending_plan_refresh = False
        self.refresh_plan(rebuild_queue=False)

    def _reset_renew_streak(self) -> None:
        self._renew_streak = 0

    def _confirm_renew_once(self) -> bool:
        """First click: explain Renew like Rescan explains itself; then remember."""
        if self.settings.value("ui/renew_explained", False, type=bool):
            return True
        reply = QMessageBox.information(
            self,
            "Renew the context queue?",
            (
                "Renew picks a fresh set of songs from the neighborhood you’re looking "
                "at on the mood map (the listen matrix).\n\n"
                "This is not shuffle, and it does not count replaced songs as skips. "
                "What’s already playing stays put.\n\n"
                "Moving the lens updates suggestions in the matrix; the queue itself "
                "holds until it runs low or you Renew.\n\n"
                "Continue?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False
        self.settings.setValue("ui/renew_explained", True)
        return True

    def _show_band(self, ctx) -> None:
        self.band_chip.setText(ctx.band_label)
        self.map.set_sky_band(ctx.band.value)

    def _renew_queue(self) -> None:
        """New recommendation pass from the current matrix — not skips, not shuffle."""
        if self._closing or self._rebuild_lock:
            return
        if not self._confirm_renew_once():
            return
        prior = list(self.session_queue)
        ctx = self.current_context()
        self._show_band(ctx)
        tracks = self._playable_tracks()
        current_id = self.player.current.id if self.player.current else None
        exempt = set(self.explicit)
        if current_id is not None:
            exempt.add(current_id)
        renewal = RenewalContext(
            prior_queue_ids=frozenset(prior),
            renew_streak=self._renew_streak,
            exempt_ids=frozenset(exempt),
            heard_ids=frozenset(self.played_history),
        )
        self.plan = build_plan(tracks, ctx, self.explicit, renewal=renewal)
        self.map.set_tracks(self.plan.ranked, current_id)
        self.matrix.set_plan(self.plan.by_quadrant)
        kept_ephemeral = {tid for tid in self.ephemeral if tid in self.plan.order}
        self.session_queue = list(self.plan.order)
        self.ephemeral = kept_ephemeral
        if current_id and current_id in self.session_queue:
            self.queue_index = self.session_queue.index(current_id)
        elif current_id:
            self.queue_index = -1
        else:
            self.queue_index = 0
        self._fill_queue()
        # Only the last replaced queue feeds the next renew; streak is bounded.
        self._renew_streak = min(3, self._renew_streak + 1)
        self._set_status("Context queue renewed from the current matrix.")

    def refresh_plan(self, keep_current: bool = True, rebuild_queue: bool = True) -> None:
        if self._closing:
            return
        if self._rebuild_lock:
            # Never drop a non-rebuild refresh (listen nudge / lens) permanently.
            if not rebuild_queue:
                self._pending_plan_refresh = True
            return
        ctx = self.current_context()
        self._show_band(ctx)
        tracks = self._playable_tracks()
        current_id = self.player.current.id if self.player.current else None
        self.plan = build_plan(tracks, ctx, self.explicit)
        self.map.set_tracks(self.plan.ranked, current_id)
        self.matrix.set_plan(self.plan.by_quadrant)
        if rebuild_queue:
            # Keep one-shot matrix pulls that still appear in the new order.
            kept_ephemeral = {tid for tid in self.ephemeral if tid in self.plan.order}
            self.session_queue = list(self.plan.order)
            self.ephemeral = kept_ephemeral
            if keep_current and current_id and current_id in self.session_queue:
                self.queue_index = self.session_queue.index(current_id)
            elif keep_current and current_id:
                # Still playing something absent from the new queue: Next should
                # land on slot 0, not skip it via queue_index += 1 from 0.
                self.queue_index = -1
            else:
                self.queue_index = 0
        self._fill_queue()
        n = len(tracks)
        self._set_status(f"{n} local tracks · lens at mood {ctx.lens_x:.2f},{ctx.lens_y:.2f}")

    def _fill_queue(self) -> None:
        self.queue_list.clear()
        self.map.set_queue_route(list(self.session_queue))
        if not self.session_queue:
            return
        by_id = {r.track.id: r for r in self.plan.ranked} if self.plan else {}
        for i, tid in enumerate(self.session_queue):
            track = self.library.get(tid)
            if not track:
                continue
            mark = "· " if tid in self.ephemeral else ""
            item = QListWidgetItem(f"{i + 1:02d}  {mark}{track.short_title}")
            item.setData(Qt.ItemDataRole.UserRole, tid)
            ranked = by_id.get(tid)
            tip = self._queue_reason(track, ranked, tid in self.ephemeral)
            if ranked:
                item.setForeground(QColor(PLAYLIST_HEX[ranked.quadrant]))
                item.setData(STAR_COLOR_ROLE, PLAYLIST_HEX[ranked.quadrant])
                item.setData(STAR_LOVED_ROLE, bool(track.loved))
            item.setData(STAR_LINK_ROLE, True)
            item.setData(STAR_CURRENT_ROLE, i == self.queue_index)
            item.setToolTip(tip)
            if i == self.queue_index:
                item.setSelected(True)
            self.queue_list.addItem(item)

    def _queue_reason(self, track, ranked, is_ephemeral: bool) -> str:
        lines = [track.label]
        if is_ephemeral:
            lines.append("⮕ You picked this from the matrix — plays once then removed")
        if ranked:
            q = ranked.quadrant
            fit_pct = f"{ranked.fit:.0%}"
            imp_pct = f"{ranked.importance:.0%}"
            if q == Quadrant.NOW:
                lines.append(f"NOW — closest to the lens ({fit_pct} fit), high importance ({imp_pct})")
            elif q == Quadrant.DEEP:
                lines.append(f"DEEP — important ({imp_pct}) but just outside the lens core")
            elif q == Quadrant.FILL:
                lines.append(f"FILL — near the lens ({fit_pct} fit) but lower importance ({imp_pct})")
            else:
                lines.append(f"SHELF — outside the lens area ({fit_pct} fit, {imp_pct} importance)")
            reasons = []
            if track.loved:
                reasons.append("♥ loved — boosted importance")
            if track.pinned:
                reasons.append("pinned mood position on the map")
            if track.play_count >= 4:
                reasons.append(f"played {track.play_count}× — familiar pick")
            elif track.play_count == 0:
                reasons.append("never played — fresh discovery")
            if track.skip_count > track.play_count and track.skip_count >= 3:
                reasons.append(f"skipped often ({track.skip_count}×) — deprioritized")
            ctx = self.current_context()
            reasons.append(f"clock band: {ctx.band_label}")
            reasons.append(f"mode: {self.mode.value.title()}")
            reasons.append(f"mood: valence {track.valence:.2f}, energy {track.energy:.2f}")
            conf = float(getattr(track, "mood_confidence", 0.5) or 0.5)
            if track.pinned:
                reasons.append("confidence 1.00 (pinned)")
            else:
                reasons.append(f"confidence {conf:.2f}")
                note = (getattr(track, "confidence_note", "") or "").strip()
                if note:
                    reasons.append(note)
                if conf < CONFIDENCE_LOW:
                    reasons.append("weak placement evidence (dimmed)")
                elif conf < CONFIDENCE_HIGH:
                    reasons.append("partial placement evidence")
            if reasons:
                lines.append("Why: " + " · ".join(reasons))
        else:
            lines.append("Added to the queue directly")
        return "\n".join(lines)

    def _mode_changed(self) -> None:
        self.mode = Mode(self.mode_box.currentData())
        self.settings.setValue("mode", self.mode.value)
        self.hint.setText(MODE_HINTS[self.mode])
        self._apply_mode_lens_scale()
        self._reset_renew_streak()
        self.refresh_plan()

    def _lens_changed(self, x: float, y: float, r: float) -> None:
        self.settings.setValue("lens_x", x)
        self.settings.setValue("lens_y", y)
        self.settings.setValue("lens_r", r)
        self._reset_renew_streak()
        self._lens_timer.start()

    def _search_picked(self, track_id: int) -> None:
        track = self.library.get(track_id)
        if not track:
            return
        _, _, radius = self.map.lens_mood()
        self.map.set_lens(track.valence, track.energy, radius)
        self.settings.setValue("lens_x", track.valence)
        self.settings.setValue("lens_y", track.energy)
        self.settings.setValue("lens_r", radius)
        self._set_status(f"Lens on {track.label}")
        # set_lens does not emit lens_changed — refresh matrix/plan for the new lens.
        self.refresh_plan(rebuild_queue=False)
        self._pull_and_play(track_id)

    def _pin_track(self, track_id: int, valence: float, energy: float) -> None:
        self.library.set_mood(track_id, valence, energy, pinned=True)
        self.refresh_plan(rebuild_queue=False)

    def add_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Add music folder", str(Path.home() / "Music"))
        if path:
            self.library.add_folder(path)
            self.start_scan()

    def start_rescan(self) -> None:
        """Force re-read tags and re-analyze every track under library folders."""
        if self._closing:
            return
        if self._lingering_workers:
            self._set_job_status("Still finishing previous scan/analyze…")
            return
        if self._scan_thread and self._scan_thread.isRunning():
            return
        n = len(self.library.all_tracks())
        reply = QMessageBox.warning(
            self,
            "Rescan whole library?",
            (
                "Rescan re-reads tags and re-analyzes mood for every track in your "
                f"library folders ({n} track{'s' if n != 1 else ''}).\n\n"
                "This can take a while on large libraries. Pinned stars keep their "
                "positions; everything else may move on the map.\n\n"
                "Continue?"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        # Must fully stop analyze before dirtying the DB / starting a new scan.
        if not self._stop_analyze(wait_ms=20000) or self._lingering_workers:
            self._set_job_status("Still finishing previous analyze — try Rescan again in a moment.")
            return
        pending = self.library.mark_all_pending_analysis()
        self._set_job_status(f"Rescanning library ({pending} tracks to re-analyze)…")
        self._start_scan_worker(force=True, on_finished=self._rescan_done)

    def _rescan_done(self, count: int, gen: int | None = None) -> None:
        if gen is not None and gen != self._scan_gen:
            return
        self._cleanup_scan_thread()
        if self._closing:
            return
        if count < 0:
            self._clear_job_status()
            self._set_status("Rescan failed.")
            return
        self._set_job_status(f"Rescanned {count} files. Re-analyzing waveforms…")
        self.refresh_plan(rebuild_queue=False)
        self.start_analyze()

    def _analyze_busy(self) -> bool:
        """True while any analyze (or post-analyze tidy) thread is still running."""
        pool = list(getattr(self, "_analyze_pool", []) or [])
        if any(t is not None and t.isRunning() for t, _w in pool):
            return True
        thread = getattr(self, "_analyze_thread", None)
        if thread is not None and thread.isRunning():
            return True
        tidy = getattr(self, "_tidy_thread", None)
        if tidy is not None and tidy.isRunning():
            return True
        return False

    def start_scan(self) -> None:
        if self._closing:
            return
        if self._lingering_workers:
            # Do not start a second scan while a timed-out worker still holds the DB.
            self._set_job_status("Waiting for previous scan/analyze to finish…")
            return
        if self._analyze_busy():
            # Dual analyze writers + scan thrash SQLite — wait (Rescan stops analyze).
            self._set_job_status("Waiting for waveform analyze to finish…")
            return
        # Refuse while prior scan refs remain — even if the thread already quit —
        # so a queued finished slot can reap its own generation (AB2/H1).
        if self._scan_thread is not None or self._scan_worker is not None:
            return
        self._set_job_status("Scanning local files…")
        self._start_scan_worker(force=False, on_finished=self._scan_done)

    def _start_scan_worker(self, *, force: bool, on_finished) -> None:
        self._cleanup_scan_thread()
        self._scan_gen += 1
        gen = self._scan_gen
        self._scan_worker = ScanWorker(self.library, force=force)
        self._scan_thread = start_worker(self._scan_worker)
        # Queue UI handlers on the GUI thread. Use real @Slot bridges — PySide 6.11
        # silently drops QueuedConnection to lambdas/partials (scan never handed
        # off to analyze on first-run).
        queued = Qt.ConnectionType.QueuedConnection
        self._scan_worker.progress.connect(self._set_job_status, queued)
        self._scan_worker.failed.connect(self._on_scan_failed, queued)
        bridge = _ScanFinishBridge(on_finished, gen, parent=self)
        self._scan_finish_bridge = bridge
        self._scan_worker.finished.connect(bridge.on_finished, queued)

    @Slot(str)
    def _on_scan_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Scan failed", message)

    def _disconnect_worker(self, worker) -> None:
        if worker is None:
            return
        for name in ("progress", "finished", "failed"):
            sig = getattr(worker, name, None)
            if sig is None:
                continue
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass

    def _linger_worker(self, thread, worker) -> None:
        """Keep a timed-out thread alive with UI signals detached until it exits."""
        if thread is None:
            return
        pair = (thread, worker)
        if pair in self._lingering_workers:
            return
        self._lingering_workers.append(pair)

        def _reap() -> None:
            # Idempotent: finished may race with the isRunning() check below.
            if pair not in self._lingering_workers:
                return
            self._lingering_workers.remove(pair)
            thread.deleteLater()
            if worker is not None:
                worker.deleteLater()
            idle = not self._lingering_workers
            if idle and self._close_library_when_idle:
                try:
                    self.library.close()
                except Exception:
                    pass
                self._close_library_when_idle = False
            if idle and self._pending_analyze_after_linger and not self._closing:
                self._pending_analyze_after_linger = False
                self.start_analyze()
            if idle and self._quit_when_idle:
                self._quit_when_idle = False
                app = QApplication.instance()
                if app is not None:
                    app.quit()

        thread.finished.connect(_reap)
        # TOCTOU: thread may have exited after wait() timed out but before connect.
        # finished would never fire — reaping now (or on next event loop tick) avoids a zombie.
        if not thread.isRunning():
            QTimer.singleShot(0, _reap)

    def _reap_worker_thread(self, thread, worker, *, wait_ms: int = 8000) -> bool:
        """Dispose a worker QThread without ever calling wait() on ourselves.

        Calling QThread.wait() from inside that same thread aborts ("Thread tried
        to wait on itself") — a common footgun when finished handlers use lambdas
        (DirectConnection on the worker thread).
        """
        if worker is not None:
            self._disconnect_worker(worker)
            if thread is not None:
                try:
                    worker.finished.connect(
                        lambda *_a, t=thread: t.quit(),
                        Qt.ConnectionType.DirectConnection,
                    )
                except (RuntimeError, TypeError):
                    pass
        if thread is None:
            if worker is not None:
                worker.deleteLater()
            return True
        if thread.isRunning():
            thread.quit()
            # Never wait on the thread we are currently executing on.
            if QThread.currentThread() == thread:
                self._linger_worker(thread, worker)
                return False
            if not thread.wait(wait_ms):
                self._linger_worker(thread, worker)
                return False
        thread.deleteLater()
        if worker is not None:
            worker.deleteLater()
        return True

    def _cleanup_scan_thread(self, wait_ms: int = 8000) -> bool:
        """Stop the scan worker. Returns True when the thread is fully stopped."""
        # Invalidate queued finished slots before clearing refs (AB2/H1).
        self._scan_gen += 1
        worker = self._scan_worker
        thread = self._scan_thread
        self._scan_worker = None
        self._scan_thread = None
        if worker is not None:
            worker.abort()
        return self._reap_worker_thread(thread, worker, wait_ms=wait_ms)

    def _stop_analyze(self, wait_ms: int = 20000) -> bool:
        """Stop analyze (+ tidy). Returns True when every thread is fully stopped."""
        # Bump generation first so a late finished signal cannot restart analyze.
        # Abort kills in-flight ffmpeg so this wait stays bounded.
        self._analyze_gen += 1
        pool = list(self._analyze_pool)
        if not pool and (self._analyze_thread is not None or self._analyze_worker is not None):
            pool = [(self._analyze_thread, self._analyze_worker)]
        tidy_pair = None
        if getattr(self, "_tidy_thread", None) is not None or getattr(self, "_tidy_worker", None) is not None:
            tidy_pair = (self._tidy_thread, self._tidy_worker)
        self._analyze_pool = []
        self._analyze_finish_bridges = []
        self._analyze_remaining = 0
        self._analyze_run_tidy = False
        self._analyze_progress_shown = 0
        self._analyze_worker = None
        self._analyze_thread = None
        self._tidy_thread = None
        self._tidy_worker = None
        self._tidy_finish_bridge = None
        for _thread, worker in pool:
            if worker is not None:
                worker.abort()
        # Shared deadline so dual workers do not stack 20s + 20s.
        deadline = time() + max(0.0, float(wait_ms) / 1000.0)
        ok = True
        for thread, worker in pool:
            left_ms = max(0, int((deadline - time()) * 1000.0))
            if not self._reap_worker_thread(thread, worker, wait_ms=left_ms):
                ok = False
        if tidy_pair is not None:
            left_ms = max(0, int((deadline - time()) * 1000.0))
            if not self._reap_worker_thread(tidy_pair[0], tidy_pair[1], wait_ms=left_ms):
                ok = False
        return ok

    def _scan_done(self, added: int, gen: int | None = None) -> None:
        # Ignore stale finished after a newer scan started / cleanup bumped gen.
        if gen is not None and gen != self._scan_gen:
            return
        # Finished already ran; reap without abort racing a live walk.
        worker = self._scan_worker
        thread = self._scan_thread
        self._scan_worker = None
        self._scan_thread = None
        self._reap_worker_thread(thread, worker, wait_ms=3000)
        if self._closing:
            return
        if added < 0:
            self._clear_job_status()
            self._set_status("Scan failed.")
            return
        self._set_job_status(f"Indexed {added} new files. Mapping mood…")
        self.refresh_plan(rebuild_queue=False)
        self.start_analyze()

    def start_analyze(self) -> None:
        if self._closing:
            return
        if self._lingering_workers:
            # Avoid overlapping ffmpeg / abort state with a timed-out analyze.
            self._pending_analyze_after_linger = True
            self._set_job_status("Waiting for previous analyze to finish…")
            return
        # Reap finished-but-not-yet-disposed analyze pool before overwrite (AB3/H2).
        prev_pool = list(getattr(self, "_analyze_pool", []) or [])
        if not prev_pool and (
            self._analyze_thread is not None or self._analyze_worker is not None
        ):
            prev_pool = [(self._analyze_thread, self._analyze_worker)]
        if prev_pool:
            if any(t is not None and t.isRunning() for t, _w in prev_pool):
                return
            self._analyze_pool = []
            self._analyze_thread = None
            self._analyze_worker = None
            for prev_thread, prev_worker in prev_pool:
                self._reap_worker_thread(prev_thread, prev_worker, wait_ms=3000)
        if self.library.closed:
            return
        # One-shot recovery: only re-queue seed-only never-heard rows after a
        # session that saw ffmpeg missing (not every launch).
        if not getattr(self, "_seed_retry_wave", False):
            self._seed_retry_wave = True
            from meridian.host_deps import host_ffmpeg_available

            if host_ffmpeg_available() and self.settings.value(
                "analyze/retry_seed_after_ffmpeg", False, type=bool
            ):
                self.library.requeue_seed_only_without_pcm()
                self.settings.setValue("analyze/retry_seed_after_ffmpeg", False)
        # Heal mass "kept prior" defer backlog once per process so a stuck DB
        # (analyzed=0 forever after failed re-listen) does not re-queue everyone.
        # Runs before pending select; does not re-fire after mid-session Rescan.
        if not getattr(self, "_healed_kept_prior", False):
            self._healed_kept_prior = True
            healed = self.library.heal_kept_prior_pending()
            if healed:
                self._set_job_status(
                    f"Kept prior mood for {healed} track{'s' if healed != 1 else ''}…"
                )
        pending_ids = self.library.unanalyzed_ids()
        if not pending_ids:
            self._clear_job_status()
            pcm_ok, deferred = self.library.analyze_session_stats()
            if deferred > 0 and pcm_ok == 0:
                self._set_status(
                    "Some tracks need another listen — mood map kept what it could."
                )
            else:
                self._set_status("Nothing left to analyze this session.")
            return
        self._pending_analyze_after_linger = False
        self._analyze_map_tick = 0
        self._last_analyze_map_refresh = 0.0
        self._analyze_progress_shown = 0
        self._analyze_gen += 1
        gen = self._analyze_gen
        from meridian.features import clear_decode_abort
        from meridian.host_deps import preferred_analyze_workers

        clear_decode_abort()
        self.library.reset_analyze_session_stats()
        worker_n = preferred_analyze_workers(self.library.folders())
        worker_n = max(1, min(2, int(worker_n)))
        if len(pending_ids) < 2:
            worker_n = 1
        queue = AnalyzeTrackQueue(pending_ids)
        queued = Qt.ConnectionType.QueuedConnection
        pool: list[tuple] = []
        bridges: list[_AnalyzeFinishBridge] = []
        for index in range(worker_n):
            worker = AnalyzeWorker(
                self.library,
                queue=queue,
                run_tidy=False,
                reset_session=False,
                clear_abort=False,
            )
            thread = start_worker(worker)
            pool.append((thread, worker))
            worker.progress.connect(self._on_analyze_progress, queued)
            # Must be a @Slot bridge — Queued lambda never delivers on PySide 6.11.
            bridge = _AnalyzeFinishBridge(self, gen, thread, worker, parent=self)
            bridges.append(bridge)
            worker.finished.connect(bridge.on_finished, queued)
        self._analyze_pool = pool
        self._analyze_finish_bridges = bridges
        self._analyze_remaining = len(pool)
        self._analyze_run_tidy = True
        # Legacy single-slot refs → primary worker (stop/reap / older tests).
        self._analyze_thread = pool[0][0]
        self._analyze_worker = pool[0][1]
        self._analyze_finish_bridge = bridges[0]

    @Slot(str, int, int)
    def _on_analyze_progress(self, name: str, i: int, n: int) -> None:
        # Dual workers can deliver Queued progress out of order — ignore backward jumps.
        shown = int(getattr(self, "_analyze_progress_shown", 0) or 0)
        if i < shown:
            return
        self._analyze_progress_shown = i
        self._set_job_status(f"Listening to waveform {i}/{n}: {name}")
        # Throttle map refresh so the sky opens during long analyzes without
        # rebaking on every track (and without waiting for the full tidy pass).
        self._analyze_map_tick += 1
        now = time()
        due = self._analyze_map_tick >= 40 and (now - self._last_analyze_map_refresh) >= 1.5
        if due or (i == 1 and self._last_analyze_map_refresh <= 0.0):
            self._analyze_map_tick = 0
            self._last_analyze_map_refresh = now
            self.refresh_plan(rebuild_queue=False)

    def _analyze_done(
        self,
        gen: int,
        thread=None,
        worker=None,
    ) -> None:
        # Always dispose the finishing worker's objects; only when the whole pool
        # for this gen is idle may we tidy / restart analyze.
        if thread is None:
            thread = self._analyze_thread
        if worker is None:
            worker = self._analyze_worker
        pool = list(getattr(self, "_analyze_pool", []) or [])
        if pool:
            self._analyze_pool = [(t, w) for t, w in pool if t is not thread and w is not worker]
        if gen == self._analyze_gen:
            if self._analyze_thread is thread:
                self._analyze_thread = None
            if self._analyze_worker is worker:
                self._analyze_worker = None
            if getattr(self, "_analyze_remaining", 0) > 0:
                self._analyze_remaining -= 1
        elif self._analyze_thread is thread:
            # Should not happen if start_analyze reaped first; clear safely.
            self._analyze_thread = None
            self._analyze_worker = None
        self._reap_worker_thread(thread, worker, wait_ms=3000)
        if gen != self._analyze_gen:
            return
        # Wait for sibling workers in the SSD dual pool.
        if getattr(self, "_analyze_remaining", 0) > 0:
            return
        self._analyze_finish_bridges = []
        if self._closing:
            return
        if getattr(self, "_analyze_run_tidy", False):
            self._analyze_run_tidy = False
            self._start_analyze_tidy(gen)
            return
        self._finish_analyze_session(gen)

    def _start_analyze_tidy(self, gen: int) -> None:
        """Run album tidy on a background thread so the GUI stays responsive."""
        if self._closing or gen != self._analyze_gen:
            return
        self._set_job_status("Tidying mood map…")
        worker = TidyWorker(self.library)
        thread = start_worker(worker)
        self._tidy_worker = worker
        self._tidy_thread = thread
        queued = Qt.ConnectionType.QueuedConnection
        bridge = _TidyFinishBridge(self, gen, thread, worker, parent=self)
        self._tidy_finish_bridge = bridge
        worker.finished.connect(bridge.on_finished, queued)

    def _analyze_tidy_done(
        self,
        gen: int,
        error: str,
        thread=None,
        worker=None,
    ) -> None:
        if thread is None:
            thread = self._tidy_thread
        if worker is None:
            worker = self._tidy_worker
        if self._tidy_thread is thread:
            self._tidy_thread = None
        if self._tidy_worker is worker:
            self._tidy_worker = None
        self._tidy_finish_bridge = None
        self._reap_worker_thread(thread, worker, wait_ms=3000)
        if gen != self._analyze_gen:
            return
        if self._closing:
            return
        if error:
            self._set_status(f"Mood map tidy failed: {error}")
        self._finish_analyze_session(gen)

    def _finish_analyze_session(self, gen: int) -> None:
        if gen != self._analyze_gen or self._closing:
            return
        # If scan added more tracks while we were analyzing, finish them.
        if self.library.unanalyzed_ids():
            self._set_job_status("More tracks to map…")
            self.refresh_plan(rebuild_queue=False)
            self.start_analyze()
            return
        self._clear_job_status()
        self.refresh_plan(rebuild_queue=False)
        pcm_ok, deferred = self.library.analyze_session_stats()
        if pcm_ok == 0 and deferred > 0:
            self._set_status(
                "Some tracks need another listen — mood map kept what it could."
            )
        elif pcm_ok == 0:
            # Tag/path concluded with no waveform this pass — do not imply a listen.
            self._set_status(
                "Mood map updated from tags — no waveform listen this pass."
            )
        elif deferred > 0:
            self._set_status(
                f"Mood map updated from local audio ({deferred} still pending)."
            )
        else:
            self._set_status("Mood map updated from local audio.")

    def _map_activated(self, track_id: int) -> None:
        """Play a map star and keep context-queue Next/Prev aligned with it."""
        if track_id in self.session_queue:
            self.queue_index = self.session_queue.index(track_id)
            self.play_id(track_id)
        else:
            self._pull_and_play(track_id)

    def _queue_activated(self, item: QListWidgetItem) -> None:
        tid = item.data(Qt.ItemDataRole.UserRole)
        if not tid:
            return
        track_id = int(tid)
        if track_id in self.session_queue:
            self.queue_index = self.session_queue.index(track_id)
        self.play_id(track_id)

    def _pull_and_play(self, track_id: int) -> None:
        """Insert a matrix pick into the context queue, play it once, then continue."""
        current_id = self.player.current.id if self.player.current else None
        at_queue_cursor = (
            track_id in self.session_queue
            and self.session_queue.index(track_id) == self.queue_index
        )
        # Re-activating the playing / current queue track: restart in place.
        # Do not pop/reinsert (strands the former next above the highlight) or
        # mark ephemeral (would one-shot a track the user is already on).
        if track_id == current_id or at_queue_cursor:
            if track_id in self.session_queue:
                self.queue_index = self.session_queue.index(track_id)
            self.play_id(track_id)
            return
        if track_id in self.session_queue:
            old = self.session_queue.index(track_id)
            self.session_queue.pop(old)
            if old < self.queue_index:
                self.queue_index -= 1
        if self.player.current and self.session_queue:
            insert_at = min(self.queue_index + 1, len(self.session_queue))
        else:
            insert_at = max(0, min(self.queue_index, len(self.session_queue)))
        self.session_queue.insert(insert_at, track_id)
        self.ephemeral.add(track_id)
        self.queue_index = insert_at
        if track_id not in self.explicit:
            self.explicit.insert(0, track_id)
            self.explicit = self.explicit[:12]
        self.play_id(track_id)

    def toggle_play(self) -> None:
        """Pause/resume current track, or start the context queue if nothing is loaded."""
        if self.player.is_playing():
            self.player.toggle()
            return
        if self.player.current is not None:
            tid = self.player.current.id
            # Denylisted / dead last track: do not retry on Space — advance or idle.
            if self.library.is_playback_denied(tid):
                self.player.clear()
            else:
                # Paused (or stopped mid-track) — resume.
                self.player.toggle()
                return
        if not self.session_queue:
            self._replenish_queue()
        if not self.session_queue:
            self._set_status("Context queue is empty — move the lens or replenish.")
            return
        self.queue_index = max(0, min(self.queue_index, len(self.session_queue) - 1))
        self.play_id(self.session_queue[self.queue_index])

    @Slot(int)
    def play_id(self, track_id: int, *, _depth: int = 0) -> None:
        if _depth > 48:
            self._set_status("No playable tracks left in the queue.")
            return
        track = self.library.get(track_id)
        if (
            track is None
            or not Path(track.path).exists()
            or self.library.is_playback_denied(track_id)
        ):
            if track is None:
                msg = "Track removed from library."
            elif self.library.is_playback_denied(track_id):
                msg = "Skipping unplayable track."
            else:
                msg = "File missing on disk."
            self._set_status(msg)
            next_id = self._skip_unplayable(track_id)
            if next_id is not None:
                playing = self.player.current.id if self.player.current else None
                # Dead next collapsed to the song already playing — keep it going.
                # Hard-cut play_id(same) would stop+restart from 0.
                if playing is not None and next_id == playing:
                    self._expect_natural_advance = False
                    self.player.release_advance_lock()
                    if playing in self.session_queue:
                        self.queue_index = self.session_queue.index(playing)
                    else:
                        self.session_queue.insert(0, playing)
                        self.queue_index = 0
                    self._fill_queue()
                    self._set_status("Only one playable track in range — waiting.")
                    return
                self.play_id(next_id, _depth=_depth + 1)
            else:
                playing = self.player.current.id if self.player.current else None
                self._expect_natural_advance = False
                if playing is not None:
                    self.session_queue = [playing]
                    self.queue_index = 0
                    self._fill_queue()
                    self.player.release_advance_lock()
                    self._set_status("Only one playable track in range — waiting.")
            return
        self._rebuild_lock = True
        outgoing = self.player.current
        outgoing_id = outgoing.id if outgoing is not None else None
        outgoing_pos = int(self.player.backend.position() or 0) if outgoing is not None else 0
        natural_advance = self._expect_natural_advance
        self._expect_natural_advance = False

        # Mid-fade: re-activate the outgoing song like Prev (no double play_count).
        if (
            self.player.is_crossfading()
            and self._crossfade_outgoing_id is not None
            and track_id == self._crossfade_outgoing_id
        ):
            settle_finish = self._outgoing_settle_finish
            self._clear_crossfade_credit()
            if settle_finish:
                self._listen_nudge(track_id, skipped=False)
            if track_id in self.session_queue:
                self.queue_index = self.session_queue.index(track_id)
            self._rebuild_lock = False
            self._play_restart(track_id)
            return

        # Same track: restart audio without bumping play_count.
        if outgoing_id is not None and outgoing_id == track_id:
            # Mid natural fade: still finish-nudge the song that was ending.
            if (
                self.player.is_crossfading()
                and self._outgoing_settle_finish
                and self._crossfade_outgoing_id is not None
            ):
                self._listen_nudge(self._crossfade_outgoing_id, skipped=False)
            # Deferred fade-in play must land before we clear credit and restart.
            if (
                self.player.is_crossfading()
                and self._pending_play_credit is not None
                and self._pending_play_credit == track_id
            ):
                self._commit_play(track_id)
            self._clear_crossfade_credit()
            self.player.stop()
            self.player.play_track(track)
            self.transport.set_track(
                track.short_title, f"{track.artist}  ·  {track.album or 'Single'}", track.loved
            )
            self._rebuild_lock = False
            self._flush_pending_plan_refresh()
            if self.plan:
                self.map.set_tracks(self.plan.ranked, track_id)
            self._fill_queue()
            return

        # Interrupting an in-flight fade: settle credit for prior outgoing + pending.
        if self.player.is_crossfading() and self._crossfade_outgoing_id is not None:
            prior = self._crossfade_outgoing_id
            settle_finish = self._outgoing_settle_finish
            pending = self._pending_play_credit
            self._clear_crossfade_credit()
            if prior != track_id and settle_finish:
                self._listen_nudge(prior, skipped=False)
            # Armed incoming abandoned early: skip/finish by position — not a play.
            if (
                pending is not None
                and pending != track_id
                and self.player.current is not None
                and pending == self.player.current.id
            ):
                pos = int(self.player.backend.position() or 0)
                self._credit_listen(pending, position_ms=pos)
                # play_id's later abandon path must not credit this id again.
                self._abandon_credited_id = pending

        self._pending_play_credit = None
        self._last_hard_play_credit = None
        already_credited = self._abandon_credited_id
        self._abandon_credited_id = None
        self.player.play_track(track)
        if self.player.is_crossfading() and outgoing_id is not None and outgoing_id != track_id:
            self._pending_play_credit = track_id
            self._crossfade_outgoing_id = outgoing_id
            if natural_advance:
                # End-of-track auto-advance: finish-nudge outgoing when the fade lands.
                self._outgoing_settle_finish = True
            else:
                # Map / matrix / search jump: credit abandon now (same 8s rule as Next).
                self._outgoing_settle_finish = False
                if already_credited != outgoing_id:
                    self._credit_listen(outgoing_id, position_ms=outgoing_pos)
        else:
            self._clear_crossfade_credit()
            # Hard-cut jump (paused/stopped): credit the abandoned track once.
            if (
                outgoing_id is not None
                and outgoing_id != track_id
                and already_credited != outgoing_id
            ):
                self._credit_listen(outgoing_id, position_ms=outgoing_pos)
            self._commit_play(track_id)
            self._last_hard_play_credit = track_id
        self.transport.set_track(track.short_title, f"{track.artist}  ·  {track.album or 'Single'}", track.loved)
        self._rebuild_lock = False
        self._flush_pending_plan_refresh()
        if self.plan:
            self.map.set_tracks(self.plan.ranked, track_id)
        self._fill_queue()

    def _clear_crossfade_credit(self) -> None:
        self._crossfade_outgoing_id = None
        self._outgoing_settle_finish = False
        self._pending_play_credit = None

    def _credit_listen(self, track_id: int, *, position_ms: int) -> None:
        """Skip vs finish from how far the listener got (matches Next’s 8s rule)."""
        skipped = position_ms < 8000
        if skipped:
            self.library.record_skip(track_id)
            self.skips_window.append(time())
        self._listen_nudge(track_id, skipped=skipped)

    def _commit_play(self, track_id: int) -> None:
        self.library.record_play(track_id, time())
        self.played_history.append(track_id)
        self.played_history = self.played_history[-48:]

    def _play_restart(self, track_id: int) -> None:
        """Hard-restart a track without bumping play_count (Prev during crossfade)."""
        track = self.library.get(track_id)
        if track is None or not Path(track.path).exists():
            return
        self._rebuild_lock = True
        self._clear_crossfade_credit()
        self._expect_natural_advance = False
        # Stop so play_track hard-cuts instead of fading and re-arming credit.
        self.player.stop()
        self.player.play_track(track)
        self.transport.set_track(track.short_title, f"{track.artist}  ·  {track.album or 'Single'}", track.loved)
        self._rebuild_lock = False
        self._flush_pending_plan_refresh()
        if self.plan:
            self.map.set_tracks(self.plan.ranked, track_id)
        self._fill_queue()

    def _playback_error(self, message: str) -> None:
        """Corrupt/unsupported media: show status and skip like a missing file."""
        self._set_status(message)
        if self._handling_playback_error or self._closing:
            return
        current = self.player.current
        if current is None:
            return
        self._handling_playback_error = True
        try:
            tid = current.id
            self.library.mark_playback_failed(tid)
            # Drop deferred fade credit, or undo a hard-cut play_count bump.
            if self._pending_play_credit == tid:
                self._pending_play_credit = None
            elif self._last_hard_play_credit == tid:
                self.library.unrecord_play(tid)
                if self.played_history and self.played_history[-1] == tid:
                    self.played_history.pop()
                self._last_hard_play_credit = None
            # If we were fading into this bad file, finish-nudge the outgoing track.
            if self.player.is_crossfading() and self._crossfade_outgoing_id is not None:
                if self._outgoing_settle_finish:
                    self._listen_nudge(self._crossfade_outgoing_id, skipped=False)
                self._clear_crossfade_credit()
            self.player.stop()
            self.player.release_advance_lock()
            self._expect_natural_advance = False
            next_id = self._skip_unplayable(tid)
            if next_id is not None:
                self.play_id(next_id)
            else:
                self.player.clear()
                self._set_status(f"{message} — no playable tracks left.")
        finally:
            self._handling_playback_error = False

    def _skip_unplayable(self, track_id: int) -> int | None:
        """Drop a missing/deleted id from the queue and return the next candidate."""
        self.played_history.append(track_id)
        self.played_history = self.played_history[-48:]
        self.ephemeral.discard(track_id)
        if track_id in self.session_queue:
            idx = self.session_queue.index(track_id)
            self.session_queue.pop(idx)
            self.queue_index = idx
        else:
            self.queue_index += 1
        if self.queue_index >= len(self.session_queue) or not self.session_queue:
            self._replenish_queue(avoid_id=track_id)
            self.queue_index = 0
        if not self.session_queue:
            self.player.release_advance_lock()
            return None
        self.queue_index = min(self.queue_index, len(self.session_queue) - 1)
        self._fill_queue()
        return self.session_queue[self.queue_index]

    def play_next(self) -> None:
        # During crossfade, current is already the incoming track at ~0ms.
        if self.player.is_crossfading() and self._crossfade_outgoing_id is not None:
            outgoing = self._crossfade_outgoing_id
            settle_finish = self._outgoing_settle_finish
            incoming_id = self.player.current.id if self.player.current else None
            incoming_pos = int(self.player.backend.position() or 0) if self.player.current else 0
            self._clear_crossfade_credit()
            if settle_finish:
                # Natural A→B: A essentially finished — never skip-credit it.
                self._listen_nudge(outgoing, skipped=False)
            # Suppress play_id abandon credit until we know we actually moved.
            if incoming_id is not None:
                self._abandon_credited_id = incoming_id
            moved = self._advance_queue(skipped=True)
            if moved and incoming_id is not None:
                skipped = incoming_pos < 8000
                if skipped:
                    self.library.record_skip(incoming_id)
                    self.skips_window.append(time())
                self._listen_nudge(incoming_id, skipped=skipped)
                self._abandon_credited_id = incoming_id
            else:
                self._abandon_credited_id = None
            return
        # Credit only after a successful move so sole-track Next cannot skip-credit.
        abandoned_id = self.player.current.id if self.player.current else None
        abandoned_pos = int(self.player.backend.position() or 0) if self.player.current else 0
        self._clear_crossfade_credit()
        if abandoned_id is not None:
            self._abandon_credited_id = abandoned_id
        moved = self._advance_queue(skipped=True)
        if moved and abandoned_id is not None:
            skipped = abandoned_pos < 8000
            if skipped:
                self.library.record_skip(abandoned_id)
                self.skips_window.append(time())
            self._listen_nudge(abandoned_id, skipped=skipped)
            self._abandon_credited_id = abandoned_id
        else:
            self._abandon_credited_id = None

    def play_prev(self) -> None:
        # During crossfade, restore the outgoing song without double play_count or
        # finish-nudging the barely-heard incoming track.
        if self.player.is_crossfading() and self._crossfade_outgoing_id is not None:
            outgoing = self._crossfade_outgoing_id
            settle_finish = self._outgoing_settle_finish
            self._clear_crossfade_credit()
            if settle_finish:
                self._listen_nudge(outgoing, skipped=False)
            if outgoing in self.session_queue:
                self.queue_index = self.session_queue.index(outgoing)
            self._play_restart(outgoing)
            return
        self._clear_crossfade_credit()
        self._expect_natural_advance = False
        if not self.session_queue:
            self._replenish_queue()
        if not self.session_queue:
            return
        # Drop one-shot matrix pulls when leaving via Prev (same as Next/advance).
        current_id = self.player.current.id if self.player.current else None
        if current_id is not None and current_id in self.ephemeral:
            self.ephemeral.discard(current_id)
            if current_id in self.session_queue:
                idx = self.session_queue.index(current_id)
                self.session_queue.pop(idx)
                if not self.session_queue:
                    self._replenish_queue(avoid_id=current_id)
                    if not self.session_queue:
                        return
                    self.queue_index = 0
                    self.play_id(self.session_queue[0])
                    return
                # Land on the previous track — do not subtract again below.
                self.queue_index = max(0, idx - 1)
                self.play_id(self.session_queue[self.queue_index])
                return
        self.queue_index = max(0, self.queue_index - 1)
        self.play_id(self.session_queue[self.queue_index])

    def _nearly_finished(self) -> None:
        if self._closing:
            return
        # Arm crossfade / advance now; finish-credit the outgoing when the fade settles.
        self._expect_natural_advance = True
        self._advance_queue(skipped=False)

    def _crossfade_settled(self, natural: bool) -> None:
        if self._closing:
            return
        # Natural fade: finish-nudge outgoing when armed.
        # Pause/seek abort keeps the incoming track — still a play, not a skip.
        _ = natural
        if self._outgoing_settle_finish and self._crossfade_outgoing_id is not None:
            self._listen_nudge(self._crossfade_outgoing_id, skipped=False)
        self._crossfade_outgoing_id = None
        self._outgoing_settle_finish = False
        credit = self._pending_play_credit
        self._pending_play_credit = None
        if (
            credit is not None
            and self.player.current is not None
            and credit == self.player.current.id
        ):
            self._commit_play(credit)

    def _track_finished_hard(self) -> None:
        if self._closing:
            return
        # Short tracks (no nearly-finished arm): nudge + advance together.
        self._clear_crossfade_credit()
        self._expect_natural_advance = False
        if self.player.current:
            self._listen_nudge(self.player.current.id, skipped=False)
        self._advance_queue(skipped=False)

    def _listen_nudge(self, track_id: int, *, skipped: bool) -> None:
        """Local-only: gently move unpinned stars from skip/finish under the lens."""
        if self._closing:
            return
        lx, ly, _ = self.map.lens_mood()
        moved = self.library.nudge_mood_from_listen(
            track_id, lens_x=lx, lens_y=ly, skipped=skipped
        )
        if moved:
            self.refresh_plan(keep_current=True, rebuild_queue=False)

    def _advance_queue(self, skipped: bool = False) -> bool:
        """Advance to the next queue track. Returns True if a different track started."""
        current_id = self.player.current.id if self.player.current else None
        if current_id is not None and current_id in self.ephemeral:
            self.ephemeral.discard(current_id)
            if current_id in self.session_queue:
                idx = self.session_queue.index(current_id)
                self.session_queue.pop(idx)
                self.queue_index = idx
            else:
                self.queue_index += 1
        else:
            self.queue_index += 1
        if self.queue_index >= len(self.session_queue) or not self.session_queue:
            self._replenish_queue(avoid_id=current_id)
            self.queue_index = 0
        if not self.session_queue:
            # Keep the still-playing track in the queue — do not strand empty.
            if current_id is not None:
                self.session_queue = [current_id]
                self.queue_index = 0
                self._fill_queue()
                self._expect_natural_advance = False
                self.player.release_advance_lock()
                self._set_status("Only one playable track in range — waiting.")
                return False
            self._expect_natural_advance = False
            self.player.release_advance_lock()
            self._set_status("Context queue is empty — move the lens or add more music.")
            return False
        self.queue_index = min(max(0, self.queue_index), len(self.session_queue) - 1)
        next_id = self.session_queue[self.queue_index]
        # Tiny libraries / dead-next collapse: never hard-cut restart the current track.
        if current_id is not None and next_id == current_id:
            self._expect_natural_advance = False
            self.player.release_advance_lock()
            self._set_status("Only one playable track in range — waiting.")
            return False
        self.play_id(next_id)
        after = self.player.current.id if self.player.current else None
        return after is not None and after != current_id

    def _replenish_queue(self, *, avoid_id: int | None = None) -> None:
        """Build a fresh context queue from lens, time of day, and matrix lists."""
        ctx = self.current_context()
        self._show_band(ctx)
        # Skip ghost rows whose files are gone so the queue cannot refill with them.
        tracks = self._playable_tracks()
        current_id = self.player.current.id if self.player.current else None
        avoid = avoid_id if avoid_id is not None else current_id
        exclude = set(self.played_history[-24:])
        hard = {avoid} if avoid is not None else set()
        self.plan = build_plan(
            tracks, ctx, self.explicit, exclude_ids=exclude, hard_exclude_ids=hard
        )
        self.map.set_tracks(self.plan.ranked, current_id)
        self.matrix.set_plan(self.plan.by_quadrant)
        order = list(self.plan.order)
        if avoid is not None:
            filtered = [tid for tid in order if tid != avoid]
            if filtered:
                order = filtered
            elif len(tracks) > 1:
                order = [t.id for t in tracks if t.id != avoid][:18]
            else:
                # Sole playable track: keep it queued. Callers must not hard-cut restart.
                order = [avoid]
        self.ephemeral = {tid for tid in self.ephemeral if tid in order}
        self.session_queue = order
        self._fill_queue()
        self._set_status(
            f"Context queue replenished · {ctx.band_label} · {len(self.session_queue)} tracks from the matrix"
        )

    def _love(self) -> None:
        if not self.player.current:
            return
        loved = self.library.toggle_loved(self.player.current.id)
        track = self.library.get(self.player.current.id)
        if track:
            self.transport.set_track(track.short_title, f"{track.artist}  ·  {track.album or 'Single'}", loved)
        self.refresh_plan(rebuild_queue=False)

    def _pos(self, pos: int) -> None:
        if self._closing:
            return
        # Once hard-cut media actually advances, keep the play credit on later errors.
        if (
            self._last_hard_play_credit is not None
            and pos >= 500
            and self.player.current is not None
            and self.player.current.id == self._last_hard_play_credit
        ):
            self._last_hard_play_credit = None
        self.transport.set_progress(pos, self._duration)

    def _dur(self, dur: int) -> None:
        if self._closing:
            return
        self._duration = dur
        self.transport.set_progress(self.player.backend.position(), dur)

    def _shutdown_player(self) -> None:
        """Stop playback and disconnect player→UI so quit cannot touch a closed DB."""
        for signal, slot in (
            (self.player.position_changed, self._pos),
            (self.player.duration_changed, self._dur),
            (self.player.state_changed, self.transport.set_playing),
            (self.player.track_nearly_finished, self._nearly_finished),
            (self.player.track_finished, self._track_finished_hard),
            (self.player.crossfade_settled, self._crossfade_settled),
            (self.player.error_occurred, self._playback_error),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        try:
            self.player.stop()
        except Exception:
            pass

    def closeEvent(self, event) -> None:
        self._closing = True
        self._pending_analyze_after_linger = False
        self._pending_plan_refresh = False
        self._clear_crossfade_credit()
        self._expect_natural_advance = False
        self._pending_play_credit = None
        self._lens_timer.stop()
        try:
            self.map._snap_timer.stop()
            self.map._lod_timer.stop()
        except Exception:
            pass
        self._shutdown_player()
        # Drop GL viewport before widget teardown (driver abort on some hosts).
        try:
            self.map.release_gpu_viewport()
        except Exception:
            pass
        # Abort kills ffmpeg; disconnect UI slots before wait so a timeout cannot UAF.
        analyze_done = self._stop_analyze(wait_ms=20000)
        scan_done = self._cleanup_scan_thread(wait_ms=10000)
        # Never close SQLite under a live worker (timed-out / lingering path).
        if analyze_done and scan_done and not self._lingering_workers:
            self.library.close()
        else:
            self._close_library_when_idle = True
            # Keep the process alive until linger reaps finish, then quit.
            self._quit_when_idle = True
            app = QApplication.instance()
            if app is not None:
                app.setQuitOnLastWindowClosed(False)
        super().closeEvent(event)
