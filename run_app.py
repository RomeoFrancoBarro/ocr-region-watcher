"""PyInstaller entry point.

`ocr_region_watcher/qt_main.py` uses a relative import (it's meant to be
run as `python -m ocr_region_watcher.qt_main`), which fails when
PyInstaller runs it directly as a top-level script -- there's no parent
package for the relative import to resolve against. This wrapper
imports the package properly instead, so the frozen exe gets the same
package-relative import machinery a normal `-m` run gets.
"""
from ocr_region_watcher.qt_main import main

if __name__ == "__main__":
    main()
