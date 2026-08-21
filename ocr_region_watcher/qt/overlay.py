"""Region/point selection overlays.

Both span every monitor (the union of all QScreen geometries, not just the
primary one -- mirrors mss's monitors[0] the original relied on) as a
single frameless, semi-transparent, always-on-top window that blocks (via
a nested QEventLoop, not a real thread) until you finish or press Escape.

No `master` parameter needed here, unlike the Tk version -- Tkinter could
only have one Tk() root at a time, so the overlay had to run as a Toplevel
of the existing app window. Qt's QApplication is a single app-wide object
regardless of how many top-level widgets exist, so this is just another
independent QWidget.
"""
from __future__ import annotations

from PySide6.QtCore import QEventLoop, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QApplication, QLabel, QWidget

_BACKGROUND = QColor(0, 0, 0, 70)


def _virtual_screen_rect() -> QRect:
    """Union of every monitor's geometry."""
    rect = QRect()
    for screen in QGuiApplication.screens():
        rect = rect.united(screen.geometry())
    return rect


class _OverlayBase(QWidget):
    INSTRUCTION = ""

    def __init__(self) -> None:
        # Plain top-level window, NOT Qt.Popup -- Popup's native Windows
        # window class turned out to be "...PopupSaveBits" (CS_SAVEBITS,
        # a style meant for small transient popups that save the pixels
        # underneath for a fast close) even with the drop-shadow variant
        # of it disabled, and win32gui confirmed that auxiliary window
        # -- not this widget's real content -- was what actually ate
        # hit-testing at fullscreen size. Popup's automatic mouse/keyboard
        # grab isn't worth inheriting that. run() grabs input explicitly
        # instead (grabMouse()/grabKeyboard(), Win32's SetCapture()-backed
        # mechanism) -- targeted at just "route all input here", with none
        # of Popup's small-transient-widget assumptions baked in.
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)

        rect = _virtual_screen_rect()
        self.setGeometry(rect)

        label = QLabel(self.INSTRUCTION, self)
        label.setStyleSheet("color: white; background: transparent; font-size: 14pt;")
        label.adjustSize()
        label.move((rect.width() - label.width()) // 2, int(rect.height() * 0.03))

        self.result = None
        self._loop: QEventLoop | None = None

    def paintEvent(self, event) -> None:
        # Explicit, not the QSS "background-color" this used to rely on --
        # a bare QWidget only auto-paints its stylesheet background when
        # nothing overrides paintEvent(); the moment a subclass does (as
        # SnipOverlay needs to, to draw its selection rectangle), that
        # automatic painting stops happening and this window renders as
        # genuinely empty (alpha=0) almost everywhere it isn't explicitly
        # drawn -- which real testing traced to Windows then treating
        # those pixels as click-through, the same "gap where nothing's
        # painted becomes a hole in the window" class of bug chased down
        # for RegionWatcher's mask earlier, just arrived at differently.
        painter = QPainter(self)
        painter.fillRect(self.rect(), _BACKGROUND)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self._finish(None)
        else:
            super().keyPressEvent(event)

    def _finish(self, result) -> None:
        self.result = result
        self.releaseMouse()
        self.releaseKeyboard()
        self.hide()
        if self._loop is not None:
            self._loop.quit()

    def run(self):
        self.show()
        QApplication.processEvents()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.ActiveWindowFocusReason)
        # The explicit grab: forces every mouse/keyboard event to this
        # widget regardless of what's under the cursor or which window
        # the OS considers "active" -- Win32's SetCapture()-backed
        # mechanism, not a foreground-window request.
        self.grabMouse()
        self.grabKeyboard()
        QApplication.processEvents()
        self._loop = QEventLoop()
        self._loop.exec()
        self.close()
        return self.result


class SnipOverlay(_OverlayBase):
    """Fullscreen drag-to-select box, like Windows' Snipping Tool."""

    INSTRUCTION = "Drag a box around the value. Esc to cancel."

    def __init__(self) -> None:
        super().__init__()
        # Deliberately not a separate QRubberBand child widget for the
        # selection rectangle -- one less widget that could end up
        # between the cursor and this overlay's own event handlers.
        # Painted directly in paintEvent() below instead, on the same
        # widget that's actually grabbing the input -- same reasoning
        # RegionWatcher's single canvas uses.
        self._origin: QPoint | None = None
        self._current: QPoint | None = None

    def paintEvent(self, event) -> None:
        super().paintEvent(event)  # the base background fill -- see its comment for why this must run every time
        if self._origin is not None and self._current is not None:
            painter = QPainter(self)
            painter.setPen(QPen(Qt.red, 2))
            painter.drawRect(QRect(self._origin, self._current).normalized())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._origin = event.position().toPoint()
            self._current = self._origin
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if self._origin is not None:
            self._current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton or self._origin is None:
            return
        end = event.position().toPoint()
        origin = self._origin
        self._origin = None
        self._current = None
        self.update()

        left, top = min(origin.x(), end.x()), min(origin.y(), end.y())
        width, height = abs(end.x() - origin.x()), abs(end.y() - origin.y())
        if width < 3 or height < 3:
            return  # accidental click -- keep the overlay open, try again
        global_top_left = self.mapToGlobal(QPoint(left, top))
        self._finish((global_top_left.x(), global_top_left.y(), width, height))


class PointOverlay(_OverlayBase):
    """Fullscreen click-to-place -- unlike SnipOverlay there's nothing to
    read, just a screen point, so a single click places it, no drag."""

    INSTRUCTION = "Click where the target should be. Esc to cancel."

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            global_pos = self.mapToGlobal(event.position().toPoint())
            self._finish((global_pos.x(), global_pos.y()))


def snip_region() -> tuple[int, int, int, int] | None:
    """Show the drag-to-select overlay; return (left, top, width, height),
    or None if cancelled with Esc."""
    return SnipOverlay().run()


def snip_point() -> tuple[int, int] | None:
    """Show the click-to-place overlay; return (x, y), or None if
    cancelled with Esc."""
    return PointOverlay().run()
