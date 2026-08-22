# Templates (per-site saved layouts) — Design

Date: 2026-08-22
Status: approved (pending final spec review)

## Goal

Today, every region, manual input, and target has to be re-dragged and
retyped by hand whenever you switch between the different sites/pages you
monitor. Templates let you save the entire live setup for a given
site under a name, and restore it with one click — no re-calibration.

A **template** is a full snapshot: regions (position/size/name/formula
key), manual inputs (name + typed value), and targets (position/name/
paste-key/click+paste toggles). Loading a template replaces whatever is
currently live; saving overwrites a template with whatever is currently
live. This only exists in the Qt UI (`ocr_region_watcher/qt_main.py`) —
the Tkinter app is retired.

## Out of scope (v1)

- Syncing/sharing templates between machines or users (each user's
  `data/templates.json` is local and gitignored).
- Multiple templates active/visible at once.
- A confirmation prompt on app close for unsaved changes (only switching
  templates warns — see below).
- Undo for delete (a confirm dialog is the only safety net).

## UI: the Templates tab

A third tab alongside Main and Events. Contents:

- **"+ Add Template" button** at the top.
- One row per saved template, each showing:
  - Its name (click to switch/load it; an inline edit field, matching
    how region/target names are already renamed elsewhere in the app,
    to rename it). Renaming persists to storage immediately — it's a
    key change, independent of Save. Renaming to a name that already
    exists is rejected and reverts to the old name, same as the
    existing blank-name rejection pattern manual inputs already use.
  - A **Save** button — enabled only on the currently *active* row (the
    only one whose on-screen state actually corresponds to it).
  - A **Delete** button — enabled on every row regardless of active
    state.
- The active row is visually highlighted (e.g. the `card` style already
  used for target/event rows, with an accent border).

No separate "template editor" view. Editing a template's contents means:
load it, add/remove/move regions or targets or manual inputs using the
controls that already exist for that, then hit Save to persist the
change. This reuses 100% of the existing add/remove/rename UI instead of
building a parallel one.

## Data model

```json
{
  "templates": {
    "Template 1": {
      "regions": [
        {"name": "Red", "formula_key": "PM", "left": 100, "top": 200, "width": 80, "height": 24},
        {"name": "Blue", "formula_key": "PW", "left": 300, "top": 200, "width": 80, "height": 24}
      ],
      "manual_inputs": [
        {"name": "C", "value": "5"},
        {"name": "Budget", "value": "500"}
      ],
      "targets": [
        {"name": "Target 1", "x": 400, "y": 500, "value_key": "M", "click_enabled": true, "paste_enabled": true}
      ]
    }
  },
  "last_active": "Template 1"
}
```

Stored at `data/templates.json` (repo root). This file holds a user's
actual screen coordinates and typed values, so it's **gitignored** —
the feature ships in the repo, saved templates stay local per user.

`last_active` lets the app restore whichever template was loaded last
when it reopens (a plain convenience — no unsaved-changes rule applies
to it, since it's just "what was on screen," not user data at risk). It
only ever points at a name that actually exists in `templates` — set on
Save and on switching to an already-saved template, never on Add
Template alone (see below).

## Modules

**`ocr_region_watcher/templates.py`** (new, shared/UI-agnostic, alongside
`formula.py`/`capture.py`/etc.):
- `TemplateStore` — load/save `data/templates.json` (tolerating a
  missing or corrupt file by falling back to an empty store rather than
  crashing); `names()`, `get(name)`, `save(name, snapshot)`,
  `delete(name)`, `rename(old, new)`, `get_last_active()` /
  `set_last_active(name)`.
- A plain equality check between two snapshot dicts is enough to
  determine "unsaved changes" — no separate dirty-flag bookkeeping to
  keep in sync with every possible mutation.

**`ocr_region_watcher/qt/app.py`** (existing file, extended):
- `_capture_snapshot()` — serializes the live `watchers`/`manual_inputs`/
  `targets` lists into the dict shape above.
- `_restore_snapshot(data)` — tears down everything currently live (reusing
  each widget's existing close/remove path), then recreates regions/
  manual inputs/targets directly from the saved dict fields.
- `_build_templates_tab()` — new tab, following the same pattern
  `_build_main_tab`/`_build_events_tab` already use.

Recreation sets each region's `formula_key` directly from the saved
value, and creates each manual input/target directly from its saved
fields — it does **not** go through the existing "1st/2nd region gets
PM/PW, 1st region pairs with a Budget input" convenience logic that live
`+ Add Region` clicks use today. That logic is a shortcut for manual
setup only; templates already store the real key/name/value explicitly
for every item, so restoring doesn't depend on recreating things in any
particular order.

## Behavior flows

**Add Template** — creates an empty entry named "Template N" (next
unused number), makes it active *in the live UI* immediately (highlighted
row, its Save button enabled), and tears down whatever was live so you
start calibrating fresh. Nothing is written to disk — including
`last_active` — until you hit Save; a template that only exists as an
empty in-memory row isn't something a future restart should try to
restore.

**Switch (click a template's name)**:
1. Compare `_capture_snapshot()` of the current live state against the
   active template's last-saved snapshot.
2. If different, show "`<name>` has unsaved changes — discard and switch
   anyway?" (Discard / Cancel). Cancel aborts the switch entirely.
3. Otherwise (or once confirmed): tear down current live state, restore
   the clicked template's snapshot, mark it active, resample each
   recreated region's color-lock (identical to what happens when a
   region is freshly dragged), and persist `last_active`.

**Save** — serializes current live state and overwrites that template's
entry on disk. Only enabled on the active row.

**Delete** — confirm dialog ("Delete '`<name>`'? Can't be undone."), then
removes it from storage. Does not touch whatever's currently live on
screen, even if the deleted template was the active one.

## Error handling

- Missing/corrupt `data/templates.json` on startup → empty template list,
  not a crash (surfaced once via a status message, not a blocking
  dialog).
- A failed write (disk full, permissions) → status message; the live
  session is unaffected either way, only persistence failed.
- A restored region that no longer matches what's on screen (resolution
  changed, page scrolled, moved to a different monitor) is caught by the
  *existing* color-lock mechanism — it just shows the same red
  "lost lock" border a live region shows today. No new handling needed.

## Testing

No test framework exists in this project yet. This adds plain
`unittest` (stdlib, no new dependency) covering `templates.py`'s pure
logic: save/load/delete/rename round-tripping through a temp JSON file,
tolerance of a missing/corrupt file, and the snapshot-equality dirty
check. The Qt wiring (`_capture_snapshot`/`_restore_snapshot`/the tab
itself) is verified by running the app, consistent with how the rest of
the project is checked today.
