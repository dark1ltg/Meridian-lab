from __future__ import annotations

from PySide6.QtCore import QEvent, QModelIndex, QStringListModel, QTimer, Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import QCompleter, QLineEdit

from meridian.library import Library, Track


class TrackSearch(QLineEdit):
    """Library search with popup results.

    Intended UX (inject = play + context-queue insert via track_chosen):
    - Single-click a result: highlight only (do not inject).
    - Double-click a result: inject immediately.
    - Arrow to a result + Enter: inject that highlighted row.
    - Enter with no highlight: inject the top hit.

    QCompleter + UnfilteredPopupCompletion can rewrite the line edit to the
    chosen label while arrowing/clicking. Those labels are display strings
    (``Artist — Title``), not something ``Library.search`` matches — a refresh
    then clears the popup and selection never injects. We keep the user query
    stable and inject from the popup highlight instead.
    """

    track_chosen = Signal(int)

    def __init__(self, library: Library, parent=None) -> None:
        super().__init__(parent)
        self.library = library
        self.setObjectName("trackSearch")
        self.setPlaceholderText("Search title, artist, album")
        self.setClearButtonEnabled(True)
        self._hits: list[Track] = []
        # Completer labels are unique even when artist/title collide.
        self._label_to_id: dict[str, int] = {}
        self._user_query = ""
        self._emitting = False
        self._guarding_text = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(90)
        self._timer.timeout.connect(self._refresh)
        self._model = QStringListModel(self)
        self._completer = QCompleter(self._model, self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setCompletionMode(QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self._completer.setMaxVisibleItems(12)
        self.setCompleter(self._completer)
        popup = self._completer.popup()
        popup.setObjectName("searchPopup")
        popup.setStyleSheet(
            "background: #121a2e; color: #d7deee; border: 1px solid #2a3a58; "
            "selection-background-color: #243049; outline: none;"
        )
        self.textChanged.connect(self._schedule)
        # Do not use QCompleter.activated / returnPressed→first-hit: activated fires on
        # single-click (wrong UX) and races with returnPressed (plays hits[0] then selection).
        # Install after setCompleter so this filter runs before QCompleter's and can
        # swallow Enter / single-click completion.
        popup.doubleClicked.connect(self._on_popup_double_clicked)
        popup.viewport().installEventFilter(self)
        popup.installEventFilter(self)
        self.installEventFilter(self)

    def _schedule(self, text: str) -> None:
        if self._emitting or self._guarding_text:
            return
        # Completer rewrote the field to a result label — restore the typed query so
        # refresh does not search a non-matching display string and wipe hits.
        if self._label_to_id and text in self._label_to_id and text != self._user_query:
            self._restore_user_query()
            return
        self._user_query = text
        self._timer.start()

    def _restore_user_query(self) -> None:
        self._guarding_text = True
        try:
            blocked = self.blockSignals(True)
            self.setText(self._user_query)
            self.blockSignals(blocked)
            self.setCursorPosition(len(self._user_query))
        finally:
            self._guarding_text = False

    @staticmethod
    def _unique_label(track: Track, used: set[str]) -> str:
        base = track.label
        if base not in used:
            used.add(base)
            return base
        # Disambiguate duplicates with album or path stem.
        alt = f"{base}  ·  {track.album}" if track.album else base
        if alt not in used:
            used.add(alt)
            return alt
        n = 2
        while True:
            label = f"{base}  ({n})"
            if label not in used:
                used.add(label)
                return label
            n += 1

    def _refresh(self) -> None:
        if self._emitting or self._guarding_text:
            return
        query = self._user_query
        self._hits = self.library.search(query)
        used: set[str] = set()
        labels: list[str] = []
        self._label_to_id = {}
        for track in self._hits:
            label = self._unique_label(track, used)
            labels.append(label)
            self._label_to_id[label] = track.id
        self._model.setStringList(labels)
        if self._hits and self.hasFocus() and query.strip():
            self._completer.complete()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt API)
        popup = self._completer.popup()
        viewport = popup.viewport()

        if watched is self and event.type() == QEvent.Type.KeyPress:
            assert isinstance(event, QKeyEvent)
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self._accept_current():
                    return True
                return super().eventFilter(watched, event)

        if watched in (popup, viewport):
            if event.type() == QEvent.Type.KeyPress:
                assert isinstance(event, QKeyEvent)
                if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    self._accept_current()
                    return True

            if event.type() == QEvent.Type.MouseButtonDblClick and isinstance(event, QMouseEvent):
                if event.button() == Qt.MouseButton.LeftButton:
                    idx = self._index_at_popup_pos(watched, event)
                    if idx.isValid():
                        self._set_popup_current(idx)
                        self._choose_index(idx)
                        return True

            # Swallow left-button release so QListView does not emit clicked →
            # QCompleter completing/activating on single-click. Press still selects.
            if event.type() == QEvent.Type.MouseButtonRelease and isinstance(event, QMouseEvent):
                if event.button() == Qt.MouseButton.LeftButton:
                    idx = self._index_at_popup_pos(watched, event)
                    if idx.isValid():
                        self._set_popup_current(idx)
                        return True

        return super().eventFilter(watched, event)

    def _index_at_popup_pos(self, watched, event: QMouseEvent) -> QModelIndex:
        popup = self._completer.popup()
        viewport = popup.viewport()
        pos = event.position().toPoint()
        if watched is popup:
            pos = viewport.mapFrom(popup, pos)
        return popup.indexAt(pos)

    def _set_popup_current(self, index: QModelIndex) -> None:
        """Highlight a row without letting QCompleter rewrite the query field."""
        popup = self._completer.popup()
        self._guarding_text = True
        try:
            blocked = self.blockSignals(True)
            query = self._user_query
            popup.setCurrentIndex(index)
            if self.text() != query:
                self.setText(query)
                self.setCursorPosition(len(query))
            self.blockSignals(blocked)
        finally:
            self._guarding_text = False

    def _accept_current(self) -> bool:
        """Inject highlighted popup row, or the top hit. Returns True if handled."""
        if not self._user_query.strip() and not self.text().strip():
            return False
        if not self._hits:
            self._refresh()
        popup = self._completer.popup()
        idx = popup.currentIndex()
        if popup.isVisible() and idx.isValid():
            self._choose_index(idx)
            return True
        if self._hits:
            self._emit_track(self._hits[0].id)
            return True
        return False

    def _on_popup_double_clicked(self, index: QModelIndex) -> None:
        if index.isValid():
            self._choose_index(index)

    def _choose_index(self, index: QModelIndex) -> None:
        label = index.data(Qt.ItemDataRole.DisplayRole)
        if isinstance(label, str):
            self._choose_label(label)

    def _choose_label(self, label: str) -> None:
        tid = self._label_to_id.get(label)
        if tid is None:
            for track in self._hits:
                if track.label == label:
                    tid = track.id
                    break
        if tid is not None:
            self._emit_track(tid)

    def _emit_track(self, tid: int) -> None:
        if self._emitting:
            return
        self._emitting = True
        try:
            self._timer.stop()
            self._completer.popup().hide()
            self.track_chosen.emit(tid)
            # Block textChanged→refresh while clearing so the popup does not reopen.
            blocked = self.blockSignals(True)
            self.clear()
            self.blockSignals(blocked)
            self._user_query = ""
            self._hits = []
            self._label_to_id = {}
            self._model.setStringList([])
        finally:
            self._emitting = False
