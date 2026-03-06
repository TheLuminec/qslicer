from parser import *
from qiskit import QuantumCircuit
from qiskitparser import QiskitParser

class OpenQASMParser(AbstractParser):
    def parse_file(self, path):
        """Parse an OpenQASM physical file into the system's QuantumProgram IR."""
        file = open(path, 'r')
        circuit = QuantumCircuit.from_qasm_file(file)
        self.source = circuit
        return self.parse()

    def parse(self):
        """Delegate parsing the OpenQASM source string to the initialized Qiskit parser."""
        # Not the greatest right now but will change in the future
        self.program = QiskitParser(self.source).parse()
        return self.program