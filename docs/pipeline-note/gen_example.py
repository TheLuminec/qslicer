"""Figure 2: run worked_example.py through the real pipeline and draw what comes out.

Writes out/example_frag.json: three QDG panels (full graph with lint markers,
backward slice from the last instruction over every dependency type, and over
DATA_QUBIT only), the annotated source listing, the IR table, and the facts the
captions quote. Run through build.py.
"""
import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = HERE / "out"
sys.path.insert(0, str(REPO / "webapp"))
sys.path.insert(0, str(REPO / "scripts"))
import analysis  # noqa: E402

src = (HERE / "worked_example.py").read_text(encoding="utf-8")
r = analysis.analyze_source(src, "worked_example.py", language="qiskit-static",
                            lint_rules=["quick", "dependence", "lintq"])
assert r["ok"], r.get("error")
nodes, edges = r["nodes"], r["edges"]
tok = r["token"]
last = len(nodes) - 1
slice_all = set(analysis.slice_program(tok, last, "backward")["slice"])
slice_dq = set(analysis.slice_program(tok, last, "backward", ["DATA_QUBIT"])["slice"])
fwd3 = set(analysis.slice_program(tok, 3, "forward")["slice"])
lint_nodes = {}
for d in r["diagnostics"]:
    for n in d.get("nodes") or []:
        lint_nodes.setdefault(n, []).append(d["code"])

X0, DX = 96, 70
LANE0, DL = 34, 42
NQ = r["program"]["num_qubits"]; NC = r["program"]["num_clbits"]
CL0 = LANE0 + (NQ - 1) * DL + 52
DC = 20
W = X0 + last * DX + 60
H = CL0 + (NC - 1) * DC + 22
NW, NH = 46, 24


def nx_(i):
    return X0 + i * DX


def ny_(n):
    q = n["qubits"]
    return LANE0 + (sum(q) / len(q)) * DL


def lab(n):
    nm = n["name"]
    if nm == "measure":
        return "M"
    if nm == "if_else":
        return "if c%d" % n["condition_clbits"][0]
    return nm


TITLES = {
    "full": f"QDG of the worked example: {len(nodes)} nodes and {len(edges)} typed edges; "
            f"lint findings mark {', '.join('n%d' % i for i in sorted(lint_nodes)) or 'no node'}",
    "all": f"Backward slice from n{last} following every dependency type: "
           f"{len(slice_all)} of {len(nodes)} nodes",
    "dq": f"Backward slice from n{last} following DATA_QUBIT edges only: "
          f"{len(slice_dq)} of {len(nodes)} nodes",
}


def panel(mode, pid):
    insl = {"full": None, "all": slice_all, "dq": slice_dq}[mode]
    allowed = {"full": None, "all": None, "dq": {"DATA_QUBIT"}}[mode]
    o = [f'<svg class="qdg" viewBox="0 0 {W} {H}" role="img" aria-labelledby="{pid}-t">',
         f'<title id="{pid}-t">{TITLES[mode]}</title>']
    o.append("<defs>")
    for mk, cls in (("mdq", "a-dq"), ("mme", "a-me"), ("mdc", "a-dc")):
        for suf, extra in (("", ""), ("f", " faded")):
            o.append(f'<marker id="{pid}-{mk}{suf}" viewBox="0 0 8 8" refX="7" refY="4" '
                     f'markerWidth="7" markerHeight="7" orient="auto"><path class="{cls}{extra}" d="M0,0 L8,4 L0,8 z"/></marker>')
    o.append("</defs>")
    for q in range(NQ):
        y = LANE0 + q * DL
        o.append(f'<line class="wire" x1="{X0-34}" y1="{y}" x2="{W-14}" y2="{y}"/>')
        o.append(f'<text class="lane" x="{X0-42}" y="{y+4}" text-anchor="end">q{q}</text>')
    for c in range(NC):
        y = CL0 + c * DC
        o.append(f'<line class="cwire" x1="{X0-34}" y1="{y-1.5}" x2="{W-14}" y2="{y-1.5}"/>')
        o.append(f'<line class="cwire" x1="{X0-34}" y1="{y+1.5}" x2="{W-14}" y2="{y+1.5}"/>')
        o.append(f'<text class="lane" x="{X0-42}" y="{y+4}" text-anchor="end">c{c}</text>')
    for n in nodes:
        q = n["qubits"]
        if len(q) > 1:
            x = nx_(n["id"])
            ys = [LANE0 + k * DL for k in q]
            cls = "conn" + ("" if insl is None or n["id"] in insl else " out")
            o.append(f'<line class="{cls}" x1="{x}" y1="{min(ys)}" x2="{x}" y2="{max(ys)}"/>')
            for y in ys:
                o.append(f'<circle class="{cls} dot" cx="{x}" cy="{y}" r="3"/>')
    for e in edges:
        s, t, dep = e["source"], e["target"], e["dependency"]
        a, b = nodes[s], nodes[t]
        live = True
        if insl is not None:
            live = s in insl and t in insl and (allowed is None or dep in allowed)
        cls = {"DATA_QUBIT": "e-dq", "MEASUREMENT": "e-me", "DATA_CLASSICAL": "e-dc"}[dep]
        mk = {"DATA_QUBIT": "mdq", "MEASUREMENT": "mme", "DATA_CLASSICAL": "mdc"}[dep]
        if not live:
            cls += " faded"
            mk += "f"
        if dep == "DATA_QUBIT":
            x1, y1 = nx_(s) + NW / 2, ny_(a)
            x2, y2 = nx_(t) - NW / 2 - 2, ny_(b)
            mx = (x1 + x2) / 2
            d = f"M{x1},{y1} C{mx},{y1} {mx},{y2} {x2},{y2}"
        else:
            clbit = a["clbits"][0]
            off = -2.5 if dep == "MEASUREMENT" else 2.5
            yc = CL0 + clbit * DC + off
            dx = -5 if dep == "MEASUREMENT" else 5
            x1, x2 = nx_(s) + dx, nx_(t) + dx
            y1 = ny_(a) + NH / 2
            y2 = ny_(b) + NH / 2 + 2
            rr = 6
            d = (f"M{x1},{y1} L{x1},{yc-rr} Q{x1},{yc} {x1+rr},{yc} "
                 f"L{x2-rr},{yc} Q{x2},{yc} {x2},{yc-rr} L{x2},{y2}")
        o.append(f'<path class="edge {cls}" d="{d}" marker-end="url(#{pid}-{mk})"/>')
    for n in nodes:
        i = n["id"]
        x, y = nx_(i), ny_(n)
        cls = "node"
        if n["is_measurement"]:
            cls += " meas"
        if n["name"] == "if_else":
            cls += " cond"
        out = insl is not None and i not in insl
        if insl is not None:
            cls += " out" if out else " in"
        if mode == "full" and i in lint_nodes:
            cls += " lint"
        if insl is not None and i == last:
            o.append(f'<rect class="critring" x="{x-NW/2-5}" y="{y-NH/2-5}" width="{NW+10}" height="{NH+10}" rx="9"/>')
        o.append(f'<rect class="{cls}" x="{x-NW/2}" y="{y-NH/2}" width="{NW}" height="{NH}" rx="5"/>')
        o.append(f'<text class="nlab{" out" if out else ""}" x="{x}" y="{y+4}" text-anchor="middle">{html.escape(lab(n))}</text>')
        o.append(f'<text class="nid{" out" if out else ""}" x="{x-NW/2+1}" y="{y-NH/2-5}" text-anchor="start">n{i}</text>')
    if mode == "full":
        xs = [nx_(i) for i in sorted(lint_nodes)]
        y = LANE0 + 3 * DL
        codes = sorted({c for v in lint_nodes.values() for c in v})
        o.append(f'<text class="lintbadge" x="{sum(xs)/len(xs)}" y="{y+NH/2+15}" text-anchor="middle">{" + ".join(codes)}</text>')
    if insl is not None:
        o.append(f'<text class="crittag" x="{nx_(last)}" y="{ny_(nodes[last])+NH/2+17}" text-anchor="middle">criterion</text>')
    o.append("</svg>")
    return "\n".join(o)


via_calls = {}
by_line = {}
for n in nodes:
    s = n["source"]
    by_line.setdefault(s["line"], []).append((n["id"], s["via"]))
    for v in s["via"]:
        via_calls.setdefault(v, []).append(n["id"])
lint_lines = {}
for d in r["diagnostics"]:
    lint_lines.setdefault(d["line"], []).append(d["code"])
rows = []
for ln, text in enumerate(src.splitlines(), 1):
    tags = []
    own = by_line.get(ln, [])
    if own:
        ids = ", ".join(f"n{i}" for i, _ in own)
        vias = sorted({v for _, vs in own for v in vs})
        tags.append(f'<span class="tag-n">{ids}</span>' + (f'<span class="tag-v">via {",".join(map(str, vias))}</span>' if vias else ""))
    if ln in via_calls:
        tags.append(f'<span class="tag-v">call site of {", ".join("n%d" % i for i in via_calls[ln])}</span>')
    for c in lint_lines.get(ln, []):
        tags.append(f'<span class="tag-l">{c}</span>')
    hl = " hl" if own and any(i in slice_all for i, _ in own) else ""
    rows.append(f'<div class="ln{hl}"><span class="no">{ln}</span><code>{html.escape(text) or " "}</code><span class="tags">{" ".join(tags)}</span></div>')

ir = []
for n in nodes:
    s = n["source"]
    ir.append(f'<tr><td>n{n["id"]}</td><td><code>{html.escape(n["name"])}</code></td><td>{n["qubits"]}</td>'
              f'<td>{n["clbits"] or "–"}</td><td>{n["condition_clbits"] or "–"}</td><td>{s["line"]}</td>'
              f'<td>{", ".join(map(str, s["via"])) if s["via"] else "–"}</td></tr>')

out = {"panel_full": panel("full", "pa"), "panel_all": panel("all", "pb"), "panel_dq": panel("dq", "pc"),
       "listing": "\n".join(rows), "irtable": "\n".join(ir),
       "facts": {"slice_all": sorted(slice_all), "slice_dq": sorted(slice_dq), "fwd3": sorted(fwd3),
                 "n_edges": len(edges), "deps": [(d["name"], d["count"]) for d in r["dependency_types"]],
                 "diags": [(d["code"], d["line"], d.get("nodes"), d["message"]) for d in r["diagnostics"]],
                 "W": W, "H": H}}
out["facts"].update({"nodes": len(nodes), "last": last})
OUT.mkdir(exist_ok=True)
(OUT / "example_frag.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
print(f"example: {len(nodes)} nodes, {len(edges)} edges, slices {len(slice_all)} / {len(slice_dq)}")
