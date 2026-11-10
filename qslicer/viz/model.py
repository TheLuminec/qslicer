"""What the QDG viewer draws, worked out without matplotlib.

``Document`` reads a file once and parses it once; each of the circuits the
file builds is then a ``Graph``: the program, its dependence graph, a fixed
layout and the edges as numpy arrays, so the view can find what is in a box
with a bisection and a mask instead of a walk over every node. Nothing here
draws or imports matplotlib, so it can be tested on its own.

The layout and the colors are the web page's (``webapp/static/js/graph.js``
and ``style.css``): instruction index across, qubit wire down, one wire per
classical bit under the qubits. Coordinates are the page's content pixels,
with y growing downward.

What ``qslicer.insights`` finds (phase-only gates, kickback, predetermined
and correlated measurements, mirrored pairs) is worked out once per circuit,
on first use, and turned into marks, card text and mirror arcs here; the
wording is the web page's.
"""

import ast
import os
import threading
from bisect import bisect_left, bisect_right
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np

from .. import gates, language_of
from ..dependencies.dependency_rules import DependencyType
from ..qdg import QDG, get_rule_profile
from ..slicer import QuantumSlicer, SlicingCriterion

# --------------------------------------------------------------------------
# Geometry, in content pixels (the same numbers as graph.js)
# --------------------------------------------------------------------------

LANE_H = 66       # vertical distance between qubit wires
TOP_PAD = 46      # room above the first wire
LEFT_PAD = 60     # room for the wire labels
NODE_H = 26
NODE_GAP = 18     # horizontal gap between consecutive instructions
MIN_NODE_W = 34
CHAR_W = 6.3
CL_GAP = 58       # last qubit wire to the first classical wire
CL_H = 22         # vertical distance between classical wires
CL_SHIFT = 12     # MEASUREMENT legs run left of a node's centre, DATA_CLASSICAL right

# --------------------------------------------------------------------------
# Colors (style.css)
# --------------------------------------------------------------------------

BG = "#1e1e2e"
BG_SUNKEN = "#181825"
SURFACE = "#252539"
SURFACE_2 = "#313244"
SURFACE_3 = "#45475a"
TEXT = "#cdd6f4"
TEXT_DIM = "#a6adc8"
TEXT_FAINT = "#7f849c"
INK = "#11111b"            # text on a node, and its outline
ACCENT = "#89b4fa"
CRITERION = "#ff6b35"
IN_SLICE = "#4a90d9"
SEVERITY_COLORS = {"error": "#f38ba8", "warning": "#f9e2af", "info": "#89dceb"}

CATEGORIES = (gates.SINGLE, gates.MULTI, gates.MEASURE, gates.CLASSICAL, gates.BARRIER)
CATEGORY_FILL = {
    gates.SINGLE: "#9aa6cf",
    gates.MULTI: "#c6a0f6",
    gates.MEASURE: "#a6e3a1",
    gates.CLASSICAL: "#f9e2af",
    gates.BARRIER: "#6c7086",
}

# Dependency types in the order the legend and the chips list them.
DEPENDENCIES = (DependencyType.DATA_QUBIT, DependencyType.ENTANGLEMENT,
                DependencyType.MEASUREMENT, DependencyType.DATA_CLASSICAL,
                DependencyType.CONTROL_CLASSICAL, DependencyType.PHASE)
DEP_COLORS = {
    DependencyType.DATA_QUBIT: "#2196F3",
    DependencyType.ENTANGLEMENT: "#E91E63",
    DependencyType.MEASUREMENT: "#4CAF50",
    DependencyType.DATA_CLASSICAL: "#FF9800",
    DependencyType.CONTROL_CLASSICAL: "#795548",
    DependencyType.PHASE: "#8D1EAC",
}
# Edges carried by a classical bit run down to its wire, along it and back up,
# as a circuit diagram draws them; the sign is the side of the node they use.
CLASSICAL_SIDE = {DependencyType.MEASUREMENT: -1, DependencyType.DATA_CLASSICAL: 1}

SEVERITY_RANK = {"info": 1, "warning": 2, "error": 3}
SEVERITY_NAME = {rank: name for name, rank in SEVERITY_RANK.items()}

# Several backward slices at once: a colour per slice, cycling after eight
# (the Okabe-Ito set, told apart with most kinds of colour blindness).
SLICE_PALETTE = ("#56B4E9", "#E69F00", "#009E73", "#F0E442",
                 "#0072B2", "#D55E00", "#CC79A7", "#E8E8E8")
MAX_SLICES = 16
SHARED_SLICE = "#bac2de"   # a node in several slices: this face, and a stripe per slice
MAX_STRIPES = 4
CHOP_FROM = CRITERION
CHOP_TO = "#f5c2e7"

# The small marks under a node, and the wires a measurement may be tied to.
MARK_COLORS = {"phase": "#94e2d5", "kickback": "#f5c2e7", "always": "#fab387"}
CORRELATED = ACCENT        # a wire whose measurement may be correlated with the criterion
ENTANGLED = "#cba6f7"      # an unmeasured wire it may still be entangled with
MIRROR = TEXT_DIM

# Mirror arcs: height above the box's top edge, growing with the pair's depth.
ARC_BASE, ARC_STEP, ARC_MAX_DEPTH = 14.0, 9.0, 6
ARC_POINTS = 24

_EMPTY_INSIGHTS: Dict[str, Any] = {"measurements": {}, "mirrors": [], "kickback": {},
                                   "no_effect": {}, "predetermined": {}, "correlations": {},
                                   "guards": {}}


def _qubit_list(qubits: Iterable[int]) -> str:
    return ", ".join(f"q{q}" for q in qubits)


def short_label(name: str, qubits: Sequence[int]) -> str:
    """The text inside a node box: no parameters, and never long enough to
    stretch the layout (``_short_label`` in webapp/analysis.py)."""
    label = name if len(name) <= 12 else name[:11] + "…"
    if qubits:
        wires = "q" + ",".join(str(q) for q in qubits)
        if len(wires) > 10:
            wires = "q" + str(qubits[0]) + "+" + str(len(qubits) - 1)
        label += " " + wires
    return label


def node_width(label: str) -> float:
    return float(max(MIN_NODE_W, round(len(label) * CHAR_W) + 16))


def format_param(value: Any) -> str:
    """A gate parameter for the hover card: multiples of pi as such, numbers short."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        text = str(value)
        return text if len(text) <= 24 else text[:23] + "…"
    if isinstance(value, int):
        return str(value)
    if value == 0:
        return "0"
    ratio = value / np.pi
    for den in (1, 2, 3, 4, 6, 8, 16, 32, 64, 128, 256, 512, 1024):
        num = ratio * den
        if abs(num - round(num)) < 1e-9 and abs(round(num)) < 1000:
            num = int(round(num))
            head = {1: "", -1: "-"}.get(num, str(num))
            return f"{head}π" + (f"/{den}" if den != 1 else "")
    return f"{value:.4g}"


# --------------------------------------------------------------------------
# One circuit
# --------------------------------------------------------------------------

class Graph:
    """A circuit laid out for drawing: nodes and edges as arrays.

    Node ids are instruction indices, and x grows with the index, so the nodes
    a vertical strip of the view covers are one contiguous run of ids.
    """

    def __init__(self, qdg: QDG, info: Optional[Dict[str, Any]] = None) -> None:
        self.qdg = qdg
        self.program = program = qdg.program
        self.info = info or {"index": 0, "name": getattr(program, "name", "") or "circuit"}
        self.n = n = len(program)
        self.num_qubits = max(1, program.num_qubits)
        self.num_clbits = program.num_clbits or 0

        self.names: List[str] = []
        self.qubits: List[Tuple[int, ...]] = []
        self.clbits: List[Tuple[int, ...]] = []
        self.reads: List[Tuple[int, ...]] = []   # condition clbits
        self.short: List[str] = []
        self.lines = np.zeros(n, dtype=np.int64)  # 0 when the front end could not tell
        self.category = np.zeros(n, dtype=np.int8)
        self.x = np.zeros(n)
        self.y = np.zeros(n)
        self.w = np.zeros(n)
        self.span_lo = np.zeros(n)   # top and bottom of the wires a node touches
        self.span_hi = np.zeros(n)

        categories: Dict[str, int] = {}
        widths: Dict[str, float] = {}
        x = float(LEFT_PAD)
        for idx in range(n):
            instruction = program[idx]
            name = instruction.name
            qubits = tuple(instruction.qubits)
            clbits = tuple(instruction.clbits)
            reads = tuple(instruction.metadata.get("condition_clbits", ()) or ())
            label = short_label(name, qubits)
            width = widths.get(label)
            if width is None:
                width = widths[label] = node_width(label)
            code = categories.get(name)
            if code is None:
                category = gates.describe(name)["category"]
                code = categories[name] = CATEGORIES.index(category) if category in CATEGORIES else 0
            self.names.append(name)
            self.qubits.append(qubits)
            self.clbits.append(clbits)
            self.reads.append(reads)
            self.short.append(label)
            self.category[idx] = code
            location = getattr(instruction, "location", None)
            if location is not None:
                self.lines[idx] = location.line
            self.w[idx] = width
            self.x[idx] = x + width / 2
            if qubits:
                lanes = [self.lane_y(q) for q in qubits]
                self.y[idx] = sum(lanes) / len(lanes)
                self.span_lo[idx], self.span_hi[idx] = min(lanes), max(lanes)
            else:
                # A store or a classical-only instruction sits on the bits it uses.
                bits = list(dict.fromkeys(clbits + reads))
                lanes = [self.clbit_y(c) for c in bits] or [self.lane_y(0)]
                self.y[idx] = sum(lanes) / len(lanes)
                self.span_lo[idx] = self.span_hi[idx] = self.y[idx]
            x += width + NODE_GAP
        self.left = self.x - self.w / 2
        self.right = self.x + self.w / 2
        self.content_width = x + LEFT_PAD
        self.content_height = (self.clbit_y(self.num_clbits - 1) + CL_H if self.num_clbits
                               else TOP_PAD + self.num_qubits * LANE_H)

        self.phase_only = np.fromiter((name in gates.PHASE for name in self.names),
                                      dtype=bool, count=n)
        self._insights: Optional[Dict[str, Any]] = None
        self._measurements: Optional[Dict[int, List[int]]] = None

        self._prepare_edges()
        self.diagnostics: List[Dict[str, Any]] = []
        self.lint_state = "off"       # "off", "pending" or "done"
        self.set_diagnostics([])

    # ---------------------------------------------------------------- wires --

    def lane_y(self, qubit: int) -> float:
        return TOP_PAD + qubit * LANE_H

    def clbit_y(self, clbit: int) -> float:
        return TOP_PAD + (self.num_qubits - 1) * LANE_H + CL_GAP + clbit * CL_H

    # ---------------------------------------------------------------- edges --

    def _prepare_edges(self) -> None:
        """Per edge, its ends, its type, its rank among the parallel edges of
        the same pair (they fan out), and for an edge carried by a classical
        bit, which bit: the k-th parallel edge of one type runs along the k-th
        bit both ends share (``prepareEdges`` in graph.js)."""
        source, target, dep, rank, clbit, rule = [], [], [], [], [], []
        seen: Dict[Any, int] = {}
        for s, t, data in self.qdg.graph.edges(data=True):
            dependency = data.get("dependency")
            pair = (s, t)
            seen[pair] = seen.get(pair, 0) + 1
            bit = -1
            if dependency in CLASSICAL_SIDE:
                key = (s, t, dependency)
                seen[key] = seen.get(key, 0) + 1
                written = self.clbits[s]
                shared = [c for c in self.reads[t] if c in written]
                if shared:
                    bit = shared[(seen[key] - 1) % len(shared)]
            source.append(s)
            target.append(t)
            dep.append(DEPENDENCIES.index(dependency) if dependency in DEPENDENCIES else 0)
            rank.append(seen[pair] - 1)
            clbit.append(bit)
            rule.append(data.get("rule", ""))
        self.edge_source = np.asarray(source, dtype=np.int64)
        self.edge_target = np.asarray(target, dtype=np.int64)
        self.edge_dep = np.asarray(dep, dtype=np.int8)
        self.edge_rank = np.asarray(rank, dtype=np.int64)
        self.edge_clbit = np.asarray(clbit, dtype=np.int64)
        self.edge_rule = rule
        lo = np.minimum(self.edge_source, self.edge_target)
        hi = np.maximum(self.edge_source, self.edge_target)
        self.edge_left = self.left[lo] if len(lo) else np.zeros(0)
        self.edge_right = self.right[hi] if len(hi) else np.zeros(0)
        # Every (node, bit) an edge runs along: such a write or read needs no tick.
        self.routed: Set[Tuple[int, int]] = set()
        for s, t, c in zip(source, target, clbit):
            if c >= 0:
                self.routed.add((s, c))
                self.routed.add((t, c))

    def dependency_counts(self) -> Dict[DependencyType, int]:
        counts = np.bincount(self.edge_dep, minlength=len(DEPENDENCIES)) if len(self.edge_dep) \
            else np.zeros(len(DEPENDENCIES), dtype=np.int64)
        return {dep: int(counts[i]) for i, dep in enumerate(DEPENDENCIES)}

    # ------------------------------------------------------------- culling --

    def nodes_in(self, x0: float, x1: float) -> range:
        """The ids of the nodes whose boxes overlap the strip ``x0..x1``."""
        first = bisect_left(self.right, x0)
        last = bisect_right(self.left, x1)
        return range(first, max(first, last))

    def edges_in(self, x0: float, x1: float) -> np.ndarray:
        """Indices of the edges whose horizontal extent overlaps ``x0..x1``."""
        if not len(self.edge_left):
            return np.zeros(0, dtype=np.int64)
        return np.flatnonzero((self.edge_left <= x1) & (self.edge_right >= x0))

    def node_at(self, x: float, y: float, slack: float = 4.0) -> Optional[int]:
        """The node under the point, its box or the wires its span covers."""
        i = bisect_left(self.right, x - slack)
        if i >= self.n or self.left[i] > x + slack:
            return None
        half = NODE_H / 2 + slack
        if abs(y - self.y[i]) <= half or self.span_lo[i] - 6 <= y <= self.span_hi[i] + 6:
            return i
        return None

    # -------------------------------------------------------------- slicing --

    def slice(self, criterion: int, direction: str = "backward",
              deps: Optional[Iterable[DependencyType]] = None) -> Set[int]:
        """The backward or forward slice from ``criterion``, following ``deps`` (all if None)."""
        follow = set(deps) if deps is not None else None
        walk = QuantumSlicer.forward_slice if direction == "forward" else QuantumSlicer.backward_slice
        return walk(self.qdg, SlicingCriterion(criterion, dep_types=follow))

    def slices(self, criteria: Iterable[int],
               deps: Optional[Iterable[DependencyType]] = None) -> Dict[int, Set[int]]:
        """One backward slice per criterion."""
        follow = set(deps) if deps is not None else None
        return QuantumSlicer.backward_slices(self.qdg, list(criteria), follow)

    def chop(self, source: int, target: int,
             deps: Optional[Iterable[DependencyType]] = None) -> Set[int]:
        """What lies on a dependence path from ``source`` to ``target``; empty
        when ``source`` cannot affect ``target``."""
        follow = set(deps) if deps is not None else None
        return QuantumSlicer.chop(self.qdg, source, target, follow)

    @property
    def measurements(self) -> Dict[int, List[int]]:
        """qubit -> its measurement nodes, in order (unmeasured qubits left out)."""
        if self._measurements is None:
            self._measurements = QuantumSlicer.measurements_of(self.program)
        return self._measurements

    def qubit_criterion(self, qubit: int) -> Optional[int]:
        """What "slice on this qubit" slices from: its last measurement, or None."""
        found = self.measurements.get(qubit)
        return found[-1] if found else None

    def qubit_at(self, y: float) -> Optional[int]:
        """The qubit wire nearest ``y``, if it is within half a lane of it."""
        q = int(round((y - TOP_PAD) / LANE_H))
        if 0 <= q < self.num_qubits and abs(y - self.lane_y(q)) <= LANE_H / 2:
            return q
        return None

    # ------------------------------------------------------------- insights --

    @property
    def insights(self) -> Dict[str, Any]:
        """``qslicer.insights.summary.summarize`` for this circuit, worked out on
        first use (about 90 ms for Shor). If it fails, nothing is found, and the
        viewer goes on without the marks."""
        if self._insights is None:
            try:
                from ..insights.summary import summarize
                found = summarize(self.program)
            except Exception:   # an insight pass must not take the viewer down
                found = {}
            self._set_insights(found)
        return self._insights

    def _set_insights(self, found: Dict[str, Any]) -> None:
        self._insights = insights = {key: found.get(key) or type(empty)()
                                     for key, empty in _EMPTY_INSIGHTS.items()}
        pairs = [p for p in insights["mirrors"]
                 if len(p) >= 3 and all(0 <= int(i) < self.n for i in p[:2])]
        self.mirror_first = np.asarray([int(p[0]) for p in pairs], dtype=np.int64)
        self.mirror_second = np.asarray([int(p[1]) for p in pairs], dtype=np.int64)
        self.mirror_depth = np.asarray([int(p[2]) for p in pairs], dtype=np.int64)
        self.mirror_pairs: Dict[int, List[int]] = {}
        for i, (a, b) in enumerate(zip(self.mirror_first, self.mirror_second)):
            self.mirror_pairs.setdefault(int(a), []).append(i)
            self.mirror_pairs.setdefault(int(b), []).append(i)

    def mirrors_of(self, nodes: Iterable[int]) -> List[int]:
        """The mirror pairs (indices into ``mirror_first``) with an end in ``nodes``."""
        self.insights   # noqa: B018 -- builds the mirror arrays
        found: Dict[int, None] = {}
        for idx in nodes:
            for i in self.mirror_pairs.get(idx, ()):
                found[i] = None
        return list(found)

    def mirrors_in(self, nodes: range) -> np.ndarray:
        """The mirror pairs with an end in the run of node ids ``nodes``."""
        self.insights   # noqa: B018 -- builds the mirror arrays
        lo, hi = nodes.start, nodes.stop
        first, second = self.mirror_first, self.mirror_second
        return np.flatnonzero(((first >= lo) & (first < hi)) | ((second >= lo) & (second < hi)))

    def mirror_partner(self, pair: int, idx: int) -> int:
        a, b = int(self.mirror_first[pair]), int(self.mirror_second[pair])
        return b if idx == a else a

    def mirror_arcs(self, pairs: Sequence[int]) -> np.ndarray:
        """An arc above the boxes from one gate of each pair to the other,
        ``(len(pairs), ARC_POINTS, 2)``; deeper (outer) pairs arc higher, so
        nested pairs on a wire never cross. An arc leaves the top edge near the
        corner facing the other gate, clear of the index label."""
        pairs = np.asarray(pairs, dtype=np.int64)
        if not len(pairs):
            return np.zeros((0, ARC_POINTS, 2))
        a, b = self.mirror_first[pairs], self.mirror_second[pairs]
        top = np.minimum(self.y[a], self.y[b]) - NODE_H / 2
        height = ARC_BASE + ARC_STEP * np.minimum(self.mirror_depth[pairs], ARC_MAX_DEPTH)
        t = np.linspace(0.0, 1.0, ARC_POINTS)[None, :]
        x0, x1 = self.right[a] - 6, self.left[b] + 6
        xs = x0[:, None] * (1 - t) + x1[:, None] * t
        ys = top[:, None] - height[:, None] * np.sin(np.pi * t)
        return np.stack([xs, ys], axis=-1)

    def marks(self, idx: int) -> List[Tuple[str, str]]:
        """The small marks under a node: (text, kind), kind a key of MARK_COLORS."""
        insights = self.insights
        found = []
        if self.phase_only[idx]:
            found.append(("φ", "phase"))
        if idx in insights["kickback"]:
            found.append(("kb", "kickback"))
        if idx in insights["predetermined"]:
            found.append((f"={insights['predetermined'][idx]}", "always"))
        return found

    def correlation(self, idx: int) -> Optional[Dict[str, Any]]:
        return self.insights["correlations"].get(idx)

    def correlation_notes(self, idx: int) -> List[str]:
        found = self.correlation(idx)
        if not found:
            return []
        notes = []
        if found.get("qubits"):
            qubits = _qubit_list(found["qubits"])
            notes.append(f"May be correlated with the measurements of {qubits}.")
        if found.get("unmeasured"):
            qubits = _qubit_list(found["unmeasured"])
            notes.append(f"May still be entangled with unmeasured {qubits}.")
        return notes

    def notes(self, idx: int) -> List[str]:
        """What the insight passes say about a node, as the hover card shows it.
        The wording is the web page's."""
        insights = self.insights
        notes = []
        if self.phase_only[idx]:
            text = ("Phase only: this gate cannot change a measurement outcome until a "
                    "non-diagonal gate follows on this wire.")
            if len(self.qubits[idx]) > 1:
                text += " It can still entangle."
            notes.append(text)
        if idx in insights["kickback"]:
            notes.append("Phase kickback: the target is left as it is and the phase lands on "
                         f"control {_qubit_list(insights['kickback'][idx])}.")
        if idx in insights["predetermined"]:
            bit = insights["predetermined"][idx]
            notes.append(f"This measurement always gives {bit}: "
                         f"the qubit is provably |{bit}> here.")
        if idx in insights["no_effect"]:
            notes.append(f"No effect here: {insights['no_effect'][idx]}.")
        notes += self.correlation_notes(idx)
        for pair in self.mirrors_of([idx]):
            other = self.mirror_partner(pair, idx)
            notes.append(f"mirrors #{other} ({self.short[other]})")
        return notes

    # ----------------------------------------------------------------- lint --

    def set_diagnostics(self, diagnostics: List[Dict[str, Any]]) -> None:
        """Findings about this circuit; each gets ``nodes`` (see attach_lint_nodes)."""
        attach_lint_nodes(diagnostics, self)
        self.diagnostics = diagnostics
        self.lint_by_node: Dict[int, List[Dict[str, Any]]] = {}
        self.lint_by_qubit: Dict[int, List[Dict[str, Any]]] = {}
        self.lint_severity = np.zeros(self.n, dtype=np.int8)
        for finding in diagnostics:
            rank = SEVERITY_RANK.get(finding.get("severity"), 1)
            for idx in finding.get("nodes", []):
                self.lint_by_node.setdefault(idx, []).append(finding)
                self.lint_severity[idx] = max(self.lint_severity[idx], rank)
            for q in finding.get("qubits", []) or []:
                if isinstance(q, int) and 0 <= q < self.num_qubits:
                    self.lint_by_qubit.setdefault(q, []).append(finding)

    # ------------------------------------------------------------- describe --

    def label(self, idx: int) -> str:
        """The full label: name, the first three parameters, the wires."""
        instruction = self.program[idx]
        params = [format_param(p) for p in list(instruction.params)[:3]]
        text = instruction.name + (f"({', '.join(params)})" if params else "")
        if instruction.qubits:
            text += " q" + ",".join(str(q) for q in instruction.qubits)
        if instruction.clbits:
            text += " → c" + ",".join(str(c) for c in instruction.clbits)
        if self.reads[idx]:
            text += "  if c" + ",".join(str(c) for c in self.reads[idx])
        return text

    def where(self, idx: int) -> str:
        location = getattr(self.program[idx], "location", None)
        if location is None:
            return ""
        text = f"line {location.line}"
        if location.end_line and location.end_line != location.line:
            text += f"-{location.end_line}"
        if location.via:
            text += " via line " + ", ".join(str(v) for v in location.via)
        return text


def attach_lint_nodes(diagnostics: List[Dict[str, Any]], graph: Graph) -> None:
    """Give each finding ``nodes``: the instructions it is about.

    The rule of ``attach_lint_nodes`` in webapp/analysis.py: nodes a linter
    named are kept when one of them really comes from the finding's line;
    otherwise every node whose source span covers the finding's ``lines`` (or
    its ``line``) is. A finding with no line gets none.
    """
    spans: Optional[List[Tuple[int, int, int]]] = None

    def span(idx: int) -> Optional[Tuple[int, int]]:
        location = getattr(graph.program[idx], "location", None)
        return None if location is None else (location.line, location.end_line or location.line)

    def covering(lines: List[int]) -> List[int]:
        nonlocal spans
        if not lines:
            return []
        if spans is None:
            spans = [(idx, *s) for idx in range(graph.n) for s in [span(idx)] if s]
        wanted = set(lines)
        return [idx for idx, first, last in spans
                if any(first <= line <= last for line in wanted)]

    for finding in diagnostics:
        named = [i for i in finding.get("nodes", []) or [] if isinstance(i, int) and 0 <= i < graph.n]
        line = finding.get("line") or 0
        if named and any((s := span(i)) and s[0] <= line <= s[1] for i in named):
            finding["nodes"] = named
            continue
        lines = finding.get("lines") or ([line] if line else [])
        finding["nodes"] = covering(lines)


# --------------------------------------------------------------------------
# One file
# --------------------------------------------------------------------------

class NeedsValue(Exception):
    """The parse stopped on a value only a run decides; ``needs`` says which."""

    def __init__(self, message: str, needs: Dict[str, Any]) -> None:
        super().__init__(message)
        self.needs = needs


def parse_values(pairs: Iterable[str]) -> Dict[str, Any]:
    """``["a=3", "name='x'"]`` to ``{"a": 3, "name": "x"}``; a value that is not
    a Python literal is kept as the string it is (what ``input()`` returns)."""
    values: Dict[str, Any] = {}
    for pair in pairs or []:
        name, sep, text = pair.partition("=")
        if not sep or not name.strip():
            raise ValueError(f"--set wants NAME=VALUE, not {pair!r}")
        try:
            values[name.strip()] = ast.literal_eval(text.strip())
        except (ValueError, SyntaxError):
            values[name.strip()] = text.strip()
    return values


class Document:
    """A file, parsed once, and the circuits it builds.

    ``values`` maps a name to the value it should get where the parse stops on
    something only a run decides (``input()``, an unseeded random draw ...);
    the parse is retried until it needs one it was not given.
    """

    def __init__(self, path: str, language: Optional[str] = None, profile: str = "baseline",
                 values: Optional[Dict[str, Any]] = None, lint: bool = True,
                 select: Optional[Sequence[str]] = None, time_limit: Optional[float] = None,
                 source: Optional[str] = None) -> None:
        self.path = path
        self.filename = os.path.basename(path) or "circuit.py"
        if source is None:
            with open(path, encoding="utf-8-sig") as handle:
                source = handle.read()
        self.source = source
        self.language = language or language_of(path)
        self.kind = f"{self.language}-static"
        self.profile = profile
        self.rules = get_rule_profile(profile)
        self.values = dict(values or {})
        self.lint_enabled = lint
        self.select = list(select) if select else None
        self.time_limit = time_limit
        self.used_values: Set[str] = set()
        self.notes: List[Tuple[Any, str]] = []
        self.circuits: List[Dict[str, Any]] = []
        self._parser: Any = None
        self._graphs: Dict[int, Graph] = {}
        self._lock = threading.Lock()   # parser.select re-selects shared parser state
        self._first = self._parse()

    def _parse(self) -> Any:
        if self.language == "qasm":
            from ..parsers.staticopenqasmparser import StaticOpenQASMParser
            parser = StaticOpenQASMParser(time_limit=self.time_limit)
            parser.source = self.source
            program = parser.parse()
            self.notes = list(parser.notes)
            self.circuits = [{"index": 0, "name": self.filename, "size": len(program),
                              "num_qubits": program.num_qubits, "line": None}]
            return program

        from ..parsers.staticqiskitparser import StaticParseError, StaticQiskitParser
        bindings: Dict[Tuple[int, str], Any] = {}
        while True:
            parser = StaticQiskitParser(filename=self.filename, time_limit=self.time_limit,
                                        bindings=bindings)
            parser.source = self.source
            try:
                program = parser.parse()
                break
            except StaticParseError as exc:
                needs = exc.needs
                if not needs:
                    raise
                key = (needs["line"], needs["name"])
                if needs["name"] in self.values and key not in bindings:
                    bindings[key] = self.values[needs["name"]]
                    self.used_values.add(needs["name"])
                    continue
                raise NeedsValue(
                    f"line {needs['line']}: `{needs['expression']}` only has a value when the "
                    f"program runs, and the circuit depends on it. Pick one with "
                    f"--set {needs['name']}=VALUE", needs) from None
        self._parser = parser
        self.notes = list(parser.notes)
        self.circuits = [dict(info) for info in parser.circuits]
        return program

    def graph(self, index: int = 0) -> Graph:
        """The ``index``-th circuit (0 is the main one), laid out; built once."""
        if not 0 <= index < max(1, len(self.circuits)):
            index = 0
        graph = self._graphs.get(index)
        if graph is None:
            with self._lock:
                if index == 0 or self._parser is None:
                    program = self._first
                else:
                    program = self._parser.select(index)
            info = self.circuits[index] if self.circuits else None
            graph = Graph(QDG(program, rules=self.rules), info)
            graph.lint_state = "pending" if self.lint_enabled else "off"
            self._graphs[index] = graph
        return graph

    def lint(self, graph: Graph) -> List[Dict[str, Any]]:
        """Run the selected linters over the circuit ``graph`` shows, sharing its
        parse and its QDG; the parser's notes come along as QS200 warnings.
        Safe to call off the main thread; the result is not applied here."""
        from ..lint import registry
        program, qdg = graph.program, graph.qdg
        context = registry.LintContext(self.source, self.filename, self.kind, self.profile,
                                       parse=lambda: (program, None), qdg=lambda: qdg)
        findings = registry.run(self.source, self.filename, self.kind, self.select,
                                self.profile, context=context)
        findings += [{
            "code": "QS200", "severity": "warning", "line": line or 0, "column": 0,
            "message": message[:1].upper() + message[1:] + ".",
            "hint": "The static parser skipped or approximated this rather than guess.",
            "tool": "parser", "rule": "parser/QS200",
        } for line, message in self.notes]
        findings.sort(key=lambda f: (f.get("line") or 0, f.get("column") or 0, f.get("code", "")))
        return findings
