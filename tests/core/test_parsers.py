"""Parser behaviour: Qiskit circuits and OpenQASM 3 text land in the same IR."""

import pytest
from qiskit import QuantumCircuit, qasm3

from qslicer.parsers.qiskitparser import QiskitParser
from qslicer.parsers.openqasmparser import OpenQASMParser
from qslicer.ir.qprogram import QuantumProgram

QASM_BELL = """OPENQASM 3.0;
include "stdgates.inc";
bit[2] c;
qubit[2] q;
h q[0];
cx q[0], q[1];
rz(0.5) q[1];
c[0] = measure q[0];
c[1] = measure q[1];
"""


def bell_circuit():
    qc = QuantumCircuit(2, 2)
    qc.h(0)
    qc.cx(0, 1)
    qc.rz(0.5, 1)
    qc.measure(0, 0)
    qc.measure(1, 1)
    return qc


def shape(program):
    """The frontend-agnostic part of the IR: name, qubits, clbits per instruction."""
    return [(i.name, list(i.qubits), list(i.clbits)) for i in program.instructions]


EXPECTED = [
    ("h", [0], []),
    ("cx", [0, 1], []),
    ("rz", [1], []),
    ("measure", [0], [0]),
    ("measure", [1], [1]),
]


@pytest.mark.requirement("FR-05")
def test_qiskit_circuit_parses_into_ir():
    program = QiskitParser(bell_circuit()).program
    assert isinstance(program, QuantumProgram)
    assert program.num_qubits == 2 and program.num_clbits == 2
    assert shape(program) == EXPECTED


@pytest.mark.requirement("FR-07")
def test_ir_records_parameters():
    program = QiskitParser(bell_circuit()).program
    assert [float(p) for p in program[2].params] == [0.5]
    assert program[0].params == []


@pytest.mark.requirement("FR-02")
@pytest.mark.requirement("FR-06")
def test_openqasm_text_matches_equivalent_qiskit_circuit():
    from_qasm = OpenQASMParser(qasm3.loads(QASM_BELL)).parse()
    from_qiskit = QiskitParser(bell_circuit()).program
    assert shape(from_qasm) == shape(from_qiskit) == EXPECTED
    assert (from_qasm.num_qubits, from_qasm.num_clbits) == (2, 2)


@pytest.mark.requirement("FR-06")
def test_openqasm_parse_file(tmp_path):
    path = tmp_path / "bell.qasm"
    path.write_text(QASM_BELL)
    program = OpenQASMParser().parse_file(str(path))
    assert shape(program) == EXPECTED


@pytest.mark.requirement("FR-06")
def test_openqasm_parse_does_not_duplicate_instructions():
    parser = OpenQASMParser(qasm3.loads(QASM_BELL))
    parser.parse()
    assert len(parser.parse()) == len(EXPECTED)


@pytest.mark.requirement("FR-02")
def test_if_test_condition_parses_from_both_frontends():
    qc = QuantumCircuit(2, 1)
    qc.measure(0, 0)
    with qc.if_test((qc.clbits[0], 1)):
        qc.x(1)
    text = qasm3.dumps(qc)
    from_qiskit = QiskitParser(qc).program
    from_qasm = OpenQASMParser(qasm3.loads(text)).parse()
    assert [i.name for i in from_qiskit.instructions] == ["measure", "if_else"]
    assert shape(from_qiskit) == shape(from_qasm)
    assert list(from_qiskit[1].metadata["condition_clbits"]) == [0]


@pytest.mark.requirement("FR-07")
def test_single_clbit_condition_reads_only_that_bit():
    qc = QuantumCircuit(2, 2)
    qc.measure(0, 0)
    with qc.if_test((qc.clbits[0], 1)):
        qc.x(1)
    program = QiskitParser(qc).program
    assert list(program[1].metadata["condition_clbits"]) == [0]


@pytest.mark.requirement("FR-05")
def test_register_condition_parses():
    qc = QuantumCircuit(2, 2)
    qc.measure(0, 0)
    with qc.if_test((qc.cregs[0], 1)):
        qc.x(1)
    program = QiskitParser(qc).program
    assert sorted(program[1].metadata["condition_clbits"]) == [0, 1]


@pytest.mark.requirement("NFR-08")
@pytest.mark.requirement("VV-08")
def test_parse_is_deterministic():
    runs = [shape(QiskitParser(bell_circuit()).program) for _ in range(3)]
    runs += [shape(OpenQASMParser(qasm3.loads(QASM_BELL)).parse()) for _ in range(2)]
    assert all(run == runs[0] for run in runs)
