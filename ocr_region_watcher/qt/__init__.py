"""PySide6 UI for OCR Region Watcher -- a parallel, more capable rebuild of the
Tkinter app in ../app.py and friends. Runs via `python -m ocr_region_watcher.qt_main`.

Everything that isn't UI (formula.py, recognize.py, capture.py, inject.py,
colorcheck.py, and the plain-logic parts of events.py) is shared, unchanged,
with the original Tkinter app -- only the widget layer is rebuilt here.
"""
