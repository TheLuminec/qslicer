import os
import sys

from Parsers.qiskitparser import QiskitParser
from qpdg import QPDG, DependencyType
from slicer import QuantumSlicer, SlicingCriterion

sys.path.append(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Expected edge sets for each test circuit (used by verify_qpdg).
# Format: (from_node_idx, to_node_idx, DependencyType)
# ---------------------------------------------------------------------------
EXPECTED_EDGES = {
    "entanglement.py": [
        # h(q0)→measure(q0), x(q1)→measure(q1) — no entanglement (no CX gate)
        (0, 2, DependencyType.DATA_QUBIT),
        (1, 3, DependencyType.DATA_QUBIT),
    ],
    "swap.py": [
        # x(q0)→swap(q0,q1)→measure(q1) — SWAP is not entangling
        (0, 1, DependencyType.DATA_QUBIT),
        (1, 2, DependencyType.DATA_QUBIT),
    ],
    "cswap.py": [
        # h(q0)→cswap; cswap→measure — cswap IS entangling
        (0, 1, DependencyType.DATA_QUBIT),
        (1, 2, DependencyType.DATA_QUBIT),
        (1, 2, DependencyType.ENTANGLEMENT),
    ],
    "independence.py": [
        # h(q0)→cx(q0,q1); cx→measure(q1); cx→measure(q0)
        (0, 1, DependencyType.DATA_QUBIT),
        (1, 2, DependencyType.DATA_QUBIT),
        (1, 3, DependencyType.DATA_QUBIT),
        (1, 2, DependencyType.ENTANGLEMENT),
        (1, 3, DependencyType.ENTANGLEMENT),
    ],
    "sequential.py": [
        # h→x→z→measure on single qubit; z is a phase gate
        (0, 1, DependencyType.DATA_QUBIT),
        (1, 2, DependencyType.DATA_QUBIT),
        (2, 3, DependencyType.DATA_QUBIT),
        (2, 3, DependencyType.PHASE),
    ],
}


def print_qpdg(program, pdg: QPDG, file_name: str, f):
    f.write(f"--- QPDG for {file_name} ---\n")
    f.write("Nodes:\n")
    for idx, node in pdg.nodes.items():
        f.write(f"  {node.idx}: {node.instruction.name} | Qubits: {node.instruction.qubits} | Clbits: {node.instruction.clbits}\n")

    f.write("Edges:\n")
    for u, v, data in pdg.graph.edges(data=True):
        dep_type = data.get('dependency').name if data.get('dependency') else 'Unknown'
        f.write(f"  {u} -> {v} [{dep_type}]\n")

    # Backward slice from the last node (typically the final measurement)
    if pdg.nodes:
        last_node = max(pdg.nodes.keys())
        criterion = SlicingCriterion(node_idx=last_node)
        bslice = QuantumSlicer.backward_slice(pdg, criterion)
        f.write(f"Backward slice from node {last_node} (all types): {sorted(bslice)}\n")

    f.write("-" * 40 + "\n")


def verify_qpdg(pdg: QPDG, file_name: str):
    if file_name not in EXPECTED_EDGES:
        return
    expected = set(EXPECTED_EDGES[file_name])
    actual = {
        (u, v, data['dependency'])
        for u, v, data in pdg.graph.edges(data=True)
        if data.get('dependency') is not None
    }
    passed = True
    for u, v, dep in expected:
        tag = f"{u}->{v} [{dep.name}]"
        if (u, v, dep) in actual:
            print(f"  PASS: {tag}")
        else:
            print(f"  FAIL: missing {tag}")
            passed = False
    for u, v, dep in sorted(actual - expected, key=lambda e: (e[0], e[1], e[2].name)):
        print(f"  UNEXPECTED: {u}->{v} [{dep.name}]")
        passed = False
    if passed:
        print(f"  All edges correct for {file_name}")


if __name__ == "__main__":
    dir_path = os.path.join("..", "Attachments", "Sample Code")

    test_files = ["entanglement.py", "cswap.py", "swap.py", "independence.py", "sequential.py"]

    # Clear previous output
    with open("qpdg_out.txt", "w") as f:
        f.write("")

    for file_name in test_files:
        file_path = os.path.join(dir_path, file_name)
        if not os.path.exists(file_path):
            print(f"Skipping {file_name} (not found)")
            continue

        parser = QiskitParser()
        try:
            parser.parse_file(file_path)
            if len(parser.program) > 0:
                pdg = QPDG(parser.program)
                with open("qpdg_out.txt", "a") as f:
                    print_qpdg(parser.program, pdg, file_name, f)
                print(f"\n{file_name}:")
                verify_qpdg(pdg, file_name)
        except Exception as e:
            print(f"Error parse/build for {file_name}: {e}")
