from qiskit_qasm3_import.types import Qubit
import argparse
import os
from collections import defaultdict

from entanglement import EntanglementGraph, EntanglementAnalyzer, _CLASSICAL_GATES
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

_NODE_COLORS = {
    "init": "#5C6370",
    "entangled": "#4A6FA5",
    "measurement": "#3B8C6E",
    "classical": "#D28E5D",
}
_EDGE_COLOR = "#C7CEDB"
_BACKGROUND = "#1E1E2E"


def _format_bits(bits):
    return ",".join(str(bit) for bit in sorted(bits))


def _format_instruction(instruction):
    qubits = _format_bits(instruction.qubits)
    label = f"{instruction.name} q[{qubits}]"
    
    if instruction.name in _CLASSICAL_GATES:
        cond = instruction.metadata.get("condition") if instruction.metadata else None
        if cond is not None:
            _, val = cond
            label += f" (c[{_format_bits(instruction.clbits)}] == {val})"
        else:
            label += f" (c[{_format_bits(instruction.clbits)}])"
        if getattr(instruction, "params", None) and hasattr(instruction.params[0], "data"):
            inner_insts = []
            for inst in instruction.params[0].data:
                op = getattr(inst, "operation", None)
                inner_insts.append(op.name)
            if inner_insts:
                label += f" => {', '.join(inner_insts)}"
    elif getattr(instruction, "clbits", None):
        label += f" -> c[{_format_bits(instruction.clbits)}]"
    return label


def _format_instruction_list(instructions, limit=10):
    if not instructions:
        return ""

    counts = defaultdict(int)
    ordered_keys = []
    for instruction in instructions:
        key = (
            instruction.name,
            tuple(sorted(instruction.qubits)),
            tuple(sorted(instruction.clbits)) if getattr(instruction, "clbits", None) else tuple(),
        )
        if key not in counts:
            ordered_keys.append((key, instruction))
        counts[key] += 1

    labels = []
    for key, instruction in ordered_keys[:limit]:
        prefix = f"{counts[key]}x " if counts[key] > 1 else ""
        labels.append(prefix + _format_instruction(instruction))

    remaining = len(ordered_keys) - limit
    if remaining > 0:
        labels.append(f"... +{remaining} more")

    return "\n".join(labels)


def _group_label(node_id, group):
    title = f"{node_id}: {_format_instruction(group.origin)}"
    qubits = f"q: {_format_bits(group.qubits)}"

    if group.kind == "measurement":
        title = f"{node_id}: measurement"
        meas_inst_list = [i for i in group.computed_instructions if i.name in {"measure", "measure_all"}]
        comp_inst_list = [i for i in group.computed_instructions if i.name not in {"measure", "measure_all"}]
        uncomp_inst_list = [i for i in group.uncomputed_instructions]
        meas_inst = _format_instruction_list(meas_inst_list)
        comp_inst = _format_instruction_list(comp_inst_list)
        uncomp_inst = _format_instruction_list(uncomp_inst_list)
        
        parts = [title, qubits]
        if meas_inst:
            parts.append(f"measurements:\n{meas_inst}")
        if comp_inst:
            parts.append(f"computed:\n{comp_inst}")
        if uncomp_inst:
            parts.append(f"uncomputed:\n{uncomp_inst}")
        return "\n".join(parts)
    elif group.kind == "classical":
        title = f"{node_id}: classical computation"
        cond_insts_list = [group.origin] + [i for i in group.computed_instructions if i.name in {"if_else", "c_if"}]
        comp_inst_list = [i for i in group.computed_instructions if i.name not in {"if_else", "c_if"}]
        uncomp_inst_list = [i for i in group.uncomputed_instructions]
        
        cond_inst = _format_instruction_list(cond_insts_list)
        comp_inst = _format_instruction_list(comp_inst_list)
        uncomp_inst = _format_instruction_list(uncomp_inst_list)
        
        parts = [title, qubits]
        if cond_inst:
            parts.append(f"conditional computes:\n{cond_inst}")
        if comp_inst:
            parts.append(f"computed:\n{comp_inst}")
        if uncomp_inst:
            parts.append(f"uncomputed:\n{uncomp_inst}")
        return "\n".join(parts)

    instructions = _format_instruction_list(group.computed_instructions)
    uncomp_inst = _format_instruction_list(group.uncomputed_instructions)
    label = ""
    if instructions:
        label += f"\ncomputed:\n{instructions}"

    if uncomp_inst:
        label += f"\nuncomputed:\n{uncomp_inst}"

    return f"{title}\n{qubits}{label}"


def _hierarchical_layout(nx_graph):
    columns = defaultdict(list)
    for node_id, data in nx_graph.nodes(data=True):
        group = data["group"]
        columns[group.depth].append(node_id)

    positions = {}
    x_spacing = 4.0
    y_spacing = 2.3
    for depth in sorted(columns):
        nodes = sorted(columns[depth])
        y_offset = (len(nodes) - 1) / 2.0
        for row, node_id in enumerate(nodes):
            positions[node_id] = (depth * x_spacing, (y_offset - row) * y_spacing)

    return positions


def _create_figure(fig_width, fig_height, show):
    try:
        return plt.subplots(figsize=(fig_width, fig_height))
    except Exception:
        if show:
            plt.switch_backend("WebAgg")
            return plt.subplots(figsize=(fig_width, fig_height))
        raise


def visualize_entanglement_graph(graph: EntanglementGraph, show=True, ax=None):
    if not show and ax is None:
        plt.switch_backend("Agg")

    nx_graph = graph.graph
    positions = _hierarchical_layout(nx_graph)

    if positions:
        max_depth = max(data["group"].depth for _, data in nx_graph.nodes(data=True))
        max_column_size = max(
            sum(1 for _, data in nx_graph.nodes(data=True) if data["group"].depth == depth)
            for depth in range(max_depth + 1)
        )
    else:
        max_depth = 0
        max_column_size = 1

    if ax is None:
        fig_width = max(11, (max_depth + 1) * 3.2)
        fig_height = max(6, max_column_size * 1.5)
        fig, ax = _create_figure(fig_width, fig_height, show)
        fig.patch.set_facecolor(_BACKGROUND)
        is_new_fig = True
    else:
        fig = ax.figure
        is_new_fig = False

    ax.set_facecolor(_BACKGROUND)
    ax.axis("off")

    for source, target in nx_graph.edges():
        start = positions[source]
        end = positions[target]
        ax.annotate(
            "",
            xy=end,
            xytext=start,
            arrowprops={
                "arrowstyle": "->",
                "color": _EDGE_COLOR,
                "lw": 1.8,
                "shrinkA": 10,
                "shrinkB": 10,
                "connectionstyle": "angle, angleA=0, angleB=90, rad=0",
            },
            zorder=1,
        )

    for node_id, data in nx_graph.nodes(data=True):
        group = data["group"]
        x, y = positions[node_id]
        color = _NODE_COLORS.get(group.kind, _NODE_COLORS["entangled"])
        ax.text(
            x,
            y,
            _group_label(node_id, group),
            ha="center",
            va="center",
            fontsize=8,
            color="white",
            linespacing=1.25,
            bbox={
                "boxstyle": "round,pad=0.55,rounding_size=0.18",
                "facecolor": color,
                "edgecolor": "white",
                "linewidth": 1.0,
                "alpha": 0.97,
            },
            zorder=2,
        )

    if positions:
        y_values = [y for _, y in positions.values()]
        y_top = max(y_values) + 1.6
        y_bottom = min(y_values) - 1.6
        for depth in sorted({data["group"].depth for _, data in nx_graph.nodes(data=True)}):
            x = depth * 4.0
            ax.axvline(x, color="#45475A", linewidth=0.8, linestyle="--", alpha=0.45, zorder=0)
            ax.text(
                x,
                y_top,
                f"Depth {depth}",
                ha="center",
                va="bottom",
                fontsize=9,
                color="#CDD6F4",
            )

        x_values = [x for x, _ in positions.values()]
        ax.set_xlim(min(x_values) - 2.2, max(x_values) + 2.2)
        ax.set_ylim(y_bottom, y_top + 0.6)

    legend_entries = [
        mpatches.Patch(color=_NODE_COLORS["init"], label="Init group"),
        mpatches.Patch(color=_NODE_COLORS["entangled"], label="Entangled group"),
        mpatches.Patch(color=_NODE_COLORS["measurement"], label="Measurement collapse"),
        mpatches.Patch(color=_NODE_COLORS["classical"], label="Classical computation"),
    ]
    ax.legend(
        handles=legend_entries,
        loc="lower center",
        bbox_to_anchor=(0.5, -0.03),
        ncol=4,
        fontsize=8,
        facecolor="#313244",
        labelcolor="white",
        framealpha=0.9,
    )

    ax.set_title(
        "Entanglement groups by dependency depth",
        color="white",
        fontsize=12,
        pad=10,
    )
    if is_new_fig:
        plt.tight_layout()

    if show and is_new_fig:
        plt.show()

    return fig, ax


def _build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Visualize entanglement groups in a left-to-right hierarchical layout.",
    )
    parser.add_argument(
        "--circuit_path",
        default=os.path.join("Attachments", "Sample Code", "grovers_algorithm_exception.py"),
        help="Path to the circuit .py file to parse.",
    )
    parser.add_argument(
        "--show",
        dest="show",
        action="store_true",
        default=True,
        help="Display the Matplotlib window. This is the default.",
    )
    parser.add_argument(
        "--no-show",
        dest="show",
        action="store_false",
        help="Build the figure without displaying it.",
    )
    return parser

if __name__ == "__main__":
    from Parsers.qiskitparser import QiskitParser
    args = _build_arg_parser().parse_args()

    parser = QiskitParser() 
    program = parser.parse_file(args.circuit_path)

    analyzer = EntanglementAnalyzer(program)
    analyzer.analyze()

    graph = EntanglementGraph(analyzer)

    visualize_entanglement_graph(graph, show=args.show)
