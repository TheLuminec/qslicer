from qprogram import QuantumProgram
from qinstruction import QuantumInstruction
from typing import List, Set

_ENTANGLING_GATES = frozenset({
    "cx", "cy", "cz", "ch", "crx", "cry", "crz", "cp", "csx",
    "ccx", "c3x", "c4x", "dcx", "ecr", "iswap", "rzz", "rxx", "ryy", "cswap",
})

_MEASUREMENT_GATES = frozenset({"measure", "measure_all"})

class EntanglementGroup:
    def __init__(self, instruction: QuantumInstruction, parent_groups: Set[int] = None):
        self.qubits = instruction.qubits
        self.origin = instruction
        self.computed_instructions = []
        self.active = True
        self.parent_groups = parent_groups if parent_groups is not None else set()

    def apply_instruction(self, instruction: QuantumInstruction):
        # Determins if instruction applies to group and updates qubits and instructions accordingly
        if self.qubits.isdisjoint(instruction.qubits):
            return False
        
        if instruction.name in _MEASUREMENT_GATES:
            self.active = False
            self.add_instruction(instruction)
            return True
        
        if instruction.name in _ENTANGLING_GATES:
            self.active = False
            return True

        if len(self.computed_instructions) > 0:
            if instruction == self.computed_instructions[-1]:
                self.pop_instruction()
                return True
            
        self.add_instruction(instruction)
        return True

    def add_instruction(self, instruction):
        self.computed_instructions.append(instruction)

    def pop_instruction(self):
        if self.computed_instructions:
            return self.computed_instructions.pop()
        return None
    
    def add_parent_group(self, group_id: int, group_qubits: Set[int]):
        self.parent_groups.add(group_id)
        self.qubits.update(group_qubits)

    def __repr__(self):
        return f"EntanglementGroup(qubits={self.qubits}, origin={self.origin}, instructions={len(self.computed_instructions)})"
    
class EntanglementAnalyzer:
    def __init__(self, program: QuantumProgram):
        self.program = program
        self.groups: List[EntanglementGroup] = []
        self.active_groups: Set[int] = set()

    def analyze(self):
        for instruction in self.program.instructions:
            if instruction.name in _ENTANGLING_GATES:
                new_group = EntanglementGroup(instruction)
                for group in self.active_groups:
                    if group.apply_instruction(instruction):
                        new_group.add_parent_group(self.groups.index(group), group.qubits)
                self.groups.append(new_group)
            else:
                for group in self.active_groups:
                    group.apply_instruction(instruction)

            self.active_groups = {group for group in self.groups if group.active}

    def get_groups(self) -> List[EntanglementGroup]:
        return self.groups
    
    def __repr__(self):
        return f"EntanglementAnalyzer(groups={len(self.groups)}, active_groups={len(self.active_groups)})"
    
if __name__ == "__main__":
    from qprogram import QuantumProgram
    from qinstruction import QuantumInstruction

    program = QuantumProgram()
    program.set_registers(num_qubits=3)
    program.add_instruction(QuantumInstruction(name="h", qubits={0, 1, 2}))
    program.add_instruction(QuantumInstruction(name="cx", qubits={0, 1}))
    program.add_instruction(QuantumInstruction(name="cx", qubits={1, 2}))
    program.add_instruction(QuantumInstruction(name="measure", qubits={0}, clbits={0}))
    program.add_instruction(QuantumInstruction(name="measure", qubits={1}, clbits={1}))
    program.add_instruction(QuantumInstruction(name="measure", qubits={2}, clbits={2}))

    analyzer = EntanglementAnalyzer(program)
    analyzer.analyze()
    print(analyzer)
    groups = analyzer.get_groups()
    for group in groups:
        print(group)