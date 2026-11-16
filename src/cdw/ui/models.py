from __future__ import annotations

from qtpy.QtCore import QAbstractTableModel, QModelIndex, Qt
from qtpy.QtGui import QColor

from cdw.domain.contracts import QueryResult
from cdw.ui.design.manager import ThemeManager

NULL_TEXT = "NULL"
MAX_CELL_CHARACTERS = 2000
ELLIPSIS = "…"
RAW_VALUE_ROLE = Qt.UserRole + 1


class ResultTableModel(QAbstractTableModel):
    def __init__(self, parent=None, *, theme=None) -> None:
        super().__init__(parent)
        self.result: QueryResult | None = None
        self.theme = theme or ThemeManager.current_manager()
        self.theme.changed.connect(self._theme_changed)

    def _theme_changed(self, style):
        if self.rowCount() and self.columnCount():
            self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, self.columnCount() - 1), [Qt.ForegroundRole])

    def set_result(self, result: QueryResult | None) -> None:
        self.beginResetModel()
        self.result = result
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() or self.result is None else len(self.result.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() or self.result is None else len(self.result.columns)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or self.result is None:
            return None
        value = self.result.rows[index.row()][index.column()]
        if role == RAW_VALUE_ROLE:
            return value
        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            text = NULL_TEXT if value is None else str(value)
            return text if len(text) <= MAX_CELL_CHARACTERS else text[:MAX_CELL_CHARACTERS] + ELLIPSIS
        if role == Qt.ForegroundRole and value is None:
            return QColor(self.theme.current.colors["muted"])
        if role == Qt.TextAlignmentRole and isinstance(value, (int, float)) and not isinstance(value, bool):
            return Qt.AlignRight | Qt.AlignVCenter
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if self.result is None:
            return None
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole:
                return self.result.columns[section]
            if role == Qt.ToolTipRole:
                return self.result.types[section]
        elif role == Qt.DisplayRole:
            return str(section + 1)
        return None
