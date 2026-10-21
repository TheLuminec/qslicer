# LintQ

[LintQ](https://github.com/sola-st/LintQ) (Paltenghi and Pradel, "Analyzing
Quantum Programs with LintQ: A Static Analysis Framework for Qiskit", FSE 2024;
MIT licensed) finds bugs in Qiskit programs with CodeQL queries. It is offered
here two ways, both selectable under **Lint** on the slicer page and gradable on
the **Benchmark** page:

| Linter | Id | What runs | Speed | Needs |
|---|---|---|---|---|
| LintQ | `lintq` | `lintq_port.py`: LintQ's ten queries reimplemented on the Python syntax tree | milliseconds | nothing |
| LintQ (original, CodeQL) | `lintq-codeql` | `codeql_lintq.py`: LintQ's own queries, run by CodeQL | ~30 s a run | CodeQL and a LintQ checkout |

Both report under the same codes, so they compare rule by rule:

| Code | LintQ query | Precision |
|---|---|---|
| LQ01 | `ql-double-measurement` | high |
| LQ02 | `ql-operation-after-measurement` | high |
| LQ03 | `ql-measure-all-abuse` | high |
| LQ04 | `ql-conditional-without-measurement` | high |
| LQ05 | `ql-ghost-composition` | high |
| LQ06 | `ql-op-after-optimization` | high |
| LQ07 | `ql-constant-classic-bit` | medium |
| LQ08 | `ql-oversized-circuit` | medium |
| LQ09 | `ql-unmeasurable-qubits` | medium |
| LQ10 | `ql-deprecated-identity` | medium |

That is LintQ's `LintQ-all.qls` suite; its `LintQ.qls` is the six high-precision
ones (select LQ01-LQ06).

## The port behaves like the original

The port rebuilds LintQ's model of a program (circuits, registers, operators
and the qubits they name, found through import resolution, local data flow,
type tracking and the control-flow graph) and keeps its limits, because the
point is LintQ's behaviour, not a better linter. Some of what that means, each
confirmed against the original:

* only explicit imports resolve Qiskit names: after `from qiskit import *`
  alone, LintQ sees no circuit at all, and many Bugs4Q programs start that way;
* `qr[0]` is an unknown qubit (LintQ resolves a subscript's register through
  CodeQL's points-to analysis, which does not reach into Qiskit);
* a gate earlier in a loop body follows a measurement later in it;
* branches whose condition is a constant (`k = 1` ... `if k > 0`) are pruned;
* `range(2, 7)` also yields qubits 0 and 1 (a quirk of LintQ's `resolveRange`).

`tests/lintq/reference.json` records the original's warnings (CodeQL 2.11.2,
LintQ `481ca24`) on 239 programs: LintQ's own tests, probe programs written for
each query's edge cases, the bundled examples and all of Bugs4Q. The port gives
exactly those 190 warnings, file by file and line by line, and nothing else;
`tests/lintq/test_lintq_conformance.py` checks it. The probes were written after
the port matched the rest and were not used to tune it.

A program with more than 3,000 quantum operations gets one `LQ00` note instead
of an analysis, since the "directly follows" queries grow with the cube of the
operations on one qubit.

## Running the original

1. Download the CodeQL CLI 2.11.x for your platform from
   [github/codeql-cli-binaries](https://github.com/github/codeql-cli-binaries/releases)
   (LintQ pins `codeql/python-all` 0.6.2) and unzip it.
2. `git clone https://github.com/sola-st/LintQ.git`, then run
   `codeql pack install` in `qlint/codeql/lib` and in `qlint/codeql/src`.
3. Set `QSLICER_CODEQL` to the `codeql` executable and `QSLICER_LINTQ` to the
   checkout. `QSLICER_CODEQL_PYTHON` optionally picks the Python the extractor
   runs on.

CodeQL 2.11's Python extractor imports `imp`, removed in Python 3.12; on 3.12
or later the adapter puts the one-function stand-in in `codeql_support/` on
the extractor's path, and runs the extractor through `codeql_support/extract.py`
rather than CodeQL's autobuild scripts, so no CodeQL file is edited. The first
run compiles the queries (one to two minutes).

Then:

```bash
python scripts/Linters/codeql_lintq.py some/dir out.json   # warnings for a directory
python tests/lintq/record_reference.py                     # re-record the reference
pytest tests/lintq -m slow                                 # the original still matches
```

`record_reference.py` also writes `benchmarks/bugs4q/external/lintq-codeql.json`,
which is how the Benchmark page shows the original's results where CodeQL is
not installed.
