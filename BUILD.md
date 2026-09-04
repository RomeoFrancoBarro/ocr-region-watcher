# Building a standalone .exe

Packages the app as a folder you can hand off or double-click into, with
no Python install required on the machine that runs it.

```
pip install pyinstaller
pyinstaller ocr_region_watcher.spec
```

Output goes to `dist/OCR Region Watcher/` -- launch
`OCR Region Watcher.exe` inside that folder (not the plain
`pyinstaller` output alone; the `_internal/` folder next to the exe has
to stay with it).

## Notes

- **One-folder, not one-file.** The installed `torch` build is CUDA-enabled,
  which makes the bundled payload multi-gigabyte. A one-file build would
  re-extract all of that to a temp directory on every launch; one-folder
  only pays that cost once, at build time.
- **First run after building**: EasyOCR needs its model weights, cached to
  the user's home directory the first time the app (in any form) runs on
  that machine -- if you've already run `python -m ocr_region_watcher.qt_main`
  there before, the built exe picks up that same cache automatically.
  Otherwise expect a one-time ~30-60s download on first launch (needs
  internet).
- **Rebuilding after code changes**: re-run `pyinstaller ocr_region_watcher.spec`.
  Delete `build/` and `dist/` first if a rebuild ever looks stale (mismatched
  hidden imports, leftover files from a renamed module).
- No custom icon is wired in yet -- the built exe uses PyInstaller's default.
