"""Shared fonts and the app-wide QSS stylesheet -- one place to tune the
look, rather than scattered font/color literals through every widget file.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget

MONO = QFont("Consolas", 9)
MONO_SMALL = QFont("Consolas", 8)
UI_SMALL = QFont("Segoe UI", 8)

GLOW_COLOR = QColor("#5865f2")  # the app's existing accent blue (QPushButton#accent) -- reused here so a pulsing glow reads as "in progress", not an error
_GLOW_MIN_BLUR = 4
_GLOW_MAX_BLUR = 20
_GLOW_DURATION_MS = 1100


def start_pulsing_glow(widget: QWidget, color: QColor = GLOW_COLOR) -> None:
    """Marks `widget` as "loading" with a soft drop-shadow whose blur
    radius breathes in and out forever -- the shared visual for "the OCR
    model is still loading in the background" (see App._load_recognizer_async),
    used on both the panel's status label and a region's floating chip.

    The animation is stashed on the widget itself so callers don't need to
    keep a reference alive; stop_pulsing_glow() tears it back down.
    """
    effect = QGraphicsDropShadowEffect(widget)
    effect.setColor(color)
    effect.setOffset(0, 0)
    effect.setBlurRadius(_GLOW_MIN_BLUR)
    widget.setGraphicsEffect(effect)

    anim = QPropertyAnimation(effect, b"blurRadius", widget)
    anim.setDuration(_GLOW_DURATION_MS)
    anim.setStartValue(_GLOW_MIN_BLUR)
    anim.setKeyValueAt(0.5, _GLOW_MAX_BLUR)
    anim.setEndValue(_GLOW_MIN_BLUR)
    anim.setEasingCurve(QEasingCurve.Type.InOutSine)
    anim.setLoopCount(-1)
    anim.start()
    widget._pulsing_glow_anim = anim  # kept alive by this ref alone -- stop_pulsing_glow() drops it


def stop_pulsing_glow(widget: QWidget) -> None:
    """Undoes start_pulsing_glow(). Harmless if none is active."""
    anim = getattr(widget, "_pulsing_glow_anim", None)
    if anim is not None:
        anim.stop()
        widget._pulsing_glow_anim = None
    widget.setGraphicsEffect(None)

STYLESHEET = """
QWidget {
    background-color: #1e1f22;
    color: #e6e6e6;
    font-family: "Segoe UI";
    font-size: 9pt;
}
QLineEdit, QComboBox {
    background-color: #2b2d31;
    border: 1px solid #3f4147;
    border-radius: 4px;
    padding: 3px 6px;
    color: #e6e6e6;
}
QLineEdit:focus, QComboBox:focus {
    border: 1px solid #5865f2;
}
QLineEdit:read-only {
    color: #949ba4;
}
QPushButton {
    background-color: #2b2d31;
    border: 1px solid #3f4147;
    border-radius: 4px;
    padding: 5px 10px;
    color: #e6e6e6;
}
QPushButton:hover { background-color: #35373c; }
QPushButton:pressed { background-color: #232428; }
QPushButton#accent {
    background-color: #5865f2;
    border: none;
    color: white;
    font-weight: 600;
}
QPushButton#accent:hover { background-color: #4752c4; }
QPushButton#success {
    background-color: #3ba55c;
    border: none;
    color: white;
    font-weight: 600;
}
QPushButton#success:hover { background-color: #339254; }
QPushButton#danger {
    background-color: #2b2d31;
    border: 1px solid #3f4147;
    color: #f04747;
}
QPushButton#danger:hover { background-color: #f04747; color: white; }
QPushButton#flatRemove {
    background: transparent;
    border: none;
    color: #f04747;
    font-weight: bold;
    padding: 2px 6px;
}
QPushButton#flatRemove:hover { background-color: #3a1f1f; border-radius: 3px; }
QCheckBox { spacing: 6px; }
QTabWidget::pane {
    border: 1px solid #3f4147;
    border-radius: 6px;
    top: -1px;
}
QTabBar::tab {
    background: #2b2d31;
    padding: 6px 16px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 2px;
}
QTabBar::tab:selected { background: #5865f2; color: white; }
QTabBar::tab:!selected:hover { background: #35373c; }
QLabel[role="section"] {
    color: #949ba4;
    font-size: 8pt;
    font-weight: 600;
}
QLabel[role="debug"] { color: #5aa9e6; font-family: "Consolas"; }
QLabel[role="status"] { color: #e69138; font-family: "Consolas"; font-size: 8pt; }
QFrame[role="card"] {
    background-color: #26282c;
    border: 1px solid #3f4147;
    border-radius: 6px;
}
QFrame[role="section-box"] {
    background-color: #26282c;
    border: 1px solid #3f4147;
    border-radius: 6px;
}
QFrame[role="row-card"] {
    background-color: #2b2d31;
    border: 1px solid #3f4147;
    border-radius: 4px;
}
"""
