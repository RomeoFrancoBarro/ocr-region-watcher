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
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget

from .style import MONO

SIZE = 22
HEADER_H = 22
COLOR = QColor("#ff9900")
MIN_HEADER_W = SIZE  # never narrower than the crosshair itself
NAME_PADDING = 36  # close button (18) + matching left spacer (18) -- margins/spacing are zero, see header setup


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

        self.header = QWidget(self)
        self.header.setStyleSheet("background-color: black;")
        layout = QHBoxLayout(self.header)
        layout.setContentsMargins(0, 0, 0, 0)  # zero -- the gray name box should fill the black bar exactly, not float inset within it
        layout.setSpacing(0)

        # Balances the close button's width on the opposite side -- without
        # this, the gray box (even with its own text centered inside it)
        # ends up shifted left within the black bar, since only the close
        # button claims space on the right.
        left_spacer = QWidget()
        left_spacer.setFixedWidth(18)
        left_spacer.setStyleSheet("background-color: black;")  # explicit -- a bare QWidget isn't guaranteed to show the parent's background through it
        layout.addWidget(left_spacer)

        self.name_edit = QLineEdit(name)
        self.name_edit.setFont(MONO)
        self.name_edit.setAlignment(Qt.AlignCenter)
        self.name_edit.setFixedHeight(HEADER_H)  # QLineEdit's own vertical size policy is Fixed -- zero layout margins alone don't stretch it to fill the header
        self.name_edit.setReadOnly(True)  # rename from the app window's own row instead -- set_name() still updates this
        self.name_edit.setStyleSheet("background-color: #222222; color: #aaaaaa; border: none; padding: 0px 2px;")
        self.name_edit.setToolTip("Read-only here -- rename this target from its row in the app window")
        layout.addWidget(self.name_edit, 1)

        close_btn = QPushButton("x")
        close_btn.setFixedWidth(18)
        close_btn.setToolTip("Remove this target")
        close_btn.setStyleSheet("color: #ff5555; background: black; border: none; font-weight: bold;")
        close_btn.clicked.connect(self._close)
        layout.addWidget(close_btn)

        self._apply_geometry()

    def set_name(self, name: str) -> None:
        name = name.strip()
        if name and name != self.name:
            self.name = name
            self.name_edit.setText(name)
            self.name_changed.emit(name)
            self._apply_geometry()  # the header needs to widen/narrow to fit the new name

    def set_value_key(self, text: str) -> None:
        """Called from the app window's own key-editing field -- exactly
        one key, or None if left blank."""
        text = text.strip()
        self.value_key = text or None

    # -- geometry: centered on (x, y), header sits above it ----------------
    def _apply_geometry(self) -> None:
        # The header (and so the whole marker) widens to fit however long
        # the current name is -- the fixed SIZE crosshair used to force
        # every name into a 22px box regardless of length. The crosshair
        # itself stays SIZE wide, centered under the header either way, so
        # it's still exactly on (x, y) no matter how wide the name makes
        # the header.
        name_w = QFontMetrics(MONO).horizontalAdvance(self.name)
        width = max(MIN_HEADER_W, name_w + NAME_PADDING)
        left = self.x - width // 2
        top = self.y - SIZE // 2
        self.setGeometry(left, top - HEADER_H, width, SIZE + HEADER_H)
        self.header.setGeometry(0, 0, width, HEADER_H)

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
        cx = (self.width() - SIZE) // 2  # crosshair centered under a header that may be wider than SIZE
        painter.drawEllipse(cx + 2, HEADER_H + 2, SIZE - 4, SIZE - 4)
        painter.drawLine(cx + SIZE // 2, HEADER_H, cx + SIZE // 2, HEADER_H + SIZE)
        painter.drawLine(cx, HEADER_H + SIZE // 2, cx + SIZE, HEADER_H + SIZE // 2)

    # -- mouse: drag to reposition, no resize (it's a point) ---------------
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
