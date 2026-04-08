"""
QPDG Visualizer — interactive matplotlib window for exploring quantum program slices.

Usage (from scripts/):
    python visualizer.py [path/to/circuit.py]

    If no path is given, runs on the sample circuits in ../Attachments/Sample Code/.

Controls:
    Prev / Next  — cycle the slicing criterion through each instruction node
    Bwd / Fwd    — toggle between backward slice and forward slice
"""

import os
import sys

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.widgets import Button
import networkx as nx
from typing import Dict, List, Set, Tuple

from qpdg import QPDG, DependencyType
from slicer import QuantumSlicer, SlicingCriterion
from Parsers.qiskitparser import QiskitParser


# ---------------------------------------------------------------------------
# Visual style
# ---------------------------------------------------------------------------

_NODE_CRITERION  = '#FF6B35'  # orange — the selected criterion node
_NODE_IN_SLICE   = '#4A90D9'  # blue   — in the current slice
_NODE_OUT_SLICE  = '#DDDDDD'  # gray   — outside the slice

_EDGE_COLORS: Dict[DependencyType, str] = {
    DependencyType.DATA_QUBIT:        '#2196F3',  # blue
    DependencyType.ENTANGLEMENT:      '#E91E63',  # pink
    DependencyType.MEASUREMENT:       '#4CAF50',  # green
    DependencyType.DATA_CLASSICAL:    '#FF9800',  # orange
    DependencyType.CONTROL_CLASSICAL: '#795548',  # brown
}

# Curvature offsets so parallel edges (MultiDiGraph) don't overlap.
# Up to 6 edges between the same pair; each gets a different arc.
_CURVE_STYLES = [
    'arc3,rad=0.0',
    'arc3,rad=0.25',
    'arc3,rad=-0.25',
    'arc3,rad=0.45',
    'arc3,rad=-0.45',
    'arc3,rad=0.6',
]


# ---------------------------------------------------------------------------
# Layout helper
# ---------------------------------------------------------------------------

def _circuit_layout(qpdg: QPDG) -> Dict[int, Tuple[float, float]]:
    """
    Places each node at (instruction_index, average_qubit_index).
    Produces a left-to-right circuit-style layout.
    Nodes with no qubits (e.g., barriers on zero qubits) fall on y = -1.
    """
    pos = {}
    for idx, node in qpdg.nodes.items():
        qubits = node.instruction.qubits
        y = sum(qubits) / len(qubits) if qubits else -1.0
        pos[idx] = (float(idx), y)
    return pos


# ---------------------------------------------------------------------------
# Core visualizer class
# ---------------------------------------------------------------------------

class QPDGVisualizer:
    """
    Interactive QPDG viewer with Prev/Next node navigation and Bwd/Fwd toggle.

    criterion_idx : index of the currently selected instruction node
    mode          : 'backward' or 'forward'
    """

    def __init__(self, qpdg: QPDG, title: str = ""):
        self.qpdg = qpdg
        self.title = title
        self.node_count = len(qpdg.nodes)
        self.criterion_idx = self.node_count - 1  # start at last instruction
        self.mode = 'backward'

        self.pos = _circuit_layout(qpdg)

        # ------------------------------------------------------------------
        # Figure layout
        # ------------------------------------------------------------------
        self.fig, self.ax = plt.subplots(figsize=(13, 7))
        self.fig.patch.set_facecolor('#1E1E2E')
        self.ax.set_facecolor('#1E1E2E')
        plt.subplots_adjust(bottom=0.18, top=0.93)

        # Button axes: [left, bottom, width, height] in figure coords
        ax_prev   = self.fig.add_axes([0.20, 0.04, 0.12, 0.07])
        ax_next   = self.fig.add_axes([0.34, 0.04, 0.12, 0.07])
        ax_toggle = self.fig.add_axes([0.52, 0.04, 0.22, 0.07])

        self.btn_prev   = Button(ax_prev,   'Prev',      color='#313244', hovercolor='#45475A')
        self.btn_next   = Button(ax_next,   'Next',      color='#313244', hovercolor='#45475A')
        self.btn_toggle = Button(ax_toggle, 'Bwd / Fwd', color='#313244', hovercolor='#45475A')

        for btn in (self.btn_prev, self.btn_next, self.btn_toggle):
            btn.label.set_color('white')

        self.btn_prev.on_clicked(self._on_prev)
        self.btn_next.on_clicked(self._on_next)
        self.btn_toggle.on_clicked(self._on_toggle)

        self._draw()
        plt.show()

    # ------------------------------------------------------------------
    # Button callbacks
    # ------------------------------------------------------------------

    def _on_prev(self, _event):
        self.criterion_idx = (self.criterion_idx - 1) % self.node_count
        self._draw()

    def _on_next(self, _event):
        self.criterion_idx = (self.criterion_idx + 1) % self.node_count
        self._draw()

    def _on_toggle(self, _event):
        self.mode = 'forward' if self.mode == 'backward' else 'backward'
        self._draw()

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _compute_slice(self) -> Set[int]:
        criterion = SlicingCriterion(node_idx=self.criterion_idx)
        if self.mode == 'backward':
            return QuantumSlicer.backward_slice(self.qpdg, criterion)
        return QuantumSlicer.forward_slice(self.qpdg, criterion)

    def _draw(self):
        self.ax.clear()
        self.ax.set_facecolor('#1E1E2E')
        self.ax.axis('off')

        slice_nodes = self._compute_slice()

        # ------------------------------------------------------------------
        # Node colours
        # ------------------------------------------------------------------
        node_colors = []
        for idx in self.qpdg.graph.nodes():
            if idx == self.criterion_idx:
                node_colors.append(_NODE_CRITERION)
            elif idx in slice_nodes:
                node_colors.append(_NODE_IN_SLICE)
            else:
                node_colors.append(_NODE_OUT_SLICE)

        # ------------------------------------------------------------------
        # Node labels  (instruction name + qubit list)
        # ------------------------------------------------------------------
        labels = {}
        for idx, node in self.qpdg.nodes.items():
            instr = node.instruction
            q_str = str(instr.qubits) if instr.qubits else ''
            labels[idx] = f"{idx}: {instr.name}\n{q_str}"

        # Draw nodes
        nx.draw_networkx_nodes(
            self.qpdg.graph, self.pos, ax=self.ax,
            node_color=node_colors, node_size=1800,
            edgecolors='white', linewidths=0.8,
        )
        nx.draw_networkx_labels(
            self.qpdg.graph, self.pos, labels=labels, ax=self.ax,
            font_size=8, font_color='white',
        )

        # ------------------------------------------------------------------
        # Edges — grouped by (u, v) pair to assign curvature offsets
        # ------------------------------------------------------------------
        # Build: { (u, v): [(dep_type, edge_key), ...] }
        edge_groups: Dict[Tuple[int, int], List[DependencyType]] = {}
        for u, v, data in self.qpdg.graph.edges(data=True):
            dep = data.get('dependency')
            edge_groups.setdefault((u, v), []).append(dep)

        for (u, v), deps in edge_groups.items():
            for i, dep in enumerate(deps):
                color = _EDGE_COLORS.get(dep, '#AAAAAA')
                style = _CURVE_STYLES[i % len(_CURVE_STYLES)]
                # Dim edges whose endpoints are both outside the slice
                alpha = 1.0 if (u in slice_nodes or v in slice_nodes) else 0.15
                nx.draw_networkx_edges(
                    self.qpdg.graph, self.pos, ax=self.ax,
                    edgelist=[(u, v)],
                    edge_color=color,
                    connectionstyle=style,
                    arrows=True,
                    arrowsize=18,
                    width=2.0,
                    alpha=alpha,
                    min_source_margin=30,
                    min_target_margin=30,
                )

        # ------------------------------------------------------------------
        # Title
        # ------------------------------------------------------------------
        criterion_node = self.qpdg.nodes[self.criterion_idx]
        instr = criterion_node.instruction
        direction = "Backward" if self.mode == 'backward' else "Forward"
        header = self.title + " — " if self.title else ""
        self.ax.set_title(
            f"{header}{direction} slice from node {self.criterion_idx} "
            f"({instr.name} q{instr.qubits})   |   "
            f"Slice: {sorted(slice_nodes)}",
            color='white', fontsize=10, pad=8,
        )

        # ------------------------------------------------------------------
        # Legend
        # ------------------------------------------------------------------
        legend_entries = [
            mpatches.Patch(color=_NODE_CRITERION, label='Criterion node'),
            mpatches.Patch(color=_NODE_IN_SLICE,  label='In slice'),
            mpatches.Patch(color=_NODE_OUT_SLICE,  label='Outside slice'),
        ]
        for dep, color in _EDGE_COLORS.items():
            legend_entries.append(
                mpatches.Patch(color=color, label=dep.name)
            )
        self.ax.legend(
            handles=legend_entries,
            loc='upper left', fontsize=7,
            facecolor='#313244', labelcolor='white',
            framealpha=0.85, borderpad=0.6,
        )

        self.fig.canvas.draw_idle()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _load_qpdg(path: str) -> QPDG:
    parser = QiskitParser()
    parser.parse_file(path)
    if len(parser.program) == 0:
        raise ValueError(f"No instructions parsed from {path}")
    return QPDG(parser.program)


if __name__ == '__main__':
    if len(sys.argv) > 1:
        circuit_path = sys.argv[1]
        qpdg = _load_qpdg(circuit_path)
        QPDGVisualizer(qpdg, title=os.path.basename(circuit_path))
    else:
        print("No circuit path provided. Please run with: python visualizer.py [path/to/circuit.py]")
