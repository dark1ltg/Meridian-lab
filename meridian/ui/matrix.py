from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QLabel,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from meridian.queue_engine import QUADRANT_SUB, QUADRANT_TITLE, Quadrant, RankedTrack
from meridian.ui.fit_list import FitList
from meridian.ui.fonts import condensed
from meridian.ui.palette import PLAYLIST_HEX


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
