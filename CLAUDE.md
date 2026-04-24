# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **Quantum Program Dependence Graph (QPDG)** tool — a Miami University capstone project. It parses quantum programs (Qiskit circuits or OpenQASM files) into a frontend-agnostic IR, then constructs a dependence graph to enable program slicing.

## Running Scripts

All scripts must be run from within the `scripts/` directory, as local imports use relative paths:

```bash
cd scripts

# Parse all sample quantum programs and print IR info
python test.py

# Build QPDGs for entanglement.py, cswap.py, swap.py and write to qpdg_out.txt
python test_qpdg.py
```

There is no test framework — tests are manual script executions.

## Dependencies

No `requirements.txt` exists. Install manually:

```bash
pip install qiskit qiskit-aer qiskit[qasm3] networkx matplotlib
```

## Architecture

The pipeline is: **source file → Parser → QuantumProgram (IR) → QPDG → NetworkX graph**

### IR Layer (`scripts/`)
- `qinstruction.py` — `QuantumInstruction`: single gate/op with `name`, `qubits`, `clbits`, `params`, `metadata`
- `qprogram.py` — `QuantumProgram`: ordered list of `QuantumInstruction`s with register sizes; supports `len()` and index access
- `parser.py` — `AbstractParser`: ABC defining `parse(source) -> QuantumProgram` and `parse_file(path) -> QuantumProgram`

### Parsers (`scripts/Parsers/`)
- `qiskitparser.py` — `QiskitParser`: converts a `qiskit.QuantumCircuit` to IR; `parse_file()` executes the `.py` script via `runpy.run_path()` and extracts the most-instructed `QuantumCircuit` found in its namespace
- `openqasmparser.py` — `OpenQASMParser`: loads an OpenQASM **3** file via `qiskit.qasm3.load()`, then delegates to `QiskitParser`

### Graph Layer (`scripts/qpdg.py`)
- `DependencyType` enum: `ENTANGLEMENT`, `MEASUREMENT`, `DATA_CLASSICAL`, `CONTROL_CLASSICAL`, `PHASE`, `DATA_QUBIT`
- `QPDGNode`: wraps a `QuantumInstruction` with an index; used as graph node keyed by `idx`
- `QPDG`: takes a `QuantumProgram`, builds a `networkx.MultiDiGraph`
  - `_build_entanglement_dependencies()` — implemented: connects multi-qubit gates to later single-qubit ops on entangled qubits
  - `_build_measurement_dependencies()` — partially implemented: checks `metadata['condition']` / `metadata['condition_clbits']`
  - `_build_classical_dependencies()` — stub (`pass`)

### Sample Circuits (`Attachments/Sample Code/`)
Reference quantum programs used for testing: `entanglement.py`, `swap.py`, `cswap.py`, `sequential.py`, `independence.py`, etc.

## Git Workflow

- `main` is protected; all merges require at least one approval (author and contributors cannot self-approve)
- Branch off sprint branches (e.g., `sprint-3`) for feature work
- Use GitLab issue templates in `.gitlab/issue_templates/` and the MR template in `.gitlab/merge_request_templates/`

## Known Limitations / In-Progress Areas

- Classical dependency detection (`_build_classical_dependencies`) is not yet implemented
- Measurement dependency detection relies on `metadata['condition_clbits']` which the Qiskit parser does not yet populate
- Phase and qubit data dependencies (`PHASE`, `DATA_QUBIT`) are defined but not built
