"""A small floating marker at a fixed screen point -- the write-back
counterpart to RegionWatcher's read side. `name` (a free-form label, purely
for your own reference) and `value_key` (exactly one formula.compute()
result key it pastes) are kept as separate fields, not one field doing
double duty. That conflation is exactly what caused confusion earlier
("target_1" not matching any result key just because nobody had renamed
the label yet) -- same class of bug regions had before `formula_key` split
display-name from lookup-key there too.

No capture rect, no OCR, no resize handles (a point, not an area to
read). Never fires on its own; only an explicit Send action in app.py
calls inject.send() for it.

No header bar any more -- renaming already only ever happened from the
app window's own row (this marker's name field was always read-only), so
dropping it cost no capability. What used to be the header's name text is
now a small chip tucked next to the crosshair -- see
_resize_and_reposition_chip().
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QLabel, QWidget

from .style import MONO_SMALL

SIZE = 22
CHIP_MARGIN = 10  # extra transparent space past the crosshair's own box, purely so the name chip can hang slightly outside it
CHIP_OVERLAP = 6  # how far the chip's top-left corner tucks inside the crosshair box's own bottom-right corner
COLOR = QColor("#ff9900")
CHIP_BG = "#0b0d10"


class TargetMarker(QWidget):
    name_changed = Signal(str)

    def __init__(self, x: int, y: int, name: str, on_close, on_change=None) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.name = name  # pure display label -- has no effect on what gets pasted, see value_key
        self.value_key: str | None = None  # exactly one formula.compute() result key Send pastes -- no cycling
        # click and paste are independent, user-controlled toggles -- not
        # one boolean forcing an all-or-nothing "click+paste" vs
        # "click-only" choice. Click always happens first when both are
        # on (see app.py's _on_send_target / inject.send), but either can
        # be off on its own -- e.g. paste-only to reuse whatever's already
        # focused from an earlier step in a sequence, without re-clicking
        # (and potentially re-toggling) it.
        self.click_enabled = True
        self.paste_enabled = True
        self.number = 0  # stable label for the Events tab's target picker; set by app.py, name can change

        self.x, self.y = x, y
        self._on_close = on_close
        self._on_change = on_change
        self._drag_origin = None

        self.name_chip = QLabel(name, self)
        self.name_chip.setFont(MONO_SMALL)
        # Informational only -- without this the chip would steal clicks
        # that should instead drag the marker (see mousePressEvent).
        self.name_chip.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.name_chip.setStyleSheet(
            f"background-color: {CHIP_BG}; color: #aaaaaa;"
            f" border: 1px solid {COLOR.name()}; border-radius: 4px; padding: 1px 5px;"
        )

        self._apply_geometry()

    def set_name(self, name: str) -> None:
        name = name.strip()
        if name and name != self.name:
            self.name = name
            self.name_chip.setText(name)
            self.name_changed.emit(name)
            self._resize_and_reposition_chip()

    def set_value_key(self, text: str) -> None:
        """Called from the app window's own key-editing field -- exactly
        one key, or None if left blank."""
        text = text.strip()
        self.value_key = text or None

    # -- geometry: centered on (x, y) ---------------------------------------
    def _apply_geometry(self) -> None:
        self._resize_and_reposition_chip()

    def _resize_and_reposition_chip(self) -> None:
        """Sizes this widget to fit the crosshair box PLUS whatever the
        chip's current name actually needs, then tucks the chip into the
        box's bottom-right corner. Same reasoning as RegionWatcher's
        _resize_and_reposition_chip: a longer name (target names are
        free-form -- "Target 1" alone is already 8 characters) must never
        get clamped back over the crosshair itself, which is exactly what
        the old fixed-margin math did."""
        self.name_chip.adjustSize()
        chip_x = max(0, SIZE - CHIP_OVERLAP)
        chip_y = max(0, SIZE - CHIP_OVERLAP)
        total_w = max(SIZE + CHIP_MARGIN, chip_x + self.name_chip.width())
        total_h = max(SIZE + CHIP_MARGIN, chip_y + self.name_chip.height())
        left = self.x - SIZE // 2
        top = self.y - SIZE // 2
        self.setGeometry(left, top, total_w, total_h)
        self.name_chip.move(chip_x, chip_y)

    def is_alive(self) -> bool:
        """Used by EventSequencer to skip a step gracefully if its target
        was removed mid-sequence rather than crash the run. Touching any
        attribute on a deleted PySide6 widget raises RuntimeError -- that,
        not visibility (a target can be legitimately hidden mid-click),
        is what "removed" actually means here."""
        try:
            self.isVisible()
            return True
        except RuntimeError:  # the underlying C++ object is already gone
            return False

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setPen(QPen(COLOR, 2))
        painter.drawEllipse(2, 2, SIZE - 4, SIZE - 4)
        painter.drawLine(SIZE // 2, 0, SIZE // 2, SIZE)
        painter.drawLine(0, SIZE // 2, SIZE, SIZE // 2)

    # -- mouse: drag to reposition, no resize (it's a point) ---------------
    # The name chip is transparent to mouse events, so every click
    # anywhere on this widget reaches these handlers directly.
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_origin = (event.globalPosition().toPoint(), self.x, self.y)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_origin:
            origin, ox0, oy0 = self._drag_origin
            gpos = event.globalPosition().toPoint()
            self.x = ox0 + (gpos.x() - origin.x())
            self.y = oy0 + (gpos.y() - origin.y())
            self._apply_geometry()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_origin:
            self._drag_origin = None
            if self._on_change:
                self._on_change(self)

    def _close(self) -> None:
        if self._on_close:
            self._on_close(self)
        self.close()
        self.deleteLater()  # schedules real C++ destruction -- close() alone only hides it,
        # which would leave is_alive() returning True forever for a target
        # still referenced by a saved event step
