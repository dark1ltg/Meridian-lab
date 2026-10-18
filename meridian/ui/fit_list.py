from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListWidget,
    QScrollBar,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)


class _ElideDelegate(QStyledItemDelegate):
    def initStyleOption(self, option: QStyleOptionViewItem, index) -> None:
        super().initStyleOption(option, index)
        option.textElideMode = Qt.TextElideMode.ElideRight
        option.features &= ~QStyleOptionViewItem.ViewItemFeature.WrapText

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        hint = super().sizeHint(option, index)
        view = self.parent()
        width = view.viewport().width() if hasattr(view, "viewport") else 1
        return QSize(max(1, width), hint.height())


class _DeadScrollBar(QScrollBar):
    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setRange(0, 0)
        self.setFixedHeight(0)
        self.hide()

    def sizeHint(self) -> QSize:
        return QSize(0, 0)


class FitList(QListWidget):
    """Vertical-only list. Long names elide; the horizontal bar never appears."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setWordWrap(False)
        self.setUniformItemSizes(False)
        self.setSpacing(0)
        self.setAutoScroll(False)
        self.setItemDelegate(_ElideDelegate(self))
        self.setHorizontalScrollBar(_DeadScrollBar(self))
        self.setStyleSheet(
            "QScrollBar:horizontal { height: 0px; max-height: 0px; min-height: 0px; border: none; }"
        )
        self.model().rowsInserted.connect(self._clamp_items)
        self.model().modelReset.connect(self._clamp_items)

    def _row_height(self) -> int:
        return self.fontMetrics().height() + 12

    def _clamp_items(self, *_args) -> None:
        width = max(1, self.viewport().width())
        height = self._row_height()
        hint = QSize(width, height)
        for i in range(self.count()):
            item = self.item(i)
            if item is not None:
                item.setSizeHint(hint)
        bar = self.horizontalScrollBar()
        bar.setRange(0, 0)
        bar.setValue(0)
        bar.hide()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._clamp_items()
        self.doItemsLayout()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(0, dy)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.angleDelta().y() == 0 and event.pixelDelta().y() == 0:
            event.accept()
            return
        super().wheelEvent(event)
        self.horizontalScrollBar().setValue(0)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._clamp_items()
