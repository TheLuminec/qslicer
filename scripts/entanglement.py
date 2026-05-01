from qprogram import QuantumProgram
from qinstruction import QuantumInstruction
from typing import List, Set
import os
import networkx as nx

_ENTANGLING_GATES = frozenset({
    "cx", "cy", "cz", "ch", "crx", "cry", "crz", "cp", "csx",
    "ccx", "c3x", "c4x", "dcx", "ecr", "iswap", "rzz", "rxx", "ryy", "cswap",
})

_MEASUREMENT_GATES = frozenset({"measure", "measure_all"})

_CLASSICAL_GATES = frozenset({"c_if", "if_else"})

class EntanglementGroup:
    def __init__(
        self,
        instruction: QuantumInstruction,
        parent_groups: Set[int] = None,
        kind: str = None,
        depth: int = 0,
    ):
        self.qubits = set(instruction.qubits.copy())
        self.origin = instruction
        self.computed_instructions = []
        self.uncomputed_instructions = []
        self.measurement_instructions = []
        self.active = True
        self.measurement_applied = False
        self.parent_groups = parent_groups if parent_groups is not None else set()
        self.kind = kind or ("init" if instruction.name == "init" else "entangled")
        self.depth = depth

    def apply_instruction(self, instruction: QuantumInstruction):
        # Determines if instruction applies to group and updates qubits and instructions accordingly.
        if self.qubits.isdisjoint(instruction.qubits):
            return False
        
        if instruction.name in _MEASUREMENT_GATES:
            self.active = False
            self.add_instruction(instruction)
            self.measurement_applied = True
            return True
        
        if instruction.name in _ENTANGLING_GATES or instruction.name in _CLASSICAL_GATES:
            self.active = False
            return True

        # Uncompute instruction if it is the same as the last instruction.
        if len(self.computed_instructions) > 0:
            if instruction == self.computed_instructions[-1]:
                self.uncompute_instruction(instruction)
                return True
            
        self.add_instruction(instruction)
        return True

    def uncompute_instruction(self, instruction: QuantumInstruction):
        self.pop_instruction()
        self.uncomputed_instructions.append(instruction)

    def add_instruction(self, instruction):
        self.computed_instructions.append(instruction)

    def add_measurement_instruction(self, instruction):
        self.measurement_instructions.append(instruction)
        self.add_instruction(instruction)

    def pop_instruction(self):
        if self.computed_instructions:
            return self.computed_instructions.pop()
        return None
    
    def add_parent_group(self, group_id: int, group_qubits: Set[int], parent_depth: int = None):
        self.parent_groups.add(group_id)
        self.qubits.update(group_qubits)
        if parent_depth is not None:
            self.depth = max(self.depth, parent_depth + 1)

    def __repr__(self):
        return (
            "EntanglementGroup("
            f"kind={self.kind}, depth={self.depth}, qubits={self.qubits}, "
            f"origin={self.origin}, parents={self.parent_groups}, "
            f"instructions={len(self.computed_instructions)}), "
            f"uncomputed={len(self.uncomputed_instructions)}"
        )
    
class EntanglementAnalyzer:
    def __init__(self, program: QuantumProgram):
        self.program = program
        self.groups: List[EntanglementGroup] = []
        self.active_groups: List[EntanglementGroup] = []
        self._pending_measurement_groups = {}

    def analyze(self):
        for qb in range(self.program.num_qubits):
            self.groups.append(
                EntanglementGroup(
                    QuantumInstruction(name="init", qubits={qb}),
                    kind="init",
                    depth=0,
                )
            )
            self.active_groups.append(self.groups[-1])

        for instruction in self.program.instructions:
            if instruction.name in _ENTANGLING_GATES or instruction.name in _CLASSICAL_GATES:
                self._pending_measurement_groups.clear()
                
                absorbed = False
                if instruction.name in _CLASSICAL_GATES:
                    for group in list(self.active_groups):
                        if group.kind == "classical":
                            group.add_instruction(instruction)
                            absorbed = True
                            break
                if absorbed:
                    continue
                    
                kind = "classical" if instruction.name in _CLASSICAL_GATES else "entangled"
                new_group = EntanglementGroup(instruction, kind=kind)
                for group in self.active_groups:
                    if group.apply_instruction(instruction):
                        new_group.add_parent_group(
                            self.groups.index(group),
                            group.qubits,
                            group.depth,
                        )
                self.groups.append(new_group)
            elif instruction.name in _MEASUREMENT_GATES:
                self._apply_measurement(instruction)
            else:
                self._pending_measurement_groups.clear()
                for group in self.active_groups:
                    group.apply_instruction(instruction)

            self.active_groups = [group for group in self.groups if group.active]

    def _measurement_qubits(self, instruction: QuantumInstruction) -> Set[int]:
        if instruction.name == "measure_all" and not instruction.qubits:
            return set(range(self.program.num_qubits))
        return set(instruction.qubits)

    def _instruction_touches_group(self, instruction: QuantumInstruction, group: EntanglementGroup) -> bool:
        if instruction.name == "measure_all":
            return True
        return not group.qubits.isdisjoint(self._measurement_qubits(instruction))

    def _apply_measurement(self, instruction: QuantumInstruction):
        touched_pending_parent_ids = set()

        for parent_id, measurement_group_id in self._pending_measurement_groups.items():
            measurement_group = self.groups[measurement_group_id]
            if self._instruction_touches_group(instruction, measurement_group):
                measurement_group.add_measurement_instruction(instruction)
                touched_pending_parent_ids.add(parent_id)

        for group in list(self.active_groups):
            if not self._instruction_touches_group(instruction, group):
                continue

            if self.groups.index(group) in self._pending_measurement_groups.values():
                continue

            parent_id = self.groups.index(group)
            if parent_id in touched_pending_parent_ids:
                continue

            group.active = False
            group.measurement_applied = True

            measurement_group = EntanglementGroup(
                instruction,
                parent_groups={parent_id},
                kind="measurement",
                depth=group.depth + 1,
            )
            measurement_group.qubits = set(group.qubits)
            measurement_group.active = True
            measurement_group.measurement_applied = True
            measurement_group.add_measurement_instruction(instruction)

            self.groups.append(measurement_group)
            self._pending_measurement_groups[parent_id] = len(self.groups) - 1

    def get_groups(self) -> List[EntanglementGroup]:
        return self.groups
    
    def __repr__(self):
        return f"EntanglementAnalyzer(groups={len(self.groups)}, active_groups={len(self.active_groups)})"
    
class EntanglementGraph:
    def __init__(self, analyzer: EntanglementAnalyzer):
        self.analyzer = analyzer
        self.graph = nx.DiGraph()
        self.build_graph()

    def build_graph(self):
        for idx, group in enumerate(self.analyzer.get_groups()):
            self.graph.add_node(idx, group=group)
            for parent_id in group.parent_groups:
                self.graph.add_edge(parent_id, idx)
    

if __name__ == "__main__":
    from Parsers.qiskitparser import QiskitParser
    parser = QiskitParser() 
    program = parser.parse_file(
        os.path.join("Attachments", "Sample Code", "grovers_algorithm_exception.py")
    )

    analyzer = EntanglementAnalyzer(program)
    analyzer.analyze()
    print(analyzer)
    groups = analyzer.get_groups()
    for group in groups:
        print(group)
        for instr in group.computed_instructions:
            print(f"  {instr}")
