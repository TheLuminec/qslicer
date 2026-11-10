"""The qslicer package's public API and the ``qslicer`` command."""

import json

import pytest

import qslicer
from qslicer.cli import main

BELL = """from qiskit import QuantumCircuit

qc = QuantumCircuit(2, 2)
qc.h(0)
qc.cx(0, 1)
qc.measure(0, 0)
qc.measure(1, 1)
"""

GHZ_QASM = """OPENQASM 3.0;
include "stdgates.inc";
qubit[3] q;
bit[3] c;
h q[0];
cx q[0], q[1];
cx q[0], q[2];
c = measure q;
"""


@pytest.fixture
def bell_file(tmp_path):
    path = tmp_path / "bell.py"
    path.write_text(BELL, encoding="utf-8")
    return str(path)


def test_parse_follows_the_file_extension(tmp_path):
    qasm = tmp_path / "ghz.qasm"
    qasm.write_text(GHZ_QASM, encoding="utf-8")
    assert [i.name for i in qslicer.parse_file(str(qasm))][:3] == ["h", "cx", "cx"]
    assert [i.name for i in qslicer.parse(BELL, "bell.py")] == ["h", "cx", "measure", "measure"]


def test_parse_rejects_an_unknown_language():
    with pytest.raises(ValueError):
        qslicer.parse(BELL, language="cirq")


def test_slices_from_a_program_or_a_graph():
    program = qslicer.parse(BELL, "bell.py")
    assert qslicer.backward_slice(program, 3) == {0, 1, 3}
    graph = qslicer.QDG(program)
    assert qslicer.forward_slice(graph, 0) == {0, 1, 2, 3}
    only_qubits = qslicer.backward_slice(program, 3, [qslicer.DependencyType.DATA_QUBIT])
    assert only_qubits <= {0, 1, 3}


def test_lint_source_selects_rules():
    source = BELL.replace("qc.h(0)", "qc.h(5)")
    findings = qslicer.lint_source(source, "bell.py", select=["quick/QS002"])
    assert [(f["code"], f["line"], f["rule"]) for f in findings] == [("QS002", 4, "quick/QS002")]


def test_example_path_finds_bundled_examples():
    assert qslicer.example_path("bell_pair.py").endswith("bell_pair.py")
    with pytest.raises(FileNotFoundError):
        qslicer.example_path("no_such_example.py")


def test_cli_lint_exit_status_and_json(bell_file, capsys):
    assert main(["lint", bell_file]) == 0
    with open(bell_file, "a", encoding="utf-8") as handle:
        handle.write("qc.h(5)\n")
    assert main(["lint", bell_file, "--format", "json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert "QS002" in {f["code"] for f in report[bell_file]}
    assert main(["lint", bell_file, "--fail-on", "never"]) == 0


def test_cli_slice_by_index_and_line(bell_file, capsys):
    assert main(["slice", bell_file, "3", "--format", "json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert [row["index"] for row in body["slice"]] == [0, 1, 3]
    assert main(["slice", bell_file, "--forward", "--line", "4", "--format", "json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["criterion"] == 0 and [r["index"] for r in body["slice"]] == [0, 1, 2, 3]


def test_cli_reports_bad_input(bell_file, tmp_path):
    with pytest.raises(SystemExit, match="outside"):
        main(["slice", bell_file, "9"])
    with pytest.raises(SystemExit, match="no instruction comes from line 2"):
        main(["slice", bell_file, "--line", "2"])
    with pytest.raises(SystemExit, match="unknown dependency type"):
        main(["slice", bell_file, "0", "--deps", "NOPE"])
    with pytest.raises(SystemExit):
        main(["list", str(tmp_path / "missing.py")])


def test_cli_list_and_rules(bell_file, capsys):
    assert main(["list", bell_file]) == 0
    assert "4 instructions" in capsys.readouterr().out
    assert main(["rules", "--format", "json"]) == 0
    ids = {linter["id"] for linter in json.loads(capsys.readouterr().out)}
    assert {"quick", "dependence", "lintq"} <= ids


def test_linters_load_once_across_threads():
    """Every thread sees every add-on, even when they all ask at once in a fresh process."""
    import os
    import subprocess
    import sys
    script = (
        "from concurrent.futures import ThreadPoolExecutor\n"
        "from qslicer.lint import registry\n"
        "with ThreadPoolExecutor(8) as pool:\n"
        "    seen = list(pool.map(lambda _: len(registry.default_selection()), range(8)))\n"
        "assert len(set(seen)) == 1, seen\n"
    )
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    result = subprocess.run([sys.executable, "-c", script], cwd=root, capture_output=True,
                            text=True, timeout=120)
    assert result.returncode == 0, result.stderr


def test_cli_slice_on_a_qubit_and_chop(bell_file, capsys):
    # BELL: 0 h q0, 1 cx q0,q1, 2 measure q0, 3 measure q1.
    assert main(["slice", bell_file, "--qubit", "1", "--format", "json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["criterion"] == 3 and [r["index"] for r in body["slice"]] == [0, 1, 3]

    assert main(["slice", bell_file, "0", "--chop", "3", "--format", "json"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert (body["direction"], body["criterion"], body["target"]) == ("chop", 0, 3)
    assert [r["index"] for r in body["slice"]] == [0, 1, 3]

    assert main(["slice", bell_file, "2", "--chop-qubit", "1"]) == 0
    assert "cannot affect" in capsys.readouterr().out


def test_cli_slice_on_an_unmeasured_qubit_says_so(tmp_path):
    path = tmp_path / "idle.py"
    path.write_text("from qiskit import QuantumCircuit\nqc = QuantumCircuit(2, 1)\n"
                    "qc.h(0)\nqc.measure(0, 0)\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exit_info:
        main(["slice", str(path), "--qubit", "1"])
    assert "never measured" in str(exit_info.value)


def test_chop_from_a_program():
    program = qslicer.parse(BELL, "bell.py")
    assert qslicer.chop(program, 0, 3) == {0, 1, 3}
    assert qslicer.chop(program, 2, 3) == set()
