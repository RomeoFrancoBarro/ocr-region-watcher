# Templates (per-site saved layouts) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user save the entire live setup (regions, manual inputs, targets) under a named template, and restore it with one click from a new "Templates" tab, instead of re-dragging regions and retyping values every time they switch between the sites they monitor.

**Architecture:** A new UI-agnostic `ocr_region_watcher/templates.py` module (`TemplateStore`) persists a `{name: snapshot}` dict plus `last_active` to `data/templates.json`. The Qt app (`ocr_region_watcher/qt/app.py`) gets a third "Templates" tab, plus `_capture_snapshot`/`_restore_snapshot`/`_teardown_live_state` methods that serialize the live `watchers`/`manual_inputs`/`targets` lists to/from that store's schema, reusing the region/target creation paths (refactored into `_create_region`/`_create_target` first) and the widgets' existing close/remove methods for teardown.

**Tech Stack:** PySide6 (already a dependency), stdlib `json`/`pathlib`/`unittest` (no new dependencies).

**Spec:** `docs/superpowers/specs/2026-08-22-templates-design.md`

## Global Constraints

- No new dependencies — `TemplateStore`'s tests use stdlib `unittest`, not pytest.
- `data/templates.json` is gitignored — no user's saved templates are ever committed.
- Tkinter is retired; `ocr_region_watcher/qt/app.py` is the only UI file in scope.
- A restored region/target gets its `formula_key`/`value_key`/toggle values directly from the saved snapshot fields — never re-derived from creation order or position.
- No separate template-editor UI — editing a template's contents means: load it, use the existing region/target/manual-input add or remove controls, then Save again.
- Switching templates tears down and rebuilds the *entire* live state (regions, manual inputs, targets) — there is no partial/merge restore.
- No confirmation prompt on app close — only switching templates with unsaved changes warns.

---

## Task 1: `TemplateStore` — persisted `{name: snapshot}` store

**Files:**
- Create: `ocr_region_watcher/templates.py`
- Create: `tests/test_templates.py`
- Create: `tests/__init__.py` (empty)
- Modify: `.gitignore`

**Interfaces:**
- Produces: `ocr_region_watcher.templates.empty_snapshot() -> dict` (shape: `{"regions": [], "manual_inputs": [], "targets": []}`); `ocr_region_watcher.templates.TemplateStore(path: Path | str | None = None)` with methods `names() -> list[str]`, `get(name: str) -> dict | None`, `save(name: str, snapshot: dict) -> None`, `delete(name: str) -> None`, `rename(old: str, new: str) -> None`, `get_last_active() -> str | None`, `set_last_active(name: str | None) -> None`, `next_default_name() -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/__init__.py` (empty file).

Create `tests/test_templates.py`:

```python
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from ocr_region_watcher.templates import TemplateStore, empty_snapshot


class TemplateStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.path = Path(self._tmp.name) / "templates.json"

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_file_starts_empty(self):
        store = TemplateStore(self.path)
        self.assertEqual(store.names(), [])
        self.assertIsNone(store.get_last_active())

    def test_corrupt_file_falls_back_to_empty(self):
        self.path.write_text("not valid json{{{", encoding="utf-8")
        store = TemplateStore(self.path)
        self.assertEqual(store.names(), [])

    def test_save_then_get_round_trips(self):
        store = TemplateStore(self.path)
        snapshot = {
            "regions": [{"name": "Red", "formula_key": "PM", "left": 1, "top": 2, "width": 3, "height": 4}],
            "manual_inputs": [{"name": "C", "value": "5"}],
            "targets": [],
        }
        store.save("Template 1", snapshot)
        self.assertEqual(store.get("Template 1"), snapshot)
        # a fresh instance reading the same path sees it too
        reloaded = TemplateStore(self.path)
        self.assertEqual(reloaded.get("Template 1"), snapshot)

    def test_get_unknown_name_returns_none(self):
        store = TemplateStore(self.path)
        self.assertIsNone(store.get("nope"))

    def test_delete_removes_it_and_clears_last_active_if_it_matched(self):
        store = TemplateStore(self.path)
        store.save("Template 1", empty_snapshot())
        store.set_last_active("Template 1")
        store.delete("Template 1")
        self.assertNotIn("Template 1", store.names())
        self.assertIsNone(store.get_last_active())

    def test_delete_unrelated_template_leaves_last_active_alone(self):
        store = TemplateStore(self.path)
        store.save("Template 1", empty_snapshot())
        store.save("Template 2", empty_snapshot())
        store.set_last_active("Template 1")
        store.delete("Template 2")
        self.assertEqual(store.get_last_active(), "Template 1")

    def test_rename_moves_snapshot_and_updates_last_active(self):
        store = TemplateStore(self.path)
        store.save("Template 1", empty_snapshot())
        store.set_last_active("Template 1")
        store.rename("Template 1", "Site A")
        self.assertIsNone(store.get("Template 1"))
        self.assertEqual(store.get("Site A"), empty_snapshot())
        self.assertEqual(store.get_last_active(), "Site A")

    def test_next_default_name_skips_taken_numbers(self):
        store = TemplateStore(self.path)
        store.save("Template 1", empty_snapshot())
        store.save("Template 2", empty_snapshot())
        self.assertEqual(store.next_default_name(), "Template 3")

    def test_next_default_name_fills_a_gap(self):
        store = TemplateStore(self.path)
        store.save("Template 2", empty_snapshot())
        self.assertEqual(store.next_default_name(), "Template 1")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_templates -v` (from the project root)
Expected: FAIL/ERROR — `ModuleNotFoundError: No module named 'ocr_region_watcher.templates'`

- [ ] **Step 3: Write the implementation**

Create `ocr_region_watcher/templates.py`:

```python
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
        except (json.JSONDecodeError, AttributeError):
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_templates -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Gitignore the data file**

Add to `.gitignore` (alongside the existing entries):

```
# Saved templates (per-user data, not source)
data/
```

- [ ] **Step 6: Commit**

```bash
git add ocr_region_watcher/templates.py tests/ .gitignore
git commit -m "Add TemplateStore: persisted per-site snapshots"
```

---

## Task 2: Refactor region/target creation into reusable helpers

**Files:**
- Modify: `ocr_region_watcher/qt/app.py`

**Interfaces:**
- Consumes: `RegionWatcher(left, top, width, height, name, on_close, on_change=None, formula_key=None)` (existing); `TargetMarker(x, y, name, on_close, on_change=None)` (existing, `.number`/`.value_key`/`.click_enabled`/`.paste_enabled` settable after construction).
- Produces: `App._create_region(left: int, top: int, width: int, height: int, name: str, formula_key: str | None) -> RegionWatcher`; `App._create_target(x: int, y: int, name: str, number: int, *, value_key: str | None = None, click_enabled: bool = True, paste_enabled: bool = True) -> TargetMarker`. Task 3's template restore calls both directly.

This is a behavior-preserving refactor for the existing live "+ Add Region"/"+ Add Target" flows, plus one real bug fix: `_add_target_row`'s click/paste checkboxes are currently hardcoded to `setChecked(True)` regardless of the target's actual `click_enabled`/`paste_enabled` — harmless today (every live-created target defaults both to `True` anyway), but wrong once Task 3 restores a target whose saved toggles were `False`. No automated test exists for this Qt file (none exists anywhere in this project yet — see the spec's Testing section); verify by running the app.

- [ ] **Step 1: Extract `_create_region` out of `_on_add_region`**

In `ocr_region_watcher/qt/app.py`, replace the existing `_on_add_region` method:

```python
    def _on_add_region(self) -> None:
        # Deliberately not hiding this window first -- the overlay covers
        # the whole virtual screen and stays on top regardless, and
        # hiding it would momentarily give up this process's
        # foreground-owner status right as the overlay needs it: Windows
        # lets a process freely hand focus to its own other windows only
        # while it still owns the foreground, and denies/ignores that
        # request otherwise (same restriction chased down earlier in this
        # project's Tkinter automation code).
        rect = snip_region()
        if rect is None:
            return

        region_index = self._next_id
        left, top, width, height = rect
        name = self._next_region_name()
        watcher = RegionWatcher(
            left, top, width, height, name,
            on_close=self._on_watcher_closed, on_change=self._on_watcher_changed,
            formula_key=self._PAIRED_FORMULA_KEYS.get(region_index),
        )
        watcher.show()
        self._resample(watcher)
        self.watchers.append(watcher)
        self._add_region_row(watcher)
        if self.recognizer is None:
            watcher.set_lines(["loading OCR..."])
        paired_name = self._PAIRED_MANUAL_INPUTS.get(region_index)
        if paired_name is not None:
            self._add_manual_input(paired_name)
        self._update_status()
```

with:

```python
    def _on_add_region(self) -> None:
        # Deliberately not hiding this window first -- the overlay covers
        # the whole virtual screen and stays on top regardless, and
        # hiding it would momentarily give up this process's
        # foreground-owner status right as the overlay needs it: Windows
        # lets a process freely hand focus to its own other windows only
        # while it still owns the foreground, and denies/ignores that
        # request otherwise (same restriction chased down earlier in this
        # project's Tkinter automation code).
        rect = snip_region()
        if rect is None:
            return

        region_index = self._next_id
        left, top, width, height = rect
        name = self._next_region_name()
        self._create_region(left, top, width, height, name, self._PAIRED_FORMULA_KEYS.get(region_index))
        paired_name = self._PAIRED_MANUAL_INPUTS.get(region_index)
        if paired_name is not None:
            self._add_manual_input(paired_name)
        self._update_status()

    def _create_region(
        self, left: int, top: int, width: int, height: int, name: str, formula_key: str | None,
    ) -> RegionWatcher:
        """Build, show, and register a region watcher from explicit
        parameters -- shared by live '+ Add Region' drags (formula_key
        inferred from creation order via _PAIRED_FORMULA_KEYS, above in
        _on_add_region) and template restores (formula_key taken directly
        from the saved snapshot, see _restore_snapshot)."""
        watcher = RegionWatcher(
            left, top, width, height, name,
            on_close=self._on_watcher_closed, on_change=self._on_watcher_changed,
            formula_key=formula_key,
        )
        watcher.show()
        self._resample(watcher)
        self.watchers.append(watcher)
        self._add_region_row(watcher)
        if self.recognizer is None:
            watcher.set_lines(["loading OCR..."])
        return watcher
```

- [ ] **Step 2: Extract `_create_target` out of `_on_add_target`, and fix the checkbox init bug**

Replace the existing `_on_add_target` method:

```python
    def _on_add_target(self) -> None:
        point = snip_point()  # see _on_add_region for why this window isn't hidden first
        if point is None:
            return

        target_index = self._next_target_id
        x, y = point
        name = self._next_target_name()
        target = TargetMarker(x, y, name, on_close=self._on_target_closed, on_change=self._on_target_changed)
        target.number = target_index
        target.show()
        self.targets.append(target)
        self._add_target_row(target)
```

with:

```python
    def _on_add_target(self) -> None:
        point = snip_point()  # see _on_add_region for why this window isn't hidden first
        if point is None:
            return

        target_index = self._next_target_id
        x, y = point
        name = self._next_target_name()
        self._create_target(x, y, name, target_index)

    def _create_target(
        self, x: int, y: int, name: str, number: int, *,
        value_key: str | None = None, click_enabled: bool = True, paste_enabled: bool = True,
    ) -> TargetMarker:
        """Build, show, and register a target marker from explicit
        parameters -- shared by live '+ Add Target' clicks (defaults: no
        paste key yet, both toggles on) and template restores (all four
        taken directly from the saved snapshot, see _restore_snapshot)."""
        target = TargetMarker(x, y, name, on_close=self._on_target_closed, on_change=self._on_target_changed)
        target.number = number
        target.value_key = value_key
        target.click_enabled = click_enabled
        target.paste_enabled = paste_enabled
        target.show()
        self.targets.append(target)
        self._add_target_row(target)
        return target
```

Then, in `_add_target_row`, fix the two hardcoded checkbox states. Change:

```python
        click_check = QCheckBox("click")
        click_check.setChecked(True)
```

to:

```python
        click_check = QCheckBox("click")
        click_check.setChecked(target.click_enabled)
```

and change:

```python
        paste_check = QCheckBox("paste")
        paste_check.setChecked(True)
```

to:

```python
        paste_check = QCheckBox("paste")
        paste_check.setChecked(target.paste_enabled)
```

(`_create_target` already sets `target.click_enabled`/`target.paste_enabled` before calling `_add_target_row`, so this now reflects whatever was actually requested instead of always defaulting to checked.)

- [ ] **Step 3: Verify by running the app**

Run: `python -m ocr_region_watcher.qt_main`
- Click **+ Add Region** twice, dragging over two different bits of on-screen text. Confirm: both regions appear, default-named "Red" then "Blue", both start reading live text, and a "Budget" manual input auto-appeared alongside the first one (the existing PM/PW/Budget pairing convenience still fires).
- Click **+ Add Target**, click anywhere on screen. Confirm the crosshair marker appears, and its row's "click"/"paste" checkboxes are both checked (matching the pre-refactor default).
- Close the app (no crash on exit).

- [ ] **Step 4: Commit**

```bash
git add ocr_region_watcher/qt/app.py
git commit -m "Refactor region/target creation into reusable _create_region/_create_target"
```

---

## Task 3: Templates tab — Add, Save, Switch, capture/restore

**Files:**
- Modify: `ocr_region_watcher/qt/app.py`

**Interfaces:**
- Consumes: `TemplateStore` from Task 1 (`names`, `get`, `save`, `set_last_active`, `next_default_name`); `App._create_region`/`App._create_target` from Task 2.
- Produces: `App._capture_snapshot() -> dict`; `App._teardown_live_state() -> None`; `App._restore_snapshot(data: dict) -> None`; `App._load_template(name: str) -> None`; `App.active_template: str | None`; `App.template_store: TemplateStore`. Tasks 4-6 build on all of these.

- [ ] **Step 1: Import `TemplateStore` and wire it into `App.__init__`**

In `ocr_region_watcher/qt/app.py`, add to the existing import block:

```python
from ..templates import TemplateStore
```

In `App.__init__`, change:

```python
        self._last_result: dict = {}
        self.sequencer = EventSequencer(fire=self._on_send_target, on_status=self._on_event_status)

        self._build_ui()
        self._add_manual_input(self._next_manual_input_name())  # C is needed by every run of this formula -- present from launch
        self._load_recognizer_async()
```

to:

```python
        self._last_result: dict = {}
        self.template_store = TemplateStore()
        self.active_template: str | None = None
        self.sequencer = EventSequencer(fire=self._on_send_target, on_status=self._on_event_status)

        self._build_ui()
        self._refresh_templates_tab()
        self._add_manual_input(self._next_manual_input_name())  # C is needed by every run of this formula -- present from launch
        self._load_recognizer_async()
```

- [ ] **Step 2: Add the third "Templates" tab**

Change `_build_ui`:

```python
        events_content = QWidget()
        self._build_events_tab(events_content)
        tabs.addTab(events_content, "Events")
```

to:

```python
        events_content = QWidget()
        self._build_events_tab(events_content)
        tabs.addTab(events_content, "Events")

        templates_content = QWidget()
        self._build_templates_tab(templates_content)
        tabs.addTab(templates_content, "Templates")
```

Add a new `_build_templates_tab` method right after `_build_events_tab`:

```python
    def _build_templates_tab(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        hint = QLabel(
            "Save the whole live setup (regions, manual inputs, targets) under "
            "a name, and switch between saved setups with one click."
        )
        hint.setStyleSheet("color: #949ba4;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        add_template_btn = QPushButton("+ Add Template")
        add_template_btn.setObjectName("accent")
        add_template_btn.clicked.connect(self._on_add_template)
        layout.addWidget(add_template_btn)

        template_scroll = QScrollArea()
        template_scroll.setWidgetResizable(True)
        template_content = QWidget()
        self.template_rows_layout = QVBoxLayout(template_content)
        self.template_rows_layout.setSpacing(6)
        self.template_rows_layout.addStretch(1)
        template_scroll.setWidget(template_content)
        layout.addWidget(template_scroll, 1)

        self.template_status_label = QLabel("")
        self.template_status_label.setProperty("role", "status")
        self.template_status_label.setWordWrap(True)
        layout.addWidget(self.template_status_label)
```

- [ ] **Step 3: Make `_add_manual_input` return the entry it created**

Change:

```python
    def _add_manual_input(self, name: str) -> None:
        entry = ManualInput(name, on_remove=self._on_manual_input_removed)
        self.manual_inputs.append(entry)
        self.manual_inputs_layout.addWidget(entry)
```

to:

```python
    def _add_manual_input(self, name: str) -> ManualInput:
        entry = ManualInput(name, on_remove=self._on_manual_input_removed)
        self.manual_inputs.append(entry)
        self.manual_inputs_layout.addWidget(entry)
        return entry
```

- [ ] **Step 4: Add snapshot capture, teardown, and restore**

Add a new section after `_on_target_changed` (right before `_click_target`):

```python
    # -- templates: saved snapshots of the whole live setup ---------------
    def _capture_snapshot(self) -> dict:
        return {
            "regions": [
                {
                    "name": w.name, "formula_key": w.formula_key,
                    "left": w.left, "top": w.top, "width": w.region_w, "height": w.region_h,
                }
                for w in self.watchers
            ],
            "manual_inputs": [
                {"name": m.name, "value": m.value_edit.text()} for m in self.manual_inputs
            ],
            "targets": [
                {
                    "name": t.name, "x": t.x, "y": t.y, "value_key": t.value_key,
                    "click_enabled": t.click_enabled, "paste_enabled": t.paste_enabled,
                }
                for t in self.targets
            ],
        }

    def _teardown_live_state(self) -> None:
        """Removes every live region/target/manual input via their own
        existing close/remove methods (not the bare Qt .close() used by
        closeEvent below -- those also clean up self.watchers/targets/
        manual_inputs and their rows, which matters here since the app
        keeps running afterward)."""
        for watcher in list(self.watchers):
            watcher._close()
        for target in list(self.targets):
            target._close()
        for entry in list(self.manual_inputs):
            entry._remove()

    def _restore_snapshot(self, data: dict) -> None:
        for r in data.get("regions", []):
            self._create_region(r["left"], r["top"], r["width"], r["height"], r["name"], r.get("formula_key"))
        # Regions restored above never call _next_region_name(), so
        # _next_id wouldn't otherwise advance -- without this, a
        # subsequently live-dragged region would reuse "Red"/"Blue" and
        # accidentally re-trigger the PM/PW/Budget auto-pairing convenience
        # meant for the first two regions of a fresh session.
        self._next_id = len(self.watchers) + 1

        for m in data.get("manual_inputs", []):
            entry = self._add_manual_input(m["name"])
            entry.value_edit.setText(m.get("value", ""))

        for t in data.get("targets", []):
            number = self._next_target_id
            self._next_target_id += 1
            self._create_target(
                t["x"], t["y"], t["name"], number,
                value_key=t.get("value_key"),
                click_enabled=t.get("click_enabled", True),
                paste_enabled=t.get("paste_enabled", True),
            )
        self._update_status()
```

- [ ] **Step 5: Add Add/Switch/Save handlers and the row-building/refresh logic**

Add right after the code from Step 4:

```python
    def _on_add_template(self) -> None:
        self._teardown_live_state()
        self.active_template = self.template_store.next_default_name()
        self._refresh_templates_tab()

    def _on_template_clicked(self, name: str) -> None:
        self._load_template(name)

    def _load_template(self, name: str) -> None:
        data = self.template_store.get(name)
        if data is None:
            return
        self._teardown_live_state()
        self._restore_snapshot(data)
        self.active_template = name
        try:
            self.template_store.set_last_active(name)
        except OSError as exc:
            self.template_status_label.setText(f"loaded '{name}', but couldn't remember it for next launch: {exc}")
        self._refresh_templates_tab()

    def _on_template_save(self, name: str) -> None:
        if name != self.active_template:
            return
        try:
            self.template_store.save(name, self._capture_snapshot())
            self.template_store.set_last_active(name)
        except OSError as exc:
            self.template_status_label.setText(f"couldn't save '{name}': {exc}")
            return
        self.template_status_label.setText(f"saved '{name}'")
        self._refresh_templates_tab()

    def _on_template_delete(self, name: str) -> None:
        try:
            self.template_store.delete(name)
        except OSError as exc:
            self.template_status_label.setText(f"couldn't delete '{name}': {exc}")
            return
        if name == self.active_template:
            self.active_template = None
        self._refresh_templates_tab()

    def _on_template_renamed(self, old_name: str, name_edit: QLineEdit) -> None:
        new_name = name_edit.text().strip()
        if not new_name or new_name == old_name:
            name_edit.setText(old_name)
            return
        try:
            self.template_store.rename(old_name, new_name)
        except OSError as exc:
            name_edit.setText(old_name)
            self.template_status_label.setText(f"couldn't rename: {exc}")
            return
        if self.active_template == old_name:
            self.active_template = new_name
        self._refresh_templates_tab()

    def _refresh_templates_tab(self) -> None:
        while self.template_rows_layout.count() > 1:  # leave the trailing stretch alone
            item = self.template_rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        names = list(self.template_store.names())
        if self.active_template is not None and self.active_template not in names:
            names.append(self.active_template)  # a just-added, not-yet-saved template

        for index, name in enumerate(names):
            self.template_rows_layout.insertWidget(index, self._build_template_row(name))

    def _build_template_row(self, name: str) -> QFrame:
        is_active = name == self.active_template
        row = QFrame()
        row.setProperty("role", "card")
        if is_active:
            row.setStyleSheet("border: 1px solid #5865f2;")
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(8, 6, 8, 6)
        row_layout.setSpacing(6)

        name_edit = QLineEdit(name)
        name_edit.setFont(MONO_SMALL)
        row_layout.addWidget(name_edit, 1)
        name_edit.editingFinished.connect(lambda n=name, e=name_edit: self._on_template_renamed(n, e))

        switch_btn = QPushButton("Active" if is_active else "Switch")
        switch_btn.setEnabled(not is_active)
        switch_btn.clicked.connect(lambda checked=False, n=name: self._on_template_clicked(n))
        row_layout.addWidget(switch_btn)

        save_btn = QPushButton("Save")
        save_btn.setEnabled(is_active)
        save_btn.clicked.connect(lambda checked=False, n=name: self._on_template_save(n))
        row_layout.addWidget(save_btn)

        delete_btn = QPushButton("Delete")
        delete_btn.setObjectName("danger")
        delete_btn.clicked.connect(lambda checked=False, n=name: self._on_template_delete(n))
        row_layout.addWidget(delete_btn)

        return row
```

- [ ] **Step 6: Verify by running the app**

Run: `python -m ocr_region_watcher.qt_main`
1. Confirm a third **Templates** tab exists.
2. On the Main tab: drag one region over some on-screen text, click **+ Add Target** somewhere, type `5` into the "C" manual input.
3. Go to **Templates**, click **+ Add Template**. Confirm: a "Template 1" row appears, highlighted, with an enabled **Save** and a disabled **Active** button (not "Switch").
4. Click **Save**. Confirm no crash, and `data/templates.json` now exists and contains that region/target/"C"=5.
5. Click **+ Add Template** again. Confirm every live region/target overlay disappears from the screen, and "Template 2" appears active.
6. Click **Switch** on the "Template 1" row. Confirm the region reappears at the same screen position, the target crosshair reappears at the same point, and "C" shows `5` again.

- [ ] **Step 7: Commit**

```bash
git add ocr_region_watcher/qt/app.py
git commit -m "Add Templates tab: Add/Save/Switch with full snapshot capture and restore"
```

---

## Task 4: Delete confirmation and rename duplicate rejection

**Files:**
- Modify: `ocr_region_watcher/qt/app.py`

**Interfaces:**
- Consumes: `App._on_template_delete`/`App._on_template_renamed` from Task 3.

- [ ] **Step 1: Add the `QMessageBox` import**

Add `QMessageBox` to the existing `PySide6.QtWidgets` import block in `ocr_region_watcher/qt/app.py`.

- [ ] **Step 2: Confirm before deleting**

Change:

```python
    def _on_template_delete(self, name: str) -> None:
        try:
            self.template_store.delete(name)
        except OSError as exc:
            self.template_status_label.setText(f"couldn't delete '{name}': {exc}")
            return
        if name == self.active_template:
            self.active_template = None
        self._refresh_templates_tab()
```

to:

```python
    def _on_template_delete(self, name: str) -> None:
        reply = QMessageBox.question(
            self, "Delete template",
            f"Delete '{name}'? Can't be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        try:
            self.template_store.delete(name)
        except OSError as exc:
            self.template_status_label.setText(f"couldn't delete '{name}': {exc}")
            return
        if name == self.active_template:
            self.active_template = None
        self._refresh_templates_tab()
```

- [ ] **Step 3: Reject renaming to a name already in use**

Change:

```python
    def _on_template_renamed(self, old_name: str, name_edit: QLineEdit) -> None:
        new_name = name_edit.text().strip()
        if not new_name or new_name == old_name:
            name_edit.setText(old_name)
            return
        try:
            self.template_store.rename(old_name, new_name)
        except OSError as exc:
            name_edit.setText(old_name)
            self.template_status_label.setText(f"couldn't rename: {exc}")
            return
        if self.active_template == old_name:
            self.active_template = new_name
        self._refresh_templates_tab()
```

to:

```python
    def _on_template_renamed(self, old_name: str, name_edit: QLineEdit) -> None:
        new_name = name_edit.text().strip()
        if not new_name or new_name == old_name:
            name_edit.setText(old_name)
            return
        if new_name in self.template_store.names():
            name_edit.setText(old_name)  # reject duplicate, revert
            return
        try:
            self.template_store.rename(old_name, new_name)
        except OSError as exc:
            name_edit.setText(old_name)
            self.template_status_label.setText(f"couldn't rename: {exc}")
            return
        if self.active_template == old_name:
            self.active_template = new_name
        self._refresh_templates_tab()
```

- [ ] **Step 4: Verify by running the app**

Run: `python -m ocr_region_watcher.qt_main`
1. With two saved templates ("Template 1", "Template 2"), click **Delete** on one. Confirm a dialog appears; click **No** — confirm it's still there. Click **Delete** again, click **Yes** — confirm the row disappears.
2. Rename the remaining template to the exact name of a fresh `+ Add Template` row before saving it. Confirm the rename reverts to the old name instead of applying (check both directions: renaming to another *saved* template's name, and to another *currently displayed but unsaved* template's name).

- [ ] **Step 5: Commit**

```bash
git add ocr_region_watcher/qt/app.py
git commit -m "Confirm before delete, reject duplicate template names on rename"
```

---

## Task 5: Warn before discarding unsaved changes on switch

**Files:**
- Modify: `ocr_region_watcher/qt/app.py`

**Interfaces:**
- Consumes: `App._capture_snapshot`, `App._load_template` from Task 3; `ocr_region_watcher.templates.empty_snapshot` from Task 1.

- [ ] **Step 1: Import `empty_snapshot`**

Change:

```python
from ..templates import TemplateStore
```

to:

```python
from ..templates import TemplateStore, empty_snapshot
```

- [ ] **Step 2: Check for unsaved changes before switching**

Change:

```python
    def _on_template_clicked(self, name: str) -> None:
        self._load_template(name)
```

to:

```python
    def _on_template_clicked(self, name: str) -> None:
        if self.active_template is not None and self.active_template != name:
            current = self._capture_snapshot()
            baseline = self.template_store.get(self.active_template) or empty_snapshot()
            if current != baseline:
                reply = QMessageBox.question(
                    self, "Unsaved changes",
                    f"'{self.active_template}' has unsaved changes -- discard and switch anyway?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
        self._load_template(name)
```

- [ ] **Step 3: Verify by running the app**

Run: `python -m ocr_region_watcher.qt_main`
1. Switch to a saved template. Drag its region to a new spot (don't Save).
2. Click **Switch** on a different template. Confirm the "unsaved changes" dialog appears.
3. Click **No** — confirm you're still on the moved-but-unswitched template, region still in the moved spot.
4. Click **Switch** again, click **Yes** — confirm it switches, and switching back afterward shows the region at its originally-saved spot (the move was discarded, not saved).
5. Click **+ Add Template** (a fresh, empty, never-saved template) then immediately click **Switch** on an existing template without adding anything. Confirm no warning fires (nothing was actually placed yet, so there's nothing to lose) -- then repeat but drag a region first, and confirm the warning fires this time.

- [ ] **Step 4: Commit**

```bash
git add ocr_region_watcher/qt/app.py
git commit -m "Warn before discarding unsaved template changes on switch"
```

---

## Task 6: Restore `last_active` template on startup

**Files:**
- Modify: `ocr_region_watcher/qt/app.py`

**Interfaces:**
- Consumes: `TemplateStore.get_last_active`, `App._load_template` from earlier tasks.

- [ ] **Step 1: Load the last-active template (if any) instead of always seeding a blank "C"**

Change, in `App.__init__`:

```python
        self._build_ui()
        self._refresh_templates_tab()
        self._add_manual_input(self._next_manual_input_name())  # C is needed by every run of this formula -- present from launch
        self._load_recognizer_async()
```

to:

```python
        self._build_ui()
        last_active = self.template_store.get_last_active()
        if last_active is not None and self.template_store.get(last_active) is not None:
            self._load_template(last_active)
        else:
            self._add_manual_input(self._next_manual_input_name())  # C is needed by every run of this formula -- present from launch
        self._refresh_templates_tab()
        self._load_recognizer_async()
```

(`_load_template` already calls `_refresh_templates_tab()` internally when it runs; the trailing call here is what covers the `else` branch. Calling it twice in the `if` branch is harmless — it just rebuilds the same row widgets again.)

- [ ] **Step 2: Verify by running the app**

1. Run `python -m ocr_region_watcher.qt_main`, switch to a saved template (e.g. "Template 1"), close the app.
2. Run it again. Confirm it launches with "Template 1" already active — its region/target/manual-input values visible immediately, no click needed.
3. Delete `data/templates.json` entirely (or rename the active template's underlying entry away via a text editor) and launch again. Confirm it falls back to the blank single-"C"-input state instead of crashing.

- [ ] **Step 3: Commit**

```bash
git add ocr_region_watcher/qt/app.py
git commit -m "Restore last-active template automatically on startup"
```

---

## Task 7: Document the feature in the README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a "Templates" section**

Add a new section to `README.md` (after the existing "Targets (write-back)" content, before "## Setup"), describing: the Templates tab; that a template is a full snapshot of every region/manual input/target; Add Template / Save / Switch / Delete / renaming inline; that switching discards unsaved changes only after a confirmation prompt; that the last-active template reloads automatically on the next launch; and that saved templates live in `data/templates.json`, which is local to the machine and not committed to the repo.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "Document the Templates feature in the README"
```
