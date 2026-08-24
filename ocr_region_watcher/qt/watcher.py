"""Persistent, movable, resizable floating frame sitting over a screen
region.

Real click-through: `setMask()` on the actual top-level window carves the
interior out of its hit-test region entirely, so the OS treats that area
as if this window plain isn't there -- clicks and the visible content
underneath both pass straight through. That's a stronger guarantee than a
single-color-key transparency trick would give, which is what let the
target-marker's own crosshair intercept its own click earlier in this
project (a solid pixel drawn in the "wrong" place broke it; a masked-out
region has no pixels to accidentally get wrong).

No separate header bar or text strip any more -- both used to sit outside
the frame (stacking ~46px of extra chrome above/below it) and covered
whatever's actually on screen there. Naming/renaming already only ever
happened from the app window's own row (this floating widget's name field
was always read-only), so dropping the header cost no capability. What
used to be the header's name + the strip's value are now both shown on a
single small chip tucked into the frame's bottom-right corner -- see
`_refresh_chip()`/`_resize_and_reposition_chip()`.
"""
from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QRegion
from PySide6.QtWidgets import QLabel, QWidget

from .style import MONO_SMALL, start_pulsing_glow, stop_pulsing_glow

BORDER = 2  # thin, matching the minimal-overlay mockup -- the old header+strip design used a thick 6px border, but that's not what a click-through interior needs; only HANDLE below governs how wide the actual draggable/resizable margin is
CORNER_RADIUS = 4  # matches the mockup's rounded frame corners
HANDLE = 12
PAD = HANDLE // 2 + 2  # room reserved on every side of the frame so all 4 corner handles paint in full -- a handle centered exactly on a corner extends HANDLE//2 past it in every direction; without this, Qt simply clips whatever a widget paints outside its own (0,0)-to-(width,height) rect, so the nw/ne/sw handles used to render only half-visible (only the se corner had the chip's own margin to grow into)
MIN_SIZE = 24
CHIP_MARGIN = 10  # extra transparent space past the frame's own edge, purely so the corner chip can hang slightly outside the border without ever overlapping the click-through interior
CHIP_OVERLAP = 8  # how far the chip's top-left corner tucks inside the frame's own bottom-right corner -- see _resize_and_reposition_chip
NAME_TEXT_COLOR = "#aaaaaa"  # same gray the old header's name field used
COLOR_LOST = QColor("#ff4444")  # shared across every region, regardless of its own identity color -- see paintEvent for why
VALUE_TEXT_COLOR = QColor("#39ff14")
CHIP_BG = "#0b0d10"

# Each region gets its own identity color (cycled in creation order by
# app.py, persisted per-region in template snapshots) instead of one shared
# border color for every frame -- this is what lets the app window's row
# and the floating frame be matched up by color at a glance. Orange is
# deliberately not in here -- that's reserved for target markers, so a
# region never gets mistaken for one. COLOR_LOST above is intentionally
# separate from this list: it must stay recognizable as "something's
# wrong" even for a region whose own identity color happens to be
# red-ish -- see paintEvent's dashed-vs-solid line style for the other
# half of that.
REGION_COLORS = [
    QColor("#ff4d4d"),  # red
    QColor("#4d94ff"),  # blue
    QColor("#4dff88"),  # green
    QColor("#d94dff"),  # magenta
    QColor("#4de3ff"),  # cyan
    QColor("#e3ff4d"),  # yellow
]


def next_region_color(index: int) -> QColor:
    """1-based creation index -> a REGION_COLORS entry, cycling once more
    regions exist than colors."""
    return REGION_COLORS[(index - 1) % len(REGION_COLORS)]


class RegionWatcher(QWidget):
    name_changed = Signal(str)  # emitted when the name changes -- from the app window's row, the only place renaming happens now
    value_changed = Signal(str)  # emitted when the displayed (joined) value text changes
    locked_changed = Signal(bool)  # emitted when the sampled background stops/resumes matching -- lets the app window's row mirror it

    def __init__(
        self,
        left: int,
        top: int,
        width: int,
        height: int,
        name: str,
        on_close,
        on_change=None,
        formula_key: str | None = None,
        color: QColor | None = None,
    ) -> None:
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.name = name
        self.formula_key = formula_key
        self.color = color or REGION_COLORS[0]  # callers always pass one (see app.py's next_region_color); this fallback is just defensive
        self.left, self.top = left, top
        # NOT self.width/self.height -- QWidget already defines width()/
        # height() as methods; shadowing them with plain int attributes
        # would silently break anything that calls them as methods.
        self.region_w, self.region_h = max(width, MIN_SIZE), max(height, MIN_SIZE)
        self.locked = True
        self.labels: list = []  # kept for Recognizer's interface; unused for now
        self.ref_color: tuple | None = None  # set by the app after each move/resize
        self.last_hash = None  # set by the app; used to skip re-recognizing unchanged crops
        self.last_value: object = None  # set by the app; most recent parsed value, for formula.compute()
        self.last_values: list = []  # set by the app; every number parsed from this region's crop, in order

        self._on_close = on_close
        self._on_change = on_change
        self._drag_origin = None
        self._resize_edge: str | None = None
        self._resize_origin = None
        self._moved_or_resized = False

        # Purely a value holder + change-signal source now, not shown
        # anywhere on this widget itself -- the app window's own row reads
        # this text, and the chip below mirrors it visually.
        self.value_edit = QLabel("--")
        self._lines: list[str] = ["--"]  # last recognized lines -- kept so a rename can refresh the chip without waiting on new OCR data

        self.value_chip = QLabel("--", self)
        self.value_chip.setFont(MONO_SMALL)
        self.value_chip.setTextFormat(Qt.TextFormat.RichText)
        # Informational only -- without this, the chip (sitting right over
        # the frame's own se resize-handle corner) would steal that
        # handle's clicks instead of letting them reach this widget's own
        # mousePressEvent below.
        self.value_chip.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self._refresh_chip()  # also handles the initial _apply_geometry() -- see _resize_and_reposition_chip
        self._style_chip()

    def set_name(self, name: str) -> None:
        """Rename from elsewhere (the app window's own row) -- this
        floating widget's chip mirrors the name (see _refresh_chip), even
        though renaming itself only ever happens from that row."""
        name = name.strip()
        if name and name != self.name:
            self.name = name
            self.name_changed.emit(name)
            self._refresh_chip()

    # -- geometry -----------------------------------------------------
    def _apply_geometry(self) -> None:
        self._resize_and_reposition_chip()

    def capture_rect(self) -> dict:
        """The interior rect to actually screenshot -- excludes the
        border and the corner resize-handles, so only real screen content
        gets read. Mirrors _resize_and_reposition_chip()'s mask interior
        exactly, so what's captured is exactly what's click-through."""
        inset = max(BORDER, HANDLE // 2)
        return {
            "left": self.left + inset,
            "top": self.top + inset,
            "width": max(self.region_w - 2 * inset, 1),
            "height": max(self.region_h - 2 * inset, 1),
        }

    def _resize_and_reposition_chip(self) -> None:
        """Sizes this widget to fit the frame PLUS whatever the chip's
        current text actually needs, then positions the chip tucked into
        the frame's bottom-right corner. PAD reserves that same room on
        every OTHER side too, purely so the nw/ne/sw corner handles paint
        in full -- see PAD's own comment.

        The chip's top-left is pinned CHIP_OVERLAP inside the frame's
        bottom-right corner -- never shifted left/up to avoid clipping,
        the widget grows to fit the chip instead. A recognized value (or,
        for TargetMarker, a target's name) can be wider than the frame
        itself is small (their real Red region is 45px wide; "$1,234.56"
        alone is wider than that) -- the old version clamped the chip's
        position to keep it inside a fixed-size margin, which for
        anything wider than that margin shoved the chip left on top of
        the frame's own content instead, and clipped whatever didn't fit.
        Growing the widget keeps the chip fully visible and never
        overlapping the frame.
        """
        self.value_chip.adjustSize()
        chip_x = PAD + max(0, self.region_w - CHIP_OVERLAP)
        chip_y = PAD + max(0, self.region_h - CHIP_OVERLAP)
        total_w = max(self.region_w + 2 * PAD, chip_x + self.value_chip.width())
        total_h = max(self.region_h + 2 * PAD, chip_y + self.value_chip.height())
        self.setGeometry(self.left - PAD, self.top - PAD, total_w, total_h)

        # The mask is everything EXCEPT the true interior (inset from the
        # capture rect's own edges, same inset capture_rect() uses) --
        # border, corner handles, and the value chip stay visible+
        # clickable; the interior is excluded from the window's hit-test
        # region entirely, which is what makes it genuinely click-through.
        # The interior itself is sized off region_w/region_h alone, never
        # off the chip, so a long value can never shrink it. It's offset
        # by PAD to land in the same place on screen as before -- PAD
        # shifted the whole widget's origin, not the frame's true position.
        inset = max(BORDER, HANDLE // 2)
        full = QRegion(0, 0, total_w, total_h)
        interior = QRegion(
            PAD + inset, PAD + inset, max(self.region_w - 2 * inset, 0), max(self.region_h - 2 * inset, 0)
        )
        self.setMask(full.subtracted(interior))

        self.value_chip.move(chip_x, chip_y)
        self.update()

    def _style_chip(self) -> None:
        # Locked: this region's own identity color, matching its row's
        # swatch in the app window. Lost: always the shared alert color,
        # dashed -- never this region's own color, which could itself be
        # red-ish and mask the "something's wrong" signal (see paintEvent).
        border = f"1px solid {self.color.name()}" if self.locked else f"1px dashed {COLOR_LOST.name()}"
        self.value_chip.setStyleSheet(
            f"background-color: {CHIP_BG}; color: {VALUE_TEXT_COLOR.name()};"
            f" border: {border}; border-radius: 4px; padding: 1px 6px;"
        )

    # -- drawing --------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.region_w, self.region_h
        # Everything below is drawn in the FRAME's own coordinate space
        # (0,0 at the frame's true top-left), then shifted by PAD in one
        # place -- PAD is the room reserved around the frame so every
        # corner handle paints in full instead of getting clipped by the
        # widget's own edges (see PAD's own comment).
        painter.translate(PAD, PAD)

        # Same reasoning as _style_chip: locked draws in this region's own
        # color; lost always draws in the shared alert color AND switches
        # to a dashed line, so the two states stay visually distinct even
        # when a region's own color happens to be close to COLOR_LOST's.
        if self.locked:
            pen = QPen(self.color, BORDER)
        else:
            pen = QPen(COLOR_LOST, BORDER)
            pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(BORDER // 2, BORDER // 2, w - BORDER, h - BORDER, CORNER_RADIUS, CORNER_RADIUS)

        painter.setPen(Qt.NoPen)
        painter.setBrush(self.color if self.locked else COLOR_LOST)
        for cx, cy in [(0, 0), (w, 0), (0, h), (w, h)]:
            painter.drawRoundedRect(cx - HANDLE // 2, cy - HANDLE // 2, HANDLE, HANDLE, 3, 3)

    def set_lines(self, lines: list) -> None:
        """Update the recognized text. The app window's row (and
        formula.compute(), via _watcher_keys) still get one " | "-joined
        string either way -- only the floating chip shows every detected
        line on its own row, same as the old strip did."""
        self._lines = lines or ["--"]
        joined = " | ".join(t or "--" for t in self._lines)
        self.value_edit.setText(joined)
        self.value_changed.emit(joined)
        self._refresh_chip()

    def _refresh_chip(self) -> None:
        """Renders the chip's text -- the region's name (muted, so it
        reads as a label) above every currently recognized line (bright,
        one per row, same green the old strip used)."""
        value_html = "<br>".join(html.escape(t or "--") for t in self._lines)
        self.value_chip.setText(
            f"<span style='color:{NAME_TEXT_COLOR};'>{html.escape(self.name)}</span><br>"
            f"<span style='color:{VALUE_TEXT_COLOR.name()};'>{value_html}</span>"
        )
        self._resize_and_reposition_chip()

    def set_ocr_loading(self, loading: bool) -> None:
        """Toggles the pulsing glow that marks this chip's value as a
        placeholder while the OCR model is still loading in the background
        (see App._load_recognizer_async / _on_recognizer_ready)."""
        if loading:
            start_pulsing_glow(self.value_chip)
        else:
            stop_pulsing_glow(self.value_chip)

    def set_locked(self, locked: bool) -> None:
        if locked != self.locked:
            self.locked = locked
            self._style_chip()
            self.update()
            self.locked_changed.emit(locked)

    # -- mouse: move / resize / close ------------------------------------
    def _corner_at(self, x: int, y: int) -> str | None:
        """x, y are raw widget-local mouse coordinates -- shift back into
        the frame's own coordinate space (see paintEvent's PAD translate)
        before comparing against region_w/region_h."""
        x, y = x - PAD, y - PAD
        w, h = self.region_w, self.region_h
        near_left, near_right = 0 <= x <= HANDLE, w - HANDLE <= x <= w
        near_top, near_bottom = 0 <= y <= HANDLE, h - HANDLE <= y <= h
        if near_top and near_left:
            return "nw"
        if near_top and near_right:
            return "ne"
        if near_bottom and near_left:
            return "sw"
        if near_bottom and near_right:
            return "se"
        return None

    # Nothing but the value chip is a child widget any more, and it's
    # transparent to mouse events -- so every click anywhere on this
    # widget now reaches these handlers directly.
    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        pos = event.position().toPoint()
        corner = self._corner_at(pos.x(), pos.y())
        if corner:
            self._resize_edge = corner
            self._resize_origin = (event.globalPosition().toPoint(), self.left, self.top, self.region_w, self.region_h)
            self._moved_or_resized = False
            return
        self._drag_origin = (event.globalPosition().toPoint(), self.left, self.top)
        self._moved_or_resized = False

    def mouseMoveEvent(self, event) -> None:
        if self._resize_edge:
            gpos = event.globalPosition().toPoint()
            origin, oleft, otop, ow, oh = self._resize_origin
            dx, dy = gpos.x() - origin.x(), gpos.y() - origin.y()
            left, top, rw, rh = oleft, otop, ow, oh
            edge = self._resize_edge
            if "n" in edge:
                top, rh = otop + dy, oh - dy
            if "s" in edge:
                rh = oh + dy
            if "w" in edge:
                left, rw = oleft + dx, ow - dx
            if "e" in edge:
                rw = ow + dx
            self.left, self.top = left, top
            self.region_w, self.region_h = max(rw, MIN_SIZE), max(rh, MIN_SIZE)
            self._apply_geometry()
            self._moved_or_resized = True
        elif self._drag_origin:
            gpos = event.globalPosition().toPoint()
            origin, oleft, otop = self._drag_origin
            dx, dy = gpos.x() - origin.x(), gpos.y() - origin.y()
            self.left, self.top = oleft + dx, otop + dy
            self._apply_geometry()
            self._moved_or_resized = True

    def mouseReleaseEvent(self, event) -> None:
        self._drag_origin = None
        self._resize_edge = None
        self._resize_origin = None
        if self._moved_or_resized:
            self._moved_or_resized = False
            if self._on_change:
                self._on_change(self)

    def _close(self) -> None:
        if self._on_close:
            self._on_close(self)
        self.close()
        self.deleteLater()  # schedules real C++ destruction -- close() alone only hides it,
        # which would leave is_alive() returning True forever for a target
        # still referenced by a saved event step
