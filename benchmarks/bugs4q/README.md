# Bugs4Q

The Qiskit bugs of [Bugs4Q](https://github.com/Z-928/Bugs4Q-NA), used to grade
the slicer's linters. Each bug is a buggy program and the program that fixed
it; the fix is the ground truth for where the fault was.

| Path | What |
|---|---|
| `manifest.json` | The 62 bugs: key, source, issue link, type, and the buggy, fixed and (where upstream has one) modification file |
| `programs/` | The programs, at their upstream paths, with upstream's `LICENSE` |
| `external/` | Recorded results of other tools, compared on every run (see below) |
| `sync_upstream.py` | Rebuilds `manifest.json` and `programs/` from a Bugs4Q checkout |

Run it from the web app's **Benchmark** page, or:

```bash
python benchmarks/run_bugs4q.py                           # the default rules
python benchmarks/run_bugs4q.py --rules quick,dependence  # whole linters, compared
python benchmarks/run_bugs4q.py --list-rules
```

Nothing in `programs/` is ever executed. Linters read them as text or through
the static front ends; many target Qiskit versions that no longer exist.

## Source and license

Bugs4Q is by Pengzhan Zhao, Jianjun Zhao, Zhongtao Miao and Shuhan Lan
(Kyushu University), licensed under the Apache License 2.0; see
`programs/LICENSE`. These files are copied from commit `bb853b8` of
https://github.com/Z-928/Bugs4Q-NA. Changes: line endings normalized to LF; only
the files the README's three Qiskit tables link are included (the Cirq and Q#
bugs are left out, since no front end here reads them).

Cite the dataset as: P. Zhao, J. Zhao, Z. Miao, S. Lan, "Bugs4Q: A Benchmark of
Real Bugs for Quantum Programs", ASE 2021 (NIER track), arXiv:2108.09744. The
upstream repository has grown since the paper's 36 bugs.

## Updating

```bash
git clone https://github.com/Z-928/Bugs4Q-NA.git /tmp/Bugs4Q-NA
python benchmarks/bugs4q/sync_upstream.py /tmp/Bugs4Q-NA
pytest tests/benchmark
```

The script reads the README tables, resolves each link (a link may name a
directory or a file that is not there; either resolves to the one Python file
in that directory), and stops on any link it cannot resolve. A link that
resolves to the wrong file goes in `LINK_FIXES`; there is one today, for
Terra #7032, whose "Fixed" link repeats the buggy file.

## Comparing another tool

Run the tool over `programs/`, then record its SARIF output:

```bash
python benchmarks/run_bugs4q.py --import-sarif results.sarif --tool lintq \
    --label "LintQ (CodeQL)" --notes "LintQ-all.qls, CodeQL 2.11.2"
```

This writes `external/lintq.json`; every later run, from the command line or
the Benchmark page, adds the tool as a row of the comparison. Results are
matched to programs by path suffix, so a run over a Bugs4Q checkout works too.
A tool without SARIF output can write the file directly:

```json
{
  "tool": "mytool", "label": "My tool", "version": "1.0", "notes": "how it was run",
  "results": {
    "Terra-6000-7100/6571_Bug/bug_version.py": [
      {"line": 6, "code": "my-rule", "message": "what it said"}
    ]
  }
}
```

A program the file does not list is taken to have had no warnings.
