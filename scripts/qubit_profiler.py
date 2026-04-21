from qprogram import QuantumProgram
from qinstruction import QuantumInstruction

class QubitProfile:
    def __init__(self, qubit_id: int):
        self.qubit_id = qubit_id
        self.instructions = []

    def add_instruction(self, instruction: QuantumInstruction):
        self.instructions.append(instruction)

class QuBitProfiler:
    def __init__(self, program: QuantumProgram):
        self.program = program
        self.qubits = {i: QubitProfile(i) for i in range(program.num_qubits)}

    def profile(self):
        pass