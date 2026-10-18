from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QLabel,
    QListWidgetItem,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from meridian.queue_engine import QUADRANT_SUB, QUADRANT_TITLE, Quadrant, RankedTrack
from meridian.ui.fit_list import FitList
from meridian.ui.fonts import condensed
from meridian.ui.palette import PLAYLIST_HEX

STAR_COLOR_ROLE = Qt.ItemDataRole.UserRole + 1
STAR_LOVED_ROLE = Qt.ItemDataRole.UserRole + 2
STAR_CURRENT_ROLE = Qt.ItemDataRole.UserRole + 3
STAR_LINK_ROLE = Qt.ItemDataRole.UserRole + 4


class StarDelegate(QStyledItemDelegate):
    """A queue row is a star, then its name. A faint thread joins the rows."""

    def initStyleOption(self, option: QStyleOptionViewItem, index) -> None:
        super().initStyleOption(option, index)
        option.textElideMode = Qt.TextElideMode.ElideRight
        option.features &= ~QStyleOptionViewItem.ViewItemFeature.WrapText

    def paint(self, painter: QPainter, option, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else None
        if style is not None:
            style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        color = QColor(index.data(STAR_COLOR_ROLE) or "#e8ecf7")
        loved = bool(index.data(STAR_LOVED_ROLE))
        current = bool(index.data(STAR_CURRENT_ROLE))
        linked = index.data(STAR_LINK_ROLE) is not False
        rect = option.rect
        cx = rect.left() + 14
        cy = rect.center().y()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if linked:
            thread = QColor(255, 246, 214, 180) if current else QColor(214, 206, 232, 90)
            pen = QPen(thread, 1.5 if current else 1.1)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawLine(QPointF(cx, rect.top()), QPointF(cx, rect.bottom()))
        radius = 5.1 if current else 3.8
        if current:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 246, 214, 80))
            painter.drawEllipse(QPointF(cx, cy), radius + 3.2, radius + 3.2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(cx, cy), radius, radius)
        if loved:
            ring = QPen(QColor("#fff4c2"), 1.15)
            painter.setPen(ring)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QPointF(cx, cy), radius + 1.7, radius + 1.7)
        painter.setPen(QColor("#f4f7ff") if current else color)
        painter.setFont(option.font)
        text_rect = rect.adjusted(26, 0, -6, 0)
        elided = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, text_rect.width())
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), elided)
        painter.restore()


class StarList(FitList):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setItemDelegate(StarDelegate(self))


class MatrixCell(QFrame):
    activated = Signal(int)
    pulled = Signal(int)

    def __init__(self, quadrant: Quadrant) -> None:
        super().__init__()
        self.quadrant = quadrant
        self.setObjectName("matrixCell")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        color = PLAYLIST_HEX[quadrant]
        title = QLabel(QUADRANT_TITLE[quadrant])
        title.setObjectName("quadTitle")
        title.setStyleSheet(f"color: {color};")
        sub = QLabel(QUADRANT_SUB[quadrant])
        sub.setObjectName("quadSub")
        sub.setWordWrap(False)
        sub.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.list = FitList()
        self.list.setObjectName("quadList")
        self.list.setFont(condensed(12))
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.itemClicked.connect(self._activate)
        layout.addWidget(title)
        layout.addWidget(sub)
        layout.addWidget(self.list, 1)
        self.setStyleSheet(
            f"""
            QFrame#matrixCell {{
                background: #121a2e;
                border: 1px solid #243049;
                border-top: 2px solid {color};
                border-radius: 10px;
            }}
            """
        )

    def _activate(self, item: QListWidgetItem) -> None:
        tid = item.data(Qt.ItemDataRole.UserRole)
        if tid:
            self.activated.emit(int(tid))

    def populate(self, items: list[RankedTrack], limit: int = 12) -> None:
        self.list.clear()
        for ranked in items[:limit]:
            row = QListWidgetItem(ranked.track.short_title)
            row.setData(Qt.ItemDataRole.UserRole, ranked.track.id)
            row.setToolTip(
                f"{ranked.track.label}\n"
                f"Fit {ranked.fit:.0%} · Importance {ranked.importance:.0%} · Urgency {ranked.urgency:.0%}"
            )
            row.setForeground(QColor(PLAYLIST_HEX[self.quadrant]))
            if ranked.track.loved:
                row.setForeground(QColor(PLAYLIST_HEX[self.quadrant]).lighter(125))
            self.list.addItem(row)


class EisenhowerMatrix(QWidget):
    track_activated = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 10, 0, 0)
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(20)
        self.cells = {
            Quadrant.NOW: MatrixCell(Quadrant.NOW),
            Quadrant.FILL: MatrixCell(Quadrant.FILL),
            Quadrant.DEEP: MatrixCell(Quadrant.DEEP),
            Quadrant.SHELF: MatrixCell(Quadrant.SHELF),
        }
        # Eisenhower layout: important on top, urgent on left
        grid.addWidget(self.cells[Quadrant.NOW], 0, 0)
        grid.addWidget(self.cells[Quadrant.FILL], 0, 1)
        grid.addWidget(self.cells[Quadrant.DEEP], 1, 0)
        grid.addWidget(self.cells[Quadrant.SHELF], 1, 1)
        for cell in self.cells.values():
            cell.activated.connect(self.track_activated)

    def set_plan(self, by_quadrant: dict[Quadrant, list[RankedTrack]]) -> None:
        for q, cell in self.cells.items():
            cell.populate(by_quadrant.get(q, []))
