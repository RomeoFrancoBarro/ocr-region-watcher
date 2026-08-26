"""Write-back counterpart to the read side (capture.py/recognize.py): click
a screen position, then paste text there via the clipboard.

Copy+paste beats simulated per-character typing here -- one atomic paste
event regardless of string length, instead of cost (and dropped-keystroke
risk in laggy/JS-validated fields) scaling with every character sent.

Deliberately manual-trigger only -- nothing in this module runs on a timer
or reacts to a value changing. It only ever executes because the Qt app's
Send button called it, once, right then. pyautogui's failsafe (drag the
mouse to a screen corner) stays on -- do not disable it here.
"""
from __future__ import annotations

import time

import pyautogui
import pyperclip

# Gives the target app a moment to actually receive focus from the click
# before the paste keystroke arrives -- too fast and the paste can land on
# whatever had focus a moment ago instead.
CLICK_TO_PASTE_DELAY_S = 0.05


def send(x: int, y: int, text: str | None = None, *, click: bool = True, paste: bool = True) -> None:
    """Click (x, y) and/or paste `text` there -- click and paste are
    independent, both default on. Click always happens first when both
    are requested; paste with click=False reuses whatever's already
    focused (e.g. from an earlier step in a sequence) without clicking,
    and potentially re-toggling, it again. Used by the Qt app's per-target
    click/paste checkboxes (ocr_region_watcher/qt/app.py).

    The click moves the OS-wide mouse cursor to (x, y) as a side effect of
    giving the target focus -- reported directly: after Send, the cursor
    was left sitting there instead of going back to wherever it started.
    So when we click, note where the mouse was first and move it back
    once we're done, even if the paste step raises.
    """
    origin = pyautogui.position() if click else None
    try:
        if click:
            pyautogui.click(x, y)
        if paste:
            if click:
                time.sleep(CLICK_TO_PASTE_DELAY_S)
            pyperclip.copy(text)
            pyautogui.hotkey("ctrl", "v")
    finally:
        if origin is not None:
            pyautogui.moveTo(*origin)
