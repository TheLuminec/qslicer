import pytest
from qiskit import QuantumCircuit

# Analyzer functions

def build(circuit):
    raise NotImplementedError("build not implemented")


def slice(qdg, target):
    raise NotImplementedError("slice not implemented")


def out(qdg, target):
    raise NotImplementedError("out not implemented")

# Basic circuits

@pytest.fixture
def single_qubit_circuit():
    qc = QuantumCircuit(1, 1)
    qc.h(0)
    qc.measure(0, 0)
    return qc


@pytest.fixture
def two_qubit_entangled():
    qc = QuantumCircuit(2, 1)
    qc.h(0)
    qc.cx(0, 1)
    qc.measure(1, 0)
    return qc


@pytest.fixture
def independent_qubits():
    qc = QuantumCircuit(2, 2)
    qc.x(0)
    qc.z(1)
    qc.measure(0, 0)
    return qc