"""Build the pipeline note: out/quantum-slicer-pipeline.html.

Steps, each a separate process so a failure says which one:

1. the Bugs4Q benchmark with quick + dependence + lintq -> out/bench.json
   (skipped with --no-bench when out/bench.json already exists)
2. gen_example.py -> out/example_frag.json (Figure 2)
3. gen_figs.py    -> out/figs_frag.json    (Figures 1, 3, 4)
4. assemble template.html with the fragments, Tables 2 and 3, and the facts
   the prose quotes (commit, Bugs4Q counts, worked-example sizes)

Run it with the project's interpreter, which has Qiskit 2.6.0:

    .venv/Scripts/python.exe docs/pipeline-note/build.py
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = HERE / "out"
RULES = "quick,dependence,lintq"

sys.path.insert(0, str(REPO / "webapp"))
sys.path.insert(0, str(REPO))
from qslicer.lint import registry as linters  # noqa: E402


def step(name, args):
    print(f"[{name}]", flush=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    subprocess.run([sys.executable, *args], cwd=REPO, env=env, check=True)


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True).stdout.strip()


# What each rule reads to decide (D) or only to excuse or locate a finding (L).
# Keep in step with the rules: a new rule without an entry here fails the build.
COLS = ["ast", "flow", "qiskit", "fail", "ir", "qdg"]
READS = {f"QS{n:03d}": {"ast": "D"} for n in range(18)}
for code in ("QS005", "QS012", "QS013"):
    READS[code] = {"ast": "D", "qiskit": "D"}
READS["QS014"] = {"fail": "D"}
READS.update({
    "QD01": {"ir": "D", "qdg": "D"},
    "QD02": {"ir": "D"},
    "QD03": {"ir": "D", "ast": "L"},
    "QD05": {"ir": "D", "ast": "L"},
    "QD06": {"ir": "D", "ast": "L"},
    "QD07": {"ir": "D", "ast": "L"},
    "QD08": {"ir": "D", "ast": "L"},
    "QD09": {"ir": "D"},
    "QD10": {"ir": "D"},
    "QD11": {"ir": "D", "ast": "D"},
    "QD12": {"ir": "D", "ast": "L"},
})
for n in range(1, 11):
    READS[f"LQ{n:02d}"] = {"ast": "D", "flow": "D"}

GROUPS = {"quick": "Quick checks · qslicer/lint/quick_checks.py · on by default",
          "dependence": "Dependence checks · qslicer/lint/plugins/dependence_checks.py · on by default",
          "lintq": "LintQ port · qslicer/lint/lintq/port.py · off by default "
                   "(the CodeQL original has the same codes)"}

RESULT_ROWS = [("selection", "All three combined"), ("quick", "Quick checks"),
               ("dependence", "Dependence checks"), ("lintq", "LintQ (port)"),
               ("external:lintq-codeql", "LintQ (original, recorded run)")]


def rule_table(bench):
    stats = {r["rule"]: r for r in bench["rules"]}
    rows = []
    for entry in linters.catalog():
        if entry["id"] not in GROUPS:
            continue
        rows.append(f'<tr class="grp"><td colspan="11">{GROUPS[entry["id"]]}</td></tr>')
        for rule in entry["rules"]:
            if rule["code"] == "LQ00":          # the port's "program too large" note
                continue
            if rule["code"] not in READS:
                sys.exit(f"build.py: add {rule['code']} to READS (what the rule reads)")
            reads = READS[rule["code"]]
            cells = "".join(
                '<td class="c">' + {"D": '<span class="dot" title="decides"></span>',
                                    "L": '<span class="dot lt" title="excuses or locates"></span>'
                                    }.get(reads.get(c), "") + "</td>"
                for c in COLS)
            s = stats.get(rule["id"], {"buggy": 0, "fixed": 0, "detected": 0})
            det = s["detected"] or '<span class="muted">0</span>'
            rows.append(f'<tr><td><code>{rule["code"]}</code></td><td>{rule["title"]}</td>'
                        f'<td><span class="sev {rule["severity"]}">{rule["severity"]}</span></td>{cells}'
                        f'<td class="num">{s["buggy"]} / {s["fixed"]}</td><td class="num">{det}</td></tr>')
    return "\n".join(rows)


def _pct(num, den):
    return f"{round(100 * num / den)}%" if den else "–"


def no_rewrite_precision(bench, cid):
    """Warning precision over the bugs whose fix is not a rewrite. In a rewrite almost
    every line is a fault line, so its warnings hit by default and inflate precision."""
    config = next(c for c in bench["configs"] if c["id"] == cid)
    rules = set(config.get("rules", []))
    hits = total = 0
    for bug in bench["bugs"]:
        if bug["truth"]["kind"] == "rewrite":
            continue
        if config["kind"] == "external":
            buggy = bug["external_warnings"].get(cid, {}).get("buggy", [])
        else:
            buggy = [w for w in bug["warnings"]["buggy"] if w["rule"] in rules]
        total += len(buggy)
        hits += sum(1 for w in buggy if w.get("line") in bug["truth"]["lines"])
    return _pct(hits, total)


def results_table(bench):
    rows = []
    no_rewrite = sum(1 for b in bench["bugs"] if b["truth"]["kind"] != "rewrite")
    for cid, label in RESULT_ROWS:
        t = bench["totals"].get(cid)
        if t is None:
            continue
        bar = round(100 * t["detected"] / t["bugs"] * 2.2)
        bold = ' style="font-weight:600"' if cid == "selection" else ""
        prec = f'{round(100 * t["warning_precision"])}%' if t["warning_precision"] is not None else "–"
        rows.append(f'<tr{bold}><td>{label}</td>'
                    f'<td class="num">{t["detected"]} / {t["bugs"]}</td>'
                    f'<td class="num">{t["detected_no_rewrites"]} / {no_rewrite}</td>'
                    f'<td class="num">{t["hit"]}</td><td class="num">{t["warned"]}</td>'
                    f'<td class="num">{t["warnings_buggy"]} / {t["warnings_fixed"]}</td>'
                    f'<td class="num">{prec}</td><td class="num">{no_rewrite_precision(bench, cid)}</td>'
                    f'<td><span class="bar" style="width:{bar}px"></span></td></tr>')
    return "\n".join(rows)


def by_source(bench):
    names = {"GitHub issue": "GitHub-issue bugs", "Stack Exchange": "Quantum Computing Stack Exchange bugs",
             "Stack Overflow": "Stack Overflow bugs"}
    total, found = Counter(), Counter()
    for bug in bench["bugs"]:
        total[bug["source"]] += 1
        found[bug["source"]] += bug["grades"]["selection"]["detected"]
    parts = [f"{found[s]} of {total[s]} {names.get(s, s + ' bugs')}" for s in total]
    return ", ".join(parts[:-1]) + " and " + parts[-1] if len(parts) > 1 else parts[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-bench", action="store_true",
                    help="reuse out/bench.json instead of re-running the benchmark")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    bench_path = OUT / "bench.json"
    if not (args.no_bench and bench_path.exists()):
        step("benchmark", ["benchmarks/run_benchmark.py", "--rules", RULES, "--json", str(bench_path)])
    step("figure 2", [str(HERE / "gen_example.py")])
    step("figures 1, 3, 4", [str(HERE / "gen_figs.py")])

    ex = json.loads((OUT / "example_frag.json").read_text(encoding="utf-8"))
    figs = json.loads((OUT / "figs_frag.json").read_text(encoding="utf-8"))
    bench = json.loads(bench_path.read_text(encoding="utf-8"))
    sel = bench["totals"]["selection"]
    kinds = Counter(b["truth"]["kind"] for b in bench["bugs"])
    facts = ex["facts"]
    deps = [(name, count) for name, count in facts["deps"] if count]
    catalog = linters.catalog()
    import qiskit

    subs = {
        "{{FIG_SYSTEM}}": figs["fig_system"], "{{FIG_SEQUENCE}}": figs["fig_sequence"],
        "{{FIG_GRADING}}": figs["fig_grading"],
        "{{PANEL_FULL}}": ex["panel_full"], "{{PANEL_ALL}}": ex["panel_all"], "{{PANEL_DQ}}": ex["panel_dq"],
        "{{LISTING}}": ex["listing"], "{{IRTABLE}}": ex["irtable"],
        "{{RULETABLE}}": rule_table(bench), "{{RESULTS}}": results_table(bench),
        "{{COMMIT}}": git("rev-parse", "--short", "HEAD"), "{{BRANCH}}": git("branch", "--show-current"),
        "{{QISKIT}}": qiskit.__version__,
        "{{DATE}}": datetime.date.today().strftime("%d %b %Y").lstrip("0"),
        "{{LINTERS}}": str(len(catalog)), "{{LINTERS_ON}}": str(sum(1 for e in catalog if e["default"])),
        # LQ00 is the port's "program too large" note, not a check; Table 2 leaves it out too
        "{{RULE_CODES}}": str(len({r["code"] for e in catalog for r in e["rules"]} - {"LQ00"})),
        "{{B_DETECTED}}": str(sel["detected"]), "{{B_BUGS}}": str(sel["bugs"]),
        "{{B_DETECTED_NR}}": str(sel["detected_no_rewrites"]),
        "{{B_NR}}": str(sum(1 for b in bench["bugs"] if b["truth"]["kind"] != "rewrite")),
        "{{B_PREC_NR}}": no_rewrite_precision(bench, "selection"),
        "{{B_PREC}}": f'{round(100 * sel["warning_precision"])}%',
        "{{B_EDITS}}": str(kinds["edit"]), "{{B_REWRITES}}": str(kinds["rewrite"]),
        "{{B_OMISSIONS}}": str(kinds["omission"]),
        "{{B_READ}}": str(bench["front_end"]["read"]), "{{B_PROGRAMS}}": str(bench["front_end"]["attempted"]),
        "{{B_ELAPSED}}": f'{bench["elapsed"]:.1f}', "{{B_BY_SOURCE}}": by_source(bench),
        "{{EX_NODES}}": str(facts["nodes"]), "{{EX_EDGES}}": str(facts["n_edges"]),
        "{{EX_EDGE_TYPES}}": ", ".join(f"{c} {n}" for n, c in deps),
        "{{EX_LAST}}": str(facts["last"]),
        "{{EX_SLICE_ALL}}": str(len(facts["slice_all"])), "{{EX_SLICE_DQ}}": str(len(facts["slice_dq"])),
    }
    page = (HERE / "template.html").read_text(encoding="utf-8")
    for key, value in subs.items():
        if key not in page:
            sys.exit(f"build.py: {key} is not in template.html")
        page = page.replace(key, value)
    if "{{" in page:
        sys.exit("build.py: template.html has a placeholder build.py does not fill")
    target = OUT / "quantum-slicer-pipeline.html"
    target.write_text(page, encoding="utf-8")
    print(f"wrote {target.relative_to(REPO)} ({len(page) // 1024} KiB)")


if __name__ == "__main__":
    main()
