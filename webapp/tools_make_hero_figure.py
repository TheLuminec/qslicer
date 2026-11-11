"""Regenerate the homepage hero figure from the real analysis output.

    python webapp/tools_make_hero_figure.py > hero.svg

The picture is the superdense-coding circuit's QDG with a backward slice from
the final measurement, laid out by the same rules as static/js/graph.js, so the
landing page shows what the slicer actually produces rather than a drawing of
what it might.

The SVG lives inline in the hero <figure> of templates/index.html -- it is a
static illustration, not something the page builds per request. If that circuit
or the layout constants change, run this again and paste the result in, rather
than editing the paths by hand.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis          # noqa: E402
import examples          # noqa: E402
from qslicer import gates  # noqa: E402

# Constants copied from static/js/graph.js.
LANE_H = 66
TOP_PAD = 46
LEFT_PAD = 60
NODE_H = 26
NODE_GAP = 18
MIN_NODE_W = 34
CHAR_W = 6.3

CRITERION = 6            # measure q1, the node the slice starts from


def node_width(label):
    return max(MIN_NODE_W, round(len(label) * CHAR_W) + 16)


def main():
    entry = examples.load("superdense_coding")
    result = analysis.analyze_source(entry["source"], filename=entry["filename"])
    assert result["ok"], result.get("error")

    sliced = set(analysis.slice_program(result["token"], CRITERION, "backward")["slice"])
    nodes = result["nodes"]
    edges = result["edges"]

    # Layout: left to right by instruction, vertically on the average qubit lane.
    layout = {}
    x = LEFT_PAD
    for node in nodes:
        width = node_width(node["short"])
        layout[node["id"]] = {
            "x": x + width / 2,
            "y": TOP_PAD + node["lane"] * LANE_H,
            "w": width,
        }
        x += width + NODE_GAP

    content_width = x + 10
    qubits = result["program"]["num_qubits"]
    content_height = TOP_PAD + (qubits - 1) * LANE_H + 60

    out = []
    out.append(
        '<svg viewBox="0 -14 %d %d" class="hero-graph" role="img"\n'
        '         aria-label="Dependence graph of a superdense coding circuit, with a '
        'backward slice from the final measurement highlighted">' % (content_width, content_height)
    )
    out.append("""
      <defs>
        <marker id="hero-arrow" viewBox="0 0 10 10" refX="9" refY="5"
                markerWidth="5" markerHeight="5" orient="auto-start-reverse"
                markerUnits="strokeWidth">
          <path d="M 0 0 L 10 5 L 0 10 z" class="hero-arrowhead"></path>
        </marker>
        <marker id="hero-arrow-dim" viewBox="0 0 10 10" refX="9" refY="5"
                markerWidth="5" markerHeight="5" orient="auto-start-reverse"
                markerUnits="strokeWidth">
          <path d="M 0 0 L 10 5 L 0 10 z" class="hero-arrowhead dim"></path>
        </marker>
      </defs>""".rstrip())

    # Qubit wires.
    out.append("\n      <g class=\"hero-wires\">")
    for q in range(qubits):
        y = TOP_PAD + q * LANE_H
        out.append(
            '        <line class="hero-wire" x1="%d" y1="%d" x2="%d" y2="%d"></line>'
            % (LEFT_PAD - 30, y, content_width - 12, y)
        )
        out.append(
            '        <text class="hero-wire-label" x="%d" y="%d">q%d</text>'
            % (LEFT_PAD - 38, y + 4, q)
        )
    out.append("      </g>")

    # Edges, bowed the same way the graph view bows them.
    out.append("\n      <g class=\"hero-edges\">")
    seen = {}
    for edge in edges:
        source, target = edge["source"], edge["target"]
        key = (source, target)
        seen[key] = seen.get(key, 0) + 1
        rank = seen[key] - 1

        a, b = layout[source], layout[target]
        x1, y1 = a["x"] + a["w"] / 2, a["y"]
        x2, y2 = b["x"] - b["w"] / 2, b["y"]
        bow = (1 if rank % 2 == 0 else -1) * (12 + 14 * (rank // 2))
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2 - bow

        in_slice = source in sliced and target in sliced
        cls = "hero-edge in-slice" if in_slice else "hero-edge dim"
        marker = "hero-arrow" if in_slice else "hero-arrow-dim"
        out.append(
            '        <path class="%s" marker-end="url(#%s)" d="M %.1f %.1f Q %.1f %.1f %.1f %.1f"></path>'
            % (cls, marker, x1, y1, mx, my, x2, y2)
        )
    out.append("      </g>")

    # Nodes.
    out.append("\n      <g class=\"hero-nodes\">")
    for node in nodes:
        pos = layout[node["id"]]
        if node["id"] == CRITERION:
            state = "criterion"
        elif node["id"] in sliced:
            state = "in-slice"
        else:
            state = "dim"
        category = node["category"].replace("-qubit", "")
        out.append(
            '        <g class="hero-node %s cat-%s" transform="translate(%.1f,%.1f)">'
            % (state, category, pos["x"], pos["y"])
        )

        # Multi-qubit gates get the circuit-style tick through every wire.
        if len(node["qubits"]) > 1:
            lanes = [TOP_PAD + q * LANE_H - pos["y"] for q in node["qubits"]]
            out.append(
                '          <line class="hero-span" x1="0" y1="%.1f" x2="0" y2="%.1f"></line>'
                % (min(lanes), max(lanes))
            )
            # A solid dot on each control and a circled plus on an X target, as
            # graph.js draws them (qubitRoles, roleMark).
            controls = gates.control_qubits(node["name"], node["qubits"])
            x_target = gates.controlled(node["name"])[1] == "x"
            for qubit, lane in zip(node["qubits"], lanes):
                if qubit in controls:
                    out.append('          <circle class="hero-tick" cx="0" cy="%.1f" r="4.4"></circle>' % lane)
                elif controls and x_target:
                    out.append(
                        '          <g class="hero-target" transform="translate(0,%.1f)"><circle r="6">'
                        '</circle><path d="M -6 0 L 6 0 M 0 -6 L 0 6"></path></g>' % lane
                    )
                else:
                    out.append('          <circle class="hero-tick" cx="0" cy="%.1f" r="3.2"></circle>' % lane)

        out.append(
            '          <rect class="hero-shape" x="%.1f" y="%.1f" width="%d" height="%d" rx="6"></rect>'
            % (-pos["w"] / 2, -NODE_H / 2, pos["w"], NODE_H)
        )
        out.append('          <text class="hero-label" x="0" y="4">%s</text>' % node["short"])
        out.append('          <text class="hero-index" x="0" y="%d">#%d</text>'
                   % (-NODE_H / 2 - 5, node["id"]))
        out.append("        </g>")
    out.append("      </g>")
    out.append("    </svg>")

    sys.stdout.write("\n".join(out) + "\n")
    print(
        "slice %s of %d instructions; viewBox %d x %d"
        % (sorted(sliced), len(nodes), content_width, content_height),
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
