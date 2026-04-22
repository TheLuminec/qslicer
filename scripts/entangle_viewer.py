import argparse
import os
from collections import defaultdict

from entanglement import EntanglementGraph, EntanglementAnalyzer
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

_NODE_COLORS = {
    "init": "#5C6370",
    "entangled": "#4A6FA5",
    "measurement": "#3B8C6E",
}
_EDGE_COLOR = "#C7CEDB"
_BACKGROUND = "#1E1E2E"


def _format_bits(bits):
    return ",".join(str(bit) for bit in sorted(bits))


def _format_instruction(instruction):
    qubits = _format_bits(instruction.qubits)
    label = f"{instruction.name} q[{qubits}]"
    if getattr(instruction, "clbits", None):
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
    title = f"{node_id}: {group.origin.name}"
    qubits = f"q: {_format_bits(group.qubits)}"
    instructions = _format_instruction_list(group.computed_instructions)

    if group.kind == "measurement":
        body_label = "measurements"
    else:
        body_label = "computed"

    if instructions:
        return f"{title}\n{qubits}\n{body_label}:\n{instructions}"
    return f"{title}\n{qubits}"


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


def visualize_entanglement_graph(graph: EntanglementGraph, show=True):
    if not show:
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

    fig_width = max(11, (max_depth + 1) * 3.2)
    fig_height = max(6, max_column_size * 1.5)
    fig, ax = _create_figure(fig_width, fig_height, show)
    fig.patch.set_facecolor(_BACKGROUND)
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
    ]
    ax.legend(
        handles=legend_entries,
        loc="lower center",
        bbox_to_anchor=(0.5, -0.03),
        ncol=3,
        fontsize=8,
        facecolor="#313244",
        labelcolor="white",
        framealpha=0.9,
    )

    ax.set_title(
        "Entanglement groups by dependency depth",
        color="white",
        fontsize=12,
        pad=14,
    )
    plt.tight_layout()

    if show:
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
