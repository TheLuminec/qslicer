"""The qubit-state pass: known basis states, correlation groups, kickback."""

import pytest
from qiskit import QuantumCircuit

from qslicer.insights.state import track_states
from qslicer.parsers.qiskitparser import QiskitParser


def track(qc):
    return track_states(QiskitParser(qc).program)


def test_bell_pair_measurements_may_be_correlated():
    qc = QuantumCircuit(2, 2)
    qc.h(0)
    qc.cx(0, 1)          # the target is |0>, and still joins the group
    qc.measure(0, 0)     # 2
    qc.measure(1, 1)     # 3
    states = track(qc)
    assert states.correlation(2) == ([3], [1], [], None)
    assert states.correlation(3) == ([2], [0], [], None)
    assert not states.predetermined and not states.kickback


def test_a_control_in_a_known_basis_state_does_not_correlate():
    qc = QuantumCircuit(3, 3)
    qc.x(0)
    qc.cx(0, 1)          # 1  control is |1>: just an x on q1
    qc.cx(2, 1)          # 2  control is |0>: never acts
    qc.measure(0, 0)     # 3
    qc.measure(1, 1)     # 4
    states = track(qc)
    assert states.predetermined == {3: 1, 4: 1}
    assert 2 in states.no_effect and 1 not in states.no_effect
    assert states.correlation(4) == ([], [], [], 1)


def test_a_controlled_gate_can_put_its_target_in_superposition():
    qc = QuantumCircuit(2, 1)
    qc.x(0)
    qc.ch(0, 1)          # control |1>: an h on q1
    qc.measure(1, 0)     # 2
    states = track(qc)
    assert states.predetermined == {}
    assert states.correlation(2) == ([], [], [], None)


def test_mirrored_gates_around_an_entangling_gate_do_not_restore_a_basis_state():
    qc = QuantumCircuit(2, 1)
    qc.h(0)
    qc.h(1)
    qc.cz(0, 1)
    qc.h(0)
    qc.measure(0, 0)     # 4
    states = track(qc)
    assert 4 not in states.predetermined
    assert states.correlation(4).unmeasured == [1]


def test_h_twice_is_a_known_state_again():
    qc = QuantumCircuit(1, 1)
    qc.h(0)
    qc.h(0)
    qc.measure(0, 0)
    assert track(qc).predetermined == {2: 0}


def test_resetting_a_partner_removes_the_correlation_not_the_randomness():
    qc = QuantumCircuit(2, 2)
    qc.h(0)
    qc.cx(0, 1)
    qc.reset(1)          # 2
    qc.measure(0, 0)     # 3  still random, tied to nothing
    qc.measure(1, 1)     # 4  always 0
    states = track(qc)
    assert states.correlation(3) == ([], [], [], None)
    assert states.predetermined == {4: 0}


def test_a_measurement_before_the_reset_stays_correlated():
    qc = QuantumCircuit(2, 2)
    qc.h(0)
    qc.cx(0, 1)
    qc.measure(1, 1)     # 2
    qc.reset(1)
    qc.measure(0, 0)     # 4
    states = track(qc)
    assert states.correlation(4) == ([2], [1], [], None)


def test_swap_moves_state_and_group():
    qc = QuantumCircuit(3, 3)
    qc.h(0)
    qc.cx(0, 1)
    qc.swap(1, 2)        # q2 now holds the entangled half, q1 is |0>
    qc.measure(0, 0)     # 3
    qc.measure(1, 1)     # 4
    qc.measure(2, 2)     # 5
    states = track(qc)
    assert states.predetermined == {4: 0}
    assert states.correlation(3) == ([5], [2], [], None)


@pytest.mark.parametrize("prepare", [("x", "h"), ("h", "z")], ids=["x-h", "h-z"])
def test_kickback_is_found_however_the_minus_state_was_made(prepare):
    qc = QuantumCircuit(2, 1)
    for gate in prepare:
        getattr(qc, gate)(1)     # q1 = |->
    qc.h(0)
    qc.cx(0, 1)          # 3  kickback onto q0
    qc.cx(0, 1)          # 4  and again: the target is still |->
    qc.h(0)
    qc.measure(0, 0)     # 6
    states = track(qc)
    assert states.kickback == {3: [0], 4: [0]}
    # Two kickbacks undo each other, and q0 never joined q1's group.
    assert states.predetermined == {6: 0}


def test_kickback_from_a_toffoli_groups_only_the_controls():
    qc = QuantumCircuit(3, 3)
    qc.x(2)
    qc.h(2)
    qc.h(0)
    qc.h(1)
    qc.ccx(0, 1, 2)      # 4
    qc.measure(range(3), range(3))   # 5, 6, 7
    states = track(qc)
    assert states.kickback == {4: [0, 1]}
    assert states.correlation(5) == ([6], [1], [], None)
    assert states.correlation(7) == ([], [], [], None)


def test_a_target_in_plus_is_left_alone():
    qc = QuantumCircuit(2)
    qc.h(0)
    qc.h(1)
    qc.cx(0, 1)
    states = track(qc)
    assert 2 in states.no_effect and not states.kickback
    assert states.final_states == {0: "+X", 1: "+X"}


def test_diagonal_gate_ignores_operands_in_a_known_basis_state():
    qc = QuantumCircuit(2, 2)
    qc.x(0)
    qc.h(1)
    qc.cz(0, 1)          # q0 is |1>: a z on q1, which becomes |->
    states = track(qc)
    assert states.final_states == {0: "-Z", 1: "-X"}


def test_classical_feed_forward_correlates_without_a_two_qubit_gate():
    qc = QuantumCircuit(2, 2)
    qc.h(0)
    qc.measure(0, 0)                     # 1
    with qc.if_test((qc.clbits[0], 1)):  # 2
        qc.x(1)                          # 3
    qc.measure(1, 1)                     # 4
    states = track(qc)
    assert states.predetermined == {}
    assert states.correlation(4) == ([1], [0], [], None)


def test_a_conditional_reset_does_not_restore_a_known_state():
    qc = QuantumCircuit(2, 2)
    qc.h(0)
    qc.h(1)
    qc.measure(0, 0)                     # 2
    with qc.if_test((qc.clbits[0], 1)):  # 3
        qc.reset(1)                      # 4
    qc.measure(1, 1)                     # 5
    assert 5 not in track(qc).predetermined


def test_barriers_change_nothing():
    qc = QuantumCircuit(2, 2)
    qc.x(0)
    qc.barrier()
    qc.measure(0, 0)
    assert track(qc).predetermined == {2: 1}


_KETS = {"+Z": [1, 0], "-Z": [0, 1], "+X": [1, 1], "-X": [1, -1], "+Y": [1, 1j], "-Y": [1, -1j]}
_GATES_1 = ["h", "x", "y", "z", "s", "sdg", "t", "tdg", "sx"]
_GATES_ANGLE_1 = ["rx", "ry", "rz", "p"]
_GATES_2 = ["cx", "cy", "cz", "ch", "swap", "cs", "csx"]
_GATES_ANGLE_2 = ["cp", "crx", "cry", "crz", "rzz"]
_GATES_3 = ["ccx", "ccz", "cswap"]


def _random_circuit(rng, n, length):
    qc = QuantumCircuit(n)
    for _ in range(length):
        kind = rng.random()
        if kind < 0.45:
            getattr(qc, rng.choice(_GATES_1))(rng.randrange(n))
        elif kind < 0.55:
            getattr(qc, rng.choice(_GATES_ANGLE_1))(rng.uniform(0.1, 3.0), rng.randrange(n))
        elif kind < 0.8:
            getattr(qc, rng.choice(_GATES_2))(*rng.sample(range(n), 2))
        elif kind < 0.9:
            getattr(qc, rng.choice(_GATES_ANGLE_2))(rng.uniform(0.1, 3.0),
                                                    *rng.sample(range(n), 2))
        else:
            getattr(qc, rng.choice(_GATES_3))(*rng.sample(range(n), 3))
    return qc


@pytest.mark.parametrize("seed", range(40))
def test_claims_hold_against_a_statevector_simulation(seed):
    """Whatever the pass claims must be true of the real state: a known state is
    that state, and qubits in different groups are uncorrelated."""
    import random

    import numpy as np
    from qiskit.quantum_info import DensityMatrix, Statevector, partial_trace

    rng = random.Random(seed)
    n = 4
    for _ in range(15):
        qc = _random_circuit(rng, n, rng.randrange(1, 14))
        states = track(qc)
        full = DensityMatrix(Statevector(qc))

        def reduced(qubits):
            return partial_trace(full, [q for q in range(n) if q not in qubits]).data

        for qubit, known in states.final_states.items():
            if known is not None:
                ket = np.array(_KETS[known], dtype=complex) / np.sqrt(2 if known[1] != "Z" else 1)
                assert np.allclose(reduced([qubit]), np.outer(ket, ket.conj()), atol=1e-8), (
                    qc.draw(output="text"), qubit, known)
        group_of = {q: i for i, group in enumerate(states.final_groups) for q in group}
        for a in range(n):
            for b in range(a + 1, n):
                if group_of[a] != group_of[b]:
                    # Little-endian: qubit a is the low factor of the pair's state.
                    product = np.kron(reduced([b]), reduced([a]))
                    assert np.allclose(reduced([a, b]), product, atol=1e-8), (
                        qc.draw(output="text"), a, b)
