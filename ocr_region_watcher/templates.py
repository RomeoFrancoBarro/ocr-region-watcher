"""Persisted named snapshots of the live setup (regions, manual inputs,
targets) -- lets you save a full calibrated layout per site/config and
switch between them with one click, instead of re-dragging regions and
retyping values every time.

Pure Python, no Qt dependency -- ocr_region_watcher/qt/app.py is the only
caller today, but this module doesn't know that.
"""
from __future__ import annotations

import json
from pathlib import Path

DEFAULT_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "templates.json"


def empty_snapshot() -> dict:
    """The shape every saved template's contents follow -- also the
    baseline an unsaved-but-active (never-yet-saved) template is compared
    against for the "unsaved changes" check."""
    return {"regions": [], "manual_inputs": [], "targets": []}


class TemplateStore:
    """Loads/saves a dict of {name: snapshot} plus which one was last
    active, to a single JSON file. Tolerates a missing or corrupt file --
    falls back to an empty store rather than raising, since losing this
    file should never crash the app on startup."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_DATA_PATH
        self._templates: dict[str, dict] = {}
        self._last_active: str | None = None
        self.load()

    def load(self) -> None:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            self._templates, self._last_active = {}, None
            return
        try:
            data = json.loads(raw)
            self._templates = dict(data.get("templates", {}))
            self._last_active = data.get("last_active")
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError):
            self._templates, self._last_active = {}, None

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"templates": self._templates, "last_active": self._last_active}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def names(self) -> list[str]:
        return list(self._templates.keys())

    def get(self, name: str) -> dict | None:
        return self._templates.get(name)

    def save(self, name: str, snapshot: dict) -> None:
        self._templates[name] = snapshot
        self._write()

    def delete(self, name: str) -> None:
        self._templates.pop(name, None)
        if self._last_active == name:
            self._last_active = None
        self._write()

    def rename(self, old: str, new: str) -> None:
        if old not in self._templates or old == new:
            return
        self._templates[new] = self._templates.pop(old)
        if self._last_active == old:
            self._last_active = new
        self._write()

    def get_last_active(self) -> str | None:
        return self._last_active

    def set_last_active(self, name: str | None) -> None:
        self._last_active = name
        self._write()

    def next_default_name(self) -> str:
        n = 1
        while f"Template {n}" in self._templates:
            n += 1
        return f"Template {n}"
