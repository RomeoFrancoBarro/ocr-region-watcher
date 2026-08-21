"""Entry point.

Usage:
    python -m ocr_region_watcher.main
"""
from __future__ import annotations

from .app import App


def main() -> None:
    App().run()


if __name__ == "__main__":
    main()
