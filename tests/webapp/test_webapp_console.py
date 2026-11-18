"""The Console tab prints what the ``qslicer`` command would for the same input."""

import os

import pytest

import analysis
from qslicer import EXAMPLES_DIR, cli


def _run_cli(capsys, argv):
    cli.main(argv)
    captured = capsys.readouterr()
    # `lint` prints its count to stderr; the console shows it after the findings.
    return (captured.out + captured.err).rstrip("\n").split("\n")


def _sections(console):
    """{command: output lines} for each ``$ qslicer ...`` block."""
    sections, current = {}, None
    for line in console.split("\n"):
        if line.startswith("$ qslicer "):
            current = line[len("$ qslicer "):]
            sections[current] = []
        elif current is not None and line:
            sections[current].append(line)
    return sections


def _analyze(name, kind):
    with open(os.path.join(EXAMPLES_DIR, name), encoding="utf-8") as handle:
        source = handle.read()
    result = analysis.analyze_source(source, filename=name, language=kind)
    assert result["ok"], result.get("error")
    return result


@pytest.mark.parametrize("name, kind", [("bell_pair.py", "qiskit-static"),
                                        ("linter_showcase.py", "qiskit-static"),
                                        ("ghz_openqasm.qasm", "qasm-static")])
def test_analysis_console_matches_the_cli(capsys, monkeypatch, name, kind):
    result = _analyze(name, kind)
    monkeypatch.chdir(EXAMPLES_DIR)
    sections = _sections(result["console"])
    assert f"list {name}" in sections
    for command, lines in sections.items():
        assert lines == _run_cli(capsys, command.split()), command


@pytest.mark.parametrize("direction, extra", [("backward", {}), ("forward", {}),
                                              ("chop", {"target_idx": None})])
def test_slice_console_matches_the_cli(capsys, monkeypatch, direction, extra):
    name = "bell_pair.py"
    result = _analyze(name, "qiskit-static")
    last = len(result["nodes"]) - 1
    if "target_idx" in extra:
        extra = {"target_idx": last}
    sliced = analysis.slice_program(result["token"], 0, direction=direction, **extra)
    assert sliced["ok"]
    monkeypatch.chdir(EXAMPLES_DIR)
    sections = _sections(sliced["console"])
    assert len(sections) == 1
    for command, lines in sections.items():
        assert command.startswith(f"slice {name} 0")
        assert lines == _run_cli(capsys, command.split()), command


def test_several_criteria_print_one_command_each(capsys, monkeypatch):
    name = "bell_pair.py"
    result = _analyze(name, "qiskit-static")
    measures = [n["id"] for n in result["nodes"] if n["is_measurement"]]
    sliced = analysis.slice_program(result["token"], node_indices=measures,
                                    dep_types=["DATA_QUBIT"])
    monkeypatch.chdir(EXAMPLES_DIR)
    sections = _sections(sliced["console"])
    assert len(sections) == len(measures)
    for command, lines in sections.items():
        assert "--deps DATA_QUBIT" in command
        assert lines == _run_cli(capsys, command.split()), command


def test_a_parse_error_is_reported_in_the_cli_words(capsys, monkeypatch, tmp_path):
    source = "from qiskit import QuantumCircuit\nn = int(input())\nqc = QuantumCircuit(n)\n"
    result = analysis.analyze_source(source, filename="broken.py", language="qiskit-static")
    assert not result["ok"]
    (tmp_path / "broken.py").write_text(source, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    listed = _sections(result["console"])["list broken.py"]
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["list", "broken.py"])
    assert listed == [str(exit_info.value)]
