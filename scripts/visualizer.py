"""
QPDG Visualizer - interactive matplotlib window for exploring quantum program slices.

Usage (from scripts/):
    python visualizer.py path/to/circuit.py [--profile PROFILE]

Controls:
    Prev / Next  - cycle the slicing criterion through each instruction node
    Bwd / Fwd    - toggle between backward slice and forward slice
"""

import argparse
import os

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.widgets import Button
import networkx as nx
from typing import Dict, List, Set, Tuple

from Parsers.qiskitparser import QiskitParser
from qpdg import QPDG, get_rule_profile, list_rule_profiles
from Dependencies.dependency_rules import DependencyRule, DependencyType
from slicer import QuantumSlicer, SlicingCriterion


# ---------------------------------------------------------------------------
# Visual style
# ---------------------------------------------------------------------------

_NODE_CRITERION = "#FF6B35"
_NODE_IN_SLICE = "#4A90D9"
_NODE_OUT_SLICE = "#DDDDDD"

_EDGE_COLORS: Dict[DependencyType, str] = {
    DependencyType.DATA_QUBIT: "#2196F3",
    DependencyType.ENTANGLEMENT: "#E91E63",
    DependencyType.MEASUREMENT: "#4CAF50",
    DependencyType.DATA_CLASSICAL: "#FF9800",
    DependencyType.CONTROL_CLASSICAL: "#795548",
    DependencyType.PHASE: "#8D1EAC",
}

_CURVE_STYLES = [
    "arc3,rad=0.0",
    "arc3,rad=0.25",
    "arc3,rad=-0.25",
    "arc, angleA=-90, angleB=0, rad=0",
    "angle, angleA=0, angleB=90, rad=0.0",
    "arc3,rad=0.45",
    "arc3,rad=-0.45",
    "arc3,rad=0.6",
]


def _circuit_layout(qpdg: QPDG) -> Dict[int, Tuple[float, float]]:
    """
    Place each node at (instruction_index, average_qubit_index).
    Produces a left-to-right circuit-style layout.
    """
    pos = {}
    for idx, node in qpdg.nodes.items():
        qubits = node.instruction.qubits
        y = sum(qubits) / len(qubits) if qubits else -1.0
        pos[idx] = (float(idx), y)
    
    return pos


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
        self.criterion_idx = self.node_count - 1
        self.mode = "backward"
        self.pos = _circuit_layout(qpdg)

        self.fig, self.ax = plt.subplots(figsize=(13, 7))
        self.fig.patch.set_facecolor("#1E1E2E")
        self.ax.set_facecolor("#1E1E2E")
        plt.subplots_adjust(bottom=0.18, top=0.93)

        ax_prev = self.fig.add_axes([0.20, 0.04, 0.12, 0.07])
        ax_next = self.fig.add_axes([0.34, 0.04, 0.12, 0.07])
        ax_toggle = self.fig.add_axes([0.52, 0.04, 0.22, 0.07])

        self.btn_prev = Button(ax_prev, "Prev", color="#313244", hovercolor="#45475A")
        self.btn_next = Button(ax_next, "Next", color="#313244", hovercolor="#45475A")
        self.btn_toggle = Button(ax_toggle, "Bwd / Fwd", color="#313244", hovercolor="#45475A")

        for button in (self.btn_prev, self.btn_next, self.btn_toggle):
            button.label.set_color("white")

        self.btn_prev.on_clicked(self._on_prev)
        self.btn_next.on_clicked(self._on_next)
        self.btn_toggle.on_clicked(self._on_toggle)

        self._draw()
        plt.show()

    def _on_prev(self, _event):
        self.criterion_idx = (self.criterion_idx - 1) % self.node_count
        self._draw()

    def _on_next(self, _event):
        self.criterion_idx = (self.criterion_idx + 1) % self.node_count
        self._draw()

    def _on_toggle(self, _event):
        self.mode = "forward" if self.mode == "backward" else "backward"
        self._draw()

    def _compute_slice(self) -> Set[int]:
        criterion = SlicingCriterion(node_idx=self.criterion_idx)
        if self.mode == "backward":
            return QuantumSlicer.backward_slice(self.qpdg, criterion)
        return QuantumSlicer.forward_slice(self.qpdg, criterion)

    def _draw(self):
        self.ax.clear()
        self.ax.set_facecolor("#1E1E2E")
        self.ax.axis("off")

        slice_nodes = self._compute_slice()
        node_colors = []
        for idx in self.qpdg.graph.nodes():
            if idx == self.criterion_idx:
                node_colors.append(_NODE_CRITERION)
            elif idx in slice_nodes:
                node_colors.append(_NODE_IN_SLICE)
            else:
                node_colors.append(_NODE_OUT_SLICE)

        labels = {}
        for idx, node in self.qpdg.nodes.items():
            instruction = node.instruction
            q_str = str(instruction.qubits) if instruction.qubits else ""
            labels[idx] = f"{idx}: {instruction.name}\n{q_str}"

        nx.draw_networkx_nodes(
            self.qpdg.graph,
            self.pos,
            ax=self.ax,
            node_color=node_colors,
            node_size=1800,
            edgecolors="white",
            linewidths=0.8,
        )
        nx.draw_networkx_labels(
            self.qpdg.graph,
            self.pos,
            labels=labels,
            ax=self.ax,
            font_size=8,
            font_color="white",
        )

        edge_groups: Dict[Tuple[int, int], List[DependencyType]] = {}
        for source, target, data in self.qpdg.graph.edges(data=True):
            dependency = data.get("dependency")
            edge_groups.setdefault((source, target), []).append(dependency)

        for (source, target), dependencies in edge_groups.items():
            for idx, dependency in enumerate(dependencies):
                color = _EDGE_COLORS.get(dependency, "#AAAAAA")
                style = _CURVE_STYLES[idx % len(_CURVE_STYLES)]
                alpha = 1.0 if (source in slice_nodes or target in slice_nodes) else 0.15
                nx.draw_networkx_edges(
                    self.qpdg.graph,
                    self.pos,
                    ax=self.ax,
                    edgelist=[(source, target)],
                    edge_color=color,
                    connectionstyle=style,
                    arrows=True,
                    arrowsize=18,
                    width=2.0,
                    alpha=alpha,
                    min_source_margin=30,
                    min_target_margin=30,
                )

        criterion_node = self.qpdg.nodes[self.criterion_idx]
        instruction = criterion_node.instruction
        direction = "Backward" if self.mode == "backward" else "Forward"
        header = self.title + " - " if self.title else ""
        self.ax.set_title(
            f"{header}{direction} slice from node {self.criterion_idx} "
            f"({instruction.name} q{instruction.qubits})   |   "
            f"Slice: {sorted(slice_nodes)}",
            color="white",
            fontsize=10,
            pad=8,
        )

        legend_entries = [
            mpatches.Patch(color=_NODE_CRITERION, label="Criterion node"),
            mpatches.Patch(color=_NODE_IN_SLICE, label="In slice"),
            mpatches.Patch(color=_NODE_OUT_SLICE, label="Outside slice"),
        ]
        for dependency, color in _EDGE_COLORS.items():
            legend_entries.append(mpatches.Patch(color=color, label=dependency.name))

        self.ax.legend(
            handles=legend_entries,
            loc="upper left",
            fontsize=7,
            facecolor="#313244",
            labelcolor="white",
            framealpha=0.85,
            borderpad=0.6,
        )

        self.fig.canvas.draw_idle()


def _load_qpdg(path: str, profile_name: str) -> QPDG:
    parser = QiskitParser()
    parser.parse_file(path)
    if len(parser.program) == 0:
        raise ValueError(f"No instructions parsed from {path}")
    return QPDG(parser.program, rules=get_rule_profile(profile_name))


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Visualize a QPDG using one of the named dependency profiles.",
    )
    parser.add_argument(
        "--circuit_path",
        default="Attachments\Sample Code\grovers_algorithm_exception.py",
        help="Path to the circuit .py file to parse")
    parser.add_argument(
        "--profile",
        default="baseline",
        help=f"Dependency profile to visualize. Available: {', '.join(list_rule_profiles())}",
    )
    return parser


if __name__ == "__main__":
    args = _build_arg_parser().parse_args()
    qpdg = _load_qpdg(args.circuit_path, args.profile)
    title = f"{os.path.basename(args.circuit_path)} ({args.profile})"
    QPDGVisualizer(qpdg, title=title)
