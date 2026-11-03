# Pipeline note: figures and write-up

This folder builds **"How the Quantum Slicer slices and lints a program"**, a
technical note on the analysis pipeline: the static front ends, the IR, the
QDG and its dependency rules, slicing, the linters, the request lifecycle and
the benchmark. It is a single self-contained HTML page with four figures and
three tables. A later version is meant to feed a paper, so the figures are
generated from the code rather than drawn by hand. The worked example and the
benchmark numbers come from running the pipeline, not from the README or the
wiki.

## Build

From the repository root, with the project's interpreter (it needs the pinned
Qiskit 2.6.0):

```bash
.venv/Scripts/python.exe docs/pipeline-note/build.py
```

The page is written to `docs/pipeline-note/out/quantum-slicer-pipeline.html`.
`out/` is ignored by git. Open the page in a browser. It loads IBM Plex Sans,
IBM Plex Mono and Source Serif 4 from Google Fonts and falls back to system
fonts offline. It follows the system light or dark theme.

`--no-bench` reuses `out/bench.json` instead of re-running the benchmark.
The benchmark takes a few seconds.

## What the build does

`build.py` runs four steps. Each step is a separate process, so a failure
names the step.

| Step | Script | Output | What it is |
|---|---|---|---|
| 1 | `benchmarks/run_benchmark.py --rules quick,dependence,lintq --json` | `out/bench.json` | Bugs4Q graded with the three built-in linters (Figure 4, Tables 2 and 3) |
| 2 | `gen_example.py` | `out/example_frag.json` | Figure 2: `worked_example.py` through `analysis.analyze_source` and `slice_program` |
| 3 | `gen_figs.py` | `out/figs_frag.json` | Figures 1, 3 and 4 as inline SVG |
| 4 | `build.py` itself | `out/quantum-slicer-pipeline.html` | Fills `template.html` with the fragments, the tables and the run's facts |

## Files

| File | Role |
|---|---|
| `build.py` | Orchestrates the build. Builds Table 2 (the rule catalog) from `linters.catalog()` and Table 3 (results per linter) from the benchmark. Fills the `{{PLACEHOLDERS}}` in the template. |
| `gen_example.py` | Figure 2. Runs the real pipeline on the worked example. Draws three QDG panels: the full graph with lint markers, the backward slice from the last instruction over every dependency type, and the same slice over DATA_QUBIT only. Also produces the source listing, which tags each line with its nodes, call sites and findings, and the IR table. |
| `gen_figs.py` | The drawing helpers (`Fig`), Figure 3 (request sequence) and Figure 4 (grading as nested sets). These layouts are hand-placed on a grid. |
| `fig1_system.py` | Figure 1 (system overview): the web layer on the left, then the analysis, lint and benchmark paths, with linting on. Its column and band positions are constants at the top of `system()`. |
| `template.html` | The page: CSS (light and dark tokens, plus the SVG vocabulary every figure uses), the write-up, and placeholders. |
| `worked_example.py` | The worked example: teleportation, a helper function and a dead loop on an ancilla. |

## Keeping it accurate

Most numbers are computed at build time:

- the commit, branch, date and Qiskit version;
- the linter and rule counts;
- every Bugs4Q figure;
- the worked example's node, edge and slice sizes.

Some content is not computed. Check it when the code changes:

- **Prose claims about the code.** These include resource limits, how each
  rule works, the two parses per request, cache size and time limits. They
  were checked against the code at the commit in the page header. Section
  10.2 records the README and wiki fixes made to match the code; reread it
  when the docs change.
- **Captions that name nodes of the worked example.** For example "n0,
  n3–n6", "lines 18–19" and "n6→n7". If you edit `worked_example.py`,
  rebuild, then reread the Figure 2 caption and the parallel-edge example in
  Section 4.
- **What each rule reads.** The `READS` table in `build.py` drives the dot
  matrix of Table 2. A newly registered rule without an entry stops the build
  and names the rule.
- **Figure 1 and Figure 3 text.** These figures describe the architecture in
  words. Update them when a stage moves or is renamed.

## Editing figures

- **Overflow checks.** `gen_figs.py` estimates text widths and prints
  `OVERFLOW` or `TOO TALL` when a line probably will not fit its box. After
  any wording change, run `build.py --no-bench` and fix what it prints.
- **Colours.** Figures take their colours from CSS classes in
  `template.html`, never from literal colours. They therefore follow the
  page theme.
  - Edge types: `e-dq`, `e-me`, `e-dc`.
  - Boxes: `box`, `box-key`, `box-off`, `box-warn`.
  - Arrows: `ar` (data flow), `ar-l` (secondary use), `ar-r` (return),
    `ar-alt` (early exit).
- **Figure 2's classical wires.** Figure 2 draws one double line per clbit
  under the qubit lanes. MEASUREMENT edges drop from the writer to the wire of
  the clbit it writes, run along it, and rise into the reader. DATA_CLASSICAL
  edges take the same route, offset and dashed.
- **Arrow labels.** Labels have a halo in the panel colour, so they stay
  readable where they cross lines.

## For a paper version

Things to settle when this becomes a paper figure:

- **Size.** The page is screen-first. Figure 1 is 1690 units wide, which
  needs a full-page-width figure or a split into the analysis and lint lanes.
- **Grayscale.** The three edge types differ by colour and dash: DATA_CLASSICAL
  is dashed, MEASUREMENT and DATA_QUBIT are both solid. A grayscale print
  needs a second dash pattern.
- **Terminology.** Use **QDG**. Older documents say "QPDG"; that name is
  stale unless it refers to the IEEE paper that introduced it.
