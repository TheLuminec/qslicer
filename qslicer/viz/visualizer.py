"""
QDG Visualizer - an interactive matplotlib window for exploring slices.

Usage (needs matplotlib: pip install qslicer[viz]):
    qslicer viz path/to/circuit.py [--profile PROFILE] [--circuit N] [--no-lint]

The file is read with the static front ends, so nothing in it runs. The view
follows the web page's graph (webapp/static/js/graph.js): instructions left to
right, one wire per qubit and per classical bit, gate boxes colored by kind,
classical edges routed along their bit, lint markers on the instructions a
finding is about, and an overview strip of the whole circuit underneath.

Large circuits
--------------
A circuit can have tens of thousands of instructions; only those in view, plus
a quarter of a view either side, are ever turned into artists. The layout is
fixed and x grows with the instruction index, so what is in view is found by
bisection (nodes) and one numpy mask (edges). Panning inside the drawn window
only moves the axes limits; leaving it, zooming, a slice or a filter rebuilds
the few hundred artists in view. The linters run on a background thread, so
the window opens before they finish.

Mouse:  click an instruction to slice from it; drag to pan; scroll to pan
        (shift+scroll up and down, ctrl+scroll to zoom); click or drag the
        overview strip to jump there; hover an instruction for its card.
Keys:   left/right previous/next criterion   b/f/d  backward/forward/toggle
        [ ]  previous/next lint finding        l  lint markers on/off
        , .  previous/next circuit             1-6  follow a dependency type
        + -  zoom    0  fit    home/end  start/end    esc  clear    ?  help
"""

import argparse
import os
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Sequence

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import (EllipseCollection, LineCollection, PathCollection,
                                    PolyCollection)
from matplotlib.colors import to_rgba, to_rgba_array
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Patch, Rectangle
from matplotlib.path import Path
from matplotlib.textpath import TextPath
from matplotlib.transforms import Bbox, TransformedBbox
from matplotlib.widgets import Button

from ..qdg import QDG, list_rule_profiles
from . import model
from .model import (ACCENT, BG, BG_SUNKEN, CATEGORIES, CATEGORY_FILL, CL_SHIFT, CRITERION,
                    DEP_COLORS, DEPENDENCIES, IN_SLICE, INK, NODE_H, SEVERITY_COLORS,
                    SEVERITY_NAME, SURFACE, SURFACE_2, SURFACE_3, TEXT, TEXT_DIM, TEXT_FAINT,
                    Document, Graph)

# --------------------------------------------------------------------------
# View constants (graph.js)
# --------------------------------------------------------------------------

READABLE_SCALE = 0.55   # fitting never zooms out past this
MIN_SCALE = 0.15        # nor can the user: the overview strip shows the rest
MAX_SCALE = 4.0
LOD_LABELS = 0.4        # below this zoom the index and line labels are hidden
LOD_TEXT = 0.25         # below this the gate names too
OVERSCAN = 0.25         # drawn beyond the view on each side, as a fraction of it
CURVE_POINTS = 14       # samples along a curved edge
ARROW_LEN, ARROW_HALF = 7.0, 3.0
DIM_NODE, DIM_EDGE, BASE_EDGE = 0.22, 0.08, 0.55
MONO = "monospace"

_CATEGORY_RGBA = to_rgba_array([CATEGORY_FILL[c] for c in CATEGORIES])
_DEP_RGBA = to_rgba_array([DEP_COLORS[d] for d in DEPENDENCIES])
_SEVERITY_RGBA = {rank: to_rgba(SEVERITY_COLORS[name]) for rank, name in SEVERITY_NAME.items()}

# A rounded box as an octagon: (sign of w/2, offset, sign of h/2, offset).
_CHAMFER = 4.0
_BOX = np.array([(-1, _CHAMFER, -1, 0), (1, -_CHAMFER, -1, 0), (1, 0, -1, _CHAMFER),
                 (1, 0, 1, -_CHAMFER), (1, -_CHAMFER, 1, 0), (-1, _CHAMFER, 1, 0),
                 (-1, 0, 1, -_CHAMFER), (-1, 0, -1, _CHAMFER)], dtype=float)

# Keys matplotlib's own key map must not see: the ones this window uses, and
# the log-scale and grid toggles, which would wreck the graph's axes. The rest
# (s to save, p and o for the toolbar's pan and zoom, h for home, q to close)
# keep working.
_TAKEN_KEYS = {"left", "right", "home", "end", "b", "f", "d", "l", "k", "L", "g", "G",
               "0", "1", "2", "3", "4", "5", "6", "+", "-", "=", "[", "]", ",", ".",
               "escape", "?", "shift+left", "shift+right"}

HELP = """\
Mouse
  click an instruction      slice from it
  drag / scroll             pan    (shift+scroll: up and down)
  ctrl+scroll               zoom around the pointer
  click the overview strip  jump there
  hover an instruction      its card: gate, source line, lint findings

Keys
  left / right    previous / next instruction as the criterion
  b  f  d         backward slice / forward slice / toggle
  1 - 6           follow or ignore a dependency type
  [  ]            previous / next lint finding
  l               lint markers on / off
  ,  .            previous / next circuit the file builds
  +  -  0         zoom in / out / fit
  home  end       start / end of the circuit
  esc             clear the slice and the marks
  ?               this help"""


def _pt(px: float, k: float, dpi: float) -> float:
    """Content pixels at zoom ``k`` in points."""
    return px * k * 72.0 / dpi


class QDGVisualizer:
    """Interactive QDG viewer.

    ``target`` is a ``model.Document`` (a file: circuits, values, lint) or,
    as before, a bare ``QDG``. ``show=False`` builds the figure without a
    window, for ``savefig`` and the tests; the linters then run inline.

    ``blit`` (by default: on in a window whose canvas can) redraws only the
    graph, the overview and the status lines after a pan, a hover or a slice,
    over a copy of the rest taken at the last full draw: the buttons and the
    legend cost more to draw than the graph itself.
    """

    def __init__(self, target: Any, title: str = "", fig=None, ax=None, show: bool = True,
                 circuit: int = 0, criterion: Optional[int] = None,
                 direction: str = "backward", blit: Optional[bool] = None) -> None:
        if isinstance(target, QDG):
            self.document = None
            self.graph = Graph(target)
        else:
            self.document = target
            self.graph = target.graph(circuit)
        self.title = title
        self.interactive = show
        self.direction = direction
        self.criterion: Optional[int] = None
        self.slice_mask: Optional[np.ndarray] = None
        self.slice_size = 0
        self.follow = set(DEPENDENCIES)
        self.show_lint = True
        self.finding: Optional[int] = None       # index into graph.diagnostics
        self.marked = np.zeros(self.graph.n, dtype=bool)
        self.marked_qubits: set = set()
        self.hovered: Optional[int] = None
        self.window: Optional[tuple] = None      # (x0, x1, k) currently drawn
        self.view = {"cx": 0.0, "cy": 0.0, "k": 1.0}
        self._press = None
        self._syncing = False
        self._lint_threads: Dict[int, threading.Thread] = {}
        self._lint_results: Dict[int, List[Dict[str, Any]]] = {}
        self._timer = None
        self.timings: Dict[str, float] = {}
        self._backgrounds: List[Any] = []
        self._regions: List[Any] = []
        self._chrome_changed = True     # a button or chip changed: the next draw is full
        self._blit_timer = None
        self._blit_pending = False

        self._build_figure(fig, ax)
        canvas = self.fig.canvas
        self._blit = bool(show and getattr(canvas, "supports_blit", False)) if blit is None else blit
        if self._blit:
            self._setup_blit()
        self._load_graph(fit=True)
        if criterion is not None:
            self.set_criterion(criterion, reveal=True)
        if show:
            plt.show()

    # ================================================================ figure ==

    def _build_figure(self, fig, ax) -> None:
        if fig is None:
            fig = plt.figure(figsize=(14, 8), facecolor=BG)
            try:
                fig.canvas.manager.set_window_title(f"qslicer viz - {self.title or 'QDG'}")
            except AttributeError:
                pass
        self.fig = fig
        fig.patch.set_facecolor(BG)
        self.ax = ax if ax is not None else fig.add_axes([0.0, 0.2, 1.0, 0.69])
        self.ax.set_facecolor(BG)
        self.ax.set_axis_off()
        self.ax.set_navigate(True)
        self.ax_over = fig.add_axes([0.012, 0.125, 0.976, 0.062])
        self.ax_over.set_facecolor(BG_SUNKEN)
        self.ax_over.set_xticks([])
        self.ax_over.set_yticks([])
        for spine in self.ax_over.spines.values():
            spine.set_color(SURFACE_3)
        self.ax_over.set_navigate(False)

        self.title_text = fig.text(0.012, 0.965, "", color=TEXT, fontsize=11, fontweight="bold",
                                   va="center")
        self.status_text = fig.text(0.012, 0.93, "", color=TEXT_DIM, fontsize=9, va="center",
                                    family=MONO)
        self.lint_text = fig.text(0.012, 0.902, "", color=TEXT_FAINT, fontsize=8.5, va="center",
                                  family=MONO)
        self.help_text = self.ax.text(0.5, 0.5, HELP, transform=self.ax.transAxes, ha="center",
                                      va="center", family=MONO, fontsize=9, color=TEXT,
                                      multialignment="left",
                                      zorder=20, visible=False,
                                      bbox=dict(boxstyle="round,pad=1", fc=SURFACE, ec=SURFACE_3))
        legend = [Patch(color=CRITERION, label="criterion"), Patch(color=IN_SLICE, label="in slice")]
        legend += [Patch(color=CATEGORY_FILL[c], label=c) for c in CATEGORIES[:4]]
        legend += [Patch(facecolor=BG, edgecolor=SEVERITY_COLORS[s], linestyle="--", label=s)
                   for s in ("error", "warning", "info")]
        fig.legend(handles=legend, loc="upper right", bbox_to_anchor=(0.995, 0.995), ncol=5,
                   fontsize=7.5, frameon=False, labelcolor=TEXT_DIM, handlelength=1.2,
                   columnspacing=1.0)

        # Buttons along the bottom; dependency chips to their right.
        def button(x, w, label, callback):
            axb = fig.add_axes([x, 0.035, w, 0.05])
            b = Button(axb, label, color=SURFACE_2, hovercolor=SURFACE_3)
            b.label.set_color(TEXT)
            b.label.set_fontsize(9)
            b.on_clicked(lambda _event: callback())
            return b

        self.btn_prev = button(0.012, 0.055, "◀ Prev", lambda: self.step(-1))
        self.btn_next = button(0.07, 0.055, "Next ▶", lambda: self.step(1))
        self.btn_dir = button(0.128, 0.075, "Backward", self.toggle_direction)
        self.btn_clear = button(0.206, 0.05, "Clear", self.clear)
        self.btn_lint = button(0.259, 0.07, "Lint: on", self.toggle_lint)
        self.btn_find_prev = button(0.332, 0.035, "[", lambda: self.step_finding(-1))
        self.btn_find_next = button(0.37, 0.035, "]", lambda: self.step_finding(1))
        self.btn_fit = button(0.408, 0.04, "Fit", self.fit)
        self.btn_help = button(0.451, 0.03, "?", self.toggle_help)
        self.btn_circ_prev = button(0.484, 0.03, "‹", lambda: self.step_circuit(-1))
        self.btn_circ_next = button(0.517, 0.03, "›", lambda: self.step_circuit(1))

        self.dep_buttons: List[Button] = []
        for dep in DEPENDENCIES:   # placed by _load_graph, which knows which exist
            axb = fig.add_axes([0.565, 0.035, 0.05, 0.05])
            b = Button(axb, "", color=SURFACE_2, hovercolor=SURFACE_3)
            b.label.set_fontsize(7.5)
            b.on_clicked(lambda _event, dep=dep: self.toggle_dependency(dep))
            self.dep_buttons.append(b)

        # Persistent artists of the graph axes; their data changes, they stay.
        ax = self.ax
        self.lanes = LineCollection([], colors=SURFACE_2, linewidths=1, linestyles=(0, (3, 5)),
                                    zorder=0)
        self.cwires = LineCollection([], colors=SURFACE_3, linewidths=0.9, zorder=0)
        self.marked_lanes = LineCollection([], colors=SEVERITY_COLORS["error"], linewidths=1.5,
                                           zorder=0.5)
        self.edges = LineCollection([], zorder=1, capstyle="butt", joinstyle="round")
        self.arrows = PolyCollection([], zorder=1.1, linewidths=0)
        self.ticks = LineCollection([], colors=TEXT_FAINT, linewidths=1.1,
                                    linestyles=(0, (2, 3)), zorder=1.2)
        self.spans = LineCollection([], zorder=2)
        self.boxes = PolyCollection([], zorder=3, joinstyle="round")
        for artist in (self.lanes, self.cwires, self.marked_lanes, self.edges, self.arrows,
                       self.ticks, self.spans, self.boxes):
            ax.add_collection(artist, autolim=False)
        self.dots = self.badges = self.tick_dots = None   # EllipseCollections, rebuilt
        self.glyphs = _Glyphs()
        self.text = PathCollection([], linewidths=0, edgecolors="none", zorder=3.5)
        self.badge_text = PathCollection([], linewidths=0, edgecolors="none", zorder=4.5)
        ax.add_collection(self.text, autolim=False)
        ax.add_collection(self.badge_text, autolim=False)
        self.drawn_nodes = self.drawn_edges = self.drawn_labels = 0
        # Wire names, kept at the left edge of the view however far it pans.
        self.lane_names: List[tuple] = []
        self.lane_boxes = PolyCollection([], facecolors=to_rgba(BG, 0.85), linewidths=0,
                                         zorder=5.9)
        self.lane_text = PathCollection([], linewidths=0, edgecolors="none", zorder=6)
        ax.add_collection(self.lane_boxes, autolim=False)
        ax.add_collection(self.lane_text, autolim=False)
        self.hover = ax.annotate("", xy=(0, 0), xytext=(14, 14), textcoords="offset points",
                                 fontsize=8.5, family=MONO, color=TEXT, zorder=15,
                                 visible=False, annotation_clip=False,
                                 bbox=dict(boxstyle="round,pad=0.6", fc=SURFACE, ec=SURFACE_3,
                                           alpha=0.97))

        self.overview_image = None
        self.overview_view = Rectangle((0, 0), 1, 1, fc=(0.537, 0.706, 0.98, 0.22),
                                       ec=ACCENT, lw=1, zorder=3)
        self.ax_over.add_patch(self.overview_view)
        self._overview_dirty = True

        canvas = fig.canvas
        manager = getattr(canvas, "manager", None)
        handler = getattr(manager, "key_press_handler_id", None)
        if handler is not None:
            # Keep matplotlib's own keys (save, fullscreen ...) except the ones used here.
            canvas.mpl_disconnect(handler)
            self._mpl_keys = canvas.mpl_connect("key_press_event", self._default_keys)
        canvas.mpl_connect("key_press_event", self._on_key)
        canvas.mpl_connect("button_press_event", self._on_press)
        canvas.mpl_connect("button_release_event", self._on_release)
        canvas.mpl_connect("motion_notify_event", self._on_motion)
        canvas.mpl_connect("scroll_event", self._on_scroll)
        canvas.mpl_connect("resize_event", self._on_resize)
        self.ax.callbacks.connect("xlim_changed", self._on_limits)
        self.ax.callbacks.connect("ylim_changed", self._on_limits)

    def _default_keys(self, event) -> None:
        if event.key not in _TAKEN_KEYS:
            from matplotlib.backend_bases import key_press_handler
            key_press_handler(event, self.fig.canvas, getattr(self.fig.canvas, "toolbar", None))

    # ================================================================== data ==

    def _load_graph(self, fit: bool = False) -> None:
        """Show ``self.graph``: wires, lint, chips, overview; nothing selected."""
        g = self.graph
        self.criterion = None
        self.slice_mask = None
        self.slice_size = 0
        self.finding = None
        self.marked = np.zeros(g.n, dtype=bool)
        self.marked_qubits = set()
        self.hovered = None
        self.hover.set_visible(False)
        self.window = None
        self._start_lint()

        right = g.content_width - model.LEFT_PAD + 20
        self.lanes.set_segments([[(model.LEFT_PAD - 30, g.lane_y(q)), (right, g.lane_y(q))]
                                 for q in range(g.num_qubits)])
        self.cwires.set_segments([[(model.LEFT_PAD - 30, g.clbit_y(c) + d), (right, g.clbit_y(c) + d)]
                                  for c in range(g.num_clbits) for d in (-1.5, 1.5)])
        self.lane_names = [(f"q{q}", g.lane_y(q), q) for q in range(g.num_qubits)]
        self.lane_names += [(f"c{c}", g.clbit_y(c), None) for c in range(g.num_clbits)]
        # A chip per dependency type this graph has, packed left to right.
        counts = g.dependency_counts()
        x = 0.565
        for i, (dep, b) in enumerate(zip(DEPENDENCIES, self.dep_buttons)):
            b.ax.set_visible(counts[dep] > 0)
            label = f"{i + 1} {dep.name} {_count(counts[dep])}"
            b.label.set_text(label)
            if counts[dep]:
                width = 0.012 + 0.0056 * len(label)
                b.ax.set_position([x, 0.035, width, 0.05])
                x += width + 0.004
        many = self.document is not None and len(self.document.circuits) > 1
        self.btn_circ_prev.ax.set_visible(many)
        self.btn_circ_next.ax.set_visible(many)
        self.ax_over.set_xlim(0, g.content_width)
        self._refresh_lint_lanes()
        self._refresh_chips()
        self._overview_dirty = True
        if fit:
            self.fit(draw=False)
        else:
            self._apply_view(force=True, draw=False)
        self._update_text()
        self._redraw()

    # ================================================================= lint ==

    def _start_lint(self) -> None:
        g, doc = self.graph, self.document
        if doc is None or g.lint_state != "pending":
            return
        index = g.info.get("index", 0)
        if not self.interactive:
            g.set_diagnostics(doc.lint(g))
            g.lint_state = "done"
            return
        if index in self._lint_threads:
            return

        def work():
            try:
                self._lint_results[index] = doc.lint(g)
            except Exception as exc:  # a linter crash must not take the window down
                self._lint_results[index] = [{
                    "code": "LINT-CRASH", "severity": "error", "line": 0, "column": 0,
                    "message": f"the linters failed: {exc}", "hint": ""}]

        thread = threading.Thread(target=work, name=f"qslicer-lint-{index}", daemon=True)
        self._lint_threads[index] = thread
        self._lint_started = time.perf_counter()
        thread.start()
        if self._timer is None:
            self._timer = self.fig.canvas.new_timer(interval=120)
            self._timer.add_callback(self._poll_lint)
        self._timer.start()

    def _poll_lint(self) -> None:
        if self.document is None:
            return
        for index, findings in list(self._lint_results.items()):
            graph = self.document.graph(index)
            graph.set_diagnostics(findings)
            graph.lint_state = "done"
            del self._lint_results[index]
            if graph is self.graph:
                self.timings["lint"] = time.perf_counter() - self._lint_started
                _print_findings(self.document, graph)
                self._refresh_lint_lanes()
                self._overview_dirty = True
                self._update_text()
                self._rebuild()
                self._redraw()
        if not any(t.is_alive() for t in self._lint_threads.values()) and not self._lint_results:
            self._timer.stop()

    def _place_lane_labels(self) -> None:
        """Wire names at the view's left edge, never under 9 screen pixels; a
        wire a lint finding is about (QD07, an idle qubit) in red."""
        g, k = self.graph, self.view["k"]
        left = self.ax.get_xlim()[0] + 5 / k
        pad = 3 / k
        paths, colors, boxes = [], [], []
        for label, y, q in self.lane_names:
            size = max(10.0, 9.0 / k) * (1.0 if q is not None else 0.9)
            width = len(label) * self.glyphs.advance * size
            paths.append(self.glyphs.path(label, left + width / 2, y + 0.36 * size, size))
            flagged = q is not None and ((self.show_lint and g.lint_state == "done"
                                          and q in g.lint_by_qubit) or q in self.marked_qubits)
            colors.append(SEVERITY_COLORS["error"] if flagged else TEXT_FAINT)
            top, bottom = y - 0.62 * size, y + 0.62 * size
            boxes.append([(left - pad, top), (left + width + pad, top),
                          (left + width + pad, bottom), (left - pad, bottom)])
        self.lane_text.set_paths(paths)
        self.lane_text.set_facecolor(colors or "none")
        self.lane_boxes.set_verts(boxes)

    def _refresh_lint_lanes(self) -> None:
        g = self.graph
        self._place_lane_labels()
        right = g.content_width - model.LEFT_PAD + 20
        self.marked_lanes.set_segments([[(model.LEFT_PAD - 30, g.lane_y(q)), (right, g.lane_y(q))]
                                        for q in sorted(self.marked_qubits)])

    # =============================================================== actions ==

    def set_criterion(self, idx: Optional[int], reveal: bool = False) -> None:
        """Slice from instruction ``idx`` (None clears the slice)."""
        g = self.graph
        self.criterion = idx
        if idx is None:
            self.slice_mask = None
            self.slice_size = 0
        else:
            start = time.perf_counter()
            follow = None if len(self.follow) == len(DEPENDENCIES) else self.follow
            ids = g.slice(idx, self.direction, follow)
            mask = np.zeros(g.n, dtype=bool)
            mask[list(ids)] = True
            self.slice_mask = mask
            self.slice_size = len(ids)
            self.timings["slice"] = time.perf_counter() - start
            if reveal:
                self.reveal([idx])
        self._overview_dirty = True
        self._update_text()
        self._rebuild()
        self._redraw()

    def step(self, delta: int) -> None:
        n = self.graph.n
        if self.criterion is None:
            idx = 0 if delta > 0 else n - 1
        else:
            idx = (self.criterion + delta) % n
        self.set_criterion(idx, reveal=True)

    def toggle_direction(self) -> None:
        self.set_direction("forward" if self.direction == "backward" else "backward")

    def set_direction(self, direction: str) -> None:
        self.direction = direction
        self.btn_dir.label.set_text(direction.capitalize())
        self._chrome_changed = True
        self.set_criterion(self.criterion)

    def toggle_dependency(self, dep) -> None:
        if dep in self.follow:
            if len(self.follow) > 1:
                self.follow.discard(dep)
        else:
            self.follow.add(dep)
        self._refresh_chips()
        self.set_criterion(self.criterion)

    def _refresh_chips(self) -> None:
        self._chrome_changed = True
        for dep, b in zip(DEPENDENCIES, self.dep_buttons):
            on = dep in self.follow
            color = DEP_COLORS[dep] if on else SURFACE_2
            b.color = color
            b.hovercolor = SURFACE_3
            b.ax.set_facecolor(color)
            b.label.set_color("white" if on else TEXT_FAINT)

    def toggle_lint(self) -> None:
        if self.graph.lint_state == "off":
            return
        self.show_lint = not self.show_lint
        self.btn_lint.label.set_text("Lint: on" if self.show_lint else "Lint: off")
        self._chrome_changed = True
        self._refresh_lint_lanes()
        self._overview_dirty = True
        self._update_text()
        self._rebuild()
        self._redraw()

    def step_finding(self, delta: int) -> None:
        """Mark the next lint finding's instructions and wires, and go to them."""
        findings = self.graph.diagnostics
        if not findings or not self.show_lint:
            return
        if self.finding is None:
            self.finding = 0 if delta > 0 else len(findings) - 1
        else:
            self.finding = (self.finding + delta) % len(findings)
        finding = findings[self.finding]
        self.marked = np.zeros(self.graph.n, dtype=bool)
        self.marked[finding.get("nodes", [])] = True
        self.marked_qubits = set(q for q in finding.get("qubits", []) or []
                                 if isinstance(q, int) and q < self.graph.num_qubits)
        self._refresh_lint_lanes()
        if finding.get("nodes"):
            self.reveal(finding["nodes"][:1])
        elif self.marked_qubits:
            y = self.graph.lane_y(min(self.marked_qubits))
            half_h = self._axes_px()[1] / self.view["k"] / 2
            if abs(y - self.view["cy"]) > half_h - 30:     # only if the wire is out of view
                self.view["cy"] = y
                self._apply_view(draw=False)
        self._overview_dirty = True
        self._update_text()
        self._rebuild()
        self._redraw()

    def step_circuit(self, delta: int) -> None:
        doc = self.document
        if doc is None or len(doc.circuits) < 2:
            return
        index = (self.graph.info.get("index", 0) + delta) % len(doc.circuits)
        start = time.perf_counter()
        self.graph = doc.graph(index)
        self._load_graph(fit=True)
        self.timings["circuit"] = time.perf_counter() - start

    def clear(self) -> None:
        self.finding = None
        self.marked = np.zeros(self.graph.n, dtype=bool)
        self.marked_qubits = set()
        self._refresh_lint_lanes()
        self.set_criterion(None)

    def toggle_help(self) -> None:
        self.help_text.set_visible(not self.help_text.get_visible())
        self._redraw()

    # ================================================================== view ==

    def _axes_px(self):
        bbox = self.ax.bbox
        return max(1.0, bbox.width), max(1.0, bbox.height)

    def fit(self, draw: bool = True) -> None:
        """The whole circuit if it fits at a readable zoom, else its start."""
        g = self.graph
        width, height = self._axes_px()
        # The bounds graph.js fits: every box with a margin, and every wire.
        min_x, max_x = g.left[0] - 50, g.right[-1] + 20
        min_y = min(float(g.y.min()) - 48, model.TOP_PAD - 48)   # room for the #index labels
        max_y = max(float(g.y.max()) + 40, g.lane_y(g.num_qubits - 1) + 30)
        if g.num_clbits:
            max_y = max(max_y, g.clbit_y(g.num_clbits - 1) + 18)
        k_w, k_h = width / (max_x - min_x) * 0.94, height / (max_y - min_y) * 0.94
        # A long strip is shown at a readable zoom from its start; but every
        # wire stays in view, so a tall circuit may go below that zoom.
        k = max(min(k_w, k_h), min(k_h, READABLE_SCALE))
        k = min(1.6, max(MIN_SCALE, k))
        self.view["k"] = k
        self.view["cy"] = (min_y + max_y) / 2
        view_w = width / k
        # A strip too long to fit starts at its left end.
        self.view["cx"] = ((min_x + max_x) / 2 if view_w >= max_x - min_x
                           else min_x - 16 / k + view_w / 2)
        self._apply_view(force=True, draw=draw)

    def reveal(self, ids: Sequence[int]) -> None:
        """Pan, keeping the zoom, so the instructions are in view."""
        g = self.graph
        ids = [i for i in ids if 0 <= i < g.n]
        if not ids:
            return
        width, height = self._axes_px()
        k = self.view["k"]
        x0, x1 = g.left[min(ids)], g.right[max(ids)]
        cx, cy = self.view["cx"], self.view["cy"]
        half_w, half_h = width / k / 2, height / k / 2
        if x0 < cx - half_w + 40 or x1 > cx + half_w - 40:
            self.view["cx"] = (x0 + x1) / 2
        ys = g.y[ids]
        if ys.min() < cy - half_h + 30 or ys.max() > cy + half_h - 30:
            self.view["cy"] = float(ys.mean())
        self._apply_view(draw=False)

    def zoom(self, factor: float, x: Optional[float] = None, y: Optional[float] = None) -> None:
        v = self.view
        k = min(MAX_SCALE, max(MIN_SCALE, v["k"] * factor))
        x = v["cx"] if x is None else x
        y = v["cy"] if y is None else y
        v["cx"] = x - (x - v["cx"]) * v["k"] / k
        v["cy"] = y - (y - v["cy"]) * v["k"] / k
        v["k"] = k
        self._apply_view()

    def pan(self, dx: float, dy: float = 0.0) -> None:
        self.view["cx"] += dx
        self.view["cy"] += dy
        self._apply_view()

    def _clamp(self) -> None:
        g, v = self.graph, self.view
        width, height = self._axes_px()
        half_w, half_h = width / v["k"] / 2, height / v["k"] / 2
        v["cx"] = min(max(v["cx"], min(half_w, g.content_width / 2) - 40),
                      max(g.content_width - half_w, g.content_width / 2) + 40)
        v["cy"] = min(max(v["cy"], min(half_h, g.content_height / 2) - 40),
                      max(g.content_height - half_h, g.content_height / 2) + 40)

    def _apply_view(self, force: bool = False, draw: bool = True) -> None:
        """Set the axes to the view; rebuild what is drawn if the view left it."""
        self._clamp()
        v = self.view
        width, height = self._axes_px()
        half_w, half_h = width / v["k"] / 2, height / v["k"] / 2
        self._syncing = True
        try:
            self.ax.set_xlim(v["cx"] - half_w, v["cx"] + half_w)
            self.ax.set_ylim(v["cy"] + half_h, v["cy"] - half_h)   # y grows downward
        finally:
            self._syncing = False
        self._place_lane_labels()
        x0, x1 = v["cx"] - half_w, v["cx"] + half_w
        win = self.window
        if force or win is None or win[2] != v["k"] or x0 < win[0] or x1 > win[1]:
            self._rebuild()
        self._move_overview_box()
        if draw:
            self._redraw()

    def _on_limits(self, _ax) -> None:
        """The toolbar (home, back, zoom to rectangle, pan) moved the axes."""
        if self._syncing:
            return
        (x0, x1), (y0, y1) = self.ax.get_xlim(), self.ax.get_ylim()
        width, _ = self._axes_px()
        if x1 <= x0:
            return
        self.view["k"] = min(MAX_SCALE, max(MIN_SCALE, width / (x1 - x0)))
        self.view["cx"], self.view["cy"] = (x0 + x1) / 2, (y0 + y1) / 2
        self._apply_view(draw=False)

    # =============================================================== drawing ==

    def _redraw(self, full: bool = False) -> None:
        """Show the current state: a full draw, or with blitting just the parts
        that change, over the background the last full draw left."""
        if self._overview_dirty:
            self._draw_overview()
        canvas = self.fig.canvas
        full, self._chrome_changed = full or self._chrome_changed, False
        if full or not self._blit or not self._backgrounds:
            self._blit_pending = False
            canvas.draw_idle()
            return
        if self._blit_timer is not None:
            # In a window: once the events already queued (a drag sends many)
            # are handled, so a burst of them costs one frame.
            if not self._blit_pending:
                self._blit_pending = True
                self._blit_timer.start()
            return
        self._blit_now()

    def _blit_now(self) -> None:
        self._blit_pending = False
        if not self._backgrounds:
            return
        canvas = self.fig.canvas
        for background in self._backgrounds:
            canvas.restore_region(background)
        self._draw_dynamic()
        for region in self._regions:
            canvas.blit(region)

    def _setup_blit(self) -> None:
        """Take the graph, the overview and the status lines out of the full
        draw; they are drawn over its copy instead (``_on_draw``)."""
        self._dynamic = [self.ax, self.ax_over, self.title_text, self.status_text, self.lint_text]
        for artist in self._dynamic:
            artist.set_animated(True)
        self.fig.canvas.mpl_connect("draw_event", self._on_draw)
        if self.interactive:
            self._blit_timer = self.fig.canvas.new_timer(interval=1)
            self._blit_timer.single_shot = True
            self._blit_timer.add_callback(self._blit_now)
        # savefig (the toolbar's, too) draws the figure once without the
        # draw event's help, so the parts left out must be put back for it.
        original = self.fig.savefig

        def savefig(*args, **kwargs):
            for artist in self._dynamic:
                artist.set_animated(False)
            try:
                return original(*args, **kwargs)
            finally:
                for artist in self._dynamic:
                    artist.set_animated(True)
                self._backgrounds = []
                self.fig.canvas.draw_idle()
        self.fig.savefig = savefig

    def _on_draw(self, _event) -> None:
        if not self.ax.get_animated():
            return      # savefig: the figure drew everything itself
        canvas = self.fig.canvas
        header = TransformedBbox(Bbox([[0.0, 0.885], [1.0, 1.0]]), self.fig.transFigure)
        self._regions = [self.ax.bbox.frozen(), self.ax_over.bbox.expanded(1.01, 1.1).frozen(),
                         header.frozen()]
        self._backgrounds = [canvas.copy_from_bbox(region) for region in self._regions]
        self._draw_dynamic()

    def _draw_dynamic(self) -> None:
        for artist in self._dynamic:
            if artist.get_visible():
                self.fig.draw_artist(artist)

    def _on_resize(self, _event) -> None:
        self._backgrounds = []      # the copy is the old size
        self._overview_dirty = True
        self._apply_view(force=True)

    def _rebuild(self) -> None:
        """Make the artists for what is in the drawn window: the view plus
        OVERSCAN of it either side, at the current zoom."""
        start = time.perf_counter()
        g, v = self.graph, self.view
        k = v["k"]
        dpi = self.fig.dpi
        width, _ = self._axes_px()
        view_w = width / k
        x0 = v["cx"] - view_w * (0.5 + OVERSCAN)
        x1 = v["cx"] + view_w * (0.5 + OVERSCAN)
        self.window = (x0, x1, k)
        span = g.nodes_in(x0, x1)
        ids = np.arange(span.start, span.stop, dtype=np.int64)
        lint_on = self.show_lint and g.lint_state == "done"

        # ---- node state, as arrays over the nodes in view ----------------
        in_slice = self.slice_mask[ids] if self.slice_mask is not None else np.ones(len(ids), bool)
        dimmed = ~in_slice if self.slice_mask is not None else np.zeros(len(ids), bool)
        face = _CATEGORY_RGBA[g.category[ids]].copy()
        edge = np.tile(to_rgba(INK), (len(ids), 1))
        lw = np.full(len(ids), 1.5)
        dashed = np.zeros(len(ids), bool)
        if self.slice_mask is not None:
            face[in_slice] = to_rgba(IN_SLICE)
            edge[in_slice] = to_rgba(TEXT)
        crit = ids == self.criterion if self.criterion is not None else np.zeros(len(ids), bool)
        face[crit] = to_rgba(CRITERION)
        edge[crit] = (1, 1, 1, 1)
        lw[crit] = 2.5
        hov = ids == self.hovered if self.hovered is not None else np.zeros(len(ids), bool)
        edge[hov] = (1, 1, 1, 1)
        lw[hov] = 2.5
        severity = g.lint_severity[ids] if lint_on else np.zeros(len(ids), np.int8)
        for rank, rgba in _SEVERITY_RGBA.items():
            sel = severity == rank
            edge[sel] = rgba
            lw[sel] = 2.5 if rank > 1 else 2.0
            dashed |= sel
        marked = self.marked[ids]
        edge[marked] = to_rgba(ACCENT)
        lw[marked] = 3.0
        dashed &= ~marked
        alpha = np.where(dimmed, DIM_NODE, 1.0)
        face[:, 3] *= alpha
        edge[:, 3] *= alpha

        # ---- boxes --------------------------------------------------------
        xs, ys, ws = g.x[ids], g.y[ids], g.w[ids]
        bx = xs[:, None] + _BOX[None, :, 0] * ws[:, None] / 2 + _BOX[None, :, 1]
        by = ys[:, None] + _BOX[None, :, 2] * NODE_H / 2 + _BOX[None, :, 3]
        self.boxes.set_verts(np.stack([bx, by], axis=-1))
        self.boxes.set_facecolor(face)
        self.boxes.set_edgecolor(edge)
        _set_lines(self.boxes, _pt(1, k, dpi) * lw, dashed)

        # ---- spans of multi-qubit gates, and a dot on every wire ---------
        multi = [j for j, idx in enumerate(ids) if len(g.qubits[idx]) > 1]
        span_rgba = _CATEGORY_RGBA[g.category[ids[multi]]].copy() if multi else np.zeros((0, 4))
        if multi:
            span_rgba[:, 3] = 0.55 * alpha[multi]
        self.spans.set_segments([[(xs[j], g.span_lo[ids[j]]), (xs[j], g.span_hi[ids[j]])]
                                 for j in multi])
        self.spans.set_color(span_rgba)
        self.spans.set_linewidth(_pt(2, k, dpi))
        dot_xy, dot_rgba = [], []
        for j in multi:
            rgba = list(_CATEGORY_RGBA[g.category[ids[j]]])
            rgba[3] = 0.8 * alpha[j]
            for q in g.qubits[ids[j]]:
                dot_xy.append((xs[j], g.lane_y(q)))
                dot_rgba.append(rgba)
        self.dots = self._ellipses(self.dots, dot_xy, 6.4, dot_rgba, zorder=2.1)

        # ---- edges --------------------------------------------------------
        self._rebuild_edges(g.edges_in(x0, x1), k, dpi)

        # ---- ticks for bit writes and reads no edge accounts for ---------
        tick_segs, tick_xy, tick_face, tick_edge = [], [], [], []
        for j, idx in enumerate(ids):
            reads = g.reads[idx]
            for kind, bits in (("write", [c for c in g.clbits[idx] if c not in reads]),
                               ("read", reads)):
                for c in bits:
                    if (idx, c) in g.routed:
                        continue
                    wy = g.clbit_y(c)
                    if abs(wy - ys[j]) <= NODE_H / 2:
                        continue
                    top = ys[j] + NODE_H / 2 if wy > ys[j] else ys[j] - NODE_H / 2
                    tx = xs[j] - CL_SHIFT
                    tick_segs.append([(tx, top), (tx, wy)])
                    tick_xy.append((tx, wy))
                    a = alpha[j]
                    tick_face.append(to_rgba(TEXT_FAINT, a) if kind == "write" else to_rgba(BG_SUNKEN, a))
                    tick_edge.append(to_rgba(TEXT_FAINT, a))
        self.ticks.set_segments(tick_segs)
        self.ticks.set_linewidth(_pt(1.1, k, dpi))
        self.tick_dots = self._ellipses(self.tick_dots, tick_xy, 5.6, tick_face, zorder=1.3,
                                        edgecolors=tick_edge, linewidths=_pt(1.3, k, dpi))

        # ---- lint badges --------------------------------------------------
        badge_j = np.flatnonzero(severity > 0)
        badge_xy = [(xs[j] + ws[j] / 2, ys[j] - NODE_H / 2) for j in badge_j]
        badge_rgba = [_SEVERITY_RGBA[int(severity[j])][:3] + (alpha[j],) for j in badge_j]
        self.badges = self._ellipses(self.badges, badge_xy, 13, badge_rgba, zorder=4,
                                     edgecolors=INK, linewidths=_pt(1.5, k, dpi))

        # ---- text, only what is readable at this zoom --------------------
        # Glyph outlines in content pixels, so they scale with the zoom as the
        # page's SVG text does, and all of it is one collection to draw.
        paths, colors = [], []
        if k >= LOD_TEXT:
            glyph = self.glyphs.path
            for j, idx in enumerate(ids):
                a = float(alpha[j])
                paths.append(glyph(g.short[idx], xs[j], ys[j] + 4, 11))
                colors.append(to_rgba(INK, a))
                if k >= LOD_LABELS:
                    paths.append(glyph(f"#{idx}", xs[j], ys[j] - NODE_H / 2 - 5, 9))
                    colors.append(to_rgba(TEXT_FAINT, a))
                    if g.lines[idx]:
                        paths.append(glyph(f"L{g.lines[idx]}", xs[j], ys[j] + NODE_H / 2 + 11, 9))
                        colors.append(to_rgba(ACCENT if marked[j] else TEXT_FAINT, a))
        keep = [i for i, p in enumerate(paths) if p is not None]
        self.text.set_paths([paths[i] for i in keep])
        self.text.set_facecolor([colors[i] for i in keep] or "none")
        self.drawn_labels = len(keep)
        # The mark in a lint badge, above the badge.
        marks = [self.glyphs.path("i" if severity[j] == 1 else "!", xs[j] + ws[j] / 2,
                                  ys[j] - NODE_H / 2 + 3.3, 9) for j in badge_j]             if k >= LOD_TEXT else []
        self.badge_text.set_paths(marks)
        self.badge_text.set_facecolor([to_rgba(INK, float(alpha[j])) for j in badge_j][:len(marks)]
                                      or "none")
        self.drawn_nodes = len(ids)
        self.timings["rebuild"] = time.perf_counter() - start

    def _rebuild_edges(self, idx: np.ndarray, k: float, dpi: float) -> None:
        """Curves for quantum edges, routed polylines for edges along a bit."""
        g = self.graph
        src, dst = g.edge_source[idx], g.edge_target[idx]
        dep, rank, bit = g.edge_dep[idx], g.edge_rank[idx], g.edge_clbit[idx]
        follow = np.array([d in self.follow for d in DEPENDENCIES])
        active = follow[dep] if len(dep) else np.zeros(0, bool)
        rgba = _DEP_RGBA[dep].copy() if len(dep) else np.zeros((0, 4))
        width = np.full(len(idx), 1.4)
        if self.slice_mask is not None:
            inside = active & self.slice_mask[src] & self.slice_mask[dst]
            rgba[:, 3] = np.where(inside, 1.0, DIM_EDGE)
            width[inside] = 2.4
        else:
            rgba[:, 3] = np.where(active, BASE_EDGE, DIM_EDGE)

        segments: List[np.ndarray] = []
        quantum = bit < 0
        if quantum.any():
            s, t, r = src[quantum], dst[quantum], rank[quantum]
            p0 = np.stack([g.right[s], g.y[s]], axis=1)
            p2 = np.stack([g.left[t], g.y[t]], axis=1)
            bow = np.where(r % 2 == 0, 1.0, -1.0) * (12 + 14 * (r // 2))
            p1 = (p0 + p2) / 2
            p1[:, 1] -= bow
            tt = np.linspace(0.0, 1.0, CURVE_POINTS)[None, :, None]
            curves = ((1 - tt) ** 2 * p0[:, None, :] + 2 * (1 - tt) * tt * p1[:, None, :]
                      + tt ** 2 * p2[:, None, :])
            segments_q = list(curves)
        else:
            segments_q = []
        segments_c = []
        for s, t, d, c in zip(src[~quantum], dst[~quantum], dep[~quantum], bit[~quantum]):
            side = model.CLASSICAL_SIDE[DEPENDENCIES[d]]
            segments_c.append(np.asarray(_classical_route(g, s, t, g.clbit_y(c) + side * 2.5,
                                                          side * CL_SHIFT)))
        # Keep the order of ``idx`` so colors line up: quantum first, then classical.
        order = np.concatenate([np.flatnonzero(quantum), np.flatnonzero(~quantum)])
        segments = segments_q + segments_c
        rgba, width, dep_sorted = rgba[order], width[order], dep[order]
        self.edges.set_segments(segments)
        self.edges.set_color(rgba)
        dashed = DEPENDENCIES.index(model.DependencyType.DATA_CLASSICAL)
        _set_lines(self.edges, _pt(1, k, dpi) * width, dep_sorted == dashed)

        # Arrowheads at the target end, along the last stretch of each edge.
        if segments:
            tips = np.array([seg[-1] for seg in segments])
            backs = np.array([seg[-2] for seg in segments])
            direction = tips - backs
            norm = np.linalg.norm(direction, axis=1, keepdims=True)
            norm[norm == 0] = 1
            direction /= norm
            normal = np.stack([-direction[:, 1], direction[:, 0]], axis=1)
            scale = np.sqrt(width / 1.4)[:, None]
            base = tips - direction * ARROW_LEN * scale
            tri = np.stack([tips, base + normal * ARROW_HALF * scale,
                            base - normal * ARROW_HALF * scale], axis=1)
            self.arrows.set_verts(tri)
            self.arrows.set_facecolor(rgba)
        else:
            self.arrows.set_verts([])
        self.drawn_edges = len(segments)

    def _ellipses(self, old, xy, diameter, facecolors, zorder, **kw):
        """An EllipseCollection of circles ``diameter`` content pixels across."""
        if old is not None:
            old.remove()
        if not xy:
            return None
        n = len(xy)
        coll = EllipseCollection(np.full(n, diameter), np.full(n, diameter), np.zeros(n),
                                 units="xy", offsets=np.asarray(xy),
                                 offset_transform=self.ax.transData, facecolors=facecolors,
                                 zorder=zorder, **kw)
        self.ax.add_collection(coll, autolim=False)
        return coll

    # ============================================================== overview ==

    def _draw_overview(self) -> None:
        """The whole circuit as marks on its wires, colored like the graph."""
        start = time.perf_counter()
        self._overview_dirty = False
        g = self.graph
        width = int(max(64, self.ax_over.bbox.width))
        band = 1 if self.show_lint and g.lint_state == "done" else 0
        rows = g.num_qubits * 2 + band
        image = np.empty((rows, width, 4))
        image[:] = to_rgba(BG_SUNKEN)
        if g.n:
            cols = np.minimum(width - 1, (g.x / g.content_width * width).astype(np.int64))
            # One mark per (instruction, wire); later writes win, so the slice goes last.
            owner = np.repeat(np.arange(g.n), [len(q) for q in g.qubits])
            wires = np.fromiter((q for qs in g.qubits for q in qs), dtype=np.int64,
                                count=len(owner))
            r = band + wires * 2
            c = cols[owner]
            color = _CATEGORY_RGBA[g.category[owner]].copy()
            # Later writes to a pixel win: outside the slice first, then the
            # slice, the marked instructions and the criterion on top.
            priority = np.zeros(len(owner), dtype=np.int8)
            if self.slice_mask is not None:
                color = color * 0.35 + np.array(to_rgba(BG_SUNKEN)) * 0.65
                inside = self.slice_mask[owner]
                color[inside] = to_rgba(IN_SLICE)
                priority[inside] = 1
            marked = self.marked[owner]
            color[marked] = to_rgba(ACCENT)
            priority[marked] = 2
            if self.criterion is not None:
                crit = owner == self.criterion
                color[crit] = to_rgba(CRITERION)
                priority[crit] = 3
            order = np.argsort(priority, kind="stable")
            image[r[order], c[order]] = color[order]
            if band:
                for rank in (1, 2, 3):   # errors last, so they win a shared pixel
                    image[0, cols[g.lint_severity == rank]] = _SEVERITY_RGBA[rank]
                tint = np.array(to_rgba(SEVERITY_COLORS["error"])) * 0.35 + \
                    np.array(to_rgba(BG_SUNKEN)) * 0.65
                for q in g.lint_by_qubit:
                    row = image[band + q * 2]
                    empty = np.all(row == to_rgba(BG_SUNKEN), axis=1)
                    row[empty] = tint
        extent = (0, g.content_width, rows, 0)
        if self.overview_image is None:
            self.overview_image = self.ax_over.imshow(image, extent=extent, aspect="auto",
                                                      interpolation="nearest", zorder=1)
        else:
            self.overview_image.set_data(image)
            self.overview_image.set_extent(extent)
        self.ax_over.set_xlim(0, g.content_width)
        self.ax_over.set_ylim(rows, 0)
        self._move_overview_box()
        self.timings["overview"] = time.perf_counter() - start

    def _move_overview_box(self) -> None:
        # Only a circuit wider than the view needs the strip.
        g = self.graph
        extent = g.right[-1] - g.left[0] + 70 if g.n else 0     # what fit() frames
        self.ax_over.set_visible(extent * self.view["k"] > self._axes_px()[0])
        (x0, x1), rows = self.ax.get_xlim(), self.ax_over.get_ylim()[0]
        # Never thinner than a few pixels, or a long circuit's view box vanishes.
        min_w = self.graph.content_width / max(64, self.ax_over.bbox.width) * 4
        if x1 - x0 < min_w:
            mid = (x0 + x1) / 2
            x0, x1 = mid - min_w / 2, mid + min_w / 2
        self.overview_view.set_bounds(x0, 0, x1 - x0, rows)

    # ================================================================ events ==

    def _toolbar_busy(self) -> bool:
        toolbar = getattr(self.fig.canvas, "toolbar", None)
        return bool(toolbar is not None and getattr(toolbar, "mode", ""))

    def _on_press(self, event) -> None:
        if event.button != 1 or self._toolbar_busy():
            return
        if event.inaxes is self.ax_over and event.xdata is not None:
            self._press = {"overview": True}
            self._jump(event.xdata)
        elif event.inaxes is self.ax:
            self._press = {"x": event.x, "y": event.y, "cx": self.view["cx"],
                           "cy": self.view["cy"], "moved": False}

    def _on_motion(self, event) -> None:
        press = self._press
        if press is not None:
            if press.get("overview"):
                if event.inaxes is self.ax_over and event.xdata is not None:
                    self._jump(event.xdata)
                return
            dx, dy = event.x - press["x"], event.y - press["y"]
            if press["moved"] or abs(dx) + abs(dy) > 4:
                press["moved"] = True
                k = self.view["k"]
                self.view["cx"] = press["cx"] - dx / k
                self.view["cy"] = press["cy"] + dy / k     # display y grows upward
                self._apply_view()
            return
        if event.inaxes is not self.ax or event.xdata is None:
            if self.hovered is not None:
                self._set_hover(None)
            return
        self._set_hover(self.graph.node_at(event.xdata, event.ydata))

    def _on_release(self, event) -> None:
        press, self._press = self._press, None
        if press is None or press.get("overview") or press["moved"]:
            return
        if event.inaxes is self.ax and event.xdata is not None:
            idx = self.graph.node_at(event.xdata, event.ydata)
            if idx is not None:
                self.set_criterion(idx)

    def _on_scroll(self, event) -> None:
        if event.inaxes not in (self.ax, self.ax_over):
            return
        step = 1 if event.button == "up" else -1
        key = event.key or ""
        if "control" in key or "ctrl" in key:
            if event.inaxes is self.ax:
                self.zoom(1.2 ** step, event.xdata, event.ydata)
            return
        width, height = self._axes_px()
        k = self.view["k"]
        if "shift" in key:
            self.pan(0, -step * height / k * 0.15)
        else:
            self.pan(-step * width / k * 0.2)

    def _jump(self, x: float) -> None:
        self.view["cx"] = x
        self._apply_view()

    def _set_hover(self, idx: Optional[int]) -> None:
        if idx == self.hovered:
            return
        self.hovered = idx
        if idx is None:
            self.hover.set_visible(False)
        else:
            g = self.graph
            self.hover.set_text(self._card(idx))
            self.hover.xy = (g.right[idx], g.y[idx] - NODE_H / 2)
            # Open the card toward the middle of the view, so it stays on screen.
            (x0, x1), (y1, y0) = self.ax.get_xlim(), self.ax.get_ylim()
            dx = 14 if g.x[idx] < (x0 + x1) / 2 else -14
            dy = -14 if g.y[idx] < (y0 + y1) / 2 else 14
            self.hover.set_position((dx, dy))
            self.hover.set_ha("left" if dx > 0 else "right")
            self.hover.set_va("top" if dy < 0 else "bottom")
            self.hover.set_visible(True)
        self._rebuild_classes_only()

    def _rebuild_classes_only(self) -> None:
        """A hover changes one outline; the window's artists stay."""
        self._rebuild()
        self._redraw()

    def _card(self, idx: int) -> str:
        g = self.graph
        info = model.gates.describe(g.names[idx])
        lines = [f"#{idx}  {g.label(idx)}"]
        if info.get("known"):
            lines.append(info["title"])
        where = g.where(idx)
        if where:
            lines.append(where)
        if self.slice_mask is not None:
            state = ("criterion" if idx == self.criterion else
                     "in the slice" if self.slice_mask[idx] else "outside the slice")
            lines.append(state)
        findings = g.lint_by_node.get(idx, []) if self.show_lint else []
        for finding in findings[:4]:
            message = finding["message"]
            message = message if len(message) <= 70 else message[:69] + "…"
            lines.append(f"{finding['severity']} {finding['code']}: {message}")
        if len(findings) > 4:
            lines.append(f"... {len(findings) - 4} more")
        return "\n".join(lines)

    def _on_key(self, event) -> None:
        key = event.key
        if key is None:
            return
        actions = {
            "right": lambda: self.step(1), "left": lambda: self.step(-1),
            "b": lambda: self.set_direction("backward"),
            "f": lambda: self.set_direction("forward"),
            "d": self.toggle_direction, "l": self.toggle_lint,
            "]": lambda: self.step_finding(1), "[": lambda: self.step_finding(-1),
            ".": lambda: self.step_circuit(1), ",": lambda: self.step_circuit(-1),
            "+": lambda: self.zoom(1.25), "=": lambda: self.zoom(1.25),
            "-": lambda: self.zoom(0.8), "0": self.fit, "escape": self.clear,
            "?": self.toggle_help,
            "home": lambda: self._jump(0), "end": lambda: self._jump(self.graph.content_width),
            "shift+right": lambda: self.pan(self._axes_px()[0] / self.view["k"] * 0.5),
            "shift+left": lambda: self.pan(-self._axes_px()[0] / self.view["k"] * 0.5),
        }
        if key in "123456" and len(key) == 1:
            dep = DEPENDENCIES[int(key) - 1]
            if self.graph.dependency_counts()[dep]:
                self.toggle_dependency(dep)
            return
        action = actions.get(key)
        if action is not None:
            action()

    # ================================================================== text ==

    def _update_text(self) -> None:
        g, doc = self.graph, self.document
        name = g.info.get("name") or ""
        head = self.title or (doc.filename if doc else "QDG")
        if doc is not None and len(doc.circuits) > 1:
            head += f"   ·   circuit {g.info.get('index', 0) + 1}/{len(doc.circuits)}: {name}"
        elif name and doc is not None and name != doc.filename \
                and not re.fullmatch(r"circuit-\d+", name):   # Qiskit's default name
            head += f"   ·   {name}"
        self.title_text.set_text(head)
        counts = f"{g.n:,} instructions, {len(g.edge_source):,} edges, {g.num_qubits} qubits"
        if g.num_clbits:
            counts += f", {g.num_clbits} clbits"
        if self.criterion is None:
            status = f"{counts}.  Click an instruction to slice from it  (? for keys)"
        else:
            where = f", line {g.lines[self.criterion]}" if g.lines[self.criterion] else ""
            follow = "" if len(self.follow) == len(DEPENDENCIES) else \
                "  following " + " ".join(d.name for d in DEPENDENCIES if d in self.follow)
            status = (f"{self.direction.capitalize()} slice from #{self.criterion} "
                      f"{g.short[self.criterion]}{where}: {self.slice_size:,} of {g.n:,} "
                      f"instructions{follow}")
        self.status_text.set_text(status)

        if g.lint_state == "off":
            lint = "Lint: not run (--no-lint)" if doc is not None else ""
            self.btn_lint.label.set_text("Lint: --")
        elif g.lint_state == "pending":
            lint = "Lint: running ..."
        elif not self.show_lint:
            lint = f"Lint: {_tally(g.diagnostics)} (markers hidden, l to show)"
        elif self.finding is not None and g.diagnostics:
            f = g.diagnostics[self.finding]
            lint = (f"[{self.finding + 1}/{len(g.diagnostics)}] {f['severity']} {f['code']} "
                    f"line {f.get('line') or '-'}: {f['message']}")
            if f.get("hint"):
                lint += f"   hint: {f['hint']}"
            lint = lint if len(lint) <= 190 else lint[:189] + "…"
        else:
            lint = f"Lint: {_tally(g.diagnostics)}" + ("   ([ and ] step through them)"
                                                     if g.diagnostics else "")
        self.lint_text.set_text(lint)
        color = TEXT_FAINT
        if g.lint_state == "done" and self.show_lint and g.diagnostics:
            worst = max(model.SEVERITY_RANK.get(f.get("severity"), 1) for f in g.diagnostics)
            color = SEVERITY_COLORS[SEVERITY_NAME[worst]] if self.finding is not None else TEXT_DIM
        self.lint_text.set_color(color)


# ------------------------------------------------------------------ helpers --

class _Glyphs:
    """Strings as filled outlines in content pixels.

    The font is monospaced, so a string is its characters side by side: each
    character's outline is made once, and a label is a few array copies, far
    cheaper to draw than a matplotlib Text (font lookup and layout per draw).
    """

    def __init__(self) -> None:
        self.prop = FontProperties(family=MONO)
        one = TextPath((0, 0), "0", size=1, prop=self.prop)
        many = TextPath((0, 0), "0" * 21, size=1, prop=self.prop)
        self.advance = (many.get_extents().x1 - one.get_extents().x1) / 20
        self._chars: Dict[str, Any] = {}
        self._strings: Dict[str, Any] = {}

    def _char(self, ch: str):
        got = self._chars.get(ch)
        if got is None:
            got = None
            if not ch.isspace():
                try:
                    path = TextPath((0, 0), ch, size=1, prop=self.prop)
                    if len(path.vertices):
                        got = (np.array(path.vertices), np.array(path.codes))
                except Exception:   # a character the font cannot draw
                    got = None
            self._chars[ch] = got if got is not None else False
        return got or None

    def _string(self, text: str):
        got = self._strings.get(text)
        if got is None:
            verts, codes = [], []
            for i, ch in enumerate(text):
                glyph = self._char(ch)
                if glyph is not None:
                    verts.append(glyph[0] + (i * self.advance, 0.0))
                    codes.append(glyph[1])
            got = (np.concatenate(verts), np.concatenate(codes)) if verts else False
            if len(self._strings) < 100000:
                self._strings[text] = got
        return got or None

    def path(self, text: str, x: float, baseline: float, size: float) -> Optional[Path]:
        """``text`` centred on ``x`` with its baseline at ``baseline`` (y grows downward)."""
        got = self._string(text)
        if got is None:
            return None
        verts, codes = got
        out = np.empty_like(verts)
        out[:, 0] = x - len(text) * self.advance * size / 2 + verts[:, 0] * size
        out[:, 1] = baseline - verts[:, 1] * size
        return Path(out, codes)


def _set_lines(collection, widths: np.ndarray, dashed: np.ndarray) -> None:
    """Per-item widths, and a dash for the items ``dashed`` marks.

    A collection pairs its widths and dash styles by broadcasting each list to
    the least common multiple of their lengths, every time either is set. Set
    one at a time while the other still has the last view's length, that is
    thousands of dash patterns per pan; a single style first keeps it at n.
    """
    collection.set_linestyle("solid")
    collection.set_linewidth(widths)
    if dashed.any():
        collection.set_linestyle([(0, (5, 3)) if d else "solid" for d in dashed])


def _classical_route(g: Graph, s: int, t: int, wire_y: float, shift: float):
    """Down (or up) from the source to the bit's wire, along it, and into the
    target (``classicalRoute`` in graph.js); a node on the wire joins from its side."""
    def leg(i, x, is_target):
        top, bottom = g.y[i] - NODE_H / 2, g.y[i] + NODE_H / 2
        if bottom < wire_y - 3:
            return [(x, bottom), (x, wire_y)]
        if top > wire_y + 3:
            return [(x, top), (x, wire_y)]
        return [((g.left[i] if is_target else g.right[i]), wire_y)]
    points = leg(s, g.x[s] + shift, False) + leg(t, g.x[t] + shift, True)[::-1]
    out = [points[0]]
    for p in points[1:]:
        if p != out[-1]:
            out.append(p)
    if len(out) == 1:
        out.append(out[0])
    return out


def _count(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 10000 else str(n)


def _tally(findings: List[Dict[str, Any]]) -> str:
    if not findings:
        return "no findings"
    counts: Dict[str, int] = {}
    for f in findings:
        counts[f.get("severity", "warning")] = counts.get(f.get("severity", "warning"), 0) + 1
    return ", ".join(f"{n} {s}{'s' if n != 1 else ''}"
                     for s in ("error", "warning", "info") for n in [counts.get(s, 0)] if n)


def _print_findings(doc: Document, graph: Graph) -> None:
    """The findings once, on the terminal, in `qslicer lint`'s format."""
    for f in graph.diagnostics:
        print(f"{doc.path}:{f.get('line') or 0}:{f.get('column') or 1}: "
              f"{f.get('severity', 'warning')} {f['code']} {f['message']}")


# ---------------------------------------------------------------------- CLI --

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qslicer viz",
        description="Draw a file's dependence graph in an interactive window. "
                    "Nothing in the file is executed.")
    parser.add_argument("circuit_path", help="the Qiskit (.py) or OpenQASM (.qasm) file to draw")
    parser.add_argument("--profile", default="baseline",
                        help=f"dependency rule profile: {', '.join(list_rule_profiles())}")
    parser.add_argument("--language", choices=("python", "qasm"),
                        help="override the language the file extension implies")
    parser.add_argument("--circuit", type=int, default=0, metavar="N",
                        help="which of the circuits the file builds to open (0, the main one)")
    parser.add_argument("--set", dest="values", action="append", default=[], metavar="NAME=VALUE",
                        help="a value for something only a run decides (input(), an unseeded "
                             "random draw ...); repeatable")
    parser.add_argument("--no-lint", action="store_true", help="do not run the linters")
    parser.add_argument("--select", nargs="+", metavar="RULE",
                        help="lint rules (quick/QS002) or linters (lintq); default: each "
                             "linter's defaults")
    start = parser.add_mutually_exclusive_group()
    start.add_argument("--criterion", type=int, metavar="INDEX",
                       help="open with a slice from this instruction")
    start.add_argument("--line", type=int, help="open with a slice from this source line")
    parser.add_argument("--forward", action="store_true", help="forward slice (default backward)")
    parser.add_argument("--save", metavar="PNG",
                        help="write the view to an image instead of opening a window")
    return parser


def _criterion_of(graph: Graph, args) -> Optional[int]:
    if args.line is not None:
        on_line = np.flatnonzero(graph.lines == args.line)
        if not len(on_line):
            raise SystemExit(f"no instruction comes from line {args.line}")
        return int(on_line[0] if args.forward else on_line[-1])
    if args.criterion is not None and not 0 <= args.criterion < graph.n:
        raise SystemExit(f"instruction {args.criterion} is outside 0..{graph.n - 1}")
    return args.criterion


def main(argv=None) -> int:
    args = _build_arg_parser().parse_args(argv)
    from ..parsers.staticqiskitparser import StaticParseError
    try:
        values = model.parse_values(args.values)
        doc = Document(args.circuit_path, args.language, args.profile, values,
                       lint=not args.no_lint, select=args.select)
    except (model.NeedsValue, ValueError) as exc:
        raise SystemExit(f"{args.circuit_path}: {exc}")
    except StaticParseError as exc:
        raise SystemExit(f"{args.circuit_path}: cannot determine the circuit statically: {exc}")
    except OSError as exc:
        raise SystemExit(f"{args.circuit_path}: {exc.strerror or exc}")
    unused = set(values) - doc.used_values
    if unused:
        print(f"note: --set {', '.join(sorted(unused))} was not needed", file=sys.stderr)
    if not doc.circuits and len(doc._first) == 0:
        raise SystemExit(f"{args.circuit_path}: the circuit has no instructions")
    if not 0 <= args.circuit < max(1, len(doc.circuits)):
        raise SystemExit(f"--circuit {args.circuit}: the file builds {len(doc.circuits)} "
                         f"circuit(s), 0..{len(doc.circuits) - 1}")
    graph = doc.graph(args.circuit)
    if graph.n == 0:
        raise SystemExit(f"{args.circuit_path}: the circuit has no instructions")
    criterion = _criterion_of(graph, args)
    title = f"{os.path.basename(args.circuit_path)} ({args.profile})"
    direction = "forward" if args.forward else "backward"
    if args.save:
        matplotlib.use("Agg")
        view = QDGVisualizer(doc, title=title, show=False, circuit=args.circuit,
                             criterion=criterion, direction=direction)
        _print_findings(doc, view.graph)
        view.fig.savefig(args.save, facecolor=BG)
        print(f"wrote {args.save}")
        return 0
    QDGVisualizer(doc, title=title, circuit=args.circuit, criterion=criterion,
                  direction=direction)
    return 0


if __name__ == "__main__":
    sys.exit(main())
