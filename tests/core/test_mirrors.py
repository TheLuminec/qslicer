"""Mirrored-gate detection (compute/uncompute shapes) over hand-built programs."""

import time

from qslicer.insights.mirrors import MirrorPair, find_mirror_pairs
from qslicer.ir.qinstruction import QuantumInstruction
from qslicer.ir.qprogram import QuantumProgram


def program(*instructions, num_qubits=3):
    prog = QuantumProgram()
    prog.set_registers(num_qubits, 1)
    for inst in instructions:
        prog.add_instruction(inst)
    return prog


def g(name, *qubits, params=None, clbits=None, **metadata):
    return QuantumInstruction(name, list(qubits), clbits=clbits, params=params, metadata=metadata)


def as_tuples(pairs):
    return [tuple(pair) for pair in pairs]


def test_nested_compute_uncompute_with_depths():
    prog = program(
        g("h", 0), g("h", 1), g("x", 0), g("x", 1), g("cz", 0, 1),
        g("x", 0), g("x", 1), g("h", 0), g("h", 1),
    )
    pairs = find_mirror_pairs(prog)
    assert as_tuples(pairs) == [(0, 7, 1), (1, 8, 1), (2, 5, 0), (3, 6, 0)]
    assert all(isinstance(pair, MirrorPair) for pair in pairs)
    assert pairs[0].first == 0 and pairs[0].second == 7 and pairs[0].depth == 1


def test_adjacent_self_inverse_pair():
    assert as_tuples(find_mirror_pairs(program(g("h", 0), g("h", 0)))) == [(0, 1, 0)]
    assert as_tuples(find_mirror_pairs(program(g("cx", 0, 1), g("cx", 0, 1)))) == [(0, 1, 0)]


def test_dagger_pairs_and_rotations():
    assert as_tuples(find_mirror_pairs(program(g("s", 0), g("sdg", 0)))) == [(0, 1, 0)]
    assert as_tuples(find_mirror_pairs(program(g("tdg", 0), g("t", 0)))) == [(0, 1, 0)]
    rz = program(g("rz", 0, params=[0.5]), g("rz", 0, params=[-0.5]))
    assert as_tuples(find_mirror_pairs(rz)) == [(0, 1, 0)]
    assert find_mirror_pairs(program(g("rz", 0, params=[0.5]), g("rz", 0, params=[0.5]))) == []
    assert find_mirror_pairs(program(g("rz", 0, params=[0.5]), g("rx", 0, params=[-0.5]))) == []
    assert find_mirror_pairs(program(g("s", 0), g("s", 0))) == []


def test_blockers_between_two_h_gates():
    blockers = [
        g("measure", 0, clbits=[0]),
        g("reset", 0),
        g("x", 0, condition=("c", 1)),
        g("x", 0, condition_clbits=[0]),
        g("x", 0, guards=[3]),
    ]
    for blocker in blockers:
        assert find_mirror_pairs(program(g("h", 0), blocker, g("h", 0))) == [], blocker


def test_blocker_is_never_paired_and_carried_body_blocks():
    class Body:
        data = []

    assert find_mirror_pairs(program(g("reset", 0), g("reset", 0))) == []
    body = g("x", 0, params=[Body()])
    assert find_mirror_pairs(program(g("h", 0), body, g("h", 0))) == []


def test_barrier_is_transparent():
    prog = program(g("h", 0), g("barrier", 0, 1), g("delay", 0, params=[10]), g("h", 0))
    assert as_tuples(find_mirror_pairs(prog)) == [(0, 3, 0)]


def test_two_qubit_gate_needs_same_qubit_order():
    assert find_mirror_pairs(program(g("cx", 0, 1), g("cx", 1, 0))) == []
    assert find_mirror_pairs(program(g("swap", 0, 1), g("swap", 1, 0))) == []
    crz = program(g("crz", 0, 1, params=[1.0]), g("crz", 0, 1, params=[-1.0]))
    assert as_tuples(find_mirror_pairs(crz)) == [(0, 1, 0)]


def test_partner_discarded_from_other_wire_is_rejected():
    # 0 x(1); 1 cx(0,1); 2 x(1) pairs (0,2) and pops cx from q1's stack as core.
    # 3 cx(0,1) finds 1 on q0's stack, but 1 is gone from q1: no pair.
    prog = program(g("x", 1), g("cx", 0, 1), g("x", 1), g("cx", 0, 1))
    assert as_tuples(find_mirror_pairs(prog)) == [(0, 2, 0)]


def test_max_span_limits_search():
    inner = [g("rz", 0, params=[0.1 * (k + 1)]) for k in range(5)]
    prog = program(g("h", 0), *inner, g("h", 0))
    assert as_tuples(find_mirror_pairs(prog, max_span=6)) == [(0, 6, 0)]
    assert find_mirror_pairs(prog, max_span=5) == []


def test_symbolic_params_never_match():
    theta = object()
    assert find_mirror_pairs(program(g("rz", 0, params=[theta]), g("rz", 0, params=[theta]))) == []
    assert find_mirror_pairs(program(g("rz", 0, params=["a"]), g("rz", 0, params=["-a"]))) == []
    assert find_mirror_pairs(program(g("rz", 0, params=[True]), g("rz", 0, params=[-1]))) == []


def test_empty_program():
    assert find_mirror_pairs(program()) == []


def test_twenty_thousand_instructions_is_fast():
    names = ["h", "x", "cx", "rz", "s", "t", "measure"]
    insts = []
    for k in range(20000):
        name = names[(k * 7 + k // 3) % len(names)]
        q = k % 5
        if name == "cx":
            insts.append(g("cx", q, (q + 1) % 5))
        elif name == "rz":
            insts.append(g("rz", q, params=[0.25 if k % 2 else -0.25]))
        elif name == "measure":
            if k % 11 == 0:
                insts.append(g("measure", q, clbits=[0]))
            else:
                insts.append(g("sdg", q))
        else:
            insts.append(g(name, q))
    prog = program(*insts, num_qubits=5)
    start = time.perf_counter()
    pairs = find_mirror_pairs(prog)
    assert time.perf_counter() - start < 1.0
    assert pairs == sorted(pairs, key=lambda pair: pair.first)
    # A long single-wire run of unmatched gates exercises the span bound.
    long_run = program(*[g("t", 0) for _ in range(20000)], num_qubits=1)
    start = time.perf_counter()
    assert find_mirror_pairs(long_run) == []
    assert time.perf_counter() - start < 1.0
