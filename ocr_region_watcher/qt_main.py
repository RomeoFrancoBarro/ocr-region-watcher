"""Entry point for the PySide6 version of OCR Region Watcher: `python -m ocr_region_watcher.qt_main`.

Runs alongside the original Tkinter app (`python -m ocr_region_watcher.main`) without
conflicting -- nothing about that version is touched or removed. Once
you're happy with this one, the Tkinter files can be retired.
"""
from __future__ import annotations

import signal
import sys

from PySide6.QtWidgets import QApplication

from .qt.app import App
from .qt.style import STYLESHEET


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)

    # Qt's event loop is native C++ -- Python already successfully
    # receives a Ctrl+C's SIGINT (it fires inside whatever callback
    # happens to be running, e.g. the 30ms _cycle timer), but the default
    # behavior -- raising KeyboardInterrupt inside that callback -- just
    # gets caught and printed by PySide6, and the C++ loop keeps going.
    # Replacing the handler to explicitly call quit() instead of raising
    # actually tells Qt's loop to stop, rather than throwing something it
    # happens to swallow.
    signal.signal(signal.SIGINT, lambda *_: app.quit())

    window = App()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
