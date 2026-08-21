"""A directly user-typed named input -- for values that don't come from the
screen at all (e.g. a percentage you supply yourself). Qt port of
../manual_input.py: same renameable-name pattern, same `.value()` contract
formula.py's readings dict relies on -- only the widget toolkit changed.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

from .style import MONO


class ManualInput(QWidget):
    def __init__(self, name: str, on_remove) -> None:
        super().__init__()
        self.name = name
        self._on_remove = on_remove

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.name_edit = QLineEdit(name)
        self.name_edit.setFixedWidth(64)
        self.name_edit.setFont(MONO)
        self.name_edit.editingFinished.connect(self._on_name_committed)
        layout.addWidget(self.name_edit)

        self.value_edit = QLineEdit()
        self.value_edit.setFont(MONO)
        self.value_edit.setAlignment(Qt.AlignRight)
        layout.addWidget(self.value_edit, 1)

        # Only C is actually a percentage (0-100 scale) -- Budget and
        # anything else typed in here is a plain amount, so the "%" only
        # shows when the name is literally "C", live-updated on rename.
        self.percent_label = QLabel("%")
        self.percent_label.setStyleSheet("color: #949ba4;")
        self.percent_label.setVisible(name == "C")
        layout.addWidget(self.percent_label)

        remove_btn = QPushButton("x")
        remove_btn.setObjectName("flatRemove")
        remove_btn.setFixedWidth(22)
        remove_btn.clicked.connect(self._remove)
        layout.addWidget(remove_btn)

    def _on_name_committed(self) -> None:
        new_name = self.name_edit.text().strip()
        if new_name:
            self.name = new_name
            self.percent_label.setVisible(self.name == "C")
        else:
            self.name_edit.setText(self.name)  # reject blank, revert to the last real name

    def _remove(self) -> None:
        self.deleteLater()
        if self._on_remove:
            self._on_remove(self)

    def value(self) -> float | None:
        """Parsed numeric value (a trailing % you typed is stripped), or
        None if the field isn't currently a valid number."""
        raw = self.value_edit.text().strip().rstrip("%").strip()
        try:
            return float(raw)
        except ValueError:
            return None
