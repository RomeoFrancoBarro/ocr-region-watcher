"""Your calculation logic goes here.

`compute` is called once per cycle with the latest value from every named
input -- both regions (read from the screen) and manual inputs (typed in
the app window), keyed by whatever name you gave them. Whatever you return
is shown in the app's Result row.

Left as a no-op stub -- the wiring (calling this every cycle, showing the
result) is already connected; this is just where the actual math goes.
Edit this file, then restart the app (it's not hot-reloaded).
"""
from __future__ import annotations


# Keys compute() needs before it'll return anything. Purely informational --
# app.py reads this (if present; falls back to nothing if a rewritten
# compute() doesn't define it) to tell you what's still missing in the
# debug panel, e.g. "missing: PW, C", instead of just a blank Result.
# M and W are no longer read from `readings` directly -- they're derived
# below from Budget/PM/PW -- so they've dropped out of REQUIRED.
REQUIRED = ("Budget", "PM", "PW", "C")


def compute(readings: dict) -> dict:
    """Region-M / region-W profit formula, with M/W allocated out of a
    single Budget rather than typed in individually.

        M = Budget * PW / (PM + PW)
        W = Budget * PM / (PM + PW)
        Profit_M = M * (PM / 100) + (C / 100) * (M + W) - (M + W)
        Profit_W = W * (PW / 100) + (C / 100) * (M + W) - (M + W)
        Ratio_M  = PW / PM

    Inputs, keyed by name in `readings` (see REQUIRED above):
      - Budget: manual input, typed in directly -- total split across M/W
      - PM, PW: regions (screen-read numbers) -- fixed keys regardless of
                the region's display name, see app.py's _PAIRED_FORMULA_KEYS
      - C:      manual input, 0-100 scale (a percentage, e.g. 5 for 5%)

    Until all four have produced a value, this returns {} (no-op), same
    as an input that hasn't been read yet -- avoids raising and showing
    `error: ...` in the Result row before the setup is complete. (The app's
    debug panel reports which ones via REQUIRED, above.) M and W are still
    included in the returned dict (derived, not looked up) so you can see
    what got allocated, same as everything else in this app.
    """
    values = {key: readings.get(key) for key in REQUIRED}
    if any(v is None for v in values.values()):
        return {}
    if not all(isinstance(v, (int, float)) for v in values.values()):
        return {}

    budget, pm, pw, c = (values[key] for key in REQUIRED)

    denom = pm + pw
    if denom == 0:
        # Both regions read 0 (or misread) -- the allocation is undefined,
        # not an error; report it plainly instead of dividing by zero.
        return {"status": "undefined (PM+PW=0)"}

    m = budget * pw / denom
    w = budget * pm / denom
    total = m + w  # == budget, kept explicit since the rest of the formula is stated in these terms
    profit_m = m * (pm / 100) + (c / 100) * total - total
    profit_w = w * (pw / 100) + (c / 100) * total - total

    result = {
        # Whole pesos, no centavos -- rounded only for display here; the
        # unrounded m/w above still feed the Profit_M/Profit_W math, so this
        # doesn't compound rounding error into those.
        "M": round(m),
        "W": round(w),
        "Profit_M": round(profit_m, 2),
        "Profit_W": round(profit_w, 2),
    }
    # PM is a live OCR read -- a misread/zero crop is possible, so guard
    # the divide rather than let a bad frame throw and blank the whole row.
    result["Ratio_M"] = round(pw / pm, 4) if pm != 0 else "undefined (PM=0)"
    return result
