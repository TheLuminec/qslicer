"""The example circuits offered on the slicer page.

Sources live in ``webapp/examples/`` as ordinary Qiskit files, described by
``manifest.json`` in that directory. To add one: drop the ``.py`` file in and
add an entry to the manifest. A file with no manifest entry still appears,
grouped under "Unsorted", so nothing goes missing.

The sample circuits under ``Attachments/Sample Code`` are left alone: they are
what the command-line visualiser points at, and some of them are named after
something other than what they contain.

Note that this module and the ``examples/`` data directory share a name. A
plain module wins over a directory with no ``__init__.py``, so the import
resolves here -- but do not add an ``__init__.py`` to ``webapp/examples/``, or
it will shadow this file.
"""

import json
import os
from typing import Any, Dict, List, Optional

WEBAPP_DIR = os.path.dirname(os.path.abspath(__file__))
EXAMPLE_DIR = os.path.join(WEBAPP_DIR, "examples")
MANIFEST_PATH = os.path.join(EXAMPLE_DIR, "manifest.json")

UNSORTED = {"id": "unsorted", "title": "Unsorted"}

# Shipped inline so the page still has something to load if the directory is
# missing entirely.
_FALLBACK_SOURCE = """from qiskit import QuantumCircuit

qc = QuantumCircuit(2, 2)
qc.h(0)
qc.cx(0, 1)
qc.measure([0, 1], [0, 1])
"""

_FALLBACK_ENTRY = {
    "key": "builtin_bell",
    "file": "",
    "category": "basics",
    "title": "Bell pair (built in)",
    "blurb": "A four-line circuit kept inside the web app itself.",
    "highlight": "",
}


def _read_manifest() -> Dict[str, Any]:
    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {"categories": [], "examples": []}


SOURCE_SUFFIXES = (".py", ".qasm")

# Which Interpreter option an example needs. The page reads this when an
# example is picked, so the dropdown follows the file rather than the other way
# around.
INTERPRETER_BY_SUFFIX = {".py": "qiskit", ".qasm": "qasm", ".qasm3": "qasm"}


def interpreter_for(filename: str) -> str:
    return INTERPRETER_BY_SUFFIX.get(os.path.splitext(filename)[1].lower(), "qiskit")


def _files_on_disk() -> List[str]:
    if not os.path.isdir(EXAMPLE_DIR):
        return []
    return sorted(
        name for name in os.listdir(EXAMPLE_DIR)
        if name.endswith(SOURCE_SUFFIXES) and not name.startswith("_")
    )


def catalogue() -> List[Dict[str, Any]]:
    """Every example that exists on disk, in manifest order.

    Manifest entries whose file is missing are dropped; files missing from the
    manifest are appended with a title derived from the filename.
    """
    manifest = _read_manifest()
    on_disk = set(_files_on_disk())

    listed: List[Dict[str, Any]] = []
    claimed = set()

    for entry in manifest.get("examples", []):
        filename = entry.get("file", "")
        if filename not in on_disk:
            continue
        claimed.add(filename)
        listed.append({
            "key": entry.get("key") or os.path.splitext(filename)[0],
            "file": filename,
            "category": entry.get("category", UNSORTED["id"]),
            "title": entry.get("title") or os.path.splitext(filename)[0],
            "blurb": entry.get("blurb", ""),
            "highlight": entry.get("highlight", ""),
            "interpreter": interpreter_for(filename),
            "available": True,
        })

    for filename in sorted(on_disk - claimed):
        listed.append({
            "key": os.path.splitext(filename)[0],
            "file": filename,
            "category": UNSORTED["id"],
            "title": os.path.splitext(filename)[0].replace("_", " ").capitalize(),
            "blurb": "Not listed in manifest.json.",
            "highlight": "",
            "interpreter": interpreter_for(filename),
            "available": True,
        })

    if not listed:
        listed.append(dict(_FALLBACK_ENTRY, available=True))
    return listed


def grouped() -> List[Dict[str, Any]]:
    """The catalogue arranged into the manifest's categories, for optgroups."""
    manifest = _read_manifest()
    categories = manifest.get("categories") or []
    order = {category["id"]: index for index, category in enumerate(categories)}
    titles = {category["id"]: category["title"] for category in categories}

    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for entry in catalogue():
        buckets.setdefault(entry["category"], []).append(entry)

    groups = []
    for category_id in sorted(buckets, key=lambda cid: order.get(cid, len(order))):
        groups.append({
            "id": category_id,
            "title": titles.get(category_id, UNSORTED["title"]),
            "examples": buckets[category_id],
        })
    return groups


def load(key: str) -> Optional[Dict[str, Any]]:
    """Return ``{key, title, filename, source, ...}`` for one example, or None."""
    if key == _FALLBACK_ENTRY["key"]:
        return dict(_FALLBACK_ENTRY, filename="bell_pair.py", source=_FALLBACK_SOURCE)

    for entry in catalogue():
        if entry["key"] != key:
            continue
        if not entry["file"]:
            return dict(entry, filename="bell_pair.py", source=_FALLBACK_SOURCE)
        try:
            with open(os.path.join(EXAMPLE_DIR, entry["file"]), "r", encoding="utf-8") as handle:
                source = handle.read()
        except OSError:
            return None
        return dict(entry, filename=entry["file"], source=source)
    return None
