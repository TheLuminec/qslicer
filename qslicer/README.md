# qslicer

Static slicing and linting for quantum programs written in **Qiskit (Python)**
or **OpenQASM 2/3**.

qslicer reads a circuit **without running it**, builds a Quantum Dependency
Graph (QDG) of its instructions, and answers the question a developer has:

> Which instructions can influence this measurement, and what does this gate
> go on to affect?

A **backward slice** from an instruction collects everything it depends on; a
**forward slice** collects everything it can affect. The same front ends feed a
set of linters that report likely bugs, with every finding tied to a source
line. Because nothing is executed, it is safe to point at code you did not
write.

## Install

```bash
pip install qslicer
pip install "qslicer[viz]"     # adds matplotlib, for `qslicer viz`
pip install "qslicer[qasm3]"   # adds qiskit-qasm3-import, for the qiskit.qasm3-based OpenQASM parser
```

Requires Python 3.10 or later. qslicer is maintained against **Qiskit 2.6**
and asks for `qiskit>=2.6,<2.7`: the static parsers and the list of removed
Qiskit APIs (QS011) follow that release.

## Command line

```bash
qslicer lint circuit.py other.py           # every linter's default rules
qslicer lint circuit.py --select quick lintq --hints
qslicer lint circuit.py --format json      # for scripts and CI
qslicer list circuit.py                    # the instructions found, with source lines
qslicer slice circuit.py 12                # backward slice from instruction 12
qslicer slice circuit.py --forward --line 8 --deps DATA_QUBIT MEASUREMENT
qslicer slice circuit.py --qubit 2         # backward from qubit 2's last measurement
qslicer slice circuit.py 5 --chop-qubit 2  # only how instruction 5 reaches it
qslicer rules                              # every linter and rule id
qslicer viz circuit.py                     # the QDG in a matplotlib window (needs [viz])
qslicer viz shor.py --circuit 3 --line 75  # another circuit the file builds, sliced from line 75
qslicer viz asks.py --set n=5 --save v.png # a value only a run decides; an image, no window
```

`lint` exits 1 when it reports anything at `--fail-on` (default `warning`) or
above, so it can gate CI or a pre-commit hook. Files ending in `.qasm`,
`.qasm2` or `.qasm3` go to the OpenQASM front end; `--language` overrides the
extension. `python -m qslicer` works too.

`viz` opens an interactive window: click an instruction to slice from it
(ctrl- or shift-click for several slices at once), click a wire name to slice
on that qubit, `c` to chop from an operation to a measurement, hover an
instruction for its gate, its controls and targets, source line, lint findings
and what the insight passes found, `m` for every mirror arc in view, `[` and `]` step through the
findings, and `?` lists the keys. It runs the linters unless
`--no-lint` is given, and `--save` writes an image instead of opening a window.

## Python

```python
import qslicer

# Read a Qiskit program without executing it (.qasm goes to the OpenQASM front end)
program = qslicer.parse_file("my_circuit.py")

# Every instruction knows its source line
for i, ins in enumerate(program.instructions):
    print(i, ins.name, "line", ins.location.line)

last = len(program) - 1
print(sorted(qslicer.backward_slice(program, last)))
print(sorted(qslicer.forward_slice(program, 0, dep_types=[qslicer.DependencyType.DATA_QUBIT])))

for finding in qslicer.lint_file("my_circuit.py"):   # or lint_source(text, filename)
    print(finding["line"], finding["code"], finding["message"])
```

`parse` and `parse_file` raise `StaticParseError` with a line number when the
circuit cannot be determined statically. The slice functions take a program or
a `QDG` you built (`qslicer.QDG(program, rules)`); `select=` picks lint rules by
id (`"quick/QS002"`) or by linter (`"lintq"`). `qslicer.example_path("bell_pair.py")`
finds the bundled example circuits.

## How it works

1. **Parse.** A static front end turns the program into a `QuantumProgram` of
   `QuantumInstruction`s: gate name, qubit and classical-bit operands,
   parameters, and a `SourceLocation` (line, column, and the call lines that
   led there).
2. **Build the QDG.** Each instruction becomes a node; dependency rules add
   edges labelled with their dependency type.
3. **Slice.** Graph reachability along incoming (backward) or outgoing
   (forward) edges, optionally restricted to some dependency types. A slice
   always contains its criterion.

| Dependency type | Meaning | Default |
|---|---|---|
| `DATA_QUBIT` | A gate writes a qubit; later gates on it depend on the writer | on |
| `MEASUREMENT` | An operation conditioned on a measurement depends on it | on |
| `DATA_CLASSICAL` | Reaching definitions on classical bits | on |
| `CONTROL_CLASSICAL` | An instruction inside an `if_test` or `switch` branch depends on the predicate it sits under | on |
| `ENTANGLEMENT` | Links an entangling gate to later operations on entangled qubits | off |
| `PHASE` | Dependencies through phase-changing gates | off |

### The static front ends

The **Qiskit** front end does not pattern-match `qc.h(0)`-shaped lines. It
interprets the module's syntax tree over its own model of `QuantumCircuit`,
registers, gates and parameters. Imports are recorded, never performed, and
anything only a real run could produce (a simulator result, a file, an
unseeded random number) becomes an unknown. Unknowns are harmless until the
circuit needs one, as a qubit index, a register size or a loop condition; then
the parse stops with that line. It covers arithmetic, containers, loops,
functions, closures, `try`/`except`, `math`, the basics of `numpy`, seeded
randomness, broadcasting, `compose`, `to_gate`, `inverse`, `decompose`,
`if_test` and the `qiskit.circuit.library` gates.

The interpreter never calls `import`, `eval`, `exec` or `compile`, refuses any
attribute starting with `_`, and bounds its work: statements and loop
iterations, instructions, call depth and container sizes. Pass
`time_limit=` (seconds) to `parse` or `parse_file` to cap wall-clock time too.

The **OpenQASM** front end uses the `openqasm3` reference parser and its own
interpreter, so it reads OpenQASM 2 and the parts of OpenQASM 3 that
`qiskit.qasm3` refuses: classical variables and arrays, subroutines, `extern`,
physical qubits, and `if`/`while`/`switch` on measured values.

## Linters

| Linter | Id | What it reports | Default |
|---|---|---|---|
| Quick checks | `quick` | `QS000`-`QS017`: errors Python or Qiskit would raise, such as syntax errors, a missing circuit, indices outside the registers, removed or unknown Qiskit APIs, and operations Qiskit rejects; and a circuit changed after it was run | on |
| Dependence checks | `dependence` | `QD01`-`QD12` (no `QD04`): quantum mistakes in the rebuilt circuit and its QDG, such as dead gates, overwritten or constant measurements, controls never set, missing measurements, idle qubits, cancelling pairs, a state read from a circuit that measures, and classical bits never written | on |
| LintQ | `lintq` | `LQ01`-`LQ10`: the ten queries of LintQ, reimplemented on the syntax tree | off |
| LintQ (original) | `lintq-codeql` | LintQ's own CodeQL queries, when `QSLICER_CODEQL` and `QSLICER_LINTQ` are set | off |

`qslicer rules` lists every rule with its severity. The quick checks read
Python source only. The dependence checks need a circuit the static parser can
rebuild.

The `lintq` port gives exactly the original's warnings, file by file and line
by line, on 239 programs (LintQ's own tests, edge-case probes, the bundled
examples and all of Bugs4Q). It keeps LintQ's limits on purpose: for example,
only explicit imports resolve Qiskit names, so after `from qiskit import *`
alone it sees no circuit.

A new linter can come from an installed package that declares it under the
`qslicer.linters` entry point group, or from any importable module named in
`QSLICER_LINTER_PLUGINS`.

## Status and limitations

qslicer is research software, and it accepts conservative approximations.

- The entanglement and phase rules ship disabled, and only the `baseline` rule
  profile exists.
- Slices do not yet drop an entangling gate that is uncomputed before the
  criterion.
- Code the interpreter does not model (user-defined classes, `transpile` and
  pass managers) is not followed. Control flow that depends on an unknown value
  and touches a circuit is refused rather than guessed.
- Static parsing unrolls loops, so very deep circuits become large graphs.

## Credits and licenses

qslicer is released under the MIT License.

The `lintq` linter is a port of [LintQ](https://github.com/sola-st/LintQ)
(Paltenghi and Pradel, "Analyzing Quantum Programs with LintQ: A Static
Analysis Framework for Qiskit", FSE 2024), Copyright (c) 2024 Matteo
Paltenghi, Michael Pradel, used under the MIT License. Its license text ships
in the package as `qslicer/lint/lintq/LICENSE`.

Source, the web app built on qslicer, the benchmark and the full documentation:
<https://github.com/TheLuminec/qslicer>
