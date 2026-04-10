from parser import *
from qiskit import QuantumCircuit
import qiskit.qasm3

try:
    from Parsers.qiskitparser import QiskitParser
except ModuleNotFoundError:
    from qiskitparser import QiskitParser

class OpenQASMParser(AbstractParser):
    def parse_file(self, path):
        """Parse an OpenQASM physical file into the system's QuantumProgram IR."""
        circuit = qiskit.qasm3.load(path)
        self.source = circuit
        return self.parse()

    def parse(self):
        """Delegate parsing the OpenQASM source string to the initialized Qiskit parser."""
        # Not the greatest right now but will change in the future
        self.program = QiskitParser(self.source).parse()
        return self.program
