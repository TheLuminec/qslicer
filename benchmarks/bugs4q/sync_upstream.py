"""Copy the Qiskit bugs of a Bugs4Q checkout into this repository.

    git clone https://github.com/Z-928/Bugs4Q-NA.git /tmp/Bugs4Q-NA
    python benchmarks/bugs4q/sync_upstream.py /tmp/Bugs4Q-NA

Bugs4Q's README lists every bug in three Qiskit tables (GitHub issues, Stack
Overflow, Quantum Computing Stack Exchange), each row linking a buggy and a
fixed program. This script reads those tables, resolves the links to files,
copies the files under ``programs/`` at their upstream paths, and writes
``manifest.json``. The Cirq and Q# tables are skipped: no front end here reads
them.

A few README links do not point at the right file; ``LINK_FIXES`` corrects
them, and every other unresolved link is reported rather than guessed.
Nothing is executed, and running the script twice gives the same output.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAMS_DIR = os.path.join(HERE, "programs")
MANIFEST = os.path.join(HERE, "manifest.json")
UPSTREAM_URL = "https://github.com/Z-928/Bugs4Q-NA"

# README section heading -> (key prefix, label)
SECTIONS = {
    "Qiskit Reproduceable Bugs from GitHub": ("github", "GitHub issue"),
    "Qiskit Reproduceable Bugs from Stackflow": ("stackoverflow", "Stack Overflow"),
    "Qiskit Reproduceable Bugs from StackExchange": ("stackexchange", "Stack Exchange"),
}

# (key, column) -> the upstream path the link should have named.
LINK_FIXES = {
    # The README's "Fixed" link for Terra #7032 repeats the buggy file.
    ("github-15", "fixed"): "Terra-6000-7100/7032_Fixed/fixed_version.py",
}

_LINK = re.compile(r"\[[^\]]*\]\(([^)]*)\)")


def _cells(row):
    """A table row's cells; ``\\|`` is a literal pipe inside a cell."""
    cells = re.split(r"(?<!\\)\|", row.strip().strip("|"))
    return [cell.replace("\\|", "|").strip() for cell in cells]


def _link(cell):
    match = _LINK.search(cell)
    return match.group(1).strip() if match else ""


def _text(cell):
    """A cell with its Markdown links reduced to their text."""
    return re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cell).replace("`", "").strip()


def _resolve(root, link):
    """The repository-relative file a README link names, or None.

    Links are relative (``./A/b.py``, ``A/b.py``, ``/A/b.py``) and sometimes
    name a directory, or a file name that is not there; either resolves to the
    single Python file in that directory.
    """
    if not link or link.startswith("http"):
        return None
    rel = unquote(link).lstrip("./").lstrip("/")
    path = os.path.join(root, rel)
    if os.path.isfile(path):
        return rel.replace(os.sep, "/")
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    if os.path.isdir(folder):
        files = sorted(f for f in os.listdir(folder) if f.endswith(".py"))
        if len(files) == 1:
            return os.path.relpath(os.path.join(folder, files[0]), root).replace(os.sep, "/")
    return None


def read_tables(root):
    """Every Qiskit bug row of the README, as manifest entries (no file copies)."""
    with open(os.path.join(root, "README.md"), encoding="utf-8") as handle:
        lines = handle.read().splitlines()

    bugs, problems = [], []
    section = None
    header = None
    for line in lines:
        if line.startswith("## "):
            section = SECTIONS.get(line[3:].strip())
            header = None
            continue
        if section is None or not line.lstrip().startswith("|"):
            continue
        cells = _cells(line)
        if header is None:
            header = [_text(c).lower() for c in cells]
            continue
        if set("".join(cells)) <= set("-: "):
            continue
        row = dict(zip(header, cells))
        prefix, label = section
        number = _text(row.get("bug id", ""))
        key = f"{prefix}-{number}"
        # GitHub rows link the issue in "issue no"; Stack Overflow rows in
        # "bug id"; Stack Exchange rows in "id".
        issue_cell = row.get("issue no") or row.get("id") or row.get("bug id", "")
        issue_url = _link(issue_cell)
        reference = _text(row.get("issue no") or row.get("id") or "")
        if not reference and issue_url:
            reference = "#" + issue_url.rstrip("/").split("/questions/")[-1].split("/")[0]

        entry = {
            "key": key,
            "source": label,
            "number": int(number) if number.isdigit() else number,
            "reference": reference,
            "url": issue_url,
            "type": _text(row.get("type", "")),
            "status": _text(row.get("status", "")),
            "version": _text(row.get("version", "")),
            "registered": _text(row.get("registered", "")),
            "resolved": _text(row.get("resolved", "")),
        }
        for column in ("buggy", "fixed", "modify"):
            fix = LINK_FIXES.get((key, column))
            path = fix or _resolve(root, _link(row.get(column, "")))
            if path is None and column != "modify":
                problems.append(f"{key}: no file for the {column} link {row.get(column)!r}")
            entry[column if column != "modify" else "mod"] = path
        if entry["mod"] in (entry["buggy"], entry["fixed"]):
            entry["mod"] = None
        bugs.append(entry)
    return bugs, problems


def upstream_commit(root):
    try:
        return subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("checkout", help="a clone of " + UPSTREAM_URL)
    args = parser.parse_args(argv)
    root = os.path.abspath(args.checkout)

    bugs, problems = read_tables(root)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1

    if os.path.isdir(PROGRAMS_DIR):
        shutil.rmtree(PROGRAMS_DIR)
    for bug in bugs:
        for column in ("buggy", "fixed", "mod"):
            rel = bug[column]
            if not rel:
                continue
            target = os.path.join(PROGRAMS_DIR, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            # Text copy: line endings normalize to LF; line numbers do not move.
            with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as src:
                text = src.read()
            with open(target, "w", encoding="utf-8", newline="\n") as dst:
                dst.write(text)
    shutil.copyfile(os.path.join(root, "LICENSE"), os.path.join(PROGRAMS_DIR, "LICENSE"))

    manifest = {
        "name": "Bugs4Q",
        "description": "real Qiskit bugs",
        "upstream": UPSTREAM_URL,
        "commit": upstream_commit(root),
        "license": "Apache-2.0",
        "bugs": bugs,
    }
    with open(MANIFEST, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(f"{len(bugs)} bugs, commit {manifest['commit'][:7] or '?'} -> {MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
