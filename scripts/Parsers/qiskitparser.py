from parser import *
from qprogram import QuantumProgram, QuantumInstruction
import runpy
import io
from contextlib import redirect_stdout
from qiskit import QuantumCircuit

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
        for instruction, qargs, cargs in circuit.data:
            # Support both Qiskit 0.x (instruction is the gate) and 1.x (instruction is CircuitInstruction)
            op = getattr(instruction, 'operation', instruction)
            name = op.name
            qubits = [circuit.qubits.index(q) for q in qargs]
            clbits = [circuit.clbits.index(c) for c in cargs]
            params = list(op.params)

            # Extract c_if classical condition if present
            condition_raw = getattr(op, 'condition', None)
            condition_clbits = []
            if condition_raw is not None:
                reg, _ = condition_raw  # (ClassicalRegister, int_value)
                for clbit in reg:
                    condition_clbits.append(circuit.clbits.index(clbit))

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
                raise ValueError(f"Failed to execute the script for parsing: {e}")
                
        # Find all QuantumCircuit objects in the script's namespace
        circuits = [v for k, v in result.items() if isinstance(v, QuantumCircuit)]
        
        if not circuits:
            raise ValueError("No QuantumCircuit assignment found in script")
            
        # Select the most complex circuit if multiple exist (assumes the main one has the most components)
        circuit = max(circuits, key=lambda c: len(c.data))
        
        self.source = circuit
        return self.parse()