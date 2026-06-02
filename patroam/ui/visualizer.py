"""PATROAM's state visualizer — a modern, responsive "core" orb.

A glowing energy core surrounded by a reactive frequency ring and rotating
HUD-style arcs. It reads sleeping-vs-active purely through motion and colour —
no text labels:

  * idle / sleeping  — dim, slow, near-flat ring (a calm breath).
  * listening        — blue, lively reactive ring.
  * thinking         — violet, fast rotation + orbiting nodes.
  * speaking         — green, strong pulsing ring.

Everything is sized from the smaller window dimension, so it scales and stays
centred as the window resizes.
"""

import math
import tkinter as tk


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _hex(rgb):
    return "#%02x%02x%02x" % rgb


def _blend(c1, c2, t):
    t = max(0.0, min(1.0, t))
    a, b = _rgb(c1), _rgb(c2)
    return _hex(tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3)))


class Visualizer(tk.Canvas):
    BG = "#0a0c11"
    FPS_MS = 33

    # core colour, glow colour, rotation speed, pulse speed, reactivity, node count
    PARAMS = {
        "idle":      ("#39435a", "#5b6a8c", 0.12, 0.45, 0.06, 0),
        "sleeping":  ("#37486a", "#5e78a8", 0.10, 0.40, 0.12, 0),
        "listening": ("#4f8ef7", "#8fbaff", 0.55, 1.40, 0.62, 3),
        "thinking":  ("#7c3aed", "#b18cf5", 1.20, 1.80, 0.45, 3),
        "speaking":  ("#22c55e", "#74e6a0", 0.85, 2.00, 0.95, 3),
    }

    def __init__(self, master, bg=None, **kw):
        super().__init__(master, bg=bg or self.BG, highlightthickness=0, bd=0, **kw)
        self._bg = bg or self.BG
        self.state_name = "idle"
        self._t = 0.0
        self._react = 0.0          # smoothed reactivity
        self._running = True
        self.after(self.FPS_MS, self._tick)

    def set_state(self, name):
        if name in self.PARAMS:
            self.state_name = name

    def stop(self):
        self._running = False

    # ── animation loop ──────────────────────────────────────────────────────
    def _tick(self):
        if not self._running:
            return
        self._t += 0.045
        try:
            self._draw()
        except tk.TclError:
            return
        self.after(self.FPS_MS, self._tick)

    def _draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            return
        core, glow, rot, pulse, react, nodes = self.PARAMS[self.state_name]
        self._react += (react - self._react) * 0.12
        t = self._t

        self.delete("all")
        cx, cy = w / 2.0, h / 2.0
        unit = min(w, h)
        ring_dim = _blend(self._bg, glow, 0.30)

        # Rotating HUD arcs (thin segmented rings at a few radii).
        for rad_f, segs, direction, ext, wdt in (
            (0.34, 3, 1, 34, 2),
            (0.40, 2, -1, 26, 2),
            (0.46, 6, 1, 10, 1),
        ):
            rad = unit * rad_f
            base = math.degrees(t * rot) * direction
            for s in range(segs):
                start = base + s * (360.0 / segs)
                self.create_arc(cx - rad, cy - rad, cx + rad, cy + rad,
                                start=start, extent=ext, style=tk.ARC,
                                outline=ring_dim, width=wdt)

        # Reactive frequency ring — radial bars whose length breathes/reacts.
        n = 60
        ri = unit * 0.150
        max_len = unit * 0.075
        for i in range(n):
            ang = 2 * math.pi * i / n
            wave = 0.45 + 0.55 * math.sin(t * 3.0 + i * 0.42)
            ln = max_len * (0.22 + self._react * wave)
            ca, sa = math.cos(ang), math.sin(ang)
            self.create_line(cx + ca * ri, cy + sa * ri,
                             cx + ca * (ri + ln), cy + sa * (ri + ln),
                             fill=glow, width=2)

        # Glowing core: faint halo down to a bright centre.
        core_r = unit * 0.085 * (1 + 0.10 * math.sin(t * pulse))
        layers = 18
        glow_r = core_r * 2.7
        for k in range(layers):
            tt = k / (layers - 1)
            rad = glow_r * (1 - tt) + core_r * tt
            self.create_oval(cx - rad, cy - rad, cx + rad, cy + rad,
                             fill=_blend(self._bg, glow, tt ** 1.7), outline="")
        self.create_oval(cx - core_r, cy - core_r, cx + core_r, cy + core_r,
                         fill=core, outline="")

        # Orbiting nodes (active states only).
        if nodes:
            orad = unit * 0.225
            dr = unit * 0.010
            for d in range(nodes):
                ang = t * rot * 2.0 + d * (2 * math.pi / nodes)
                x = cx + math.cos(ang) * orad
                y = cy + math.sin(ang) * orad
                self.create_oval(x - dr, y - dr, x + dr, y + dr,
                                 fill=glow, outline="")
