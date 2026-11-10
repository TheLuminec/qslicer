"""The ``qslicer viz`` viewer: its model (layout, culling, lint, values) and
the matplotlib view, drawn on the Agg canvas so no window opens."""

import os

import numpy as np
import pytest

matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backend_bases import KeyEvent, MouseEvent  # noqa: E402

import qslicer  # noqa: E402
from qslicer.cli import main as cli_main  # noqa: E402
from qslicer.viz import model  # noqa: E402
from qslicer.viz.visualizer import QDGVisualizer, main as viz_main  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(qslicer.__file__), "examples")
SHOR = os.path.join(EXAMPLES, "shors_algorithm.py")
TELEPORT = os.path.join(EXAMPLES, "quantum_teleportation.py")
SHOWCASE = os.path.join(EXAMPLES, "linter_showcase.py")

ASKS = """from qiskit import QuantumCircuit
n = int(input())
qc = QuantumCircuit(n)
for i in range(n):
    qc.h(i)
"""


@pytest.fixture(autouse=True)
def close_figures():
    yield
    plt.close("all")


@pytest.fixture(scope="module")
def shor():
    """Shor's main circuit, parsed and linted once for the module (the stress test)."""
    doc = model.Document(SHOR)
    graph = doc.graph(0)
    graph.set_diagnostics(doc.lint(graph))
    graph.lint_state = "done"
    return doc


def _view(target, **kwargs):
    view = QDGVisualizer(target, show=False, **kwargs)
    view.fig.canvas.draw()
    return view


# ---------------------------------------------------------------- model --

def test_layout_runs_left_to_right_on_the_wires():
    doc = model.Document(TELEPORT, lint=False)
    g = doc.graph()
    assert g.n == len(doc._first)
    assert np.all(np.diff(g.x) > 0) and np.all(g.left[1:] > g.right[:-1])
    for idx in range(g.n):
        qubits = g.qubits[idx]
        if qubits:
            assert g.y[idx] == pytest.approx(np.mean([g.lane_y(q) for q in qubits]))
    assert g.content_height > g.clbit_y(g.num_clbits - 1)


def test_culling_finds_exactly_what_overlaps_a_strip():
    g = model.Document(TELEPORT, lint=False).graph()
    for x0, x1 in [(0, 50), (g.x[3], g.x[3]), (g.left[2] - 1, g.right[5] + 1), (0, g.content_width)]:
        nodes = [i for i in range(g.n) if g.right[i] >= x0 and g.left[i] <= x1]
        assert list(g.nodes_in(x0, x1)) == nodes
        edges = [e for e in range(len(g.edge_source))
                 if g.edge_left[e] <= x1 and g.edge_right[e] >= x0]
        assert list(g.edges_in(x0, x1)) == edges


def test_classical_edges_run_along_a_bit_both_ends_share():
    g = model.Document(TELEPORT, lint=False).graph()
    classical = np.flatnonzero(g.edge_clbit >= 0)
    assert len(classical)
    for e in classical:
        s, t, c = g.edge_source[e], g.edge_target[e], g.edge_clbit[e]
        assert c in g.clbits[s] and c in g.reads[t]
        assert (s, c) in g.routed and (t, c) in g.routed


def test_node_at_hits_boxes_and_misses_gaps():
    g = model.Document(TELEPORT, lint=False).graph()
    for idx in range(g.n):
        assert g.node_at(g.x[idx], g.y[idx]) == idx
    gap = (g.right[0] + g.left[1]) / 2
    assert g.node_at(gap, g.y[0]) is None


def test_slice_is_the_slicers():
    doc = model.Document(TELEPORT, lint=False)
    g = doc.graph()
    last = g.n - 1
    assert g.slice(last) == qslicer.backward_slice(doc._first, last)
    assert g.slice(0, "forward") == qslicer.forward_slice(doc._first, 0)


def test_lint_findings_land_on_their_instructions():
    doc = model.Document(SHOWCASE)
    g = doc.graph()
    g.set_diagnostics(doc.lint(g))
    by_code = {f["code"]: f for f in g.diagnostics}
    # QD08: the two identical x gates that cancel, on lines 18 and 19.
    assert sorted(g.lines[i] for i in by_code["QD08"]["nodes"]) == [18, 19]
    assert g.lint_severity[by_code["QD08"]["nodes"][0]] >= model.SEVERITY_RANK["info"]
    # QD07: an idle wire has no instruction, so the wire is flagged instead.
    assert 3 in g.lint_by_qubit


def test_values_answer_what_only_a_run_decides(tmp_path):
    path = tmp_path / "asks.py"
    path.write_text(ASKS, encoding="utf-8")
    with pytest.raises(model.NeedsValue) as caught:
        model.Document(str(path), lint=False)
    assert caught.value.needs["name"] == "n" and "--set n=" in str(caught.value)
    doc = model.Document(str(path), lint=False, values=model.parse_values(["n=3"]))
    assert len(doc._first) == 3 and doc.used_values == {"n"}


def test_parse_values_keeps_non_literals_as_strings():
    assert model.parse_values(["a=3", "b='x'", "c=hello", "d=[1, 2]"]) == \
        {"a": 3, "b": "x", "c": "hello", "d": [1, 2]}
    with pytest.raises(ValueError):
        model.parse_values(["novalue"])


def test_format_param_reads_multiples_of_pi():
    assert model.format_param(np.pi / 4) == "π/4"
    assert model.format_param(-np.pi) == "-π"
    assert model.format_param(3 * np.pi / 8) == "3π/8"
    assert model.format_param(0.1234567) == "0.1235"


# ----------------------------------------------------------------- view --

def test_view_draws_and_slices_a_small_circuit(tmp_path):
    view = _view(model.Document(TELEPORT))
    g = view.graph
    view.set_criterion(g.n - 1)
    assert view.slice_size == len(qslicer.backward_slice(view.document._first, g.n - 1))
    view.toggle_direction()
    assert view.direction == "forward" and view.slice_size == 1   # nothing follows the last
    view.step(1)
    assert view.criterion == 0
    view.clear()
    assert view.criterion is None and view.slice_mask is None
    out = tmp_path / "teleport.png"
    view.fig.savefig(out)
    assert out.stat().st_size > 10_000


def test_view_accepts_a_bare_qdg_as_before():
    program = qslicer.parse_file(TELEPORT)
    view = _view(qslicer.QDG(program), title="teleport")
    assert view.graph.lint_state == "off" and view.graph.n == len(program)
    view.set_criterion(2)
    assert view.slice_size >= 1


def test_mouse_and_keys_drive_the_view():
    view = _view(model.Document(TELEPORT))
    g, canvas = view.graph, view.fig.canvas
    x, y = view.ax.transData.transform((g.x[1], g.y[1]))
    for name in ("button_press_event", "button_release_event"):
        canvas.callbacks.process(name, MouseEvent(name, canvas, x, y, button=1))
    assert view.criterion == 1
    canvas.callbacks.process("motion_notify_event", MouseEvent("motion_notify_event", canvas, x, y))
    assert view.hovered == 1 and view.hover.get_visible()
    assert f"#1  {g.label(1)}" in view.hover.get_text()
    for key in ("right", "f", "l", "?"):
        canvas.callbacks.process("key_press_event", KeyEvent("key_press_event", canvas, key))
    assert view.criterion == 2 and view.direction == "forward"
    assert not view.show_lint and view.help_text.get_visible()


def test_lint_markers_and_finding_steps():
    view = _view(model.Document(SHOWCASE))
    g = view.graph
    assert g.lint_state == "done" and g.diagnostics
    view.step_finding(1)
    finding = g.diagnostics[view.finding]
    assert list(np.flatnonzero(view.marked)) == finding["nodes"]
    assert finding["code"] in view.lint_text.get_text()
    view.toggle_lint()
    view.step_finding(1)     # hidden markers: nothing to step through
    assert view.finding == 0


def test_shor_draws_only_what_is_in_view(shor):
    """12,821 instructions and 24,154 edges, but a few hundred artists at most."""
    view = _view(shor)
    g = view.graph
    assert (g.n, len(g.edge_source)) == (12821, 24154)
    assert 0 < view.drawn_nodes < 300 and view.drawn_edges < 1000
    view.set_criterion(g.n // 2, reveal=True)
    assert view.slice_mask[g.n // 2]
    lo, hi = view.ax.get_xlim()
    assert lo <= g.x[g.n // 2] <= hi
    view.zoom(0.01)                        # clamped at the zoom limit
    assert view.view["k"] == pytest.approx(0.15)
    assert view.drawn_nodes < 400 and view.drawn_labels == 0
    view._jump(g.content_width)
    assert view.drawn_nodes and max(view.graph.nodes_in(*view.window[:2])) == g.n - 1


def test_shor_circuit_picker_and_lint(shor):
    view = _view(shor)
    assert len(shor.circuits) == 11
    assert any(f["code"] == "QD01" and f["nodes"] for f in view.graph.diagnostics)
    view.step_circuit(1)
    assert view.graph.info["index"] == 1 and view.graph.n == 1269
    assert "circuit 2/11" in view.title_text.get_text()
    view.step_circuit(-1)
    assert view.graph is shor.graph(0)          # kept, not rebuilt


def test_blitting_redraws_over_the_saved_background(shor, tmp_path):
    view = QDGVisualizer(shor, show=False, blit=True)
    canvas = view.fig.canvas
    canvas.draw()
    assert view._backgrounds and view.ax.get_animated()
    view.set_criterion(100)
    view.pan(500)
    view._set_hover(100)
    out = tmp_path / "shor.png"
    view.fig.savefig(out)                      # draws the animated parts too
    assert out.stat().st_size > 50_000 and view.ax.get_animated()


# ------------------------------------------------------------------ CLI --

def test_cli_saves_an_image(tmp_path, capsys):
    out = tmp_path / "view.png"
    assert cli_main(["viz", SHOWCASE, "--line", "19", "--save", str(out)]) == 0
    assert out.stat().st_size > 10_000
    printed = capsys.readouterr().out
    assert "QD08" in printed and f"wrote {out}" in printed


def test_cli_explains_a_missing_value(tmp_path):
    path = tmp_path / "asks.py"
    path.write_text(ASKS, encoding="utf-8")
    with pytest.raises(SystemExit, match="--set n=VALUE"):
        viz_main([str(path), "--save", str(tmp_path / "x.png")])
    assert viz_main([str(path), "--set", "n=2", "--no-lint",
                     "--save", str(tmp_path / "x.png")]) == 0


def test_cli_rejects_a_line_with_no_instruction_and_a_bad_circuit(tmp_path):
    with pytest.raises(SystemExit, match="no instruction comes from line 1"):
        viz_main([TELEPORT, "--line", "1", "--save", str(tmp_path / "x.png")])
    with pytest.raises(SystemExit, match="--circuit 5"):
        viz_main([TELEPORT, "--circuit", "5", "--save", str(tmp_path / "x.png")])
