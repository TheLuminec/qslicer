"""Figures 1, 3 and 4: system overview, request sequence and benchmark grading.

The layouts are hand-placed on a grid. ``Fig.box`` prints an OVERFLOW or TOO TALL
warning when a line of text is estimated not to fit its box, so run this after
any wording change and fix what it reports. Figure 4 reads its counts from
out/bench.json. Writes out/figs_frag.json. Run through build.py.
"""
import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
sys.path.insert(0, str(HERE))

LEAD = 15  # line height for body text inside boxes


def esc(s):
    return html.escape(s, quote=True)


class Fig:
    def __init__(self, pid):
        self.pid = pid
        self.o = []

    def add(self, s):
        self.o.append(s)

    def rect(self, x, y, w, h, cls="box", rx=6):
        self.add(f'<rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}"/>')

    def text(self, x, y, s, cls="t", anchor="start"):
        self.add(f'<text class="{cls}" x="{x}" y="{y}" text-anchor="{anchor}">{esc(s)}</text>')

    def box(self, x, y, w, h, title, lines=(), cls="box", tcls="bh", pad=10, sub=None):
        """A box with a title and body lines. A line starting with '`' is set in mono."""
        self.rect(x, y, w, h, cls)
        ty = y + 19
        self.text(x + pad, ty, title, tcls)
        if len(title) * 7.3 > w - 2 * pad + 4:
            print(f"OVERFLOW {self.pid} title {title!r}")
        if sub:
            ty += 14
            self.text(x + pad, ty, sub, "sub")
        ty += LEAD + 2
        for line in lines:
            if line == "":
                ty += 6
                continue
            c = "t"
            if line.startswith("`"):
                c, line = "m", line[1:]
            elif line.startswith("~"):
                c, line = "tm", line[1:]
            est = len(line) * (6.65 if c == "m" else 6.0)
            if est > w - 2 * pad + 4:
                print(f"OVERFLOW {self.pid} [{title}] {line!r}: ~{est:.0f}px > {w - 2*pad}px")
            self.text(x + pad, ty, line, c)
            ty += LEAD
        if ty - LEAD + 8 > y + h:
            print(f"TOO TALL {self.pid} [{title}]: text ends {ty - LEAD + 8:.0f} > box {y + h}")
        return ty

    def arrow(self, pts, cls="ar", label=None, lx=None, ly=None, lanchor="middle", lcls="al", marker=None):
        marker = marker or {"ar": "ah", "ar-l": "ahl", "ar-loc": "ahl", "ar-r": "ahr", "ar-alt": "ahb"}[cls]
        d = "M" + " L".join(f"{x},{y}" for x, y in pts)
        self.add(f'<path class="{cls}" d="{d}" marker-end="url(#{self.pid}-{marker})"/>')
        if label:
            if lx is None:
                (x1, y1), (x2, y2) = pts[0], pts[1]
                lx, ly = (x1 + x2) / 2, (y1 + y2) / 2 - 5
            for k, part in enumerate(label.split("\n")):
                self.text(lx, ly + k * 13, part, lcls, lanchor)

    def badge(self, x, y, n):
        self.add(f'<circle class="step" cx="{x}" cy="{y}" r="9"/>')
        self.text(x, y + 4, str(n), "stepn", "middle")

    def defs(self):
        p = self.pid
        return ("<defs>"
                f'<marker id="{p}-ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path class="ahp" d="M0,0 L10,5 L0,10 z"/></marker>'
                f'<marker id="{p}-ahl" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path class="ahp-l" d="M0,0 L10,5 L0,10 z"/></marker>'
                f'<marker id="{p}-ahr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path class="ahp-r" d="M0,0 L10,5 L0,10 z"/></marker>'
                f'<marker id="{p}-ahb" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path class="ahp-b" d="M0,0 L10,5 L0,10 z"/></marker>'
                "</defs>")

    def svg(self, w, h, label):
        return (f'<svg class="fig-svg" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(label)}">'
                + self.defs() + "\n".join(self.o) + "</svg>")


def doc(f, x, y, w, h, title, sub):
    fold = 14
    f.add(f'<path class="doc" d="M{x},{y} L{x+w-fold},{y} L{x+w},{y+fold} L{x+w},{y+h} L{x},{y+h} Z"/>')
    f.add(f'<path class="docfold" d="M{x+w-fold},{y} L{x+w-fold},{y+fold} L{x+w},{y+fold}"/>')
    f.text(x + 10, y + 26, title, "bh")
    for k, s in enumerate(sub):
        f.text(x + 10, y + 44 + k * LEAD, s, "t")


# ---------------------------------------------------------------------------
# Figure 1: system overview (laid out in fig1_system.py)
# ---------------------------------------------------------------------------

def system():
    from fig1_system import system as layout
    return layout(Fig)


# ---------------------------------------------------------------------------
# Figure 4: request sequence
# ---------------------------------------------------------------------------

def sequence():
    f = Fig("f4")
    lanes = ["Browser", "app.py", "analysis.py", "registry.py", "static parser", "QDG · slicer", "LRU cache"]
    subs = ["slicer.js", "routes", "analyze_source", "LintContext", "Qiskit / OpenQASM", "qdg.py · slicer.py", "_CACHE (24)"]
    X0, DX = 90, 186
    W = X0 + DX * (len(lanes) - 1) + 100
    xs = [X0 + DX * k for k in range(len(lanes))]
    top = 20
    for x, name, sub in zip(xs, lanes, subs):
        f.rect(x - 78, top, 156, 44, "box box-key", 6)
        f.text(x, top + 19, name, "bh", "middle")
        f.text(x, top + 35, sub, "m", "middle")
    msgs = [
        ("sec", "POST /api/analyze"),
        (0, 1, "{source, filename, language, lint_rules}"),
        (1, 1, "validate: types, language, ≤ 256 KiB"),
        (1, 2, "analyze_source()"),
        (2, 2, "refuse qiskit / python / py; source_kind()"),
        (2, 3, "linters.run(selection)"),
        (3, 4, "_static_parse, 5 s, once"),
        (4, 3, "program or failure", "ret"),
        (3, 3, "each linter; tag; sort by line"),
        (3, 2, "diagnostics", "ret"),
        (2, 1, "QS000 present → {ok: false, syntax_error}", "alt"),
        (2, 4, "_parse_source, 20 s"),
        (4, 2, "QuantumProgram + notes (QS200)", "ret"),
        (2, 5, "QDG(program, rules)"),
        (2, 6, "put(token, {qdg, program})"),
        (2, 2, "nodes, edges, entanglement, chips; attach_lint_nodes"),
        (2, 0, "payload, HTTP 200 with ok flag", "ret"),
        ("sec", "click a node · POST /api/slice"),
        (0, 1, "{token, node_idx, direction, dep_types}"),
        (1, 2, "slice_program()"),
        (2, 6, "get(token)"),
        (6, 2, "qdg, or miss → stale_token (409)", "ret"),
        (2, 5, "backward_slice / forward_slice"),
        (5, 2, "set of node ids", "ret"),
        (2, 0, "{slice, size, total}", "ret"),
    ]
    y = top + 44 + 30
    rows = []
    for m in msgs:
        if m[0] == "sec":
            rows.append(("sec", y, m[1]))
            y += 34
        else:
            rows.append(("msg", y, m))
            y += 28
    H = y + 10
    for x in xs:
        f.add(f'<line class="life" x1="{x}" y1="{top+44}" x2="{x}" y2="{H-8}"/>')
    for kind, y, m in rows:
        if kind == "sec":
            f.rect(14, y - 16, W - 28, 24, "secband", 4)
            f.text(24, y, m, "sect")
            continue
        a, b, label = m[0], m[1], m[2]
        style = m[3] if len(m) > 3 else ""
        if a == b:
            x = xs[a]
            f.add(f'<path class="ar" d="M{x},{y-6} L{x+22},{y-6} L{x+22},{y+6} L{x+4},{y+6}" marker-end="url(#f4-ah)"/>')
            f.text(x + 30, y + 4, label, "al", "start")
            continue
        x1, x2 = xs[a], xs[b]
        cls = {"ret": "ar-r", "alt": "ar-alt", "": "ar"}[style]
        f.arrow([(x1, y), (x2 + (-4 if x2 > x1 else 4), y)], cls, None)
        lx = (x1 + x2) / 2
        f.text(lx, y - 5, label, "al" + (" alt" if style == "alt" else ""), "middle")
    return f.svg(W, H, "Sequence of one analysis request and one slice request: the linters run "
                       "first on their own static parse, a syntax error stops the request, the main "
                       "parse builds the QDG, which is cached under a token; a slice request reuses the "
                       "cached QDG and never re-parses.")


# ---------------------------------------------------------------------------
# Figure 5: grading as nested sets
# ---------------------------------------------------------------------------

def grading(totals):
    f = Fig("f5")
    W, H = 1160, 400
    # left: inputs
    doc(f, 20, 40, 150, 100, "buggy.py", ["warnings W_b", "from the selected", "rules"])
    doc(f, 20, 180, 150, 100, "fixed.py", ["warnings W_f"])
    f.box(200, 110, 210, 120, "ground_truth", [
        "difflib.SequenceMatcher on",
        "whitespace-stripped lines",
        "fault lines F ⊆ buggy lines",
        "edit · rewrite (> 50%) ·",
        "omission (neighbours) · none",
    ], "box")
    f.arrow([(170, 90), (200, 150)], "ar")
    f.arrow([(170, 230), (200, 190)], "ar")
    # nested sets
    t = totals
    f.rect(450, 20, 690, 360, "set set1", 14)
    f.text(470, 46, "warned", "bh")
    f.text(470, 64, "W_b ≠ ∅", "m")
    f.text(1120, 46, f"{t['warned']} / {t['bugs']}", "big", "end")
    f.rect(500, 90, 610, 270, "set set2", 12)
    f.text(520, 116, "hit", "bh")
    f.text(520, 134, "some w ∈ W_b has line(w) ∈ F", "m")
    f.text(1090, 116, f"{t['hit']} / {t['bugs']}", "big", "end")
    f.rect(550, 160, 530, 180, "set set3", 10)
    f.text(570, 186, "detected", "bh")
    f.text(570, 204, "a hit whose rule r fires fewer times", "m")
    f.text(570, 220, "on the fix:  |W_f(r)| < |W_b(r)|", "m")
    f.text(1060, 186, f"{t['detected']} / {t['bugs']}", "big", "end")
    f.text(570, 252, f"excluding rewrites: {t['detected_no_rewrites']}", "t")
    f.text(570, 270, f"warnings on a fault line: {t['hit_warnings']} of {t['warnings_buggy']} "
                     f"({round(100*t['warning_precision'])}%)", "t")
    f.text(570, 288, f"warnings left on fixed programs: {t['warnings_fixed']}", "t")
    f.text(570, 316, "Bugs4Q @ bb853b8, quick + dependence + lintq, Qiskit 2.6.0", "sub")
    f.arrow([(410, 170), (450, 170)], "ar")
    f.arrow([(170, 70), (450, 70)], "ar")
    f.text(310, 64, "W_b", "al", "middle")
    f.arrow([(170, 262), (550, 262)], "ar")
    f.text(470, 256, "W_f", "al", "middle")
    return f.svg(W, H, "Benchmark grading as nested sets: a bug is warned if any selected rule fires on "
                       "the buggy program, hit if a warning lands on a fault line taken from the fix's "
                       "diff, and detected if the hitting rule fires fewer times on the fixed program.")


if __name__ == "__main__":
    bench = json.loads((OUT / "bench.json").read_text(encoding="utf-8"))
    out = {"fig_system": system(), "fig_sequence": sequence(),
           "fig_grading": grading(bench["totals"]["selection"])}
    (OUT / "figs_frag.json").write_text(json.dumps(out), encoding="utf-8")
    print("figures:", {k: len(v) for k, v in out.items()})
