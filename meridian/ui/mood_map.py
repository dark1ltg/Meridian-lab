from __future__ import annotations

from math import hypot, log2

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QMouseEvent,
    QNativeGestureEvent,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QGestureEvent,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QPinchGesture,
)

from meridian.context import LENS_RADIUS_DEFAULT, LENS_RADIUS_MAX, LENS_RADIUS_MIN
from meridian.queue_engine import Quadrant, RankedTrack
from meridian.ui.fonts import sans
from meridian.features import CONFIDENCE_HIGH, CONFIDENCE_LOW
from meridian.ui.palette import (
    LOW_CONFIDENCE_OPACITY,
    LOW_TRUST_ALPHA,
    LOW_TRUST_QCOLOR,
    MID_CONFIDENCE_ALPHA,
    MID_CONFIDENCE_OPACITY,
    PIN_HEAD_RADIUS,
    PIN_RING_QCOLOR,
    PIN_RING_WIDTH,
    PLAYLIST_QCOLOR,
    STAR_RADIUS,
    STAR_Z,
)

# View magnification on top of fit-to-widget (1 = whole night sky).
VIEW_ZOOM_MIN = 1.0
VIEW_ZOOM_MAX = 14.0
VIEW_WHEEL_FACTOR = 1.06
VIEW_NATIVE_OUTLIER = 1.25
# Below this zoom, stars are one baked texture (smooth pinch). Above: live items in view.
LIVE_STARS_ZOOM = 2.4
LOD_LABEL_START = 2.6
LOD_GLOW_START = 2.4
LOD_CHROME_FADE_START = 1.4
LOD_CHROME_FADE_END = 4.0
CLUSTER_PULL = 0.42
CLUSTER_GRID = 48.0
LOD_DEFER_MS = 48
LABEL_CAP = 48
LIVE_STAR_CAP = 220          # max interactive stars while zoomed in
FIELD_SCALE = 2              # bake retina-ish starfield
SCENE_W = 800
SCENE_H = 600
HIT_RADIUS_SKY = 14.0
# Tighter than hover/snap — avoids turning a nearby pan into an accidental pin.
HIT_RADIUS_SKY_GRAB = 7.0
SKY_DRAG_SLOP = 8.0


class TrackStar(QGraphicsEllipseItem):
    def __init__(self, ranked: RankedTrack, parent=None) -> None:
        r = STAR_RADIUS[ranked.quadrant]
        super().__init__(-r, -r, r * 2, r * 2, parent)
        self.ranked = ranked
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setToolTip(ranked.track.label)
        self._map: MoodMap | None = None
        self._press_scene: QPointF | None = None

        self._glow = QGraphicsEllipseItem(self)
        self._glow.setPen(QPen(Qt.PenStyle.NoPen))
        self._glow.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._glow.setZValue(-1)
        self._glow.hide()

        # Lock ring + tack head — visible only when the track is pinned.
        self._pin_ring = QGraphicsEllipseItem(self)
        self._pin_ring.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self._pin_ring.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._pin_ring.setZValue(2)
        self._pin_ring.hide()
        self._pin_head = QGraphicsEllipseItem(self)
        self._pin_head.setPen(QPen(Qt.PenStyle.NoPen))
        self._pin_head.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._pin_head.setZValue(3)
        self._pin_head.hide()

        self._label = QGraphicsSimpleTextItem(self)
        self._label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        # Accept clicks so double-click on the title still reaches the star.
        self._label.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self._label.setZValue(30)
        self._label.setBrush(QBrush(QColor("#e8ecf7")))
        self._label.setFont(sans(10))
        self._label.hide()
        self._lod_key = (-1, False, False)

        self.apply_style(ranked, current=False)

    def shape(self) -> QPainterPath:
        # Live stars: hit close to the drawn body so dense clusters stay distinguishable.
        # (Sky-mode picking uses HIT_RADIUS_SKY on the baked field, not this shape.)
        path = QPainterPath()
        r = self.rect().width() * 0.5 + 4.0
        path.addEllipse(QPointF(0.0, 0.0), r, r)
        return path

    def apply_style(self, ranked: RankedTrack, current: bool) -> None:
        self.ranked = ranked
        r = STAR_RADIUS[ranked.quadrant]
        if ranked.track.loved:
            r += 0.8
        if ranked.track.pinned:
            r += 0.7
        self.setRect(-r, -r, r * 2, r * 2)
        conf = float(getattr(ranked.track, "mood_confidence", 0.5) or 0.5)
        # Pins are locked truth — always draw as high-trust body color.
        if ranked.track.pinned or current:
            band = "high"
        elif conf >= CONFIDENCE_HIGH:
            band = "high"
        elif conf >= CONFIDENCE_LOW:
            band = "mid"
        else:
            band = "low"

        if band == "low":
            color = QColor(LOW_TRUST_QCOLOR)
            color.setAlpha(LOW_TRUST_ALPHA)
            self.setOpacity(LOW_CONFIDENCE_OPACITY)
        elif band == "mid":
            color = QColor(PLAYLIST_QCOLOR[ranked.quadrant])
            color.setAlpha(MID_CONFIDENCE_ALPHA)
            self.setOpacity(MID_CONFIDENCE_OPACITY)
        else:
            color = QColor(PLAYLIST_QCOLOR[ranked.quadrant])
            self.setOpacity(1.0)
        self.setBrush(QBrush(color))

        pinned = bool(ranked.track.pinned)
        if current:
            pen = QPen(QColor("#ffffff"), 2.0)
            pen.setCosmetic(True)
            self.setPen(pen)
            self.setZValue(14)
        elif pinned:
            # Body uses a quiet edge; the lock ring carries the pin signal.
            pen = QPen(QColor(8, 12, 22, 160), 1.0)
            pen.setCosmetic(True)
            self.setPen(pen)
            self.setZValue(STAR_Z[ranked.quadrant] + 3)
        elif ranked.track.loved:
            pen = QPen(QColor("#fff4c2"), 1.4)
            pen.setCosmetic(True)
            self.setPen(pen)
            self.setZValue(STAR_Z[ranked.quadrant] + 1)
        elif band == "low":
            pen = QPen(QColor(20, 28, 42, 200), 1.0)
            pen.setCosmetic(True)
            self.setPen(pen)
            self.setZValue(max(1, STAR_Z[ranked.quadrant] - 2))
        else:
            pen = QPen(QColor(8, 12, 22, 180), 1.0)
            pen.setCosmetic(True)
            self.setPen(pen)
            self.setZValue(STAR_Z[ranked.quadrant])

        if pinned:
            ring_pad = 3.2 if current else 2.6
            self._pin_ring.setRect(-r - ring_pad, -r - ring_pad, (r + ring_pad) * 2, (r + ring_pad) * 2)
            ring_pen = QPen(PIN_RING_QCOLOR, PIN_RING_WIDTH)
            ring_pen.setCosmetic(True)
            self._pin_ring.setPen(ring_pen)
            self._pin_ring.show()
            hr = PIN_HEAD_RADIUS
            self._pin_head.setRect(-hr, -r - ring_pad - hr * 2.2, hr * 2, hr * 2)
            self._pin_head.setBrush(QBrush(PIN_RING_QCOLOR))
            self._pin_head.show()
        else:
            self._pin_ring.hide()
            self._pin_head.hide()

        tip = f"{ranked.track.label}\n{ranked.quadrant.value.upper()}"
        tip += f"\nconfidence {conf:.2f}"
        if pinned:
            tip += " — pinned (locked by you)"
        elif band == "low":
            tip += " — weak guess"
        elif band == "mid":
            tip += " — partial evidence"
        note = (getattr(ranked.track, "confidence_note", "") or "").strip()
        if note:
            tip += f"\n{note}"
        self.setToolTip(tip)

        glow_r = r * (3.8 if pinned else 3.4)
        self._glow.setRect(-glow_r, -glow_r, glow_r * 2, glow_r * 2)
        glow_c = QColor(color)
        if pinned:
            glow_c.setAlpha(110)
        else:
            glow_c.setAlpha(40 if band == "low" else (65 if band == "mid" else 90))
        self._glow.setBrush(QBrush(glow_c))

        title = ranked.track.label
        if " — " in title:
            title = title.split(" — ", 1)[-1]
        if len(title) > 28:
            title = title[:27] + "…"
        self._label.setText(title)
        br = self._label.boundingRect()
        self._label.setPos(-br.width() / 2, r + (8 if pinned else 4))
        if band == "low":
            self._label.setBrush(QBrush(QColor("#9AA8C0")))
        elif band == "mid":
            self._label.setBrush(QBrush(QColor("#c5cde0")))
        else:
            self._label.setBrush(QBrush(QColor("#ffffff" if current or pinned else "#dce3f4")))

    def set_lod(self, glow_a: float, label_on: bool) -> None:
        show_glow = glow_a > 0.02
        key = (int(glow_a * 20), show_glow, label_on)
        if key == self._lod_key:
            return
        self._lod_key = key
        if show_glow:
            self._glow.setOpacity(0.55 * glow_a)
            self._glow.show()
        else:
            self._glow.hide()
        if label_on:
            self._label.show()
        else:
            self._label.hide()

    def hoverEnterEvent(self, event) -> None:
        self.setScale(1.45)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self.setScale(1.0)
        super().hoverLeaveEvent(event)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange and self.scene():
            pos: QPointF = value
            pos.setX(max(40.0, min(760.0, pos.x())))
            pos.setY(max(36.0, min(560.0, pos.y())))
            return pos
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged
            and self._map is not None
            and self._press_scene is not None
        ):
            self._map._update_drag_position(self.ranked.track.id, self.pos())
        return super().itemChange(change, value)

    def mousePressEvent(self, event) -> None:
        self._press_scene = event.scenePos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if not self._map:
            return
        dragged = False
        if self._press_scene is not None:
            delta = event.scenePos() - self._press_scene
            dragged = hypot(delta.x(), delta.y()) > 8
        self._press_scene = None
        if dragged:
            self._map.star_moved(self)
        else:
            self._map.snap_lens_to_star(self)


class LensItem(QGraphicsEllipseItem):
    def __init__(self) -> None:
        super().__init__(-90, -90, 180, 180)
        self.setBrush(QBrush(QColor(109, 74, 255, 42)))
        ring = QPen(QColor("#6D4AFF"), 2.5)
        ring.setCosmetic(True)
        ring.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self.setPen(ring)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        # Below live stars (z≥7) so stars under the lens stay clickable / draggable.
        self.setZValue(6)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self._map: MoodMap | None = None
        halo = QPen(QColor("#F4F1FF"), 3.7)
        halo.setCosmetic(True)
        halo.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        self._halo = QGraphicsEllipseItem(-90, -90, 180, 180, self)
        self._halo.setPen(halo)
        self._halo.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self._halo.setZValue(-1)

    def setRect(self, *args) -> None:
        super().setRect(*args)
        self._halo.setRect(self.rect())

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            pos: QPointF = value
            # Keep the lens center on the map panel (ellipse may spill past edges).
            pos.setX(max(40.0, min(760.0, pos.x())))
            pos.setY(max(36.0, min(560.0, pos.y())))
            return pos
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and self._map:
            self._map.lens_moved()
        return super().itemChange(change, value)


class MoodMap(QGraphicsView):
    """Mood sky: baked starfield for smooth pinch; live stars only when zoomed in."""

    lens_changed = Signal(float, float, float)
    track_pinned = Signal(int, float, float)
    track_activated = Signal(int)
    track_hovered = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setBackgroundBrush(QBrush(QColor("#0e1424")))
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.grabGesture(Qt.GestureType.PinchGesture)
        self._gpu_enabled = self._enable_opengl_viewport()
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Highlight, QColor("#6D4AFF"))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#0b1020"))
        self.setPalette(pal)
        self._scene = QGraphicsScene(0, 0, SCENE_W, SCENE_H, self)
        self._scene.setPalette(pal)
        self.setScene(self._scene)
        self._ranked: dict[int, RankedTrack] = {}
        self._positions: dict[int, QPointF] = {}
        self._current_id: int | None = None
        self._stars: dict[int, TrackStar] = {}
        self._sky_chrome: list[QGraphicsItem] = []
        self._star_grid: dict[tuple[int, int], list[tuple[int, QPointF]]] = {}
        self._radius = LENS_RADIUS_DEFAULT
        self._radius_scale = 1.0  # mode bias; ring shows radius * scale
        self._user_zoom = VIEW_ZOOM_MIN
        self._zoom_target = VIEW_ZOOM_MIN
        self._zoom_anchor = QPoint()
        self._pinch_zoom0 = VIEW_ZOOM_MIN
        self._prefer_native_zoom = False
        self._native_zoom_clear = None
        self._panning = False
        self._hand_drag_armed = False  # ScrollHandDrag press still needs a matching release
        self._zoom_active = False
        self._lod_band = -1
        self._cluster_once = True
        # Keep one live star visible in sky mode while the user presses/drags it.
        self._sky_hold_id: int | None = None
        self._sky_press_scene: QPointF | None = None
        self._sky_dragged = False
        self._sky_candidate_id: int | None = None
        # Press on star core — wait for move before pan vs pin (avoids viewport nudge).
        self._sky_defer_pan = False
        self._field_sig: object | None = None
        self._pending_snap_id: int | None = None
        self._snap_timer = QTimer(self)
        self._snap_timer.setSingleShot(True)
        self._snap_timer.setInterval(220)
        self._snap_timer.timeout.connect(self._flush_pending_snap)
        self._lod_timer = QTimer(self)
        self._lod_timer.setSingleShot(True)
        self._lod_timer.setInterval(LOD_DEFER_MS)
        self._lod_timer.timeout.connect(self._flush_zoom_lod)
        self._draw_backdrop()
        self._field = QGraphicsPixmapItem()
        self._field.setZValue(5)
        self._field.setShapeMode(QGraphicsPixmapItem.ShapeMode.BoundingRectShape)
        self._field.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._scene.addItem(self._field)
        self.lens = LensItem()
        self._scene.addItem(self.lens)
        self.lens.setPos(420, 300)
        self.lens._map = self
        self._apply_lens_rect()

    def _enable_opengl_viewport(self) -> bool:
        import os

        # Opt-in only: QOpenGLWidget construction can native-abort on broken EGL.
        # MERIDIAN_NO_GL=1 (default unless MERIDIAN_GL is set) keeps a plain viewport.
        if os.environ.get("MERIDIAN_NO_GL", "").strip().lower() in {"1", "true", "yes"}:
            return False
        gl_mode = os.environ.get("MERIDIAN_GL", "").strip().lower()
        if gl_mode not in {"1", "true", "yes", "on", "gpu", "desktop", "force"}:
            return False
        try:
            from PySide6.QtGui import QSurfaceFormat
            from PySide6.QtOpenGLWidgets import QOpenGLWidget
        except ImportError:
            return False
        try:
            gl = QOpenGLWidget(self)
            fmt = QSurfaceFormat()
            fmt.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
            fmt.setSwapBehavior(QSurfaceFormat.SwapBehavior.DoubleBuffer)
            fmt.setSwapInterval(1)
            fmt.setSamples(4)
            gl.setFormat(fmt)
            self.setViewport(gl)
            self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
            self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontSavePainterState, True)
            self.setOptimizationFlag(
                QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing, True
            )
            return True
        except Exception:
            return False

    def release_gpu_viewport(self) -> None:
        """Swap out QOpenGLWidget before window teardown to avoid driver abort on quit."""
        if not getattr(self, "_gpu_enabled", False):
            return
        from PySide6.QtWidgets import QWidget

        try:
            plain = QWidget()
            self.setViewport(plain)
            self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
        except Exception:
            pass
        self._gpu_enabled = False

    def _draw_backdrop(self) -> None:
        for item in self._sky_chrome:
            self._scene.removeItem(item)
        self._sky_chrome.clear()
        rect = QRectF(40, 36, 720, 524)
        panel = self._scene.addRect(rect, QPen(QColor("#1f2a44"), 1.2), QBrush(QColor("#121a2e")))
        panel.setZValue(0)
        self._sky_chrome.append(panel)
        for i in range(1, 4):
            y = 36 + 524 * i / 4
            line = self._scene.addLine(40, y, 760, y, QPen(QColor(255, 255, 255, 18), 1))
            line.setZValue(1)
            self._sky_chrome.append(line)
            x = 40 + 720 * i / 4
            v = self._scene.addLine(x, 36, x, 560, QPen(QColor(255, 255, 255, 18), 1))
            v.setZValue(1)
            self._sky_chrome.append(v)
        font = sans(11)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
        for x, y, text in ((400, 22, "KINETIC  ↑"), (400, 582, "STILL  ↓")):
            item = self._scene.addSimpleText(text, font)
            item.setBrush(QBrush(QColor("#8b95ad")))
            item.setPos(x - item.boundingRect().width() / 2, y - 8)
            item.setZValue(2)
            self._sky_chrome.append(item)
        for x, y, text, angle in ((16, 298, "SHADOW", -90), (784, 298, "GLOW", 90)):
            item = self._scene.addSimpleText(text, font)
            item.setBrush(QBrush(QColor("#8b95ad")))
            br = item.boundingRect()
            item.setTransformOriginPoint(br.center())
            item.setRotation(angle)
            item.setPos(x - br.width() / 2, y - br.height() / 2)
            item.setZValue(2)
            self._sky_chrome.append(item)
        glow = QRadialGradient(400, 300, 280)
        glow.setColorAt(0.0, QColor(110, 200, 197, 28))
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        path = QPainterPath()
        path.addEllipse(QPointF(400, 300), 280, 220)
        gitem = self._scene.addPath(path, QPen(Qt.PenStyle.NoPen), QBrush(glow))
        gitem.setZValue(1)
        self._sky_chrome.append(gitem)
        legend_x = 52
        legend_y = 48
        font_l = sans(11, medium=True)
        font_l.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.6)
        for q in (Quadrant.NOW, Quadrant.DEEP, Quadrant.FILL, Quadrant.SHELF):
            dot = self._scene.addEllipse(
                legend_x, legend_y, 8, 8, QPen(Qt.PenStyle.NoPen), QBrush(PLAYLIST_QCOLOR[q])
            )
            dot.setZValue(4)
            self._sky_chrome.append(dot)
            label = self._scene.addSimpleText(q.value.upper(), font_l)
            label.setBrush(QBrush(PLAYLIST_QCOLOR[q]))
            label.setPos(legend_x + 12, legend_y - 3)
            label.setZValue(4)
            self._sky_chrome.append(label)
            legend_x += 78

    def mood_to_pos(self, valence: float, energy: float) -> QPointF:
        return QPointF(40 + valence * 720, 560 - energy * 524)

    def pos_to_mood(self, pos: QPointF) -> tuple[float, float]:
        valence = (pos.x() - 40) / 720
        energy = (560 - pos.y()) / 524
        return max(0.0, min(1.0, valence)), max(0.0, min(1.0, energy))

    def set_tracks(self, ranked: list[RankedTrack], current_id: int | None) -> None:
        self._current_id = current_id
        self._ranked = {r.track.id: r for r in ranked}
        # Keep in-progress pin drags so play/nudge/refresh cannot snap the star back.
        locked = self._drag_locked_ids()
        preserved: dict[int, QPointF] = {}
        for tid in locked:
            star = self._stars.get(tid)
            if star is not None:
                preserved[tid] = QPointF(star.pos())
            elif tid in self._positions:
                preserved[tid] = QPointF(self._positions[tid])
        # Drop live stars that vanished; keep others until LOD sync.
        for tid in list(self._stars):
            if tid not in self._ranked:
                self._scene.removeItem(self._stars.pop(tid))
                if self._sky_hold_id == tid:
                    self._sky_hold_id = None
        # Never keep drag positions for tracks that left the ranked set (KeyError guard).
        self._positions = {
            r.track.id: self.mood_to_pos(r.track.valence, r.track.energy) for r in ranked
        }
        for tid, pos in preserved.items():
            if tid in self._ranked:
                self._positions[tid] = pos
            elif self._sky_hold_id == tid:
                self._sky_hold_id = None
            elif self._sky_candidate_id == tid:
                self._sky_candidate_id = None
                self._sky_press_scene = None
                self._sky_defer_pan = False
        self._rebuild_star_grid()
        sig = self._field_signature()
        if sig != self._field_sig:
            self._field_sig = sig
            self._rebuild_starfield()
        self._lod_band = -1
        self._sync_live_stars(force=True)

    def _field_signature(self) -> object:
        """Cheap fingerprint so play/nudge/lens refresh can skip a full rebake."""
        parts: list[tuple] = []
        for tid in sorted(self._ranked):
            r = self._ranked[tid]
            p = self._positions[tid]
            conf = float(getattr(r.track, "mood_confidence", 0.5) or 0.5)
            parts.append(
                (
                    tid,
                    round(p.x(), 1),
                    round(p.y(), 1),
                    r.quadrant.value,
                    bool(r.track.loved),
                    bool(r.track.pinned),
                    round(conf, 2),
                )
            )
        return (self._current_id, tuple(parts))

    def _drag_locked_ids(self) -> set[int]:
        """Track ids currently being pressed/dragged — positions must not reset."""
        locked: set[int] = set()
        if self._sky_hold_id is not None:
            locked.add(self._sky_hold_id)
        if self._sky_candidate_id is not None:
            locked.add(self._sky_candidate_id)
        for tid, star in self._stars.items():
            if star._press_scene is not None:
                locked.add(tid)
        return locked

    def _rebuild_starfield(self) -> None:
        """Bake every track into one pixmap — pinch then only scales this texture."""
        w, h = SCENE_W * FIELD_SCALE, SCENE_H * FIELD_SCALE
        img = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.scale(FIELD_SCALE, FIELD_SCALE)
        painter.setPen(Qt.PenStyle.NoPen)
        n_ranked = len(self._ranked)
        # Large libraries: keep a readable minimum radius and brighten stacked cores
        # instead of shrinking stars into invisibility (was 0.55 above 2500).
        if n_ranked > 2500:
            base_scale = 0.72
            density_mode = True
        elif n_ranked > 1200:
            base_scale = 0.80
            density_mode = True
        else:
            base_scale = 0.85
            density_mode = False
        low_alpha = min(210, LOW_TRUST_ALPHA + 45) if density_mode else LOW_TRUST_ALPHA

        # Draw lower-confidence stars first so bright / high-trust read on top.
        def _conf_band(r: RankedTrack) -> int:
            if r.track.pinned:
                return 2
            c = float(getattr(r.track, "mood_confidence", 0.5) or 0.5)
            if c >= CONFIDENCE_HIGH:
                return 2
            if c >= CONFIDENCE_LOW:
                return 1
            return 0

        order = sorted(
            self._ranked.values(),
            key=lambda r: (
                _conf_band(r),
                STAR_Z[r.quadrant] + (2 if r.track.loved else 0),
            ),
        )
        # Cheap density underlay so stacked cohorts read as bright regions, not emptiness.
        if density_mode and n_ranked:
            cell = 10.0
            counts: dict[tuple[int, int], int] = {}
            for item in order:
                pos = self._positions[item.track.id]
                key = (int(pos.x() // cell), int(pos.y() // cell))
                counts[key] = counts.get(key, 0) + 1
            for (cx, cy), count in counts.items():
                if count < 3:
                    continue
                # log2 so a 500-stack glows without blowing out the whole map.
                alpha = min(100, 18 + int(22 * log2(count)))
                glow = QColor(160, 178, 220, alpha)
                painter.setBrush(QBrush(glow))
                radius = 5.0 + min(12.0, count * 0.08)
                painter.drawEllipse(
                    QPointF(cx * cell + cell * 0.5, cy * cell + cell * 0.5),
                    radius,
                    radius,
                )
        for item in order:
            pos = self._positions[item.track.id]
            r = STAR_RADIUS[item.quadrant] * base_scale
            if item.track.loved:
                r += 0.5
            if item.track.pinned:
                r += 0.45
            band = _conf_band(item)
            if item.track.id == self._current_id:
                color = QColor("#ffffff")
                r += 0.6
            elif band == 0:
                color = QColor(LOW_TRUST_QCOLOR)
                color.setAlpha(low_alpha)
                r *= 0.85
            elif band == 1:
                color = QColor(PLAYLIST_QCOLOR[item.quadrant])
                color.setAlpha(MID_CONFIDENCE_ALPHA)
                r *= 0.92
            else:
                color = QColor(PLAYLIST_QCOLOR[item.quadrant])
            # Floor radius so large-library stacks stay pickable at default zoom.
            r = max(r, 3.2 if density_mode else 2.6)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawEllipse(pos, r, r)
            # Lock ring + tack so pins read on the baked sky (not only live LOD).
            if item.track.pinned:
                ring = QPen(PIN_RING_QCOLOR, 1.35)
                painter.setPen(ring)
                painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
                painter.drawEllipse(pos, r + 2.4, r + 2.4)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(PIN_RING_QCOLOR))
                painter.drawEllipse(
                    QPointF(pos.x(), pos.y() - r - 3.2),
                    PIN_HEAD_RADIUS * 0.85,
                    PIN_HEAD_RADIUS * 0.85,
                )
        painter.end()
        pix = QPixmap.fromImage(img)
        self._field.setPixmap(pix)
        self._field.setScale(1.0 / FIELD_SCALE)
        self._field.setPos(0, 0)
        self._field.setVisible(True)
        self._field_sig = self._field_signature()

    def _rebuild_star_grid(self) -> None:
        grid: dict[tuple[int, int], list[tuple[int, QPointF]]] = {}
        cell = CLUSTER_GRID
        for tid, p in self._positions.items():
            key = (int(p.x() // cell), int(p.y() // cell))
            grid.setdefault(key, []).append((tid, p))
        self._star_grid = grid

    def _nearest_track_id(self, scene_pos: QPointF, max_dist: float) -> int | None:
        cell = CLUSTER_GRID
        cx0 = int(scene_pos.x() // cell)
        cy0 = int(scene_pos.y() // cell)
        span = max(1, int(max_dist // cell) + 1)
        best_id: int | None = None
        best_d = max_dist
        for gx in range(cx0 - span, cx0 + span + 1):
            for gy in range(cy0 - span, cy0 + span + 1):
                for tid, p in self._star_grid.get((gx, gy), ()):
                    d = hypot(p.x() - scene_pos.x(), p.y() - scene_pos.y())
                    if d < best_d:
                        best_d = d
                        best_id = tid
        return best_id

    def lens_mood(self) -> tuple[float, float, float]:
        vx, ey = self.pos_to_mood(self.lens.pos())
        return vx, ey, self._radius

    def set_radius_scale(self, scale: float) -> None:
        """Mode radius multiplier so the drawn ring matches queue selection."""
        self._radius_scale = max(0.05, float(scale))
        self._apply_lens_rect()

    def _effective_radius(self) -> float:
        return max(LENS_RADIUS_MIN, min(LENS_RADIUS_MAX, self._radius * self._radius_scale))

    def _apply_lens_rect(self) -> None:
        """Lens ellipse in scene px ≡ mood radius on valence (720) / energy (524) axes."""
        r = self._effective_radius()
        rx = r * 720.0
        ry = r * 524.0
        self.lens.setRect(-rx, -ry, rx * 2.0, ry * 2.0)

    def set_lens(self, valence: float, energy: float, radius: float) -> None:
        self._radius = max(LENS_RADIUS_MIN, min(LENS_RADIUS_MAX, radius))
        self.lens.setPos(self.mood_to_pos(valence, energy))
        self._apply_lens_rect()

    def snap_lens_to_star(self, star: TrackStar) -> None:
        track = star.ranked.track
        pos = self.mood_to_pos(track.valence, track.energy)
        self._positions[track.id] = pos
        star.setPos(pos)
        self.set_lens(track.valence, track.energy, self._radius)

    def lens_moved(self) -> None:
        x, y, r = self.lens_mood()
        self.lens_changed.emit(x, y, r)

    def star_moved(self, star: TrackStar) -> None:
        v, e = self.pos_to_mood(star.pos())
        self._positions[star.ranked.track.id] = QPointF(star.pos())
        self._rebuild_star_grid()
        self._rebuild_starfield()
        self.track_pinned.emit(star.ranked.track.id, v, e)

    def _update_drag_position(self, tid: int, pos: QPointF) -> None:
        """Keep grid in sync while a star is dragged (hover / nearest stay accurate)."""
        self._positions[tid] = QPointF(pos)
        self._rebuild_star_grid()

    def reset_view(self) -> None:
        self._end_zoom_interaction()
        self._user_zoom = VIEW_ZOOM_MIN
        self._zoom_target = VIEW_ZOOM_MIN
        self._pinch_zoom0 = VIEW_ZOOM_MIN
        self._cluster_once = True
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._lod_band = -1
        self._sync_live_stars(force=True)

    def _apply_base_fit(self) -> None:
        center = self.mapToScene(self.viewport().rect().center())
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        if self._user_zoom > VIEW_ZOOM_MIN + 1e-3:
            self.scale(self._user_zoom, self._user_zoom)
            self.centerOn(center)
        self._lod_band = -1
        self._sync_live_stars(force=True)

    def _begin_zoom_interaction(self) -> None:
        if self._zoom_active:
            return
        self._zoom_active = True
        self._cluster_once = True
        # Hide live stars during the gesture — keep an in-progress pin visible.
        locked = self._drag_locked_ids()
        for tid, star in self._stars.items():
            if tid in locked:
                star.show()
            else:
                star.hide()
        self.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    def _end_zoom_interaction(self) -> None:
        self._zoom_active = False
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    def _schedule_zoom_lod(self) -> None:
        self._lod_timer.start()

    def _flush_zoom_lod(self) -> None:
        self._end_zoom_interaction()
        self._sync_live_stars(force=True)

    def _update_chrome(self, zoom: float) -> None:
        if zoom <= LOD_CHROME_FADE_START:
            chrome = 1.0
        elif zoom >= LOD_CHROME_FADE_END:
            chrome = 0.08
        else:
            t = (zoom - LOD_CHROME_FADE_START) / (LOD_CHROME_FADE_END - LOD_CHROME_FADE_START)
            chrome = 1.0 - 0.92 * t
        for item in self._sky_chrome:
            item.setOpacity(chrome)

    def _ensure_interactive_star(self, tid: int) -> TrackStar | None:
        """Materialize one movable star (used for sky-mode click/drag)."""
        item = self._ranked.get(tid)
        if item is None or tid not in self._positions:
            return None
        star = self._stars.get(tid)
        if star is None:
            star = TrackStar(item)
            star._map = self
            self._scene.addItem(star)
            self._stars[tid] = star
        else:
            star.apply_style(item, current=tid == self._current_id)
        star.setPos(self._positions[tid])
        star.setZValue(40)
        star.show()
        return star

    def _sync_live_stars(self, *, force: bool = False) -> None:
        """Sky = baked field only. Zoomed in = sparse live stars in the viewport."""
        zoom = self._user_zoom
        band = int(zoom * 4)
        if not force and band == self._lod_band:
            return
        self._lod_band = band
        self._update_chrome(zoom)

        if zoom < LIVE_STARS_ZOOM or self._zoom_active:
            locked = self._drag_locked_ids()
            for tid, star in self._stars.items():
                if tid in locked:
                    # Keep drag position (may differ from DB after a mid-drag refresh).
                    if tid in self._positions:
                        star.setPos(self._positions[tid])
                    star.show()
                else:
                    star.hide()
            self._field.setOpacity(1.0)
            return

        # Fade the baked field slightly so live bodies read clearly.
        self._field.setOpacity(0.35)
        vis = self.mapToScene(self.viewport().rect()).boundingRect()
        vis.adjust(-60, -60, 60, 60)
        locked = self._drag_locked_ids()

        # Prefer current / loved / high-fit tracks among those in view.
        candidates: list[tuple[float, RankedTrack]] = []
        for tid, pos in self._positions.items():
            if not vis.contains(pos) and tid not in locked:
                continue
            item = self._ranked.get(tid)
            if item is None:
                continue
            score = item.fit * 0.7 + item.importance * 0.3
            if item.track.loved:
                score += 0.5
            if tid == self._current_id:
                score += 1.0
            if tid in locked:
                score += 2.0
            candidates.append((score, item))
        candidates.sort(key=lambda x: x[0], reverse=True)
        keep_ids = {item.track.id for _, item in candidates[:LIVE_STAR_CAP]}
        keep_ids.update(locked & self._ranked.keys())

        for tid in list(self._stars):
            if tid not in keep_ids:
                self._scene.removeItem(self._stars.pop(tid))

        if zoom <= LOD_GLOW_START:
            glow_a = 0.0
        else:
            glow_a = min(1.0, (zoom - LOD_GLOW_START) / 2.4)
        labels_on = zoom >= LOD_LABEL_START
        labeled = 0
        for _, item in candidates[:LIVE_STAR_CAP]:
            tid = item.track.id
            pos = self._positions[tid]
            star = self._stars.get(tid)
            if star is None:
                star = TrackStar(item)
                star._map = self
                self._scene.addItem(star)
                self._stars[tid] = star
            else:
                star.apply_style(item, current=tid == self._current_id)
            if tid not in locked:
                star.setPos(pos)
            else:
                # Preserve the in-progress drag; keep _positions aligned.
                self._positions[tid] = QPointF(star.pos())
            star.show()
            show_label = False
            if labels_on and labeled < LABEL_CAP:
                show_label = True
                labeled += 1
            star.set_lod(glow_a, show_label)

    def _anchor_point(self, view_pos: QPoint | QPointF) -> QPoint:
        return view_pos.toPoint() if isinstance(view_pos, QPointF) else QPoint(view_pos)

    def _cluster_anchor(self, view_pos: QPoint) -> QPoint:
        if not self._star_grid:
            return view_pos
        scene_pt = self.mapToScene(view_pos)
        radius = max(28.0, 110.0 / max(self._user_zoom, 1.0))
        cell = CLUSTER_GRID
        cx0 = int(scene_pt.x() // cell)
        cy0 = int(scene_pt.y() // cell)
        span = max(1, int(radius // cell) + 1)
        nearby: list[QPointF] = []
        best: QPointF | None = None
        best_d = radius * 1.35
        for gx in range(cx0 - span, cx0 + span + 1):
            for gy in range(cy0 - span, cy0 + span + 1):
                for _tid, p in self._star_grid.get((gx, gy), ()):
                    d = hypot(p.x() - scene_pt.x(), p.y() - scene_pt.y())
                    if d <= radius:
                        nearby.append(p)
                    if d < best_d:
                        best_d = d
                        best = p
        if len(nearby) < 2:
            if best is None:
                return view_pos
            nearby = [best]
        cx = sum(p.x() for p in nearby) / len(nearby)
        cy = sum(p.y() for p in nearby) / len(nearby)
        focus = QPointF(
            scene_pt.x() * (1.0 - CLUSTER_PULL) + cx * CLUSTER_PULL,
            scene_pt.y() * (1.0 - CLUSTER_PULL) + cy * CLUSTER_PULL,
        )
        return self.mapFromScene(focus)

    def _set_zoom_target(self, zoom: float, view_pos: QPoint | QPointF) -> None:
        self._begin_zoom_interaction()
        self._zoom_target = max(VIEW_ZOOM_MIN, min(VIEW_ZOOM_MAX, zoom))
        anchor = self._anchor_point(view_pos)
        # Cluster pull once per gesture — not every native tick.
        if self._cluster_once and self._zoom_target > self._user_zoom + 1e-4:
            anchor = self._cluster_anchor(anchor)
            self._cluster_once = False
        self._zoom_anchor = anchor
        self._apply_zoom_level(self._zoom_target, self._zoom_anchor)

    def _multiply_zoom(self, factor: float, view_pos: QPoint | QPointF) -> None:
        if factor <= 0:
            return
        self._set_zoom_target(self._zoom_target * factor, view_pos)

    def _apply_zoom_level(self, new_zoom: float, view_pos: QPoint) -> None:
        new_zoom = max(VIEW_ZOOM_MIN, min(VIEW_ZOOM_MAX, new_zoom))
        if abs(new_zoom - self._user_zoom) < 1e-5:
            # Wheel/pinch past the zoom ceiling still begins a gesture that hides
            # live stars — schedule LOD end so they come back.
            self._schedule_zoom_lod()
            return
        if new_zoom <= VIEW_ZOOM_MIN + 1e-3:
            self.reset_view()
            return
        before = self.mapToScene(view_pos)
        actual = new_zoom / self._user_zoom
        self._user_zoom = new_zoom
        self.scale(actual, actual)
        after = self.mapToScene(view_pos)
        delta = after - before
        self.translate(delta.x(), delta.y())
        self._schedule_zoom_lod()

    def _arm_native_zoom_priority(self) -> None:
        self._prefer_native_zoom = True
        if self._native_zoom_clear is not None:
            self._native_zoom_clear.stop()
        clear = QTimer(self)
        clear.setSingleShot(True)
        clear.setInterval(350)
        clear.timeout.connect(self._clear_native_zoom_priority)
        clear.start()
        self._native_zoom_clear = clear

    def _clear_native_zoom_priority(self) -> None:
        self._prefer_native_zoom = False
        self._native_zoom_clear = None

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta == 0:
                delta = event.pixelDelta().y()
            if delta == 0:
                return
            self._cluster_once = True
            factor = VIEW_WHEEL_FACTOR if delta > 0 else 1.0 / VIEW_WHEEL_FACTOR
            self._multiply_zoom(factor, event.position())
            event.accept()
            return
        step = 0.01 if self._radius <= 0.12 else 0.02
        delta_y = event.angleDelta().y()
        if delta_y == 0:
            delta_y = event.pixelDelta().y()
        if delta_y == 0:
            return
        delta = step if delta_y > 0 else -step
        self._radius = max(LENS_RADIUS_MIN, min(LENS_RADIUS_MAX, self._radius + delta))
        self.set_lens(*self.lens_mood())
        self.lens_moved()
        event.accept()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.NativeGesture:
            return self._native_gesture(event)
        if event.type() == QEvent.Type.Gesture:
            return self._gesture_event(event)
        return super().event(event)

    def _native_gesture(self, event: QNativeGestureEvent) -> bool:
        gtype = event.gestureType()
        if gtype == Qt.NativeGestureType.BeginNativeGesture:
            self._cluster_once = True
            return False
        if gtype == Qt.NativeGestureType.ZoomNativeGesture:
            self._arm_native_zoom_priority()
            raw = float(event.value())
            if abs(raw) > VIEW_NATIVE_OUTLIER:
                event.accept()
                return True
            factor = 1.0 + raw
            pos = self.mapFromGlobal(event.globalPosition().toPoint())
            self._multiply_zoom(factor, pos)
            event.accept()
            return True
        if gtype == Qt.NativeGestureType.SmartZoomNativeGesture:
            self.reset_view()
            event.accept()
            return True
        return False

    def _gesture_event(self, event: QGestureEvent) -> bool:
        pinch = event.gesture(Qt.GestureType.PinchGesture)
        if not isinstance(pinch, QPinchGesture):
            return False
        if self._prefer_native_zoom:
            event.accept()
            return True
        center = pinch.centerPoint()
        if pinch.state() == Qt.GestureState.GestureStarted:
            self._pinch_zoom0 = self._zoom_target
            self._cluster_once = True
            self._set_zoom_target(self._pinch_zoom0, center)
            event.accept()
            return True
        if pinch.state() == Qt.GestureState.GestureUpdated:
            total = float(pinch.totalScaleFactor())
            if total > 0:
                self._set_zoom_target(self._pinch_zoom0 * total, center)
            event.accept()
            return True
        if pinch.state() in (
            Qt.GestureState.GestureFinished,
            Qt.GestureState.GestureCanceled,
        ):
            self._flush_zoom_lod()
            event.accept()
            return True
        return False

    @staticmethod
    def _is_interactive_item(item: QGraphicsItem | None) -> bool:
        while item is not None:
            if isinstance(item, (TrackStar, LensItem)):
                return True
            item = item.parentItem()
        return False

    @staticmethod
    def _track_star_from_item(item: QGraphicsItem | None) -> TrackStar | None:
        while item is not None:
            if isinstance(item, TrackStar):
                return item
            item = item.parentItem()
        return None

    def _flush_pending_snap(self) -> None:
        candidate = self._pending_snap_id
        self._pending_snap_id = None
        if candidate is None or candidate not in self._ranked:
            return
        live = self._ensure_interactive_star(candidate)
        if live is not None:
            self.snap_lens_to_star(live)
            self.lens_moved()
        if self._user_zoom < LIVE_STARS_ZOOM:
            self._lod_band = -1
            self._sync_live_stars(force=True)

    def _cancel_pending_snap(self) -> None:
        self._snap_timer.stop()
        self._pending_snap_id = None

    def _finish_hand_drag(self, event) -> None:
        """Complete a ScrollHandDrag press so QGraphicsView drops sticky pan state."""
        if not self._hand_drag_armed and not self._panning:
            if self.dragMode() == QGraphicsView.DragMode.ScrollHandDrag:
                self.setDragMode(QGraphicsView.DragMode.NoDrag)
            return
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self._panning = False
        if self._hand_drag_armed:
            release = QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                event.position(),
                event.globalPosition(),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                event.modifiers(),
            )
            self._hand_drag_armed = False
            super().mouseReleaseEvent(release)

    def _begin_hand_pan(self, event) -> None:
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._panning = True
        self._hand_drag_armed = True
        super().mousePressEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.position().toPoint())
            if not self._is_interactive_item(item):
                scene_pt = self.mapToScene(event.position().toPoint())
                # Zoomed in: baked dots aren't live — materialize the nearest star.
                if self._user_zoom >= LIVE_STARS_ZOOM:
                    tid = self._nearest_track_id(scene_pt, HIT_RADIUS_SKY)
                    if tid is not None and tid in self._ranked:
                        star = self._ensure_interactive_star(tid)
                        if star is not None:
                            super().mousePressEvent(event)
                            return
                    self._begin_hand_pan(event)
                    return
                # Sky mode: remember nearest star for click-snap / tight grab-to-pin.
                tid = self._nearest_track_id(scene_pt, HIT_RADIUS_SKY)
                self._sky_candidate_id = tid if tid in self._ranked else None
                self._sky_press_scene = QPointF(scene_pt)
                self._sky_hold_id = None
                self._sky_dragged = False
                self._sky_defer_pan = False
                self._cancel_pending_snap()
                if self._sky_candidate_id is not None:
                    self.track_hovered.emit(self._ranked[self._sky_candidate_id].track.label)
                    star_pos = self._positions.get(self._sky_candidate_id)
                    grab = (
                        star_pos is not None
                        and hypot(
                            scene_pt.x() - star_pos.x(),
                            scene_pt.y() - star_pos.y(),
                        )
                        <= HIT_RADIUS_SKY_GRAB
                    )
                    if grab:
                        # Wait for move — don't pan first (avoids viewport nudge on pin).
                        self._sky_defer_pan = True
                        event.accept()
                        return
                self._begin_hand_pan(event)
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and (
            self._sky_hold_id is not None
            or self._sky_candidate_id is not None
            or self._sky_defer_pan
        ):
            tid = self._sky_hold_id
            star = self._stars.get(tid) if tid is not None else None
            dragged = self._sky_dragged
            candidate = self._sky_candidate_id
            self._sky_hold_id = None
            self._sky_press_scene = None
            self._sky_dragged = False
            self._sky_candidate_id = None
            self._sky_defer_pan = False
            self._finish_hand_drag(event)
            if star is not None and dragged:
                self.star_moved(star)
            elif not dragged and candidate is not None and candidate in self._ranked:
                # Defer snap so a double-click empty can cancel it.
                self._pending_snap_id = candidate
                self._snap_timer.start()
            if self._user_zoom < LIVE_STARS_ZOOM and (star is not None and dragged):
                self._lod_band = -1
                self._sync_live_stars(force=True)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and (
            self._hand_drag_armed or self._panning
        ):
            self._finish_hand_drag(event)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self._cancel_pending_snap()
        item = self.itemAt(event.position().toPoint())
        star = self._track_star_from_item(item)
        if star is not None:
            self.track_activated.emit(star.ranked.track.id)
            event.accept()
            return
        if not self._is_interactive_item(item):
            # Tight core only — otherwise empty double-click returns to the full sky.
            tid = self._nearest_track_id(
                self.mapToScene(event.position().toPoint()), HIT_RADIUS_SKY_GRAB
            )
            if tid is not None and tid in self._ranked:
                self.track_activated.emit(tid)
                event.accept()
                return
            self.reset_view()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:
        # Sky: convert a core-star press into drag-to-pin without a prior pan nudge.
        if (
            self._user_zoom < LIVE_STARS_ZOOM
            and self._sky_hold_id is None
            and self._sky_candidate_id is not None
            and self._sky_press_scene is not None
            and event.buttons() & Qt.MouseButton.LeftButton
        ):
            pos = self.mapToScene(event.position().toPoint())
            delta = pos - self._sky_press_scene
            if hypot(delta.x(), delta.y()) > SKY_DRAG_SLOP:
                star_pos = self._positions.get(self._sky_candidate_id)
                grab = (
                    self._sky_defer_pan
                    or (
                        star_pos is not None
                        and hypot(
                            self._sky_press_scene.x() - star_pos.x(),
                            self._sky_press_scene.y() - star_pos.y(),
                        )
                        <= HIT_RADIUS_SKY_GRAB
                    )
                )
                if grab:
                    star = self._ensure_interactive_star(self._sky_candidate_id)
                    if star is not None:
                        if self._hand_drag_armed or self._panning:
                            self._finish_hand_drag(event)
                        self._sky_defer_pan = False
                        self._sky_hold_id = self._sky_candidate_id
                        self._sky_dragged = True
                        star.setPos(
                            QPointF(
                                max(40.0, min(760.0, pos.x())),
                                max(36.0, min(560.0, pos.y())),
                            )
                        )
                        self._update_drag_position(self._sky_hold_id, star.pos())
                        event.accept()
                        return
                # Far from star core → pure pan; drop candidate so release won't snap.
                self._sky_candidate_id = None
                self._sky_defer_pan = False
                if not self._hand_drag_armed and not self._panning:
                    self._begin_hand_pan(event)
                    return
            elif self._sky_defer_pan:
                event.accept()
                return
        if self._sky_hold_id is not None and event.buttons() & Qt.MouseButton.LeftButton:
            star = self._stars.get(self._sky_hold_id)
            if star is not None:
                pos = self.mapToScene(event.position().toPoint())
                pos.setX(max(40.0, min(760.0, pos.x())))
                pos.setY(max(36.0, min(560.0, pos.y())))
                star.setPos(pos)
                self._update_drag_position(self._sky_hold_id, pos)
                self._sky_dragged = True
                self.track_hovered.emit(star.ranked.track.label)
                event.accept()
                return
        item = self.itemAt(event.position().toPoint())
        star = self._track_star_from_item(item)
        if star is not None:
            self.track_hovered.emit(star.ranked.track.label)
        elif self._user_zoom < LIVE_STARS_ZOOM:
            tid = self._nearest_track_id(
                self.mapToScene(event.position().toPoint()), HIT_RADIUS_SKY
            )
            if tid is not None and tid in self._ranked:
                self.track_hovered.emit(self._ranked[tid].track.label)
        elif self._user_zoom >= LIVE_STARS_ZOOM:
            tid = self._nearest_track_id(
                self.mapToScene(event.position().toPoint()), HIT_RADIUS_SKY
            )
            if tid is not None and tid in self._ranked:
                self.track_hovered.emit(self._ranked[tid].track.label)
        super().mouseMoveEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_base_fit()
