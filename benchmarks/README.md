# Benchmarks

Datasets of buggy programs and the programs that fixed them, used to grade the
linters. Each folder here with a `manifest.json` is a dataset; the web app's
**Benchmark** page lists them all, and `run_benchmark.py` runs any of them.

| Dataset | Bugs | What |
|---|---|---|
| [`bugs4q/`](bugs4q/README.md) | 62 | Real Qiskit bugs from GitHub issues, Stack Overflow and Stack Exchange |

```bash
python benchmarks/run_benchmark.py                                  # Bugs4Q, the default rules
python benchmarks/run_benchmark.py --manifest bugs4q --rules quick,dependence,lintq
python benchmarks/run_benchmark.py --manifest path/to/manifest.json # a dataset anywhere
python benchmarks/run_benchmark.py --list-datasets
python benchmarks/run_benchmark.py --list-rules
python benchmarks/run_benchmark.py --json report.json               # the full report
```

## Adding a dataset

Make a folder here with:

| Path | What |
|---|---|
| `manifest.json` | The bugs (below) |
| `programs/` | The buggy and fixed programs, at any paths the manifest names |
| `external/` | Optional: recorded results of other tools (see [bugs4q/README.md](bugs4q/README.md#comparing-another-tool)) |

```json
{
  "name": "My bugs",
  "description": "seeded student-style bugs",
  "upstream": "https://...", "commit": "...", "license": "MIT",
  "bugs": [
    {"key": "bell-swap-1", "buggy": "bell/swap-1/buggy.py", "fixed": "bell/original.py",
     "source": "seeded", "reference": "control and target swapped", "url": "", "type": "cx"}
  ]
}
```

Only `bugs`, and each bug's `key`, `buggy` and `fixed`, are required; the rest
is shown where present. Ground truth comes from the diff between the two
programs (`webapp/benchmark.py` explains how), so nothing else needs labelling.
A manifest outside this folder can be run with `--manifest <path>`, but only
folders here appear on the web app.

A dataset that appends scaffolding to its programs (the `qiskit-llm-bugs`
harness adds a call of the solution's function, so a static reader sees a
circuit) can give each bug `"ignore_from_line": {"buggy": 18, "fixed": 17}`:
warnings from that line on are left out of the grading, since they say nothing
about the program itself.
