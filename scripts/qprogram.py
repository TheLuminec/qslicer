from qinstruction import *

class QuantumProgram:
    """
    Canonical representation of a quantum program.

    This structure should be frontend-agnostic and serve as
    the input for dependency graph construction.
    """

    def __init__(self) -> None:
        """Initialize an empty quantum program."""
        self.num_qubits: int = 0
        self.num_clbits: int = 0
        self.instructions: List[QuantumInstruction] = []

    def add_instruction(self, instruction: QuantumInstruction) -> None:
        """Append a generic quantum instruction to the program's sequence."""
        self.instructions.append(instruction)

    def set_registers(self, num_qubits: int, num_clbits: int = 0) -> None:
        """Configure the total capacities for both quantum and classical registers."""
        self.num_qubits = num_qubits
        self.num_clbits = num_clbits

    def __repr__(self) -> str:
        """Return a string representation detailing register sizes and instruction count."""
        return (
            f"QuantumProgram(qubits={self.num_qubits}, "
            f"clbits={self.num_clbits}, "
            f"instructions={len(self.instructions)})"
        )

    def __getitem__(self, key: int) -> QuantumInstruction:
        """Retrieve a specific instruction directly via index access."""
        return self.instructions[key]

    def __len__(self) -> int:
        """Return the overall length in terms of number of instructions."""
        return len(self.instructions)