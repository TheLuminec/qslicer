from parser import *
from qprogram import QuantumProgram, QuantumInstruction
import runpy
import io
from contextlib import redirect_stdout
from qiskit import QuantumCircuit
from qiskit.circuit import ClassicalRegister, Clbit
from qiskit.circuit.classical import expr

def _condition_clbits(circuit: QuantumCircuit, condition) -> list:
    """Indices of the classical bits a condition reads.

    A condition is ``(Clbit, value)``, ``(ClassicalRegister, value)``, or a
    classical expression; only the bits it actually names are returned.
    """
    if condition is None:
        return []
    if isinstance(condition, tuple):
        targets = [condition[0]]
    else:
        targets = [var.var for var in expr.iter_vars(condition)]

    indices = []
    for target in targets:
        bits = list(target) if isinstance(target, ClassicalRegister) else [target]
        for bit in bits:
            if isinstance(bit, Clbit):
                index = circuit.find_bit(bit).index
                if index not in indices:
                    indices.append(index)
    return indices


class QiskitParser(AbstractParser):
    """
    Parser for Qiskit QuantumCircuit objects.
    File parsing is done by executing the file and extracting the QuantumCircuit object.

    Expected source type:
        qiskit.QuantumCircuit
    """
    def __init__(self, source: QuantumCircuit = None) -> None:
        """Initialize the parser, optionally parsing the QuantumCircuit immediately."""
        super().__init__(source)
        if source is not None:
            self.parse()

    def parse(self) -> QuantumProgram:
        """Parse the contained QuantumCircuit source into a QuantumProgram IR."""
        self.preprocess()

        circuit = self.source

        # Extract register information
        self.program.set_registers(
            num_qubits=circuit.num_qubits,
            num_clbits=circuit.num_clbits,
        )

        # Iterate over instructions
        for instruction in circuit.data:
            op = instruction.operation
            name = op.name
            qubits = [circuit.find_bit(q).index for q in instruction.qubits]
            clbits = [circuit.find_bit(c).index for c in instruction.clbits]
            params = list(op.params)

            # Classical condition, e.g. from `with qc.if_test((clbit, value))`
            condition_raw = getattr(op, 'condition', None)
            condition_clbits = _condition_clbits(circuit, condition_raw)

            ir_instr = QuantumInstruction(
                name=name,
                qubits=qubits,
                clbits=clbits,
                params=params,
                metadata={
                    "frontend": "qiskit",
                    "condition": condition_raw,
                    "condition_clbits": condition_clbits,
                },
            )

            self.program.add_instruction(ir_instr)

        self.validate()
        return self.program
    
    def parse_file(self, path: str) -> QuantumProgram:
        """Dynamically execute a python script and extract the generated QuantumCircuit."""
        # Suppress stdout to avoid unwanted print statements (like circuit.draw) during parsing
        with io.StringIO() as buf, redirect_stdout(buf):
            try:
                # Execute the file to get its namespace
                result = runpy.run_path(path)
            except Exception as e:
                raise ValueError(f"Failed to execute the script for parsing: {e}") from e
                
        # Find all QuantumCircuit objects in the script's namespace
        circuits = [v for k, v in result.items() if isinstance(v, QuantumCircuit)]
        
        if not circuits:
            raise ValueError("No QuantumCircuit assignment found in script")
            
        # Select the most complex circuit if multiple exist (assumes the main one has the most components)
        circuit = max(circuits, key=lambda c: len(c.data))
        
        self.source = circuit
        return self.parse()