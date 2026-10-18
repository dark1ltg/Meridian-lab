from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEasingCurve, QObject, QUrl, QVariantAnimation, Signal, Slot
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from meridian.library import Track

# Always-on overlap between outgoing and incoming tracks (skips, queue, jumps).
CROSSFADE_MS = 3000


@dataclass
class _Deck:
    player: QMediaPlayer
    output: QAudioOutput


class Player(QObject):
    position_changed = Signal(int)
    duration_changed = Signal(int)
    state_changed = Signal(bool)
    track_finished = Signal()
    track_nearly_finished = Signal()
    # True = natural fade completed; False = interrupted (skip/pause/seek/jump).
    crossfade_settled = Signal(bool)
    error_occurred = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._master = 0.85
        self._active = 0
        self._decks = (self._make_deck(), self._make_deck())
        self._wire_deck(0)
        self._wire_deck(1)
        self.current: Track | None = None
        self._crossfading = False
        self._advance_emitted = False
        self._seek_hold_advance = False
        self._incoming_ended = False
        self._outgoing: _Deck | None = None
        self._xfade = QVariantAnimation(self)
        self._xfade.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._xfade.valueChanged.connect(self._on_xfade_progress)
        self._xfade.finished.connect(self._on_xfade_finished)

    def _make_deck(self) -> _Deck:
        output = QAudioOutput(self)
        output.setVolume(self._master)
        player = QMediaPlayer(self)
        player.setAudioOutput(output)
        return _Deck(player=player, output=output)

    def _wire_deck(self, index: int) -> None:
        deck = self._decks[index]
        deck.player.positionChanged.connect(lambda v, i=index: self._on_position(i, v))
        deck.player.durationChanged.connect(lambda v, i=index: self._on_duration(i, v))
        deck.player.playbackStateChanged.connect(lambda s, i=index: self._on_state(i, s))
        deck.player.mediaStatusChanged.connect(lambda s, i=index: self._on_status(i, s))
        deck.player.errorOccurred.connect(lambda *_a, i=index: self._on_error(i))

    @property
    def backend(self) -> QMediaPlayer:
        return self._decks[self._active].player

    @property
    def output(self) -> QAudioOutput:
        return self._decks[self._active].output

    def _on_position(self, index: int, value: int) -> None:
        if index != self._active:
            return
        self.position_changed.emit(int(value))
        if self._crossfading or self._advance_emitted:
            return
        duration = int(self.backend.duration() or 0)
        # Short tracks use EndOfMedia; avoid trusting provisional VBR durations.
        if duration < 12000:
            return
        # Never arm a crossfade in the first 10s — early wrong durations often look "done".
        if int(value) < 10000:
            return
        fade = self._fade_ms(duration)
        remaining = duration - int(value)
        if self._seek_hold_advance:
            # User scrubbed into the fade window — wait until they leave it.
            if remaining > fade:
                self._seek_hold_advance = False
            else:
                return
        if 0 <= remaining <= fade:
            self._advance_emitted = True
            self.track_nearly_finished.emit()

    def _on_duration(self, index: int, value: int) -> None:
        if index == self._active:
            self.duration_changed.emit(int(value))

    def _on_state(self, index: int, state: QMediaPlayer.PlaybackState) -> None:
        if index == self._active:
            self.state_changed.emit(state == QMediaPlayer.PlaybackState.PlayingState)

    def _on_status(self, index: int, status: QMediaPlayer.MediaStatus) -> None:
        if status != QMediaPlayer.MediaStatus.EndOfMedia:
            return
        if index != self._active:
            return
        if self._crossfading:
            # Short incoming can end while the outgoing fade is still running.
            # Ignore spurious EndOfMedia at the very start of a longer incoming track.
            if self._incoming_end_is_real():
                self._incoming_ended = True
            return
        if self._advance_emitted:
            return
        self.track_finished.emit()

    def _incoming_end_is_real(self) -> bool:
        """True when EndOfMedia during a fade means the incoming track actually finished."""
        pos = int(self.backend.position() or 0)
        dur = int(self.backend.duration() or 0)
        # Tiny tracks (≲ fade) legitimately end during the outgoing fade.
        if dur > 0 and dur <= CROSSFADE_MS + 1000:
            return True
        # Heard a meaningful amount — treat as a real end.
        if pos >= 500:
            return True
        return False

    def _on_error(self, index: int) -> None:
        # Only the active (incoming) deck matters. Outgoing errors during a fade must
        # not be treated as failure of the song that just started.
        if index != self._active:
            return
        err = self._decks[index].player.errorString() or "Playback failed"
        try:
            from meridian.host_deps import libx264_missing_status, should_warn_missing_libx264

            if should_warn_missing_libx264():
                err = f"{err} — {libx264_missing_status()}"
        except Exception:
            pass
        self.error_occurred.emit(err)

    @staticmethod
    def _fade_ms(duration_ms: int) -> int:
        if duration_ms <= 0:
            return CROSSFADE_MS
        return max(400, min(CROSSFADE_MS, duration_ms // 3))

    def play_track(self, track: Track) -> None:
        """Play track with always-on crossfade (queue, skips, matrix pulls, double-clicks)."""
        if self._crossfading:
            self._finish_crossfade(immediate=True)

        same = self.current is not None and self.current.id == track.id
        can_fade = (
            not same
            and self.current is not None
            and self.backend.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        )
        self.current = track
        self._advance_emitted = False
        self._seek_hold_advance = False
        self._incoming_ended = False
        if can_fade:
            self._start_crossfade(track)
        else:
            self._hard_cut(track)

    def _hard_cut(self, track: Track) -> None:
        other = self._decks[1 - self._active]
        other.player.stop()
        other.output.setVolume(0.0)
        active = self._decks[self._active]
        active.output.setVolume(self._master)
        # Stop first so same-URL setSource+play actually restarts (Qt Multimedia).
        active.player.stop()
        active.player.setSource(QUrl.fromLocalFile(track.path))
        active.player.play()

    def _start_crossfade(self, track: Track) -> None:
        incoming_index = 1 - self._active
        outgoing = self._decks[self._active]
        incoming = self._decks[incoming_index]
        self._outgoing = outgoing
        self._active = incoming_index
        self._crossfading = True
        self._incoming_ended = False

        duration = int(outgoing.player.duration() or 0)
        fade = self._fade_ms(duration) if duration > 0 else CROSSFADE_MS

        incoming.output.setVolume(0.0)
        incoming.player.setSource(QUrl.fromLocalFile(track.path))
        incoming.player.play()

        self._xfade.stop()
        self._xfade.setDuration(fade)
        self._xfade.setStartValue(0.0)
        self._xfade.setEndValue(1.0)
        self._xfade.start()

    def _on_xfade_progress(self, value: object) -> None:
        if not self._crossfading or self._outgoing is None:
            return
        t = float(value)
        self._outgoing.output.setVolume(self._master * (1.0 - t))
        self._decks[self._active].output.setVolume(self._master * t)

    def _on_xfade_finished(self) -> None:
        self._finish_crossfade(immediate=False)

    def _finish_crossfade(self, *, immediate: bool) -> None:
        if immediate:
            self._xfade.stop()
        outgoing = self._outgoing
        self._outgoing = None
        was_crossfading = self._crossfading
        self._crossfading = False
        incoming_ended = self._incoming_ended
        self._incoming_ended = False
        if outgoing is not None:
            outgoing.player.stop()
            outgoing.output.setVolume(0.0)
        self._decks[self._active].output.setVolume(self._master)
        self.position_changed.emit(int(self.backend.position() or 0))
        self.duration_changed.emit(int(self.backend.duration() or 0))
        self.state_changed.emit(self.is_playing())
        if not was_crossfading:
            return
        # Also catch EndOfMedia that arrived on the same tick as fade end.
        if (
            not immediate
            and not incoming_ended
            and self.backend.mediaStatus() == QMediaPlayer.MediaStatus.EndOfMedia
            and self._incoming_end_is_real()
        ):
            incoming_ended = True
        self.crossfade_settled.emit(not immediate)
        if not immediate and incoming_ended and not self._advance_emitted:
            self._advance_emitted = True
            self.track_finished.emit()

    def release_advance_lock(self) -> None:
        """Allow EndOfMedia / manual Next after a failed auto-advance.

        If we are still inside the end-fade window, hold re-arming
        ``track_nearly_finished`` until position leaves that window (same
        idea as seek-into-fade). Clearing ``_advance_emitted`` alone would
        thrash replenish/map rebuilds on every position tick.
        """
        self._advance_emitted = False
        duration = int(self.backend.duration() or 0)
        position = int(self.backend.position() or 0)
        if duration >= 12000:
            fade = self._fade_ms(duration)
            remaining = duration - position
            self._seek_hold_advance = 0 <= remaining <= fade
        else:
            self._seek_hold_advance = False

    @Slot()
    def toggle(self) -> None:
        if self._crossfading:
            self._finish_crossfade(immediate=True)
        if self.backend.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.backend.pause()
        else:
            self.backend.play()

    @Slot()
    def stop(self) -> None:
        if self._crossfading:
            self._finish_crossfade(immediate=True)
        for deck in self._decks:
            deck.player.stop()
            deck.output.setVolume(0.0)
        self._decks[self._active].output.setVolume(self._master)

    def clear(self) -> None:
        """Stop and drop current so Play cannot retry a dead track."""
        self.stop()
        self.current = None
        self._advance_emitted = False
        self._seek_hold_advance = False
        self._incoming_ended = False

    def seek(self, ms: int) -> None:
        if self._crossfading:
            self._finish_crossfade(immediate=True)
        duration = int(self.backend.duration() or 0)
        position = max(0, int(ms))
        if duration > 0:
            position = min(position, duration)
        self.backend.setPosition(position)
        self._advance_emitted = False
        # Scrubbing into the fade window must not immediately arm auto-advance.
        if duration >= 12000:
            fade = self._fade_ms(duration)
            remaining = duration - position
            self._seek_hold_advance = 0 <= remaining <= fade
        else:
            self._seek_hold_advance = False

    def set_volume(self, value: float) -> None:
        self._master = max(0.0, min(1.0, value))
        if self._crossfading and self._outgoing is not None:
            t = float(self._xfade.currentValue() or 0.0)
            self._outgoing.output.setVolume(self._master * (1.0 - t))
            self._decks[self._active].output.setVolume(self._master * t)
        else:
            self.output.setVolume(self._master)

    def is_crossfading(self) -> bool:
        return bool(self._crossfading)

    def is_playing(self) -> bool:
        return self.backend.playbackState() == QMediaPlayer.PlaybackState.PlayingState
