"""A directly user-typed named input -- for values that don't come from the
screen at all (e.g. a percentage you supply yourself). Same renameable-name
pattern regions use, and the same `.value()` contract formula.py's readings
dict relies on.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from .style import MONO


class ManualInput(QFrame):
    def __init__(self, name: str, on_remove) -> None:
        super().__init__()
        self.name = name
        self._on_remove = on_remove
        self.setProperty("role", "card")

        col = QVBoxLayout(self)
        col.setContentsMargins(10, 8, 10, 8)
        col.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        # Hollow, unlike a region's filled swatch -- there's no on-screen
        # marker for this to match up with, so an outline (not a color)
        # is the honest signal here.
        chip = QWidget()
        chip.setFixedSize(10, 10)
        chip.setStyleSheet("border: 1.5px solid #3f4147; border-radius: 3px; background: transparent;")
        top_row.addWidget(chip)

        self.name_edit = QLineEdit(name)
        self.name_edit.setFixedWidth(64)
        self.name_edit.setFont(MONO)
        self.name_edit.setStyleSheet(
            "QLineEdit { border: none; background: transparent; font-weight: 600; padding: 0; }"
            "QLineEdit:focus { border: 1px solid #5865f2; border-radius: 3px; background: #1e1f22; }"
        )
        self.name_edit.editingFinished.connect(self._on_name_committed)
        top_row.addWidget(self.name_edit)

        self.value_edit = QLineEdit()
        self.value_edit.setFont(MONO)
        self.value_edit.setAlignment(Qt.AlignRight)
        self.value_edit.setStyleSheet(
            "QLineEdit { border: none; background: #1e1f22; border-radius: 3px; padding: 2px 6px; }"
            "QLineEdit:focus { border: 1px solid #5865f2; }"
        )
        top_row.addWidget(self.value_edit, 1)

        # Only C is actually a percentage (0-100 scale) -- Budget and
        # anything else typed in here is a plain amount, so the "%" only
        # shows when the name is literally "C", live-updated on rename.
        self.percent_label = QLabel("%")
        self.percent_label.setStyleSheet("color: #949ba4;")
        self.percent_label.setVisible(name == "C")
        top_row.addWidget(self.percent_label)

        remove_btn = QPushButton("x")
        remove_btn.setObjectName("flatRemove")
        remove_btn.setFixedWidth(20)
        remove_btn.clicked.connect(self._remove)
        top_row.addWidget(remove_btn)
        col.addLayout(top_row)

        status_label = QLabel("manual input")
        status_label.setStyleSheet("color: #949ba4; font-size: 8pt;")
        col.addWidget(status_label)

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
