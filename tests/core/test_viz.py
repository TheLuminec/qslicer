"""The ``qslicer viz`` viewer: its model (layout, culling, lint, values) and
the matplotlib view, drawn on the Agg canvas so no window opens."""

import os

import numpy as np
import pytest

matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backend_bases import KeyEvent, MouseEvent  # noqa: E402
from matplotlib.colors import to_rgba  # noqa: E402

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


def test_shor_with_every_mirror_arc_still_draws_only_what_is_in_view(shor):
    """The mirror toggle on: arcs only for pairs with an end in the drawn window."""
    view = _view(shor)
    g = view.graph
    assert len(g.mirror_first) > 1000                 # thousands of pairs in the circuit
    view.toggle_mirrors()
    window = g.nodes_in(*view.window[:2])
    assert view.drawn_arcs == len(g.mirrors_in(window)) and 0 < view.drawn_arcs < 300
    assert 0 < view.drawn_nodes < 300 and view.drawn_edges < 1000 and view.drawn_marks < 300
    view.zoom(0.01)
    assert view.drawn_nodes < 400 and view.drawn_arcs < 600 and view.drawn_marks == 0
    for idx in (g.n - 1, g.n // 2, 100):
        view.toggle_criterion(idx)
    assert view.drawn_stripes < 2000
    assert len(view.ax.get_children()) < 40           # the collections are reused, not added


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


# ------------------------------------------------- slices and insights --

KICKBACK = os.path.join(EXAMPLES, "phase_kickback.py")
UNMEASURED = """from qiskit import QuantumCircuit
qc = QuantumCircuit(3, 1)
qc.h(0)
qc.cx(0, 1)
qc.t(2)
qc.cz(0, 2)
qc.measure(0, 0)
"""


def _click(view, x, y, key=None):
    canvas = view.fig.canvas
    x, y = view.ax.transData.transform((x, y))
    for name in ("button_press_event", "button_release_event"):
        canvas.callbacks.process(name, MouseEvent(name, canvas, x, y, button=1, key=key))


def test_slice_on_a_measured_qubit_by_its_wire_name():
    view = _view(model.Document(TELEPORT, lint=False))
    g = view.graph
    hit = next(h for h in view._lane_hits if h[4] == 2)
    _click(view, (hit[0] + hit[1]) / 2, (hit[2] + hit[3]) / 2)
    assert view.criterion == 12 and g.names[12] == "measure"      # q2's last measurement
    assert view.slice_size == len(qslicer.backward_slice(view.document._first, 12))
    # It may be correlated with q0's and q1's measurements: said, and the wires tinted.
    assert "May be correlated with the measurements of q0, q1." in view.status_text.get_text()
    assert view._correlated_wires() == ({0, 1}, set())
    labels = view.lane_text.get_facecolor()
    assert np.allclose(labels[0], to_rgba(model.CORRELATED))
    assert np.allclose(labels[1], to_rgba(model.CORRELATED))
    assert not np.allclose(labels[2], to_rgba(model.CORRELATED))


def test_slice_on_an_unmeasured_qubit_says_so_and_changes_nothing(tmp_path):
    path = tmp_path / "unmeasured.py"
    path.write_text(UNMEASURED, encoding="utf-8")
    view = _view(model.Document(str(path), lint=False))
    view.set_criterion(1)
    view.slice_qubit(2)
    assert view.status_text.get_text() == \
        "q2 is never measured, so there is no measurement to slice from."
    assert view.criterion == 1 and not view.criteria
    view.slice_qubit(0)
    assert view.criterion == view.graph.n - 1
    # The measurement of q0 may still be entangled with q1 and q2, never measured.
    assert view._correlated_wires()[1] and "unmeasured" in view.status_text.get_text()


def test_several_slices_with_a_colour_and_a_number_each():
    view = _view(model.Document(TELEPORT, lint=False))
    g = view.graph
    _click(view, g.x[12], g.y[12])                      # a plain click: one slice
    _click(view, g.x[7], g.y[7], key="control")         # ctrl: one more
    _click(view, g.x[6], g.y[6], key="shift")           # shift works too
    assert view.criteria == {12: 1, 7: 2, 6: 3} and view.criterion is None
    slices = {c: qslicer.backward_slice(view.document._first, c) for c in (12, 7, 6)}
    for row, c in enumerate((12, 7, 6)):
        assert set(np.flatnonzero(view.slice_stack[row])) == slices[c]
    # A node in exactly one slice has that slice's colour; one in several, stripes.
    only_12 = (slices[12] - slices[7] - slices[6]).pop()
    rgba = view.boxes.get_facecolor()[only_12 - view.graph.nodes_in(*view.window[:2]).start]
    assert np.allclose(rgba, to_rgba(model.SLICE_PALETTE[0]))
    shared = sum(1 for i in range(g.n) if sum(i in s for s in slices.values()) >= 2)
    stripes = sum(min(model.MAX_STRIPES, sum(i in s for s in slices.values()))
                  for i in range(g.n) if sum(i in s for s in slices.values()) >= 2)
    assert view.drawn_stripes == stripes and shared
    assert view.legend_text.get_paths() and len(view.legend_boxes.get_paths()) == 4
    assert "in slices" in view._card(4)
    # Taking one out keeps the others' numbers (and colours); the next gets the free one.
    _click(view, g.x[7], g.y[7], key="control")
    assert view.criteria == {12: 1, 6: 3}
    assert set(np.flatnonzero(view.slice_stack[1])) == slices[6]
    view.toggle_criterion(9)
    assert view.criteria[9] == 2
    # A plain click goes back to one slice.
    _click(view, g.x[3], g.y[3])
    assert view.criteria == {} and view.criterion == 3 and view.slice_stack is None


def test_at_most_sixteen_slices():
    view = _view(model.Document(SHOR, lint=False))
    for idx in range(17):
        view.toggle_criterion(idx)
    assert len(view.criteria) == 16 and "At most 16" in view.status_text.get_text()
    assert sorted(view.criteria.values()) == list(range(1, 17))


def test_chop_from_an_operation_to_a_measurement():
    view = _view(model.Document(TELEPORT, lint=False))
    g = view.graph
    canvas = view.fig.canvas
    canvas.callbacks.process("key_press_event", KeyEvent("key_press_event", canvas, "c"))
    assert view.direction == "chop" and view.btn_dir.label.get_text() == "Chop"
    _click(view, g.x[0], g.y[0])
    assert view.chop_from == 0 and view.chop_to is None and view.slice_mask is None
    hit = next(h for h in view._lane_hits if h[4] == 2)
    _click(view, (hit[0] + hit[1]) / 2, (hit[2] + hit[3]) / 2)    # the wire name: q2's measurement
    assert view.chop_to == 12
    chop = qslicer.QuantumSlicer.chop(g.qdg, 0, 12)
    assert chop and set(np.flatnonzero(view.slice_mask)) == chop
    assert "on a path between them" in view.status_text.get_text()
    assert "chop: from" in view._card(0) and "chop: to" in view._card(12)
    texts = sorted(len(p.vertices) for p in view.pill_text.get_paths())
    assert len(texts) >= 2                                         # "from" and "to" tags


def test_an_empty_chop_says_so_and_keeps_both_ends():
    view = _view(model.Document(TELEPORT, lint=False))
    g = view.graph
    view.set_direction("chop")
    view.set_criterion(12)      # a measurement at the end cannot affect the first gate
    view.set_criterion(0)
    assert (view.chop_from, view.chop_to) == (12, 0)
    assert view.slice_size == 0 and not view.slice_mask.any()
    assert view.status_text.get_text().endswith("This operation cannot affect that measurement.")
    faces = view.boxes.get_facecolor()
    start = g.nodes_in(*view.window[:2]).start
    assert faces[12 - start][3] == 1 and faces[0 - start][3] == 1     # the ends are not dimmed
    assert faces[5 - start][3] < 1                                     # the rest is


def test_node_marks_and_what_they_say():
    view = _view(model.Document(KICKBACK, lint=False))
    g = view.graph
    marks = {idx: [kind for _, kind in g.marks(idx)] for idx in range(g.n)}
    assert marks[3] == ["kickback"] and marks[5] == ["always"]
    assert view.drawn_marks == sum(len(m) for m in marks.values())
    card = view._card(3).replace("\n", " ")
    assert ("Phase kickback: the target is left as it is and the phase lands on control q0."
            in card)
    assert "This measurement always gives 1: the qubit is provably |1> here." in \
        view._card(5).replace("\n", " ")
    view.zoom(0.01)                       # small decorations go at low zoom
    assert view.drawn_marks == 0


def test_phase_only_and_no_effect_notes():
    g = model.Document(SHOWCASE, lint=False).graph()
    cz = g.names.index("cz")
    notes = " ".join(g.notes(cz))
    assert notes.startswith("Phase only: this gate cannot change a measurement outcome until a "
                            "non-diagonal gate follows on this wire. It can still entangle.")
    assert "No effect here: an operand is |0>, so no phase is applied." in notes
    assert ("phase" in [kind for _, kind in g.marks(cz)])
    t = model.Document(TELEPORT, lint=False).graph()
    rz = t.names.index("rz")
    assert t.notes(rz)[0].endswith("follows on this wire.")            # one qubit: no entangling


def test_mirror_arcs_for_the_hovered_node_the_criterion_and_the_toggle():
    view = _view(model.Document(SHOWCASE, lint=False))
    g = view.graph
    assert [g.mirror_first.tolist(), g.mirror_second.tolist()] == [[1], [2]]
    assert view.drawn_arcs == 0                      # none by default
    assert "mirrors #2 (x q1)" in view._card(1) and "cancel" not in view._card(1)
    view._set_hover(2)
    assert view.drawn_arcs == 1
    view._set_hover(0)
    assert view.drawn_arcs == 0
    view.set_criterion(1)
    assert view.drawn_arcs == 1
    view.clear()
    canvas = view.fig.canvas
    canvas.callbacks.process("key_press_event", KeyEvent("key_press_event", canvas, "m"))
    assert view.show_mirrors and view.drawn_arcs == 1
    arc = view.arcs.get_segments()[0]
    assert arc[:, 1].min() < g.y[1] - model.NODE_H / 2                # above the boxes


def test_mirror_arcs_turn_away_from_the_middle_of_the_graph(tmp_path):
    """Up on the top half of the qubits and on the middle wire, down on the bottom half."""
    path = tmp_path / "mirrors.py"
    path.write_text("from qiskit import QuantumCircuit\nqc = QuantumCircuit(3)\n"
                    "for q in range(3):\n    qc.h(q)\n    qc.t(q)\n    qc.h(q)\n",
                    encoding="utf-8")
    g = model.Document(str(path), lint=False).graph()
    assert len(g.insights["mirrors"]) == 3           # also builds the mirror arrays
    assert g.mirror_first.tolist() == [0, 3, 6]      # one h ... h pair per wire, q0 to q2
    top, middle, bottom = g.mirror_arcs([0, 1, 2])
    half = model.NODE_H / 2
    assert top[:, 1].min() < g.y[0] - half and top[:, 1].max() <= g.y[0] - half
    assert middle[:, 1].min() < g.y[3] - half and middle[:, 1].max() <= g.y[3] - half
    assert bottom[:, 1].max() > g.y[6] + half and bottom[:, 1].min() >= g.y[6] + half


def test_control_dependence_of_an_if_test_has_a_chip_and_can_be_ignored():
    view = _view(model.Document(TELEPORT, lint=False))
    g = view.graph
    control = model.DependencyType.CONTROL_CLASSICAL
    assert g.names[8] == "if_else" and not g.qubits[8]
    assert g.dependency_counts()[control] == 2
    chip = view.dep_buttons[model.DEPENDENCIES.index(control)]
    assert chip.ax.get_visible() and "CONTROL_CLASSICAL" in chip.label.get_text()
    assert chip.ax.get_position().x1 <= 1.0                            # on screen
    view.set_criterion(9)                     # the x inside the first if_test
    assert view.slice_mask[8]
    view.toggle_dependency(control)
    assert not view.slice_mask[8]


def test_insights_failing_means_nothing_found(monkeypatch):
    import qslicer.insights.summary as summary

    def boom(_program):
        raise RuntimeError("no")
    monkeypatch.setattr(summary, "summarize", boom)
    g = model.Document(KICKBACK, lint=False).graph()
    assert g.insights["kickback"] == {} and g.marks(3) == [] and len(g.mirror_first) == 0
    view = _view(g.qdg)
    assert view.drawn_nodes == g.n


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
