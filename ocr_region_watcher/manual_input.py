"""A directly user-typed named input -- for values that don't come from the
screen at all (e.g. a percentage you supply yourself), living in the app's
own window since there's no screen position to anchor it to like a region.
"""
from __future__ import annotations

import tkinter as tk


class ManualInput:
    def __init__(self, parent: tk.Misc, name: str, on_remove) -> None:
        self.name = name
        self._on_remove = on_remove

        self.frame = tk.Frame(parent)
        self.frame.pack(fill="x", pady=1)

        self.name_var = tk.StringVar(value=name)
        name_entry = tk.Entry(self.frame, textvariable=self.name_var, width=6, font=("Consolas", 9))
        name_entry.pack(side="left", padx=(0, 4))
        name_entry.bind("<Return>", self._on_name_committed)
        name_entry.bind("<FocusOut>", self._on_name_committed)

        self.value_var = tk.StringVar(value="")
        value_entry = tk.Entry(self.frame, textvariable=self.value_var, font=("Consolas", 9), justify="right")
        value_entry.pack(side="left", fill="x", expand=True)

        tk.Label(self.frame, text="%", font=("Consolas", 9), fg="gray").pack(side="left", padx=(2, 4))

        tk.Button(
            self.frame, text="x", command=self._remove, font=("Consolas", 8, "bold"),
            fg="#cc3333", relief="flat", bd=0, padx=3,
        ).pack(side="left")

    def _on_name_committed(self, event: tk.Event | None = None) -> None:
        new_name = self.name_var.get().strip()
        if new_name:
            self.name = new_name
        else:
            self.name_var.set(self.name)  # reject blank, revert to the last real name

    def _remove(self) -> None:
        self.frame.destroy()
        if self._on_remove:
            self._on_remove(self)

    def value(self) -> float | None:
        """Parsed numeric value (a trailing % you typed is stripped), or
        None if the field isn't currently a valid number."""
        raw = self.value_var.get().strip().rstrip("%").strip()
        try:
            return float(raw)
        except ValueError:
            return None
