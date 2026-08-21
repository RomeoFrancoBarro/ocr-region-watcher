"""Main application: a small toolbar with "+ Add Region". Clicking it opens
the snipping-tool overlay -- drag a box, release, and it becomes a
persistent, movable, resizable frame sitting right on the value, with the
live-recognized text shown below it. No naming/label prompts (for now):
select and it just starts reading.
"""
from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from . import formula, inject
from .calibrate import snip_point, snip_region
from .capture import ScreenGrabber
from .colorcheck import sample_reference_color, still_locked
from .events import EventSequencer
from .manual_input import ManualInput
from .recognize import EasyOCRRecognizer
from .target import TargetMarker
from .watcher import RegionWatcher

CYCLE_MS = 30  # floor between cycles; real cadence is however long a cycle actually takes


class App:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("OCR Region Watcher")
        self.root.geometry("240x110")
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.grabber = ScreenGrabber()
        self.recognizer: EasyOCRRecognizer | None = None
        self.watchers: list[RegionWatcher] = []
        self.manual_inputs: list[ManualInput] = []
        self.targets: list[TargetMarker] = []
        self._next_id = 1
        self._next_manual_id = 1
        self._next_target_id = 1
        self._last_result: dict = {}  # most recent formula.compute() output, for Send buttons to read
        self.sequencer = EventSequencer(self.root, fire=self._on_send_target, on_status=self._on_event_status)
        self._after_id: str | None = None
        self._recognizer_loading = False
        # Tk calls aren't safe from a background thread, so the loader
        # thread hands its result off here instead of touching self.root
        # directly; the main thread's own _cycle() picks it up each tick.
        self._recognizer_queue: queue.Queue = queue.Queue()

        self._build_ui()
        self._on_add_manual_input()  # C is needed by every run of this formula -- present
        # from launch instead of waiting on a "+ Manual Input" click; same
        # method the button itself calls, so it still consumes the "C"
        # default slot correctly (a real button click next defaults to
        # input_2, not a second C).
        self._load_recognizer_async()  # start now, in the background, so it's likely
        # already ready by the time you've dragged your first region -- not
        # triggered by (and blocking) that first drag.
        self._cycle()  # runs continuously from launch, regardless of watcher count

    def _build_ui(self) -> None:
        self.root.geometry("300x520")
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True)

        main_tab = tk.Frame(notebook, padx=10, pady=10)
        events_tab = tk.Frame(notebook, padx=10, pady=10)
        notebook.add(main_tab, text="Main")
        notebook.add(events_tab, text="Events")

        self._build_main_tab(main_tab)
        self._build_events_tab(events_tab)

    def _build_main_tab(self, frame: tk.Frame) -> None:
        buttons = tk.Frame(frame)
        buttons.pack(fill="x")
        tk.Button(buttons, text="+ Add Region", command=self._on_add_region).pack(side="left", fill="x", expand=True)
        tk.Button(buttons, text="+ Manual Input", command=self._on_add_manual_input).pack(
            side="left", fill="x", expand=True, padx=(4, 0)
        )

        buttons2 = tk.Frame(frame)
        buttons2.pack(fill="x", pady=(4, 0))
        tk.Button(buttons2, text="+ Add Target", command=self._on_add_target).pack(side="left", fill="x", expand=True)

        self.status_var = tk.StringVar(value="0 region(s). Drag a box to add one.")
        tk.Label(frame, textvariable=self.status_var, anchor="w", fg="gray", wraplength=280, justify="left").pack(
            fill="x", pady=(8, 8)
        )

        tk.Label(frame, text="Regions (read from screen):", anchor="w", fg="gray", font=("Segoe UI", 8)).pack(fill="x")
        self.region_rows_frame = tk.Frame(frame)
        self.region_rows_frame.pack(fill="x", pady=(0, 8))

        tk.Label(frame, text="Manual inputs (typed, not read from screen):", anchor="w", fg="gray", font=("Segoe UI", 8)).pack(
            fill="x"
        )
        self.manual_inputs_frame = tk.Frame(frame)
        self.manual_inputs_frame.pack(fill="x", pady=(0, 8))

        # Beta/testing aid: the exact dict formula.compute() receives, after
        # a comma-name region (e.g. "M,PM") has been split into its keys --
        # separate from each region's own raw-OCR value shown above, since
        # that's pre-split and this is post-split.
        tk.Label(frame, text="Parsed values (debug):", anchor="w", fg="gray", font=("Segoe UI", 8)).pack(fill="x")
        self.readings_var = tk.StringVar(value="--")
        tk.Label(
            frame, textvariable=self.readings_var, font=("Consolas", 9), anchor="w", fg="#0066cc",
            wraplength=280, justify="left",
        ).pack(fill="x", pady=(0, 8))

        # Write-back side: each target clicks its screen position and pastes
        # the named result value there, but ONLY when its own Send button is
        # pressed -- never automatically, see target.py / inject.py. (The
        # Events tab automates pressing Send in a timed sequence.)
        tk.Label(
            frame, text="Targets (click a name -> Send pastes it there):", anchor="w", fg="gray", font=("Segoe UI", 8)
        ).pack(fill="x")
        self.target_rows_frame = tk.Frame(frame)
        self.target_rows_frame.pack(fill="x", pady=(0, 4))

        self.target_status_var = tk.StringVar(value="")
        tk.Label(
            frame, textvariable=self.target_status_var, font=("Consolas", 8), anchor="w", fg="#cc6600",
            wraplength=280, justify="left",
        ).pack(fill="x", pady=(0, 8))

        result_row = tk.Frame(frame)
        result_row.pack(fill="x", side="bottom", pady=(8, 0))
        tk.Label(result_row, text="Result:", font=("Segoe UI", 9, "bold")).pack(side="left")
        self.result_var = tk.StringVar(value="--")
        tk.Label(result_row, textvariable=self.result_var, font=("Consolas", 9), anchor="w").pack(
            side="left", padx=(4, 0), fill="x", expand=True
        )

    def _build_events_tab(self, frame: tk.Frame) -> None:
        """An automated sequence of "fire this target, after waiting this
        long" steps -- the timed, unattended counterpart to manually
        clicking each target's own Send button in order. See
        ocr_region_watcher/events.py's EventSequencer, which does the actual
        scheduling; this just builds/edits its `events` list and drives
        Start/Stop."""
        tk.Label(
            frame, text="Runs your targets in order, automatically -- add steps below:",
            anchor="w", fg="gray", wraplength=280, justify="left",
        ).pack(fill="x", pady=(0, 8))

        tk.Button(frame, text="+ Add Event", command=self._on_add_event).pack(fill="x")

        self.event_rows_frame = tk.Frame(frame)
        self.event_rows_frame.pack(fill="x", pady=(8, 8))

        self.loop_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            frame, text="Loop (restart from step 1 after the last step)", variable=self.loop_var,
            command=self._on_loop_toggled, font=("Segoe UI", 9),
        ).pack(anchor="w")

        run_row = tk.Frame(frame)
        run_row.pack(fill="x", pady=(8, 4))
        tk.Button(run_row, text="Start", command=self._on_start_events, bg="#2e7d32", fg="white").pack(
            side="left", fill="x", expand=True
        )
        tk.Button(run_row, text="Stop", command=self._on_stop_events, bg="#c62828", fg="white").pack(
            side="left", fill="x", expand=True, padx=(4, 0)
        )

        self.event_status_var = tk.StringVar(value="")
        tk.Label(
            frame, textvariable=self.event_status_var, font=("Consolas", 8), anchor="w", fg="#cc6600",
            wraplength=280, justify="left",
        ).pack(fill="x", pady=(8, 0))

    # -- manual (typed, not screen-read) inputs --------------------------
    def _next_manual_input_name(self) -> str:
        defaults = ["C"]  # first manual input defaults to this; after that, generic
        name = defaults[self._next_manual_id - 1] if self._next_manual_id <= len(defaults) else f"input_{self._next_manual_id}"
        self._next_manual_id += 1
        return name

    def _add_manual_input(self, name: str) -> None:
        entry = ManualInput(self.manual_inputs_frame, name, on_remove=self._on_manual_input_removed)
        self.manual_inputs.append(entry)

    def _on_add_manual_input(self) -> None:
        self._add_manual_input(self._next_manual_input_name())

    def _on_manual_input_removed(self, entry: ManualInput) -> None:
        if entry in self.manual_inputs:
            self.manual_inputs.remove(entry)

    # -- adding / removing regions --------------------------------------
    def _next_region_name(self) -> str:
        defaults = ["Red", "Blue"]  # first two regions default to these; after that, generic
        name = defaults[self._next_id - 1] if self._next_id <= len(defaults) else f"region_{self._next_id}"
        self._next_id += 1
        return name

    # Regions 1 and 2 always hold PM/PW's payout values. Their *display*
    # names still default to Red/Blue (freely renamable), but their
    # formula key is fixed here regardless. M and W are no longer typed in
    # directly -- formula.compute() now derives them from Budget/PM/PW --
    # so only region 1 gets a paired manual input, and it's Budget, not M.
    # Region 2 and beyond get no paired input.
    _PAIRED_FORMULA_KEYS = {1: "PM", 2: "PW"}
    _PAIRED_MANUAL_INPUTS = {1: "Budget"}

    def _on_add_region(self) -> None:
        self.root.withdraw()
        rect = snip_region(master=self.root)
        self.root.deiconify()
        if rect is None:
            return

        region_index = self._next_id  # 1-based, before _next_region_name() advances it
        left, top, width, height = rect
        name = self._next_region_name()
        watcher = RegionWatcher(
            self.root, left, top, width, height, name,
            on_close=self._on_watcher_closed, on_change=self._on_watcher_changed,
            formula_key=self._PAIRED_FORMULA_KEYS.get(region_index),
        )
        self._resample(watcher)
        self.watchers.append(watcher)
        self._add_region_row(watcher)
        if self.recognizer is None:
            watcher.set_lines(["loading OCR..."])
        paired_name = self._PAIRED_MANUAL_INPUTS.get(region_index)
        if paired_name is not None:
            self._add_manual_input(paired_name)
        self._update_status()

    def _add_region_row(self, watcher: RegionWatcher) -> None:
        """A row in the app window mirroring this region -- same pattern as
        a manual input's row. Shares the watcher's own name/value StringVars
        (rather than copies), so typing in either place -- this row or the
        floating frame's own header -- stays in sync automatically."""
        row = tk.Frame(self.region_rows_frame)
        row.pack(fill="x", pady=1)

        name_entry = tk.Entry(row, textvariable=watcher.name_var, width=6, font=("Consolas", 9))
        name_entry.pack(side="left", padx=(0, 4))
        name_entry.bind("<Return>", watcher.on_name_committed)
        name_entry.bind("<FocusOut>", watcher.on_name_committed)

        value_entry = tk.Entry(row, textvariable=watcher.value_var, font=("Consolas", 9), state="readonly", justify="right")
        value_entry.pack(side="left", fill="x", expand=True)

        tk.Button(
            row, text="x", command=watcher._close, font=("Consolas", 8, "bold"),
            fg="#cc3333", relief="flat", bd=0, padx=3,
        ).pack(side="left", padx=(4, 0))

        watcher.row_frame = row

    def _on_watcher_closed(self, watcher: RegionWatcher) -> None:
        # Fires regardless of which "x" was clicked -- the floating frame's
        # own close button, or this row's -- so both views of the region
        # always disappear together.
        if watcher in self.watchers:
            self.watchers.remove(watcher)
        row = getattr(watcher, "row_frame", None)
        if row is not None:
            row.destroy()
        self._update_status()

    def _on_watcher_changed(self, watcher: RegionWatcher) -> None:
        # moved or resized -- its old reference color / change-hash no longer apply
        self._resample(watcher)

    def _resample(self, watcher: RegionWatcher) -> None:
        image = self.grabber.grab(watcher.capture_rect())
        watcher.ref_color = sample_reference_color(image)
        watcher.last_hash = None

    def _update_status(self) -> None:
        self.status_var.set(f"{len(self.watchers)} region(s). Drag a box to add one.")

    # -- adding / removing targets (write-back: click + paste) ------------
    def _next_target_name(self) -> str:
        # Target 1 defaults to cycling M then W on successive Sends (same
        # comma convention regions use to split one crop into two keys --
        # here it's one screen position taking two values, one per Send,
        # not one crop). No rename needed to reuse it for both. Target 2+
        # stay generic -- they're typically click-only "confirm"/"next"
        # steps with no value of their own, see the paste checkbox.
        defaults = ["M,W"]
        name = defaults[self._next_target_id - 1] if self._next_target_id <= len(defaults) else f"target_{self._next_target_id}"
        self._next_target_id += 1
        return name

    def _on_add_target(self) -> None:
        self.root.withdraw()
        point = snip_point(master=self.root)
        self.root.deiconify()
        if point is None:
            return

        x, y = point
        name = self._next_target_name()
        target = TargetMarker(
            self.root, x, y, name,
            on_close=self._on_target_closed, on_change=self._on_target_changed,
        )
        target.send_index = 0  # which name in a comma-separated cycle the next Send uses
        target.number = self._next_target_id - 1  # stable label for the Events tab's target picker; name can change
        self.targets.append(target)
        self._add_target_row(target)

    def _on_move_target(self, target: TargetMarker) -> None:
        """Re-place an existing target's screen position -- same
        click-to-place overlay as +Add Target, but updates this target in
        place instead of creating a new one. Easier than dragging a small
        22px marker precisely."""
        self.root.withdraw()
        point = snip_point(master=self.root)
        self.root.deiconify()
        if point is None:
            return
        target.x, target.y = point
        target._apply_geometry()

    def _add_target_row(self, target: TargetMarker) -> None:
        """A two-line block in the app window mirroring this target --
        name field (synced with the floating marker's own header, same
        pattern as region rows) + "paste" checkbox on top, its Send/Move/
        remove buttons below (four buttons no longer fit one line now that
        Move exists). Renaming is live, so the same target can be reused
        for different values across a multi-step sequence: rename to "M",
        Send, rename to "W", Send again -- both go to the same screen
        position (or just name it "M,W" once -- see the cycling in
        _on_send_target).
        """
        container = tk.Frame(self.target_rows_frame, relief="groove", borderwidth=1)
        container.pack(fill="x", pady=2)

        top = tk.Frame(container)
        top.pack(fill="x", padx=3, pady=(2, 0))
        name_entry = tk.Entry(top, textvariable=target.name_var, width=8, font=("Consolas", 9))
        name_entry.pack(side="left", padx=(0, 4))
        name_entry.bind("<Return>", target.on_name_committed)
        name_entry.bind("<FocusOut>", target.on_name_committed)

        # Checked (default): Send looks the name up in the last result and
        # pastes it, same as before. Unchecked: Send is a plain click, no
        # clipboard/paste at all, no name lookup needed -- for a step that's
        # just a "confirm"/"next"-style button in whatever you're targeting.
        target.paste_var = tk.BooleanVar(value=True)
        tk.Checkbutton(top, text="paste", variable=target.paste_var, font=("Consolas", 8)).pack(side="left")

        bottom = tk.Frame(container)
        bottom.pack(fill="x", padx=3, pady=(0, 2))
        tk.Button(bottom, text="Send", command=lambda t=target: self._on_send_target(t), font=("Consolas", 8)).pack(
            side="left", padx=(0, 4)
        )
        tk.Button(bottom, text="Move", command=lambda t=target: self._on_move_target(t), font=("Consolas", 8)).pack(
            side="left", padx=(0, 4)
        )
        tk.Button(
            bottom, text="x", command=target._close, font=("Consolas", 8, "bold"),
            fg="#cc3333", relief="flat", bd=0, padx=3,
        ).pack(side="right")

        target.row_frame = container

    def _on_target_closed(self, target: TargetMarker) -> None:
        if target in self.targets:
            self.targets.remove(target)
        row = getattr(target, "row_frame", None)
        if row is not None:
            row.destroy()
        # Any saved event steps pointing at this target still work (the
        # sequencer skips a removed target on its own), but refresh the
        # Events tab so its target picker stops offering something gone.
        self._rebuild_event_rows()

    def _on_target_changed(self, target: TargetMarker) -> None:
        pass  # just moved -- nothing to resample, unlike a region (no capture/OCR involved)

    # -- events tab: automated sequence of target Sends -------------------
    def _on_add_event(self) -> None:
        if not self.targets:
            self.event_status_var.set("add a target first (Main tab) before adding an event step")
            return
        self.sequencer.events.append({"target": self.targets[0], "delay": 1.0})
        self._rebuild_event_rows()

    def _remove_event(self, index: int) -> None:
        del self.sequencer.events[index]
        self._rebuild_event_rows()

    def _move_event(self, index: int, delta: int) -> None:
        events = self.sequencer.events
        new_index = index + delta
        if 0 <= new_index < len(events):
            events[index], events[new_index] = events[new_index], events[index]
            self._rebuild_event_rows()

    def _rebuild_event_rows(self) -> None:
        """Full rebuild from self.sequencer.events on every add/remove/
        reorder/target-removal -- simpler and less bug-prone than trying to
        shuffle individual row widgets in place, and this list is always
        small (a handful of steps)."""
        for child in self.event_rows_frame.winfo_children():
            child.destroy()

        for index, event in enumerate(self.sequencer.events):
            row = tk.Frame(self.event_rows_frame, relief="groove", borderwidth=1)
            row.pack(fill="x", pady=2)

            tk.Label(row, text=f"{index + 1}.", font=("Consolas", 9)).pack(side="left", padx=(3, 2))

            labels = {f"#{t.number} ({t.name})": t for t in self.targets}
            current_label = next((lbl for lbl, t in labels.items() if t is event["target"]), "")
            picker_var = tk.StringVar(value=current_label)
            picker = ttk.Combobox(
                row, textvariable=picker_var, values=list(labels.keys()), state="readonly", width=12, font=("Consolas", 8)
            )
            picker.pack(side="left", padx=(0, 4))
            picker.bind("<<ComboboxSelected>>", lambda e, i=index, lbls=labels, var=picker_var: self._on_event_target_picked(i, lbls, var))

            tk.Label(row, text="wait", font=("Consolas", 8)).pack(side="left")
            delay_var = tk.StringVar(value=str(event["delay"]))
            delay_entry = tk.Entry(row, textvariable=delay_var, width=4, font=("Consolas", 8))
            delay_entry.pack(side="left", padx=(2, 2))
            delay_entry.bind("<Return>", lambda e, i=index, var=delay_var: self._on_event_delay_committed(i, var))
            delay_entry.bind("<FocusOut>", lambda e, i=index, var=delay_var: self._on_event_delay_committed(i, var))
            tk.Label(row, text="s", font=("Consolas", 8)).pack(side="left", padx=(0, 4))

            tk.Button(row, text="^", command=lambda i=index: self._move_event(i, -1), font=("Consolas", 7), padx=2).pack(side="left")
            tk.Button(row, text="v", command=lambda i=index: self._move_event(i, 1), font=("Consolas", 7), padx=2).pack(side="left")
            tk.Button(
                row, text="x", command=lambda i=index: self._remove_event(i), font=("Consolas", 8, "bold"),
                fg="#cc3333", relief="flat", bd=0, padx=3,
            ).pack(side="right")

    def _on_event_target_picked(self, index: int, labels: dict, var: tk.StringVar) -> None:
        target = labels.get(var.get())
        if target is not None:
            self.sequencer.events[index]["target"] = target

    def _on_event_delay_committed(self, index: int, var: tk.StringVar) -> None:
        try:
            self.sequencer.events[index]["delay"] = max(0.0, float(var.get()))
        except ValueError:
            var.set(str(self.sequencer.events[index]["delay"]))  # reject, revert to the last valid value

    def _on_loop_toggled(self) -> None:
        self.sequencer.loop = self.loop_var.get()

    def _on_start_events(self) -> None:
        self.sequencer.start()

    def _on_stop_events(self) -> None:
        self.sequencer.stop()

    def _on_event_status(self, text: str) -> None:
        self.event_status_var.set(text)

    def _click_target(self, target: TargetMarker, action) -> None:
        """Run `action` (a click, click+paste, whatever) with this target's
        own floating marker hidden first, and a short settle delay before
        clicking -- the marker's crosshair sits with solid pixels crossing
        exactly through (target.x, target.y), the same point Send clicks,
        and hiding it removes any chance it intercepts its own click.
        Defensive, not a confirmed fix for any specific reported failure
        (see ocr_region_watcher/app.py git history / conversation for what
        was and wasn't actually verified about this). Restored even if
        `action`
        raises.
        """
        target.win.withdraw()
        target.win.update()
        time.sleep(0.15)  # let the window manager actually finish hiding it
        try:
            action()
        finally:
            target.win.deiconify()

    def _on_send_target(self, target: TargetMarker) -> None:
        if not target.paste_var.get():
            try:
                self._click_target(target, lambda: inject.click_only(target.x, target.y))
            except Exception as exc:  # e.g. pyautogui's failsafe -- report, don't crash the app
                self.target_status_var.set(f"'{target.name}': click failed -- {exc}")
                return
            self.target_status_var.set(f"clicked {target.name} at ({target.x}, {target.y})")
            return

        # A comma-separated name (e.g. "M,W") cycles: this Send uses
        # whichever one is next in the cycle, not always the first.
        # A plain single name (no comma) always uses that one name --
        # exactly today's behavior, nothing changes for target 2/3.
        keys = [n.strip() for n in target.name.split(",") if n.strip()]
        if not keys:
            self.target_status_var.set("target has no name set")
            return
        key = keys[target.send_index % len(keys)]

        if key not in self._last_result:
            self.target_status_var.set(
                f"'{key}': not in the current result ({', '.join(self._last_result) or 'empty -- inputs incomplete?'})"
            )
            return
        value = self._last_result[key]
        try:
            self._click_target(target, lambda: inject.click_and_paste(target.x, target.y, str(value)))
        except Exception as exc:  # e.g. pyautogui's failsafe -- report, don't crash the app
            self.target_status_var.set(f"'{key}': send failed -- {exc}")
            return
        # Only advance the cycle on an actual successful send -- a missing
        # value or a failed click leaves it pointing at the same step, so
        # retrying doesn't skip ahead.
        target.send_index += 1
        next_hint = f" (next: {keys[target.send_index % len(keys)]})" if len(keys) > 1 else ""
        self.target_status_var.set(f"sent {key}={value} to ({target.x}, {target.y}){next_hint}")

    def _load_recognizer_async(self) -> None:
        if self.recognizer is not None or self._recognizer_loading:
            return
        self._recognizer_loading = True
        self.status_var.set("Loading OCR model in the background...")

        def worker() -> None:
            self._recognizer_queue.put(EasyOCRRecognizer(gpu=False))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_recognizer_ready(self) -> None:
        try:
            self.recognizer = self._recognizer_queue.get_nowait()
        except queue.Empty:
            return
        self._recognizer_loading = False
        self._update_status()

    # -- the read loop ----------------------------------------------------
    def _cycle(self) -> None:
        if self.recognizer is None and self._recognizer_loading:
            self._poll_recognizer_ready()

        for watcher in list(self.watchers):
            rect = watcher.capture_rect()
            image = self.grabber.grab(rect)

            if watcher.ref_color is not None:
                locked = still_locked(image, watcher.ref_color)
                watcher.set_locked(locked)
                if not locked:
                    continue  # keep polling it (in case it re-locks), don't OCR garbage

            if self.recognizer is None:
                continue

            crop_hash = hash(image.tobytes())
            if watcher.last_hash == crop_hash:
                continue  # unchanged since last cycle -- nothing new to recognize
            watcher.last_hash = crop_hash

            reading = self.recognizer.read(image, watcher)
            # Show exactly what OCR read, unfiltered -- no extracting-just-the-
            # number here. One row per detected line, so a multi-line value
            # shows and is recognized as multiple lines instead of being
            # forced into (and garbling) a single one.
            watcher.set_lines([line or "?" for line in reading.lines] or ["?"])
            # `reading.value` is what formula.compute() actually gets -- kept
            # separately from the display text above, and only updated on a
            # successful parse, so a momentary bad read doesn't blank out a
            # value the formula was already using.
            if reading.ok:
                watcher.last_value = reading.value
                watcher.last_values = reading.values

        self._update_result()
        self._after_id = self.root.after(CYCLE_MS, self._cycle)

    def _watcher_keys(self, watcher: RegionWatcher) -> list:
        """The formula-dict key(s) this region maps to, in order.

        A fixed `formula_key` (e.g. PM/PW, independent of the display name)
        wins if set; otherwise the display name itself is the key -- split
        on commas, so "M,PM" maps to two keys from one region's two numbers.
        """
        if watcher.formula_key is not None:
            return [watcher.formula_key]
        return [n.strip() for n in watcher.name.split(",") if n.strip()]

    def _gather_readings(self) -> dict:
        readings = {}
        for watcher in self.watchers:
            keys = self._watcher_keys(watcher)
            if len(keys) > 1:
                for key, value in zip(keys, watcher.last_values):
                    readings[key] = value
            elif keys and watcher.last_value is not None:
                readings[keys[0]] = watcher.last_value
        for entry in self.manual_inputs:
            value = entry.value()
            if value is not None:
                readings[entry.name] = value
            elif entry.value_var.get().strip():
                readings[entry.name] = entry.value_var.get().strip()
        return readings

    def _update_result(self) -> None:
        readings = self._gather_readings()
        # formula.REQUIRED (if the formula declares it) names the keys
        # compute() actually needs -- e.g. "missing: PW, W, C" even before
        # the PW region or the W/C manual inputs have been added at all,
        # not just "added but not yet read". Optional: a formula that
        # doesn't define REQUIRED just gets no missing-list, same as today.
        required = getattr(formula, "REQUIRED", ())
        missing = [key for key in required if key not in readings]
        debug_text = ", ".join(f"{k}={v}" for k, v in readings.items()) if readings else "--"
        if missing:
            debug_text += (" | " if readings else "") + f"missing: {', '.join(missing)}"
        self.readings_var.set(debug_text)
        try:
            result = formula.compute(readings)
        except Exception as exc:  # your formula, your bugs -- show them, don't crash the app
            self.result_var.set(f"error: {exc}")
            self._last_result = {}  # nothing valid to Send while the formula's erroring
            return
        self.result_var.set(", ".join(f"{k}={v}" for k, v in result.items()) if result else "--")
        self._last_result = result  # what each target's Send button will look its name up in

    def _on_close(self) -> None:
        self.sequencer.stop()  # cancel any pending scheduled step before the root goes away
        if self._after_id is not None:
            self.root.after_cancel(self._after_id)
        for watcher in list(self.watchers):
            watcher.win.destroy()
        for target in list(self.targets):
            target.win.destroy()
        self.grabber.close()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    App().run()


if __name__ == "__main__":
    main()
