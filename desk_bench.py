"""Desktop stick bench. Reads XInput. Does not write to the controller."""

from __future__ import annotations

import ctypes
import math
import tkinter as tk
from ctypes import wintypes

BG = "#080b12"
CARD = "#12192a"
LINE = "#293246"
CYAN = "#7dd3fc"
BLUE = "#2563eb"
MUTED = "#9da8bc"
WHITE = "#f5f7ff"


class _Gamepad(ctypes.Structure):
    _fields_ = [
        ("wButtons", wintypes.WORD),
        ("bLeftTrigger", wintypes.BYTE),
        ("bRightTrigger", wintypes.BYTE),
        ("sThumbLX", wintypes.SHORT),
        ("sThumbLY", wintypes.SHORT),
        ("sThumbRX", wintypes.SHORT),
        ("sThumbRY", wintypes.SHORT),
    ]


class _State(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wintypes.DWORD), ("Gamepad", _Gamepad)]


def _lib():
    try:
        return ctypes.windll.xinput1_4
    except OSError:
        return ctypes.windll.xinput1_3


def _axis(raw: int) -> float:
    if abs(raw) < 7800:
        return 0.0
    value = max(-1.0, min(1.0, raw / 32767.0))
    return value


def read_pad(swap: bool) -> tuple[bool, float, float, float, float]:
    try:
        lib = _lib()
    except OSError:
        return False, 0.0, 0.0, 0.0, 0.0
    state = _State()
    for index in range(4):
        if lib.XInputGetState(index, ctypes.byref(state)) == 0:
            g = state.Gamepad
            lx, ly = _axis(g.sThumbLX), _axis(g.sThumbLY)
            rx, ry = _axis(g.sThumbRX), _axis(g.sThumbRY)
            if swap:
                lx, ly, rx, ry = rx, ry, lx, ly
            return True, lx, ly, rx, ry
    return False, 0.0, 0.0, 0.0, 0.0


class Bench(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("RCMTool")
        self.configure(bg=BG)
        self.geometry("920x640")
        self.minsize(760, 560)
        self.swap = False
        self.connected = False

        tk.Label(self, text="PRIVATE SESSION", fg=CYAN, bg=BG, font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=22, pady=(16, 0))
        tk.Label(self, text="RCMTool", fg=WHITE, bg=BG, font=("Segoe UI", 22, "bold")).pack(anchor="w", padx=22)
        tk.Label(
            self,
            text="Desktop window. Reads the pad. Does not write to it. Left stick is the top well.",
            fg=MUTED,
            bg=BG,
            font=("Segoe UI", 10),
        ).pack(anchor="w", padx=22, pady=(0, 10))

        card = tk.Frame(self, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="both", expand=True, padx=18, pady=8)
        tk.Label(card, text="Live sticks", fg=WHITE, bg=CARD, font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=16, pady=(12, 0))
        self.status = tk.Label(card, text="Waiting for an Xbox pad", fg=MUTED, bg=CARD, font=("Segoe UI", 9))
        self.status.pack(anchor="e", padx=16)

        row = tk.Frame(card, bg=CARD)
        row.pack(fill="both", expand=True, padx=8, pady=8)
        self.left = tk.Canvas(row, width=280, height=280, bg=CARD, highlightthickness=0)
        self.left.pack(side="left", padx=12)
        self.right = tk.Canvas(row, width=280, height=280, bg=CARD, highlightthickness=0)
        self.right.pack(side="left", padx=12)

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=18, pady=12)
        tk.Button(bar, text="Left stick dead? Swap sticks", command=self.toggle, bg="#0a0f19", fg=WHITE, relief="flat", padx=12, pady=8).pack(side="left")
        self.readout = tk.Label(bar, text="", fg=MUTED, bg=BG, font=("Consolas", 10))
        self.readout.pack(side="left", padx=16)

        self.after(16, self.tick)

    def toggle(self) -> None:
        self.swap = not self.swap

    def tick(self) -> None:
        ok, lx, ly, rx, ry = read_pad(self.swap)
        self.connected = ok
        self.status.configure(text="Pad live" if ok else "No XInput pad. Plug in and press a button.")
        self.readout.configure(text=f"LX {lx:+.3f}  LY {ly:+.3f}    RX {rx:+.3f}  RY {ry:+.3f}")
        self._draw(self.left, "LX / LY", lx, ly)
        self._draw(self.right, "RX / RY", rx, ry)
        self.after(16, self.tick)

    def _draw(self, canvas: tk.Canvas, title: str, x: float, y: float) -> None:
        canvas.delete("all")
        w = int(canvas.winfo_width() or 280)
        h = int(canvas.winfo_height() or 280)
        cx, cy, r = w / 2, h / 2 + 8, min(w, h) * 0.34
        canvas.create_text(cx, 18, text=title, fill=CYAN, font=("Segoe UI", 9, "bold"))
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=LINE, width=2)
        canvas.create_oval(cx - r * 0.72, cy - r * 0.72, cx + r * 0.72, cy + r * 0.72, outline="#245a9c")
        canvas.create_line(cx - r, cy, cx + r, cy, fill="#202a3d")
        canvas.create_line(cx, cy - r, cx, cy + r, fill="#202a3d")
        px = cx + x * r * 0.72
        py = cy - y * r * 0.72
        canvas.create_oval(px - 16, py - 16, px + 16, py + 16, fill=BLUE, outline=CYAN, width=2)


def main() -> None:
    Bench().mainloop()


if __name__ == "__main__":
    main()
