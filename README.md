# OCR Region Watcher

Point it at any value on screen (any app/site -- generic, not wired to a
specific page) and it reads it live, right where it is.

## How it works

Click **+ Add Region**, drag a box over a value like a snipping tool, let
go. That's it -- no dialogs. The box stays exactly there as a small
cyan-bordered frame:

- The **inside of the frame is fully transparent and click-through** -- the
  real page/app underneath is still fully visible and interactive. It's not
  a screenshot sitting on top; the frame is just a marker.
- **Drag the border** (or the text strip below it) to move the frame.
  **Drag a corner handle** to resize it.
- A **header above the frame** holds the region's name (defaults to
  `Red` for the first region, `Blue` for the second, `region_3`/
  `region_4`/... after that -- click in and type to rename it; this is what
  a formula will key off of later) plus a read-only copy of the current
  value (selectable/copyable, unlike the strip's plain canvas text). Click
  the **x** there to remove the region.
- The **strip below the frame** shows the live-recognized value, updating
  continuously (same value as the header's copy).
- The **app window also gets a row for the region** the moment you add it --
  same name + read-only value, kept in sync with the floating frame's
  header automatically (they share the same underlying fields, so renaming
  or removing from either place updates both). Handy for seeing every
  region at a glance without hunting across the screen for each one.

**Manual inputs**: click **+ Manual Input** in the app window for a value
that isn't read from the screen at all -- you type it in directly (e.g. a
percentage you supply yourself). Same renameable-name pattern as regions,
just typed instead of watched, and it only ever lives in the app window
(there's no screen position to anchor a floating frame to).

Every cycle, each frame's interior gets cropped straight off the screen (no
full-screen scanning -- just that one small rect), sanity-checked against
the background color sampled when it was placed (moved/resized frames
re-sample automatically), and OCR'd. If the sampled color drifts too far
(window moved, page scrolled, something now covering it), the border turns
red instead of showing a stale or garbage reading. Unchanged crops are
skipped rather than re-read, so it naturally runs as fast as the source is
actually changing.

**Multi-line regions**: if a frame spans more than one line of text (stacked
values, e.g. one row above another), the strip grows to show one row per
detected line, and each line is recognized separately -- feeding multiple
stacked lines into a single recognition pass otherwise produces garbled,
hallucinated output instead of an error.

**Targets (write-back)**: the read side above only ever looks at the screen.
Targets are the opposite direction -- click **+ Add Target**, then click
once (no drag, it's a point) where you want a value *sent to*, e.g. a stake
field in some other app. It shows up as a small orange crosshair marker
(draggable to reposition, renamable, removable, same pattern as a region)
plus a row in the app window with a **paste** checkbox and its own **Send**
button. **Nothing sends automatically** -- a target only ever acts because
you clicked its Send button, never on a timer or because a value changed.

- **paste checked (default)**: Send looks the target's name up in
  `formula.compute()`'s most recent result -- name it `M` and Send clicks
  that screen position, then pastes the current `M` value there via the
  clipboard (faster and more reliable than simulating individual
  keystrokes, especially into apps that re-validate on every keypress). If
  the name doesn't match a current result key (inputs still incomplete, or
  a typo), Send does nothing and says why instead of pasting something
  wrong.
- **paste unchecked**: Send is a plain click, nothing copied or pasted --
  for a "confirm"/"next"-step target that just needs clicking, no text.
- **Renaming is live**, so one target can stand in for more than one value
  across a multi-step sequence -- e.g. Send while named `M` (pastes M),
  rename to `W`, Send again (pastes W into that same screen position).

## Templates

The **Templates** tab (third tab, after Main and Events) saves the whole
live setup under a name, and lets you switch between saved setups with one
click -- handy for keeping a separate calibrated layout per site/config
instead of re-dragging every region and retyping every value each time you
switch between them. A template is a snapshot of the *entire* layout --
every region's position, size, and name; every manual input's name and
current text; every target's position and click/paste settings -- not just
whatever values happen to be showing on screen at that moment.

- **+ Add Template** starts a new one (default-named `Template 1`,
  `Template 2`, ...) and clears whatever's currently live so you calibrate
  it from scratch. It isn't written to disk until you hit **Save**.
- Each template gets its own row: its name (editable), a **Switch**/
  **Active** button (reads **Active** and is disabled for whichever
  template is currently loaded), a **Save** button (only enabled for the
  active one), and a **Delete** button.
- **Renaming is inline** -- click into the name field, edit it, then click
  away or press Enter. A blank name or one that collides with another
  template's name is rejected and the field reverts.
- **Switching** discards whatever's currently live and loads the other
  template's saved regions/manual inputs/targets in its place -- but only
  after a confirmation prompt naming the active template (e.g. `'Template
  1' has unsaved changes -- discard and switch anyway?`) if it has changes
  it hasn't saved yet. Nothing to lose means no prompt.
- **Deleting** asks to confirm too (it can't be undone).
- The **last-active template reloads automatically** the next time the app
  launches -- close the app with one selected and it's exactly where you
  left it on the next run, no need to switch back to it by hand.

Saved templates live in `data/templates.json`. That's per-machine data, not
source, so `data/` is gitignored -- it's never committed to the repo.

## Setup

```
pip install -r requirements.txt
```

## Usage

```
python -m ocr_region_watcher.qt_main
```
Opens the app and starts loading the OCR model in the background right
away (not waiting for your first drag). Click **+ Add Region**, drag a box,
repeat for each value. If you drag before the model's ready, the strip
shows `loading OCR...` and fills in automatically the moment it is --
usually before you've even finished placing your first region. Very first
run ever downloads the model weights (~30-60s, one time, needs internet);
cached after that (~3.5s to load into memory on this machine).

## Wiring up your calculation

Connected end-to-end -- `ocr_region_watcher/formula.py`'s `compute(readings)` is called
every cycle, and its return shows live in the **Result** row at the bottom
of the app window. `readings` maps each region/manual-input's current name
to its current value (a number if it parsed as one, otherwise the raw text)
-- an input that hasn't produced a value yet just isn't in the dict, so use
`readings.get("name")` rather than assuming a key exists. The stub itself
still just returns `{}` (no-op) -- that's the part left for you to fill in.
Edit `formula.py`, save, and **restart the app** (not hot-reloaded). If your
formula raises an exception, the Result row shows `error: ...` instead of
crashing the app.

## Notes / current limits

- **No persistence (for now)**: regions and manual inputs (including
  renames) live only for the current run -- closing the app clears them.
  Straightforward to add once the interaction itself feels right.
- **Recognition engine**: EasyOCR, unrestricted (reads real text, not just
  digits) by default. Single-line regions use a fast path that skips
  EasyOCR's text-detector network entirely, since calibration already tells
  it exactly where the text is -- measured on this machine: **~17ms/region
  (~58 reads/sec)** on CPU. Multi-line regions use the full detector
  pipeline instead (needed for reliably telling lines apart), at higher
  latency (~130-150ms) -- only paid on regions that actually have multiple
  lines, not on every region every cycle.
- **Numeric accuracy vs. reading real text**: recognizing everything
  (letters, symbols, not just digits) is what makes real text readable at
  all, but it's a bit noisier on pure numbers than a digits-only allowlist
  would be. If that noise ever matters for a specific region, the fix is a
  per-region known-vocabulary hint (not built yet).
- **GPU**: this machine has an RTX 4060, but the installed `torch` build is
  CPU-only, so `EasyOCRRecognizer(gpu=True)` won't help until a CUDA build
  of torch is installed. Worth doing only if measurement shows OCR is
  actually the bottleneck.
- **Multi-monitor**: the selection overlay spans your full virtual desktop
  (all monitors), not just the primary one.
- **Targets are real mouse/keyboard automation**: a Send click genuinely
  moves the cursor, clicks, and sends a paste keystroke into whatever's
  under that point -- there's no simulation/dry-run mode yet. `pyautogui`'s
  failsafe stays on: slamming the mouse to any screen corner aborts an
  in-flight click+paste.
