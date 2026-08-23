"""Main application window -- a tabbed window (Main / Events / Templates)
driving the shared backend (formula.py, recognize.py, capture.py,
inject.py, colorcheck.py) through a Qt widget layer.
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainterPath, QRegion
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import formula, inject
from ..capture import ScreenGrabber
from ..colorcheck import sample_reference_color, still_locked
from ..recognize import EasyOCRRecognizer
from ..templates import TemplateStore, empty_snapshot
from .events import EventSequencer
from .manual_input import ManualInput
from .overlay import snip_point, snip_region
from .style import MONO, MONO_SMALL
from .target import TargetMarker
from .watcher import RegionWatcher, next_region_color

CYCLE_MS = 30  # same floor as the Tkinter version's cycle


def _section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("role", "section")
    return label


def _row_card(*, layout_cls=QVBoxLayout) -> tuple[QFrame, QVBoxLayout | QHBoxLayout]:
    """A subtly-bordered rounded frame for ONE row -- region/manual-input/
    target rows are each their own card, not one shared frame wrapping a
    whole section's worth of rows (see _rows_container). See style.py's
    QFrame[role=card]."""
    frame = QFrame()
    frame.setProperty("role", "card")
    layout = layout_cls(frame)
    layout.setContentsMargins(10, 8, 10, 8)
    layout.setSpacing(4)
    return frame, layout


def _rows_container() -> tuple[QWidget, QVBoxLayout]:
    """A plain vertical list for a collapsible section's rows -- no border
    of its own, just spacing between the individual row cards inside it."""
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    return container, layout


def _collapsible_section(title: str, content: QWidget, *, expanded: bool = True) -> QWidget:
    """Wraps `content` (a card, usually) behind a clickable header that
    shows/hides it. Pure session-local UI state -- not saved to templates,
    not restored across restarts, and every section starts expanded on a
    fresh launch (a returning user shouldn't find their own regions/
    targets hidden without having collapsed them themselves)."""
    wrapper = QWidget()
    outer = QVBoxLayout(wrapper)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(4)

    toggle_btn = QPushButton()
    toggle_btn.setCheckable(True)
    toggle_btn.setChecked(expanded)
    toggle_btn.setCursor(Qt.PointingHandCursor)
    toggle_btn.setStyleSheet(
        "QPushButton { text-align: left; background: transparent; border: none;"
        " color: #949ba4; font-size: 8pt; font-weight: 600; padding: 4px 2px; border-radius: 4px; }"
        "QPushButton:hover { background-color: #26282c; }"
    )

    def _set_text(checked: bool) -> None:
        toggle_btn.setText(f"{'▾' if checked else '▸'}  {title}")

    _set_text(expanded)
    content.setVisible(expanded)

    def _on_toggled(checked: bool) -> None:
        _set_text(checked)
        content.setVisible(checked)

    toggle_btn.toggled.connect(_on_toggled)

    outer.addWidget(toggle_btn)
    outer.addWidget(content)
    return wrapper


class _RecognizerLoader(QObject):
    ready = Signal(object)

    def load(self) -> None:
        recognizer = EasyOCRRecognizer(gpu=False)
        self.ready.emit(recognizer)


class _TitleBar(QFrame):
    """Custom header for the main window -- it's frameless (see App.__init__,
    docked-panel style rather than a normal resizable window), so this
    replaces the OS title bar it no longer has: something to grab to move
    the window, and a close button. Drag math mirrors the exact pattern
    RegionWatcher/TargetMarker already use for their own dragging."""

    def __init__(self, app_window: QWidget) -> None:
        super().__init__()
        self._app_window = app_window
        self._drag_origin: tuple | None = None
        self.setFixedHeight(30)
        self.setStyleSheet("background-color: #17181a;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 0, 6, 0)
        layout.setSpacing(2)
        title = QLabel("OCR Region Watcher")
        title.setStyleSheet("color: #949ba4; font-weight: 600; font-size: 9pt;")
        layout.addWidget(title)
        layout.addStretch(1)

        chrome_btn_style = (
            "QPushButton { background: transparent; border: none; color: #949ba4;"
            " font-weight: 700; padding: 2px 0; }"
            "QPushButton:hover { background-color: #26282c; border-radius: 3px; }"
        )

        minimize_btn = QPushButton("-")
        minimize_btn.setFixedWidth(24)
        minimize_btn.setToolTip("Minimize")
        minimize_btn.setStyleSheet(chrome_btn_style)
        minimize_btn.clicked.connect(app_window.showMinimized)
        layout.addWidget(minimize_btn)

        close_btn = QPushButton("x")
        close_btn.setObjectName("flatRemove")
        close_btn.setFixedWidth(24)
        close_btn.setToolTip("Close (quits the app)")
        close_btn.clicked.connect(app_window.close)
        layout.addWidget(close_btn)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_origin = (event.globalPosition().toPoint(), self._app_window.pos())

    def mouseMoveEvent(self, event) -> None:
        if self._drag_origin:
            origin, start_pos = self._drag_origin
            delta = event.globalPosition().toPoint() - origin
            self._app_window.move(start_pos + delta)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_origin = None


class App(QWidget):
    def __init__(self) -> None:
        # Frameless + always-on-top -- a docked utility panel, not a normal
        # resizable window: no OS title bar (see _TitleBar), no OS resize
        # edges either, so this is a fixed size for now rather than a size
        # nothing could actually drag-resize. Still the default Qt.Window
        # type underneath (not Qt.Tool), so it keeps a normal taskbar
        # entry/Alt-Tab presence -- only the chrome changed.
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setWindowTitle("OCR Region Watcher")
        self.setFixedSize(300, 640)
        self._apply_rounded_mask()
        self._move_to_default_position()

        self.grabber = ScreenGrabber()
        self.recognizer: EasyOCRRecognizer | None = None
        self._recognizer_loading = False
        self.watchers: list[RegionWatcher] = []
        self.manual_inputs: list[ManualInput] = []
        self.targets: list[TargetMarker] = []
        self._next_id = 1
        self._next_manual_id = 1
        self._next_target_id = 1
        self._last_result: dict = {}
        self.template_store = TemplateStore()
        self.active_template: str | None = None
        # What "nothing has changed yet" looks like when nothing is loaded
        # from a saved template -- captured by _seed_blank_slate(), since
        # the "C" manual input it seeds is itself already a difference from
        # a truly empty snapshot. See _confirm_discard_if_dirty.
        self._pending_baseline: dict | None = None
        self.sequencer = EventSequencer(fire=self._on_send_target, on_status=self._on_event_status)

        self._build_ui()
        if self.template_store.load_error:
            self.template_status_label.setText(self.template_store.load_error)
        last_active = self.template_store.get_last_active()
        if last_active is not None and self.template_store.get(last_active) is not None:
            try:
                self._load_template(last_active)
            except (KeyError, TypeError, ValueError):
                # A restore that died partway through (e.g. the second
                # region's saved dict is missing a key) leaves everything it
                # already recreated on screen -- clear that out before
                # falling back, so the blank-slate fallback really is blank.
                self._teardown_live_state()
                self._seed_blank_slate()
        else:
            self._seed_blank_slate()
        self._refresh_templates_tab()
        self._load_recognizer_async()

        self._cycle_timer = QTimer(self)
        self._cycle_timer.timeout.connect(self._cycle)
        self._cycle_timer.start(CYCLE_MS)

    def _apply_rounded_mask(self) -> None:
        """Rounds the window's own corners -- there's no OS chrome left to
        do that for us (see __init__), so it's a manual mask instead. Only
        needs doing once since the window is a fixed size."""
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 10, 10)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def _move_to_default_position(self) -> None:
        """Starts docked against the right edge of the primary screen,
        clear of the top-left area where regions/targets usually end up --
        rather than wherever Qt's own default placement happens to land,
        which (now that this window is always-on-top) could open directly
        on top of whatever you're trying to watch, or of a region/target
        you're about to place. Only a starting position -- drag the header
        bar to move it anywhere; that position isn't remembered across
        restarts."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        self.move(geo.right() - self.width(), geo.top() + 40)

    # -- layout -------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(_TitleBar(self))

        tabs = QTabWidget()
        outer.addWidget(tabs)

        main_scroll = QScrollArea()
        main_scroll.setWidgetResizable(True)
        main_content = QWidget()
        main_scroll.setWidget(main_content)
        self._build_main_tab(main_content)

        # The Result row lives outside the scroll area, in its own tab
        # wrapper -- pinned to the bottom of the Main tab and always
        # visible, regardless of how many sections above are expanded or
        # how far the scroll area is scrolled.
        main_tab = QWidget()
        main_tab_layout = QVBoxLayout(main_tab)
        main_tab_layout.setContentsMargins(0, 0, 0, 0)
        main_tab_layout.setSpacing(0)
        main_tab_layout.addWidget(main_scroll, 1)
        main_tab_layout.addWidget(self._build_result_bar())
        tabs.addTab(main_tab, "Main")

        events_content = QWidget()
        self._build_events_tab(events_content)
        tabs.addTab(events_content, "Events")

        templates_content = QWidget()
        self._build_templates_tab(templates_content)
        tabs.addTab(templates_content, "Templates")

    def _build_main_tab(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # No "+ Manual Input" button -- C and Budget are the only manual
        # inputs this formula needs, and both are already added
        # automatically (C at launch, Budget paired with the first
        # region); there's nothing left for a user click to add.
        buttons = QHBoxLayout()
        add_region_btn = QPushButton("+ Add Region")
        add_region_btn.setObjectName("accent")
        add_region_btn.setToolTip("Drag a box over a value on screen to start reading it")
        add_region_btn.clicked.connect(self._on_add_region)
        buttons.addWidget(add_region_btn)
        add_target_btn = QPushButton("+ Add Target")
        add_target_btn.setToolTip("Click a screen position to click+paste a result value into later")
        add_target_btn.clicked.connect(self._on_add_target)
        buttons.addWidget(add_target_btn)
        layout.addLayout(buttons)

        self.status_label = QLabel("0 region(s). Drag a box to add one.")
        self.status_label.setStyleSheet("color: #949ba4;")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        region_rows, self.region_rows_layout = _rows_container()
        layout.addWidget(_collapsible_section("REGIONS", region_rows))

        manual_rows, self.manual_inputs_layout = _rows_container()
        layout.addWidget(_collapsible_section("MANUAL INPUTS", manual_rows))

        layout.addWidget(_section_label("PARSED VALUES (DEBUG)"))
        self.readings_label = QLabel("--")
        self.readings_label.setProperty("role", "debug")
        self.readings_label.setWordWrap(True)
        layout.addWidget(self.readings_label)

        target_rows, self.target_rows_layout = _rows_container()
        layout.addWidget(_collapsible_section("TARGETS", target_rows))

        self.target_status_label = QLabel("")
        self.target_status_label.setProperty("role", "status")
        self.target_status_label.setWordWrap(True)
        layout.addWidget(self.target_status_label)

        layout.addWidget(_section_label("SAVE THIS SETUP"))
        save_as_template_btn = QPushButton("Save as Template")
        save_as_template_btn.setToolTip(
            "Save everything above (regions, manual inputs, targets) as a new template -- "
            "manage existing templates from the Templates tab"
        )
        save_as_template_btn.clicked.connect(self._on_save_as_template)
        layout.addWidget(save_as_template_btn)
        self.save_template_status_label = QLabel("")
        self.save_template_status_label.setProperty("role", "status")
        self.save_template_status_label.setWordWrap(True)
        layout.addWidget(self.save_template_status_label)

        layout.addStretch(1)

    def _build_result_bar(self) -> QWidget:
        """A footer pinned outside the Main tab's scroll area -- see
        _build_ui -- so it's always visible no matter how much is
        expanded/scrolled above it."""
        bar = QFrame()
        bar.setStyleSheet("QFrame { background-color: #202225; border-top: 1px solid #3f4147; }")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(14, 10, 14, 10)
        result_title = QLabel("Result:")
        result_title.setStyleSheet("font-weight: 700;")
        layout.addWidget(result_title)
        self.result_label = QLabel("--")
        self.result_label.setFont(MONO)
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label, 1)
        return bar

    def _build_events_tab(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        hint = QLabel("Runs your targets in order, automatically -- add steps below:")
        hint.setStyleSheet("color: #949ba4;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        add_event_btn = QPushButton("+ Add Event")
        add_event_btn.setToolTip("Add a step: fire one of your targets, after waiting a bit")
        add_event_btn.clicked.connect(self._on_add_event)
        layout.addWidget(add_event_btn)

        event_scroll = QScrollArea()
        event_scroll.setWidgetResizable(True)
        event_content = QWidget()
        self.event_rows_layout = QVBoxLayout(event_content)
        self.event_rows_layout.setSpacing(8)
        self.event_rows_layout.addStretch(1)
        event_scroll.setWidget(event_content)
        layout.addWidget(event_scroll, 1)

        self.loop_checkbox = QCheckBox("Loop (restart from step 1 after the last step)")
        self.loop_checkbox.toggled.connect(self._on_loop_toggled)
        layout.addWidget(self.loop_checkbox)

        run_row = QHBoxLayout()
        start_btn = QPushButton("Start")
        start_btn.setObjectName("success")
        start_btn.clicked.connect(self._on_start_events)
        run_row.addWidget(start_btn)
        stop_btn = QPushButton("Stop")
        stop_btn.setObjectName("danger")
        stop_btn.clicked.connect(self._on_stop_events)
        run_row.addWidget(stop_btn)
        layout.addLayout(run_row)

        self.event_status_label = QLabel("")
        self.event_status_label.setProperty("role", "status")
        self.event_status_label.setWordWrap(True)
        layout.addWidget(self.event_status_label)

    def _build_templates_tab(self, parent: QWidget) -> None:
        layout = QVBoxLayout(parent)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        hint = QLabel(
            "Switch to load a saved setup live -- move things, then Save to persist changes. "
            "Save a new one from the Main tab's 'Save as Template' button."
        )
        hint.setStyleSheet("color: #949ba4;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        add_template_btn = QPushButton("+ Clear (start a new setup)")
        add_template_btn.setObjectName("accent")
        add_template_btn.setToolTip("Clears everything below to a blank slate -- doesn't create a template by itself, Save as Template does that")
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

    # -- manual (typed, not screen-read) inputs --------------------------
    def _next_manual_input_name(self) -> str:
        defaults = ["C"]
        name = defaults[self._next_manual_id - 1] if self._next_manual_id <= len(defaults) else f"input_{self._next_manual_id}"
        self._next_manual_id += 1
        return name

    def _add_manual_input(self, name: str) -> ManualInput:
        entry = ManualInput(name, on_remove=self._on_manual_input_removed)
        self.manual_inputs.append(entry)
        self.manual_inputs_layout.addWidget(entry)
        return entry

    def _on_manual_input_removed(self, entry: ManualInput) -> None:
        if entry in self.manual_inputs:
            self.manual_inputs.remove(entry)

    # -- adding / removing regions --------------------------------------
    def _next_region_name(self) -> str:
        defaults = ["Red", "Blue"]
        name = defaults[self._next_id - 1] if self._next_id <= len(defaults) else f"region_{self._next_id}"
        self._next_id += 1
        return name

    _PAIRED_FORMULA_KEYS = {1: "PM", 2: "PW"}
    _PAIRED_MANUAL_INPUTS = {1: "Budget"}

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
        color = next_region_color(region_index)
        self._create_region(left, top, width, height, name, self._PAIRED_FORMULA_KEYS.get(region_index), color)
        paired_name = self._PAIRED_MANUAL_INPUTS.get(region_index)
        if paired_name is not None:
            self._add_manual_input(paired_name)
        self._update_status()

    def _create_region(
        self, left: int, top: int, width: int, height: int, name: str, formula_key: str | None, color,
    ) -> RegionWatcher:
        """Build, show, and register a region watcher from explicit
        parameters -- shared by live '+ Add Region' drags (formula_key and
        color both inferred from creation order, above in _on_add_region)
        and template restores (both taken directly from the saved
        snapshot, see _restore_snapshot)."""
        watcher = RegionWatcher(
            left, top, width, height, name,
            on_close=self._on_watcher_closed, on_change=self._on_watcher_changed,
            formula_key=formula_key, color=color,
        )
        watcher.show()
        self._resample(watcher)
        self.watchers.append(watcher)
        self._add_region_row(watcher)
        if self.recognizer is None:
            watcher.set_lines(["loading OCR..."])
        return watcher

    def _add_region_row(self, watcher: RegionWatcher) -> None:
        row, col = _row_card()

        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        # Matches the floating frame's own border color -- lets you match
        # a row to its on-screen marker at a glance instead of only by name.
        swatch = QLabel()
        swatch.setFixedSize(10, 10)
        swatch.setStyleSheet(f"background-color: {watcher.color.name()}; border-radius: 3px;")
        top_row.addWidget(swatch)

        # Borderless/transparent until focused -- reads as plain bold text
        # at rest, but is still a real editable field (click in, type,
        # commits on losing focus) exactly as before.
        name_edit = QLineEdit(watcher.name)
        name_edit.setFixedWidth(64)
        name_edit.setFont(MONO)
        name_edit.setStyleSheet(
            "QLineEdit { border: none; background: transparent; font-weight: 600; padding: 0; }"
            "QLineEdit:focus { border: 1px solid #5865f2; border-radius: 3px; background: #1e1f22; }"
        )
        name_edit.editingFinished.connect(lambda: watcher.set_name(name_edit.text()))
        watcher.name_changed.connect(name_edit.setText)
        top_row.addWidget(name_edit)

        # The live value here -- not shown a second time on the floating
        # overlay's own corner chip (which already shows the same text).
        value_label = QLineEdit(watcher.value_edit.text())
        value_label.setReadOnly(True)
        value_label.setFont(MONO)
        value_label.setAlignment(Qt.AlignRight)
        value_label.setStyleSheet("QLineEdit { border: none; background: transparent; padding: 0; }")
        watcher.value_changed.connect(value_label.setText)
        top_row.addWidget(value_label, 1)

        remove_btn = QPushButton("x")
        remove_btn.setObjectName("flatRemove")
        remove_btn.setFixedWidth(20)
        remove_btn.clicked.connect(watcher._close)
        top_row.addWidget(remove_btn)
        col.addLayout(top_row)

        status_label = QLabel()
        status_label.setStyleSheet("color: #949ba4; font-size: 8pt;")

        def _update_status(locked: bool = True) -> None:
            bits = ["region"]
            if watcher.formula_key:
                bits.append(watcher.formula_key)
            bits.append("locked" if locked else "lost signal")
            status_label.setText(" · ".join(bits))

        _update_status(watcher.locked)
        watcher.locked_changed.connect(_update_status)
        col.addWidget(status_label)

        watcher.row_widget = row
        self.region_rows_layout.addWidget(row)

    def _on_watcher_closed(self, watcher: RegionWatcher) -> None:
        if watcher in self.watchers:
            self.watchers.remove(watcher)
        row = getattr(watcher, "row_widget", None)
        if row is not None:
            row.deleteLater()
        self._update_status()

    def _on_watcher_changed(self, watcher: RegionWatcher) -> None:
        self._resample(watcher)

    def _resample(self, watcher: RegionWatcher) -> None:
        image = self.grabber.grab(watcher.capture_rect())
        watcher.ref_color = sample_reference_color(image)
        watcher.last_hash = None

    def _update_status(self) -> None:
        self.status_label.setText(f"{len(self.watchers)} region(s). Drag a box to add one.")

    # -- adding / removing targets (write-back: click + paste) ------------
    def _next_target_name(self) -> str:
        defaults = ["Target 1"]
        name = defaults[self._next_target_id - 1] if self._next_target_id <= len(defaults) else f"Target {self._next_target_id}"
        self._next_target_id += 1
        return name

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

    def _add_target_row(self, target: TargetMarker) -> None:
        container, col = _row_card()

        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        # Same circular-chip language as the crosshair's own name chip on
        # screen -- orange for every target (unlike regions, targets don't
        # get individual identity colors; there's nothing on-screen shaped
        # like a frame to color-match to, just the crosshair itself).
        chip = QWidget()
        chip.setFixedSize(10, 10)
        chip.setStyleSheet("background-color: #ff9900; border-radius: 5px;")
        top_row.addWidget(chip)

        name_edit = QLineEdit(target.name)
        name_edit.setFixedWidth(72)
        name_edit.setFont(MONO_SMALL)
        name_edit.setToolTip("Just a label -- doesn't affect what gets pasted")
        name_edit.setStyleSheet(
            "QLineEdit { border: none; background: transparent; font-weight: 600; padding: 0; }"
            "QLineEdit:focus { border: 1px solid #5865f2; border-radius: 3px; background: #1e1f22; }"
        )
        name_edit.editingFinished.connect(lambda: target.set_name(name_edit.text()))
        target.name_changed.connect(name_edit.setText)
        top_row.addWidget(name_edit)

        key_edit = QLineEdit(target.value_key or "")
        key_edit.setFont(MONO_SMALL)
        key_edit.setPlaceholderText("paste key, e.g. M")
        key_edit.setToolTip("Which single formula.compute() result key Send pastes here")
        key_edit.setStyleSheet(
            "QLineEdit { border: none; background: #1e1f22; border-radius: 3px; padding: 2px 6px; }"
            "QLineEdit:focus { border: 1px solid #5865f2; }"
        )
        key_edit.editingFinished.connect(lambda: target.set_value_key(key_edit.text()))
        top_row.addWidget(key_edit, 1)
        col.addLayout(top_row)

        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)
        # Independent, user-controlled toggles -- not one checkbox forcing
        # an all-or-nothing "click+paste" vs "click-only" choice. Click
        # always fires first when both are on (see _on_send_target).
        click_check = QCheckBox("click")
        click_check.setChecked(target.click_enabled)
        click_check.setToolTip("Click this target's screen position when Send fires")
        click_check.toggled.connect(lambda checked: setattr(target, "click_enabled", checked))
        bottom_row.addWidget(click_check)

        paste_check = QCheckBox("paste")
        paste_check.setChecked(target.paste_enabled)
        paste_check.setToolTip(
            "Paste the current value when Send fires. With click also on, click happens first;"
            " with click off, pastes into whatever's already focused."
        )
        paste_check.toggled.connect(lambda checked: setattr(target, "paste_enabled", checked))
        bottom_row.addWidget(paste_check)

        send_btn = QPushButton("Send")
        send_btn.clicked.connect(lambda: self._on_send_target(target))
        bottom_row.addWidget(send_btn)

        bottom_row.addStretch(1)
        remove_btn = QPushButton("x")
        remove_btn.setObjectName("flatRemove")
        remove_btn.setFixedWidth(22)
        remove_btn.clicked.connect(target._close)
        bottom_row.addWidget(remove_btn)
        col.addLayout(bottom_row)

        target.row_widget = container
        self.target_rows_layout.addWidget(container)

    def _on_target_closed(self, target: TargetMarker) -> None:
        if target in self.targets:
            self.targets.remove(target)
        row = getattr(target, "row_widget", None)
        if row is not None:
            row.deleteLater()
        self._rebuild_event_rows()

    def _on_target_changed(self, target: TargetMarker) -> None:
        pass  # just moved -- nothing to resample, no capture/OCR involved

    # -- templates: saved snapshots of the whole live setup ---------------
    def _capture_snapshot(self) -> dict:
        return {
            "regions": [
                {
                    "name": w.name, "formula_key": w.formula_key, "color": w.color.name(),
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
        keeps running afterward).

        The event sequence goes with them: its steps hold direct references
        to the target markers destroyed below, so keeping them would leave
        the Events tab listing steps that can never fire again."""
        self.sequencer.stop()
        self.sequencer.events.clear()
        for watcher in list(self.watchers):
            watcher._close()
        for target in list(self.targets):
            target._close()
        for entry in list(self.manual_inputs):
            entry._remove()
        # Not redundant with the rebuild each _on_target_closed triggers:
        # that one doesn't fire when there were no live targets left to
        # close, which is exactly the case after a step's target was
        # removed by hand and only its dangling step remained.
        self._rebuild_event_rows()

    def _restore_snapshot(self, data: dict) -> None:
        # index (1-based, restore order) is the fallback for a template
        # saved before regions had colors at all -- e.g. this machine's
        # own existing data/templates.json -- so an old file still loads
        # with a sensible color instead of needing a "color" key it never
        # had.
        for index, r in enumerate(data.get("regions", []), start=1):
            color = QColor(r["color"]) if r.get("color") else next_region_color(index)
            self._create_region(r["left"], r["top"], r["width"], r["height"], r["name"], r.get("formula_key"), color)
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
        # Teardown cleared the event sequence (those steps pointed at the
        # targets it destroyed); rebuild the rows so the Events tab shows
        # that plainly instead of going quietly stale.
        self._rebuild_event_rows()

    def _confirm_discard_if_dirty(self) -> bool:
        """True if it's safe to tear down the current live state -- either
        nothing would be lost, or the user explicitly confirmed discarding
        it. Shared by every action that tears down or replaces live state
        (clearing to a new setup, Switch)."""
        current = self._capture_snapshot()
        if self.active_template is not None:
            # active_template, whenever set, always already names an entry
            # in the store (Save as Template only ever points it at a name
            # it just saved) -- the empty_snapshot() fallback here is only
            # for the unlikely case it got deleted out from under us.
            baseline = self.template_store.get(self.active_template) or empty_snapshot()
            text = f"'{self.active_template}' has unsaved changes -- discard and switch anyway?"
        else:
            # Nothing loaded from a saved template -- compare against
            # whatever a blank slate looked like right after the last
            # reset (see _pending_baseline), so work built since then still
            # warns before being discarded, even though it isn't "in" any
            # named template yet.
            baseline = self._pending_baseline or empty_snapshot()
            text = "You have unsaved changes -- discard them?"
        if current == baseline:
            return True
        reply = QMessageBox.question(self, "Unsaved changes", text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        return reply == QMessageBox.Yes

    def _seed_blank_slate(self) -> None:
        """What a fresh launch (or the Templates tab's clear button) looks
        like: counters reset, nothing active, just the one manual input
        every run of this formula needs. Captures _pending_baseline right
        after, so the dirty check has something to compare a not-yet-saved
        setup against."""
        self._next_id = self._next_manual_id = self._next_target_id = 1
        self.active_template = None
        self._add_manual_input(self._next_manual_input_name())  # C is needed by every run of this formula
        self._pending_baseline = self._capture_snapshot()

    def _on_add_template(self) -> None:
        if not self._confirm_discard_if_dirty():
            return
        self._teardown_live_state()
        self._seed_blank_slate()
        self._refresh_templates_tab()

    def _on_template_clicked(self, name: str) -> None:
        # The row's Switch button is already disabled while name is the
        # active template (see _build_template_row), so this only ever
        # fires for a *different* template -- nothing to no-op here.
        if not self._confirm_discard_if_dirty():
            return
        self._load_template(name)

    def _load_template(self, name: str) -> None:
        data = self.template_store.get(name)
        if data is None:
            return
        self._teardown_live_state()
        self._restore_snapshot(data)
        self.active_template = name
        # A template saved before some now-standard field existed (e.g. a
        # region's color, added after this file may have been written)
        # restores with a synthesized default for that field -- persist it
        # immediately so the stored snapshot matches what was just
        # restored. Without this, _confirm_discard_if_dirty's very next
        # comparison (freshly captured vs. the still-old-shaped stored
        # dict) would flag "unsaved changes" for a load where nothing was
        # actually edited.
        fresh = self._capture_snapshot()
        if fresh != data:
            try:
                self.template_store.save(name, fresh)
            except OSError:
                pass  # not fatal -- a real edit will still prompt to save normally
        try:
            self.template_store.set_last_active(name)
        except OSError as exc:
            self.template_status_label.setText(f"loaded '{name}', but couldn't remember it for next launch: {exc}")
        self._refresh_templates_tab()

    def _on_save_as_template(self) -> None:
        default = self.template_store.next_default_name()
        name, ok = QInputDialog.getText(self, "Save as Template", "Name:", text=default)
        if not ok:
            return
        name = name.strip()
        if not name:
            self.save_template_status_label.setText("name can't be blank")
            return
        if name in self.template_store.names():
            self.save_template_status_label.setText(f"'{name}' already exists -- pick a different name")
            return
        try:
            self.template_store.save(name, self._capture_snapshot())
            self.template_store.set_last_active(name)
        except OSError as exc:
            self.save_template_status_label.setText(f"couldn't save: {exc}")
            return
        self.active_template = name
        self.save_template_status_label.setText(f"saved as '{name}'")
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

    def _on_template_renamed(self, old_name: str, name_edit: QLineEdit) -> None:
        new_name = name_edit.text().strip()
        if not new_name or new_name == old_name:
            name_edit.setText(old_name)
            return
        # active_template, whenever set, always names an entry already in
        # the store (Save as Template only ever points it at a name it just
        # saved) -- no separate "pending, not-yet-saved" name can collide
        # here the way one could when + Add Template used to allocate a
        # name before Save.
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

    def _refresh_templates_tab(self) -> None:
        while self.template_rows_layout.count() > 1:  # leave the trailing stretch alone
            item = self.template_rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # active_template, whenever set, always names an entry already in
        # the store -- see _on_template_renamed's comment -- so there's no
        # "pending, not-yet-saved" row to special-case here any more.
        for index, name in enumerate(self.template_store.names()):
            self.template_rows_layout.insertWidget(index, self._build_template_row(name))

    def _build_template_row(self, name: str) -> QFrame:
        is_active = name == self.active_template
        row = QFrame()
        row.setProperty("role", "card")
        if is_active:
            # Selector-scoped (like every rule in style.py) so the accent
            # border lands on the frame alone -- an unscoped stylesheet
            # propagates to every child, outlining the row's name field and
            # all three buttons too.
            row.setStyleSheet('QFrame[role="card"] { border: 1px solid #5865f2; }')
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(8, 6, 8, 6)
        row_layout.setSpacing(6)

        name_edit = QLineEdit(name)
        name_edit.setFont(MONO_SMALL)
        row_layout.addWidget(name_edit, 1)
        name_edit.editingFinished.connect(lambda n=name, e=name_edit: self._on_template_renamed(n, e))

        switch_btn = QPushButton("Active" if is_active else "Switch")
        switch_btn.setEnabled(not is_active)
        switch_btn.setToolTip("Load this template live -- move things, then Save to persist changes")
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

    def _click_target(self, target: TargetMarker, action) -> None:
        """Run `action` (whatever inject.send() call this Send actually
        needs) with this target's own marker window hidden first.

        TargetMarker is a real, always-on-top window sitting exactly at
        (target.x, target.y) -- unlike RegionWatcher it has no click-through
        mask over its interior, so without this, a click there lands on
        the marker's own window instead of passing through to whatever's
        actually underneath it (reported directly: Send was clicking the
        target icon itself, not what it's pointing at). Restored
        afterward even if `action` raises. Same fix, same reasoning, as
        the Tkinter version's _click_target.
        """
        target.hide()
        QApplication.processEvents()
        time.sleep(0.15)  # let the window manager actually finish hiding it
        try:
            action()
        finally:
            target.show()

    def _on_send_target(self, target: TargetMarker) -> None:
        if not target.click_enabled and not target.paste_enabled:
            self.target_status_label.setText(f"'{target.name}': neither click nor paste is checked -- nothing to do")
            return

        # Only figure out a value to paste if paste is actually on --
        # click-only doesn't need (or look up) any value_key at all.
        text = None
        if target.paste_enabled:
            key = target.value_key
            if not key:
                self.target_status_label.setText(f"'{target.name}': no paste key set -- fill in its key field")
                return
            if key not in self._last_result:
                self.target_status_label.setText(
                    f"'{key}': not in the current result ({', '.join(self._last_result) or 'empty -- inputs incomplete?'})"
                )
                return
            text = str(self._last_result[key])

        try:
            # click always first when both are on, see inject.send()
            self._click_target(
                target, lambda: inject.send(target.x, target.y, text, click=target.click_enabled, paste=target.paste_enabled)
            )
        except Exception as exc:  # e.g. pyautogui's failsafe -- report, don't crash the app
            self.target_status_label.setText(f"'{target.name}': send failed -- {exc}")
            return

        parts = []
        if target.click_enabled:
            parts.append("clicked")
        if target.paste_enabled:
            parts.append(f"pasted {target.value_key}={text}")
        self.target_status_label.setText(f"{target.name}: {' + '.join(parts)} at ({target.x}, {target.y})")

    # -- events tab: automated sequence of target Sends -------------------
    def _on_add_event(self) -> None:
        if not self.targets:
            self.event_status_label.setText("add a target first (Main tab) before adding an event step")
            return
        self.sequencer.events.append({"target": self.targets[0], "delay": 0})  # ms -- smallest QTimer accepts
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
        while self.event_rows_layout.count() > 1:  # leave the trailing stretch alone
            item = self.event_rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for index, event in enumerate(self.sequencer.events):
            row = QFrame()
            row.setProperty("role", "card")
            col = QVBoxLayout(row)
            col.setContentsMargins(8, 6, 8, 6)
            col.setSpacing(5)

            top_row = QHBoxLayout()
            top_row.setSpacing(6)
            step_label = QLabel(f"Step {index + 1}")
            step_label.setStyleSheet("color: #949ba4; font-weight: 600;")
            top_row.addWidget(step_label)

            picker = QComboBox()
            picker.setFont(MONO_SMALL)
            labels = {f"#{t.number} ({t.name})": t for t in self.targets}
            for label in labels:
                picker.addItem(label)
            current_label = next((lbl for lbl, t in labels.items() if t is event["target"]), "")
            if current_label:
                picker.setCurrentText(current_label)
            picker.currentTextChanged.connect(lambda text, i=index, lbls=labels: self._on_event_target_picked(i, lbls, text))
            top_row.addWidget(picker, 1)
            col.addLayout(top_row)

            bottom_row = QHBoxLayout()
            bottom_row.setSpacing(6)
            bottom_row.addWidget(QLabel("wait"))
            delay_edit = QLineEdit(str(event["delay"]))
            delay_edit.setFixedWidth(60)
            delay_edit.setFont(MONO_SMALL)
            delay_edit.editingFinished.connect(lambda i=index, e=delay_edit: self._on_event_delay_committed(i, e))
            bottom_row.addWidget(delay_edit)
            bottom_row.addWidget(QLabel("ms"))
            bottom_row.addStretch(1)

            up_btn = QPushButton("^")
            up_btn.setFixedWidth(26)
            up_btn.setToolTip("Move this step earlier")
            up_btn.clicked.connect(lambda checked=False, i=index: self._move_event(i, -1))
            bottom_row.addWidget(up_btn)
            down_btn = QPushButton("v")
            down_btn.setFixedWidth(26)
            down_btn.setToolTip("Move this step later")
            down_btn.clicked.connect(lambda checked=False, i=index: self._move_event(i, 1))
            bottom_row.addWidget(down_btn)
            remove_btn = QPushButton("x")
            remove_btn.setObjectName("flatRemove")
            remove_btn.setFixedWidth(26)
            remove_btn.setToolTip("Remove this step")
            remove_btn.clicked.connect(lambda checked=False, i=index: self._remove_event(i))
            bottom_row.addWidget(remove_btn)
            col.addLayout(bottom_row)

            self.event_rows_layout.insertWidget(index, row)

    def _on_event_target_picked(self, index: int, labels: dict, text: str) -> None:
        target = labels.get(text)
        if target is not None and index < len(self.sequencer.events):
            self.sequencer.events[index]["target"] = target

    def _on_event_delay_committed(self, index: int, edit: QLineEdit) -> None:
        try:
            self.sequencer.events[index]["delay"] = max(0, int(float(edit.text())))  # ms -- whole numbers
        except ValueError:
            edit.setText(str(self.sequencer.events[index]["delay"]))

    def _on_loop_toggled(self, checked: bool) -> None:
        self.sequencer.loop = checked

    def _on_start_events(self) -> None:
        # The 30ms background cycle (region reads -> _gather_readings() ->
        # _last_result) keeps running by default -- and each step's
        # _click_target blocks for ~150-200ms (hide settle delay + the
        # click-to-paste delay), giving that cycle repeated chances to
        # fire *during* a run. Confirmed directly: a value present when
        # step 1 fires can be gone by the time step 2 fires, because a
        # cycle tick recomputed _last_result in between -- step 1 sends
        # correctly, every step after it silently no-ops (key not in the
        # now-different result), which is exactly "only one step runs".
        # Pausing for the run's duration keeps every step working from
        # the same stable snapshot; _on_event_status resumes it once the
        # run stops or finishes on its own.
        self._cycle_timer.stop()
        self.sequencer.start()

    def _on_stop_events(self) -> None:
        self.sequencer.stop()

    def _on_event_status(self, text: str) -> None:
        self.event_status_label.setText(text)
        if not self.sequencer.running and not self._cycle_timer.isActive():
            self._cycle_timer.start(CYCLE_MS)

    # -- OCR loading + the read cycle --------------------------------------
    def _load_recognizer_async(self) -> None:
        if self.recognizer is not None or self._recognizer_loading:
            return
        self._recognizer_loading = True
        self.status_label.setText("Loading OCR model in the background...")

        self._loader = _RecognizerLoader()
        self._loader.ready.connect(self._on_recognizer_ready)
        self._loader_thread = threading.Thread(target=self._loader.load, daemon=True)
        self._loader_thread.start()

    def _on_recognizer_ready(self, recognizer: EasyOCRRecognizer) -> None:
        self.recognizer = recognizer
        self._recognizer_loading = False
        self._update_status()

    def _cycle(self) -> None:
        for watcher in list(self.watchers):
            rect = watcher.capture_rect()
            image = self.grabber.grab(rect)

            if watcher.ref_color is not None:
                locked = still_locked(image, watcher.ref_color)
                watcher.set_locked(locked)
                if not locked:
                    continue

            if self.recognizer is None:
                continue

            crop_hash = hash(image.tobytes())
            if watcher.last_hash == crop_hash:
                continue
            watcher.last_hash = crop_hash

            reading = self.recognizer.read(image, watcher)
            watcher.set_lines([line or "?" for line in reading.lines] or ["?"])
            if reading.ok:
                watcher.last_value = reading.value
                watcher.last_values = reading.values

        self._update_result()

    def _watcher_keys(self, watcher: RegionWatcher) -> list:
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
            elif entry.value_edit.text().strip():
                readings[entry.name] = entry.value_edit.text().strip()
        return readings

    def _update_result(self) -> None:
        readings = self._gather_readings()
        required = getattr(formula, "REQUIRED", ())
        missing = [key for key in required if key not in readings]
        debug_text = ", ".join(f"{k}={v}" for k, v in readings.items()) if readings else "--"
        if missing:
            debug_text += (" | " if readings else "") + f"missing: {', '.join(missing)}"
        self.readings_label.setText(debug_text)
        try:
            result = formula.compute(readings)
        except Exception as exc:
            self.result_label.setText(f"error: {exc}")
            self._last_result = {}
            return
        self.result_label.setText(", ".join(f"{k}={v}" for k, v in result.items()) if result else "--")
        self._last_result = result

    def closeEvent(self, event) -> None:
        self.sequencer.stop()
        self._cycle_timer.stop()
        for watcher in list(self.watchers):
            watcher.close()
        for target in list(self.targets):
            target.close()
        self.grabber.close()
        super().closeEvent(event)
