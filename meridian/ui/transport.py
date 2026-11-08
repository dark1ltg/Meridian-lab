from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal, Slot
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from meridian.features import taste_label

from meridian.ui.fonts import sans


def fmt_ms(ms: int) -> str:
    secs = max(0, ms // 1000)
    return f"{secs // 60}:{secs % 60:02d}"


class VolumeSlider(QSlider):
    """Horizontal volume bar with the current percent drawn in the groove."""

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setObjectName("volumeSlider")
        self.setRange(0, 100)
        self.setSingleStep(5)
        self.setPageStep(5)
        self.setTickInterval(5)
        self.setFixedSize(148, 26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self.rect().adjusted(1, 5, -1, -5)
        path = QPainterPath()
        path.addRoundedRect(QRectF(track), 8, 8)
        painter.fillPath(path, QColor("#243049"))
        frac = max(0.0, min(1.0, self.value() / 100.0))
        if frac > 0:
            painter.save()
            painter.setClipPath(path)
            fill = QRectF(track)
            fill.setWidth(track.width() * frac)
            painter.fillRect(fill, QColor("#e8b86d"))
            painter.restore()
        painter.setPen(QColor("#0b1020") if frac >= 0.45 else QColor("#e8edf7"))
        painter.setFont(sans(12))
        painter.drawText(track, int(Qt.AlignmentFlag.AlignCenter), f"{self.value()}%")


class SeekSlider(QSlider):
    """Click or drag anywhere on the bar to jump to that point in the track.

    Quiet ticks mark the stretches the short listen actually used. A click on
    a tick jumps to that stretch.
    """

    taste_activated = Signal(int, str)

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setObjectName("seekSlider")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setTracking(True)
        self.setSingleStep(1000)
        self.setPageStep(5000)
        self.setMouseTracking(True)
        self._tastes: list[tuple[str, int]] = []
        self._taste_press: tuple[int, str] | None = None

    def set_tastes(self, marks: list[tuple[str, int]]) -> None:
        self._tastes = [(str(role), int(ms)) for role, ms in marks if int(ms) >= 0]
        self._taste_press = None
        self.update()

    def _value_at(self, x: float) -> int:
        span = max(1, self.width())
        ratio = max(0.0, min(1.0, x / span))
        return int(round(self.minimum() + ratio * (self.maximum() - self.minimum())))

    def _x_for(self, ms: int) -> float:
        span = max(1, self.maximum() - self.minimum())
        ratio = (int(ms) - self.minimum()) / span
        return max(0.0, min(float(self.width()), ratio * float(self.width())))

    def _tick_at(self, x: float) -> tuple[int, str] | None:
        if not self.isEnabled() or self.maximum() <= self.minimum() or not self._tastes:
            return None
        best: tuple[int, str] | None = None
        best_dist = 7.0
        for role, ms in self._tastes:
            if ms > self.maximum():
                continue
            dist = abs(float(x) - self._x_for(ms))
            if dist <= best_dist:
                best = (ms, role)
                best_dist = dist
        return best

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self.isEnabled() or self.maximum() <= self.minimum() or not self._tastes:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(214, 206, 220, 150))
        pen.setWidthF(1.4)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        mid = self.height() / 2.0
        for _role, ms in self._tastes:
            if ms > self.maximum():
                continue
            x = self._x_for(ms)
            painter.drawLine(int(round(x)), int(mid - 6), int(round(x)), int(mid + 6))
        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            tick = self._tick_at(event.position().x())
            if tick is not None:
                self._taste_press = tick
                self.setSliderDown(True)
                self.setValue(tick[0])
                event.accept()
                return
            self._taste_press = None
            self.setSliderDown(True)
            self.setValue(self._value_at(event.position().x()))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._taste_press is not None:
            event.accept()
            return
        if self.isSliderDown():
            self.setValue(self._value_at(event.position().x()))
            event.accept()
            return
        tick = self._tick_at(event.position().x())
        if tick is not None:
            QToolTip.showText(event.globalPosition().toPoint(), taste_label(tick[1]), self)
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._taste_press is not None:
            ms, role = self._taste_press
            self._taste_press = None
            self.setValue(ms)
            self.blockSignals(True)
            self.setSliderDown(False)
            self.blockSignals(False)
            self.taste_activated.emit(ms, taste_label(role))
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self.setValue(self._value_at(event.position().x()))
            self.setSliderDown(False)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class TransportBar(QWidget):
    play_toggled = Signal()
    previous = Signal()
    next = Signal()
    seeked = Signal(int)
    taste_jumped = Signal(int, str)
    volume_changed = Signal(float)
    love_toggled = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("transport")
        self._seeking = False
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 10, 16, 12)
        root.setSpacing(14)

        self.title = QLabel("Nothing in the well")
        self.title.setObjectName("nowTitle")
        self.meta = QLabel("Import a folder to seed the map")
        self.meta.setObjectName("nowMeta")

        info = QVBoxLayout()
        info.addWidget(self.title)
        info.addWidget(self.meta)

        self.prev_btn = QPushButton("◀")
        self.play_btn = QPushButton("▶")
        self.next_btn = QPushButton("▶▶")
        self.love_btn = QPushButton("♡")
        for btn, name in (
            (self.prev_btn, "ghostBtn"),
            (self.play_btn, "playBtn"),
            (self.next_btn, "ghostBtn"),
            (self.love_btn, "ghostBtn"),
        ):
            btn.setObjectName(name)
            btn.setFixedSize(42, 42)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)

        self.prev_btn.clicked.connect(self.previous)
        self.play_btn.clicked.connect(self.play_toggled)
        self.next_btn.clicked.connect(self.next)
        self.love_btn.clicked.connect(self.love_toggled)

        self.volume = VolumeSlider()
        self.volume.setValue(85)
        self.volume.valueChanged.connect(self._on_volume)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        controls.addLayout(info, 1)
        controls.addWidget(self.prev_btn)
        controls.addWidget(self.play_btn)
        controls.addWidget(self.next_btn)
        controls.addWidget(self.love_btn)
        controls.addSpacing(32)
        vol = QLabel("VOL")
        vol.setObjectName("volLabel")
        controls.addWidget(vol, 0, Qt.AlignmentFlag.AlignVCenter)
        controls.addWidget(self.volume, 0, Qt.AlignmentFlag.AlignVCenter)

        self.seek = SeekSlider()
        self.seek.setRange(0, 1000)
        self.seek.setEnabled(False)
        self.elapsed = QLabel("0:00")
        self.remain = QLabel("0:00")
        self.elapsed.setObjectName("timeLabel")
        self.remain.setObjectName("timeLabel")
        self.seek.sliderPressed.connect(self._press)
        self.seek.sliderMoved.connect(self._scrub)
        self.seek.valueChanged.connect(self._preview_time)
        self.seek.sliderReleased.connect(self._release)
        self.seek.taste_activated.connect(self._taste)

        seek_row = QHBoxLayout()
        seek_row.addWidget(self.elapsed)
        seek_row.addWidget(self.seek, 1)
        seek_row.addWidget(self.remain)

        root.addLayout(controls)
        root.addLayout(seek_row)

    def _on_volume(self, value: int) -> None:
        snapped = max(0, min(100, int(round(value / 5) * 5)))
        if self.volume.value() != snapped:
            self.volume.blockSignals(True)
            self.volume.setValue(snapped)
            self.volume.blockSignals(False)
        self.volume_changed.emit(snapped / 100.0)

    def _press(self) -> None:
        self._seeking = True

    def _scrub(self, value: int) -> None:
        self._preview_time(value)
        self.seeked.emit(value)

    def _preview_time(self, value: int) -> None:
        if self._seeking or self.seek.isSliderDown():
            self.elapsed.setText(fmt_ms(value))
            self.remain.setText(fmt_ms(max(0, self.seek.maximum() - value)))

    def _release(self) -> None:
        self._seeking = False
        self.seeked.emit(self.seek.value())

    def _taste(self, ms: int, label: str) -> None:
        self._seeking = False
        self.elapsed.setText(fmt_ms(ms))
        self.remain.setText(fmt_ms(max(0, self.seek.maximum() - ms)))
        self.seeked.emit(ms)
        self.taste_jumped.emit(ms, label)

    def set_tastes(self, marks: list[tuple[str, int]]) -> None:
        self.seek.set_tastes(marks)

    @Slot(int, int)
    def set_progress(self, position: int, duration: int) -> None:
        duration = max(int(duration), 0)
        if duration <= 0:
            self.seek.setEnabled(False)
            self.elapsed.setText("0:00")
            self.remain.setText("0:00")
            return
        self.seek.setEnabled(True)
        if not self._seeking and not self.seek.isSliderDown():
            self.seek.blockSignals(True)
            if self.seek.maximum() != duration:
                self.seek.setRange(0, duration)
            self.seek.setValue(max(0, min(position, duration)))
            self.seek.blockSignals(False)
            self.elapsed.setText(fmt_ms(position))
            self.remain.setText(fmt_ms(max(0, duration - position)))
        else:
            # While scrubbing, remain follows the slider — not the live playhead.
            scrub = int(self.seek.value())
            self.remain.setText(fmt_ms(max(0, duration - scrub)))

    def set_playing(self, playing: bool) -> None:
        self.play_btn.setText("❚❚" if playing else "▶")

    def set_track(self, title: str, meta: str, loved: bool) -> None:
        self.title.setText(title)
        self.meta.setText(meta)
        self.love_btn.setText("♥" if loved else "♡")
