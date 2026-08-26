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
    return {"regions": [], "manual_inputs": [], "targets": [], "events": [], "loop": False}


def events_snapshot(events: list[dict], targets: list) -> list[dict]:
    """Serializes an EventSequencer's `events` (each holding a *live*
    target object -- see ocr_region_watcher/qt/events.py) into the saved
    shape: {"target_index": <position within the saved "targets" list>,
    "delay": ...}. A live object reference can't survive a JSON round
    trip, but its position in the same list that "targets" itself is
    built from can.

    A step whose target isn't in `targets` any more (removed by hand
    while the step still pointed at it -- the app tolerates this
    mid-edit as a "dangling" step) is dropped rather than saved, since a
    step with no target can't fire on restore either.

    Doesn't import anything Qt-specific -- `targets` only ever needs to
    support `in` and `.index()`, so this is exercised directly in tests
    with plain stand-in objects instead of real TargetMarkers.
    """
    result = []
    for event in events:
        target = event["target"]
        if target not in targets:
            continue
        result.append({"target_index": targets.index(target), "delay": event["delay"]})
    return result


def events_from_snapshot(data: list[dict], targets: list) -> list[dict]:
    """The inverse of events_snapshot() -- rebuilds an EventSequencer's
    `events` against a freshly-restored `targets` list. An entry whose
    target_index is missing, not an int, or out of range for the
    restored targets (hand-edited JSON, or a template whose targets
    section didn't fully restore) is skipped rather than raising."""
    result = []
    for entry in data:
        index = entry.get("target_index")
        if not isinstance(index, int) or isinstance(index, bool) or not (0 <= index < len(targets)):
            continue
        result.append({"target": targets[index], "delay": entry.get("delay", 0)})
    return result


class TemplateStore:
    """Loads/saves a dict of {name: snapshot} plus which one was last
    active, to a single JSON file. Tolerates a missing or corrupt file --
    falls back to an empty store rather than raising, since losing this
    file should never crash the app on startup."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_DATA_PATH
        self._templates: dict[str, dict] = {}
        self._last_active: str | None = None
        # Set by load() when the file exists but couldn't be read or parsed
        # -- not for the ordinary "no file yet" case (a brand-new install),
        # which isn't an error. The app surfaces this once via a status
        # message rather than a blocking dialog, per the "never crash on
        # a corrupt file" contract -- but silently losing every saved
        # template deserves *some* visible sign something went wrong.
        self.load_error: str | None = None
        self.load()

    def load(self) -> None:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self._templates, self._last_active = {}, None
            return
        except OSError as exc:
            self._templates, self._last_active = {}, None
            self.load_error = f"couldn't read {self.path.name}: {exc}"
            return
        try:
            data = json.loads(raw)
            raw_templates = data.get("templates", {})
            if not isinstance(raw_templates, dict):
                raise ValueError("templates value is not a dict")
            # Filter down to exactly {str: dict} here rather than trusting
            # whatever the file held: a bad *member* of an otherwise-valid
            # container (e.g. {"templates": {"A": 5}}) parses fine and only
            # blows up much later, inside the UI's restore path, where this
            # method's own except clause can no longer see it. Same for
            # last_active -- a non-str there is unusable (and, if a list,
            # unhashable) as a lookup key downstream.
            self._templates = {
                name: snapshot for name, snapshot in raw_templates.items()
                if isinstance(name, str) and isinstance(snapshot, dict)
            }
            last_active = data.get("last_active")
            self._last_active = last_active if isinstance(last_active, str) else None
        except (json.JSONDecodeError, AttributeError, TypeError, ValueError) as exc:
            self._templates, self._last_active = {}, None
            self.load_error = f"{self.path.name} is corrupt, ignoring it: {exc}"

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
