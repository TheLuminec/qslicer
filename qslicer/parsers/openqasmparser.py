from .base import AbstractParser
from qiskit import QuantumCircuit
import qiskit.qasm3

from .qiskitparser import QiskitParser

class OpenQASMParser(AbstractParser):
    def parse_file(self, path):
        """Parse an OpenQASM physical file into the system's QuantumProgram IR."""
        circuit = qiskit.qasm3.load(path)
        self.source = circuit
        return self.parse()

    def parse(self):
        """Delegate parsing the OpenQASM source string to the initialized Qiskit parser."""
        # Not the greatest right now but will change in the future.
        # QiskitParser parses in its constructor, so take the program it has
        # already built; calling parse() again appends every instruction twice.
        self.program = QiskitParser(self.source).program
        return self.program
