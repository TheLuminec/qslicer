# Quantum Program Analysis Framework (QPDG‑Testing)

## Overview

This project implements a static analysis framework for **quantum programs**. At its core it provides:

- A **frontend‑agnostic intermediate representation (IR)** to describe quantum circuits as sequences of instructions.
- Construction of a **Quantum Program Dependence Graph (QPDG)** that captures how operations depend on each other via classical and quantum effects.
- A **slicer** that can compute backward or forward program slices based on dependency types.
- An **entanglement analyser** that approximates entanglement groups and tracks when operations are uncomputed or measured.
- Optional **visualisation utilities** for debugging dependency graphs and entanglement structure.

These tools enable researchers and developers to reason about quantum programs, debug and optimise circuits, and explore how quantum‑specific phenomena such as entanglement and measurement affect program behaviour.

## Getting started

### Prerequisites

The framework requires Python 3.10+ and the following Python packages:

- [networkx](https://networkx.org/) - for graph construction and traversal.
- [qiskit](https://qiskit.org/) - used by the Qiskit parser to load QuantumCircuit objects.

Install the dependencies using `pip`:

```bash
pip install -r requirements.txt
```

If you plan to use the Qiskit parser, install qiskit separately via `pip install qiskit`.

### Directory structure

```
project/  
├── scripts/ # Core modules and utilities  
│ ├── Dependencies/ # Dependency rules used to build the QPDG  
│ ├── Parsers/ # Language-specific parsers (Qiskit, OpenQASM)  
│ ├── combined_visualizer.py  
│ ├── entangle_viewer.py  
│ ├── entanglement.py # Approximate entanglement analysis  
│ ├── parser.py # Abstract parser base class  
│ ├── qinstruction.py # IR instruction class  
│ ├── qpdg.py # QPDG construction and rule management  
│ ├── qprogram.py # IR program container  
│ ├── slicer.py # Slicing algorithms  
│ └── visualizer.py  
├── Attachments/ # Sample circuits used for testing/visualisation  
├── requirements.txt  
└── README.md # This file
```
## Usage

The typical workflow is:

- **Parse a quantum circuit** into the intermediate representation. Use one of the provided parsers; for example, the Qiskit parser executes a Python file that builds a qiskit.QuantumCircuit and extracts its gates into the IR.
- **Build the QPDG** for the program. The QPDG constructor takes a QuantumProgram and an optional list of dependency rules. By default, it builds edges for data dependencies on qubits and classical bits and links measurements to classically conditioned operations.
- **Slice the program**. Use QuantumSlicer.backward_slice or QuantumSlicer.forward_slice with a SlicingCriterion specifying the instruction index and the dependency types of interest. The result is a set of instruction indices that belong to the slice.
- **Analyse entanglement**. The EntanglementAnalyzer creates groups that approximate how qubits become entangled over the course of a program. Although not integrated into the QPDG by default, it can be used to explore entanglement dependencies and inform slicing.

### Example: slicing a small circuit

```python
from scripts.Parsers.qiskitparser import QiskitParser  
from scripts.qpdg import QPDG  
from scripts.slicer import QuantumSlicer, SlicingCriterion  
# Parse a Qiskit circuit defined in a file  
parser = QiskitParser()  
program = parser.parse_file('Attachments/Sample Code/simple_bell.py')  
# Build the QPDG using default rules  
graph = QPDG(program)  
# Slice backwards from the measurement on qubit 0 (index 3)  
criterion = SlicingCriterion(node_idx=3)  
slice_indices = QuantumSlicer.backward_slice(graph, criterion)  
print('Backward slice:', sorted(slice_indices))

# To visualize the QPDG
from scripts.visualizer import QPDGVisualizer
visualizer = QPDGVisualizer(graph)
```
This example collects all instructions that could influence the measurement at node 3. The set of indices returned can be used to extract a sub‑circuit or highlight relevant operations.

## Core modules

Below is a brief overview of the main modules. For details, consult the docstrings in the source files.

| Module | Description|
|-------------------|-------------------|
| **qinstruction.py** | Defines the QuantumInstruction class used to represent a single quantum operation. Includes attributes for gate name, qubits, classical bits, parameters and metadata.|
| **qprogram.py** | Represents an ordered list of QuantumInstruction objects along with register sizes. Acts as the IR for subsequent analysis.|
| **parser.py / Parsers** | Provides an abstract parser and language‑specific parsers (e.g., QiskitParser, OpenQASMParser) to convert frontend quantum languages into the IR.|
| **Dependencies/** | Contains dependency rules for building the QPDG. Current rules cover data dependencies on qubits (DataQubitRule), measurement dependencies (MeasurementRule), classical data dependencies (DataClassicalRule) and an empty placeholder for control dependencies. Additional rules (entanglement, phase) are implemented but not enabled by default.|
| **qpdg.py** | Builds the QPDG. Each instruction becomes a node and dependency rules emit labelled edges between nodes. The rule set can be customised when constructing the graph.|
| **slicer.py** | Implements backward and forward slicing. Slicing can be constrained by dependency type and a reachability threshold (default 1.0).|
| **entanglement.py** | Implements EntanglementGraph and EntanglementAnalyzer to approximate entanglement groups and track measured/uncomputed state. Edges can be extracted and visualised as an auxiliary graph.|
| **visualizer.py** | Contains helper functions to draw dependency graphs and entanglement graphs using matplotlib. Supports interactive or static display.|
| **combined_visualizer.py** | Utility script to load, parse, build QPDG, analyse entanglement and generate combined visualisations.|
| **slicer.py**                          | Implements backward and forward slicing. Given a QPDG and a SlicingCriterion, it performs a breadth‑first traversal of the graph along selected dependency types.                                                                                                                                                                                   |
| **entanglement.py**                    | Approximates entanglement relationships by grouping qubits whenever an entangling operation occurs. Tracks uncomputation (self‑inverse gates) and measurement to model when entanglement is destroyed. Not yet integrated into the QPDG.                                                                                                            |
| **visualizer.py / entangle_viewer.py** | Utilities for visualising the dependency graph and entanglement groups using NetworkX/Matplotlib.                                                                                                                                                                                                                                                   |

## Understanding dependency types

The QPDG extends the classical Program Dependence Graph by incorporating quantum‑specific dependencies. Supported dependency types include:

| Type                 | Meaning                                                                                                                                    |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| **DataQubit**        | A gate writes to a qubit; later gates using that qubit depend on the writer (def‑use).                                                     |
| **Measurement**      | Later operations that are classically conditioned on the result of a measurement depend on the measurement.                                |
| **DataClassical**    | Classic reaching‑definition dependency on classical bits (used to implement classical logic).                                              |
| **ControlClassical** | Placeholder for control dependencies; currently not implemented.                                                                           |
| **Entanglement**     | (Disabled by default) Connects entangling gates to subsequent operations on entangled qubits. Enables tracking of entanglement dependence. |
| **Phase**            | (Disabled by default) Tracks dependencies arising from phase‑changing gates. Useful for capturing qubit‑data dependence and uncomputation. |

Additional rules can be enabled or customised when constructing a QPDG. See scripts/Dependencies/rules.py for details.

