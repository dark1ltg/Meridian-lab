"""Matrix song rows sit a little taller than the queue list."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MERIDIAN_NO_GL", "1")

from PySide6.QtWidgets import QApplication, QListWidgetItem

from meridian.queue_engine import Quadrant
from meridian.ui.fit_list import FitList
from meridian.ui.matrix import EisenhowerMatrix


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_matrix_rows_have_two_pixels_more_air(qapp) -> None:
    matrix = EisenhowerMatrix()
    queue = FitList()
    queue.setFont(matrix.cells[Quadrant.NOW].list.font())
    queue.addItem(QListWidgetItem("Keep"))
    queue_height = queue.item(0).sizeHint().height()

    for quadrant, cell in matrix.cells.items():
        cell.list.addItem(QListWidgetItem(quadrant.value))
        row = cell.list.item(0).sizeHint().height()
        assert row == queue_height + 4
        assert cell.list._line_extra == 4

    assert queue._line_extra == 0
