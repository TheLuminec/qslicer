"""Run a benchmark of buggy and fixed programs from the command line.

    python benchmarks/run_benchmark.py                          # Bugs4Q, default rules (as the site)
    python benchmarks/run_benchmark.py --manifest path/to/manifest.json
    python benchmarks/run_benchmark.py --rules quick,dependence # whole linters
    python benchmarks/run_benchmark.py --rules quick/QS005,dependence/QD01
    python benchmarks/run_benchmark.py --json report.json       # the full report
    python benchmarks/run_benchmark.py --list-datasets
    python benchmarks/run_benchmark.py --list-rules
    python benchmarks/run_benchmark.py --import-sarif results.sarif --tool mytool --label "My tool"

``--manifest`` takes a manifest path, or the name of a folder under
``benchmarks/`` that holds one (default: ``bugs4q``); ``webapp/benchmark.py``
describes the manifest format. ``--rules`` takes the same rule ids the web
app's rule picker stores, so a command-line run and a run from the Benchmark
page grade the same thing. ``--import-sarif`` records another tool's results
(any SARIF log, for example from a tool run over the dataset's ``programs/``)
in the dataset's ``external/`` folder, after which every run on that dataset
compares against it. Exit status is 0 unless the arguments are wrong.
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (os.path.join(ROOT, "webapp"), os.path.join(ROOT, "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)

import benchmark  # noqa: E402
import linters  # noqa: E402


def _pct(value):
    return "-" if value is None else f"{100 * value:.0f}%"


def print_report(report, show_bugs=True, out=sys.stdout):
    data = report["dataset"]
    at = f" @ {data['commit'][:7]}" if data["commit"] else ""
    print(f"{data['name']}{at}: {data['bugs']} {data['description'] or 'bugs'} "
          f"({', '.join(f'{n} {k}' for k, n in sorted(data['kinds'].items()))})", file=out)
    print(f"Rules: {len(report['selection'])} selected across "
          f"{', '.join(l['label'] for l in report['linters']) or 'no linter'}; "
          f"QPDG profile {report['profile']}", file=out)
    if report["front_end"]:
        fe = report["front_end"]
        print(f"Static front end read {fe['read']} of {fe['attempted']} programs", file=out)
    print(file=out)

    header = ("Tool", "Detected", "No rewrites", "Hit", "Warned", "Warnings buggy/fixed",
              "On fault line")
    rows = []
    for config in report["configs"]:
        t = report["totals"][config["id"]]
        rows.append((config["label"], f"{t['detected']}/{t['gradable']}",
                     str(t["detected_no_rewrites"]), str(t["hit"]), str(t["warned"]),
                     f"{t['warnings_buggy']}/{t['warnings_fixed']}",
                     _pct(t["warning_precision"])))
    widths = [max(len(r[i]) for r in rows + [header]) for i in range(len(header))]
    for row in [header] + rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)), file=out)

    print("\nPer rule (buggy / fixed firings, hits, bugs detected):", file=out)
    for rule in report["rules"]:
        if rule["buggy"] or rule["fixed"]:
            print(f"  {rule['rule']:<18} {rule['title']:<30} {rule['buggy']:>3} / "
                  f"{rule['fixed']:<3} hits {rule['hits']:<3} detected {rule['detected']}",
                  file=out)

    if show_bugs:
        print("\nDetected bugs:", file=out)
        for bug in report["bugs"]:
            grade = bug["grades"]["selection"]
            if grade["detected"]:
                print(f"  {bug['key']:<18} {bug['reference']:<10} {bug['truth']['kind']:<8} "
                      f"{', '.join(grade['detected_by'])} at line(s) "
                      f"{', '.join(map(str, grade['hit_lines']))}", file=out)
    print(f"\n({report['elapsed']}s)", file=out)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Grade lint rules on a benchmark of "
                                                 "buggy and fixed programs.")
    parser.add_argument("--manifest", default=benchmark.DEFAULT_DATASET,
                        help="a manifest path, or a dataset folder under benchmarks/ "
                             f"(default: {benchmark.DEFAULT_DATASET})")
    parser.add_argument("--rules", help="comma-separated rule or linter ids (default: the "
                                        "site's default selection)")
    parser.add_argument("--profile", default="baseline", help="QPDG rule profile")
    parser.add_argument("--json", metavar="PATH", help="write the full report here")
    parser.add_argument("--no-external", action="store_true",
                        help="leave recorded external tools out of the comparison")
    parser.add_argument("--quiet", action="store_true", help="omit the per-bug list")
    parser.add_argument("--list-datasets", action="store_true",
                        help="list the datasets under benchmarks/ and exit")
    parser.add_argument("--list-rules", action="store_true", help="list every rule id and exit")
    parser.add_argument("--import-sarif", metavar="SARIF",
                        help="record a SARIF log as an external tool and exit")
    parser.add_argument("--tool", help="with --import-sarif: short id for the tool")
    parser.add_argument("--label", default="", help="with --import-sarif: display name")
    parser.add_argument("--notes", default="", help="with --import-sarif: how it was run")
    args = parser.parse_args(argv)

    if args.list_datasets:
        for name in benchmark.dataset_ids():
            data = benchmark.summary(name)
            print(f"{name:<12} {data['name']}: {data['bugs']} {data['description'] or 'bugs'}")
        return 0

    if args.list_rules:
        for entry in linters.catalog():
            state = "default" if entry["default"] else "opt-in"
            if not entry["available"]:
                state = f"unavailable: {entry['unavailable_reason']}"
            print(f"{entry['id']}  {entry['label']} ({state})")
            for rule in entry["rules"]:
                print(f"  {rule['id']:<18} {rule['severity']:<8} {rule['title']}")
        return 0

    try:
        dataset = benchmark.load(args.manifest)
    except KeyError as exc:
        parser.error(f"{exc.args[0]} (see --list-datasets)")

    if args.import_sarif:
        if not args.tool:
            parser.error("--import-sarif needs --tool")
        record = benchmark.import_sarif(args.import_sarif, args.tool, args.label,
                                        notes=args.notes, dataset=dataset)
        os.makedirs(dataset.external_dir, exist_ok=True)
        target = os.path.join(dataset.external_dir, f"{args.tool}.json")
        with open(target, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(record, handle, indent=2)
            handle.write("\n")
        matched = sum(len(v) for v in record["results"].values())
        print(f"{matched} results in {len(record['results'])} programs -> {target} "
              f"({record['unmatched_results']} outside {dataset.name} dropped)")
        return 0

    selection = None
    if args.rules:
        selection = [r.strip() for r in args.rules.split(",") if r.strip()]
        unknown = [r for r in selection if not linters.normalize_selection([r])]
        if unknown:
            parser.error(f"unknown rule ids: {', '.join(unknown)} (see --list-rules)")
    report = benchmark.run(selection, profile=args.profile,
                           include_external=not args.no_external, use_cache=False,
                           dataset=dataset)
    print_report(report, show_bugs=not args.quiet)
    if args.json:
        with open(args.json, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
