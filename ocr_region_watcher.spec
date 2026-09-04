# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for OCR Region Watcher.

Build with:
    pyinstaller ocr_region_watcher.spec

Produces a one-folder distribution in `dist/OCR Region Watcher/` --
launch `dist/OCR Region Watcher/OCR Region Watcher.exe`. One-folder
(COLLECT), not one-file, is deliberate: torch's CUDA build makes the
bundled payload multi-gigabyte, and a one-file build would re-extract
all of that to a temp directory on every launch instead of once at
build time.

`collect_all` for easyocr and torch pulls in their non-Python data
files (easyocr's character-list/config files; torch's own DLLs) that
PyInstaller's static import scan can't infer on its own -- PySide6 is
covered by PyInstaller's built-in hook and needs no extra handling
here.
"""
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = []
tmp_ret = collect_all('easyocr')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('torch')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['run_app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='OCR Region Watcher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='OCR Region Watcher',
)
