from qinstruction import *

class QuantumProgram:
    """
    Canonical representation of a quantum program.

    This structure should be frontend-agnostic and serve as
    the input for dependency graph construction.
    """

    def __init__(self) -> None:
        self.num_qubits: int = 0
        self.num_clbits: int = 0
        self.instructions: List[QuantumInstruction] = []

    def add_instruction(self, instruction: QuantumInstruction) -> None:
        self.instructions.append(instruction)

    def set_registers(self, num_qubits: int, num_clbits: int = 0) -> None:
        self.num_qubits = num_qubits
        self.num_clbits = num_clbits

    def __repr__(self) -> str:
        return (
            f"QuantumProgram(qubits={self.num_qubits}, "
            f"clbits={self.num_clbits}, "
            f"instructions={len(self.instructions)})"
        )