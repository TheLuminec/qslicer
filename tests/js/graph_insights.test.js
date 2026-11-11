// Pure helpers behind several slices at once, mirror links and the insight
// marks in graph.js, loaded in a vm with stub globals as graph_swatches.test.js
// does. The SVG they end up in is not covered here. Run: node --test tests/js
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const JS_DIR = path.join(__dirname, '..', '..', 'webapp', 'static', 'js');
const CSS = fs.readFileSync(path.join(__dirname, '..', '..', 'webapp', 'static', 'css', 'style.css'), 'utf8');

function loadGraph() {
  const context = { window: {}, document: { addEventListener: () => {} } };
  vm.createContext(context);
  for (const file of ['swatches.js', 'mathfmt.js', 'graph.js']) {
    vm.runInContext(fs.readFileSync(path.join(JS_DIR, file), 'utf8'), context, { filename: file });
  }
  return context.window.QGraph;
}

const QGraph = loadGraph();

// Arrays and Maps made inside the vm have the other realm's prototypes, which
// deepStrictEqual counts as a difference: copy them out first.
const plain = (map) => Object.fromEntries([...map].map(([k, v]) => [k, [...v]]));

/* ----------------------------------------------------- several slices -- */

test('eight slice colors, distinct, cycling with the slice number', () => {
  const { COLORS, sliceClass, sliceColor } = QGraph.slices;
  assert.strictEqual(COLORS.length, 8);
  assert.strictEqual(new Set(COLORS).size, 8);
  assert.strictEqual(sliceClass(0), 'sl-0');
  assert.strictEqual(sliceClass(9), 'sl-1');
  assert.strictEqual(sliceColor(8), COLORS[0]);
});

test('the page CSS has the same slice colors, in the same order', () => {
  QGraph.slices.COLORS.forEach((color, k) => {
    const match = CSS.match(new RegExp('--slice-' + k + ':\\s*(#[0-9a-fA-F]{6})'));
    assert.ok(match, '--slice-' + k + ' missing');
    assert.strictEqual(match[1].toLowerCase(), color.toLowerCase());
    assert.ok(CSS.includes('.node.sl-' + k + ' .node-shape'), 'no fill rule for sl-' + k);
  });
});

test('membership: each node lists the slices it is in, in slice order, once each', () => {
  const member = QGraph.slices.membership([
    { node_idx: 5, slice: [0, 1, 5] },
    { node_idx: 7, slice: [1, 7, 1] },
    { node_idx: 9, slice: [1, 9] },
  ]);
  assert.deepStrictEqual(plain(member), { 0: [0], 1: [0, 1, 2], 5: [0], 7: [1], 9: [2] });
  assert.strictEqual(QGraph.slices.membership(null).size, 0);
});

test('stripes: at most four, then how many more', () => {
  const stripes = (positions) => {
    const out = QGraph.slices.stripes(positions);
    return { shown: [...out.shown], more: out.more };
  };
  assert.strictEqual(QGraph.slices.MAX_STRIPES, 4);
  assert.deepStrictEqual(stripes([0, 2]), { shown: [0, 2], more: 0 });
  assert.deepStrictEqual(stripes([0, 1, 2, 3, 4, 5]), { shown: [0, 1, 2, 3], more: 2 });
});

test('a node in one slice takes its color; in several, the neutral look with stripes', () => {
  const g = Object.create(QGraph.prototype);
  Object.assign(g, {
    slice: new Set([1, 2]), criterion: null, hoveredId: null, marked: null, selectedQubit: null,
    showLint: false, lint: {}, chop: null, mirrorPartners: null, criteria: [2, 9],
    member: QGraph.slices.membership([{ node_idx: 2, slice: [1, 2] }, { node_idx: 9, slice: [1, 9] }]),
  });
  const node = (id) => ({ id, category: 'single-qubit', qubits: [0] });
  assert.match(g._nodeClass(node(1)), /\bsl-many\b/);
  assert.match(g._nodeClass(node(1)), /\bstriped\b/);
  assert.match(g._nodeClass(node(2)), /\bsl-0\b.*\bcriterion\b/);
  assert.match(g._nodeClass(node(3)), /\bdimmed\b/);
  assert.deepStrictEqual([...g.sliceMember(1)], [0, 1]);
});

/* ------------------------------------------------------ mirror links -- */

test('mirror index: each end lists its pairs', () => {
  const byNode = QGraph.mirrors.index([[3, 5, 0], [6, 7, 0], [2, 8, 1]]);
  assert.deepStrictEqual(plain(byNode), { 2: [2], 3: [0], 5: [0], 6: [1], 7: [1], 8: [2] });
});

test('a mirror arc runs between the inner top corners and rises with depth, capped', () => {
  const a = { x: 100, y: 46, w: 34, h: 26 };
  const b = { x: 300, y: 46, w: 34, h: 26 };
  const inner = QGraph.mirrors.arc(a, b, 0);
  const outer = QGraph.mirrors.arc(a, b, 2);
  assert.ok(inner.d.startsWith('M 113 31 C 113 '), inner.d);
  assert.ok(inner.d.endsWith(' 287 31'), inner.d);
  assert.ok(outer.rise > inner.rise, 'a pair around another is drawn higher');
  assert.strictEqual(QGraph.mirrors.arc(a, b, 100).rise, QGraph.mirrors.CAP);
  // The box covers the peak, so culling keeps an arc whose ends are off screen.
  assert.ok(inner.box.y0 <= 31 - inner.rise && inner.box.x0 <= 113 && inner.box.x1 >= 287);
  // Ends given right to left draw the same arc.
  assert.strictEqual(QGraph.mirrors.arc(b, a, 0).d, inner.d);
});

test('a mirror arc on the bottom half of the qubits hangs below its nodes', () => {
  const a = { x: 100, y: 46, w: 34, h: 26 };
  const b = { x: 300, y: 46, w: 34, h: 26 };
  const up = QGraph.mirrors.arc(a, b, 1);
  const down = QGraph.mirrors.arc(a, b, 1, true);
  // From the inner bottom corners, as far below them as the other is above the top.
  assert.ok(down.d.startsWith('M 113 61 C 113 '), down.d);
  assert.ok(down.d.endsWith(' 287 61'), down.d);
  assert.strictEqual(down.rise, up.rise);
  assert.ok(down.box.y0 === 61 && down.box.y1 >= 61 + down.rise, 'the box covers the dip');
  assert.ok(up.box.y1 === 31 && up.box.y0 <= 31 - up.rise);
  // Above the middle of the lanes, or exactly on it, the arc goes up; below, down.
  assert.strictEqual(QGraph.mirrors.hangs({ y: 40 }, { y: 40 }, 100), false);
  assert.strictEqual(QGraph.mirrors.hangs({ y: 100 }, { y: 100 }, 100), false);
  assert.strictEqual(QGraph.mirrors.hangs({ y: 160 }, { y: 160 }, 100), true);
});

/* --------------------------------------------------- insight wording -- */

test('correlation sentences: singular and plural, unmeasured only when there are any', () => {
  const say = (entry) => [...QGraph.insightText.correlation(entry)];
  assert.deepStrictEqual(say({ measurements: [9], qubits: [0], unmeasured: [] }),
    ['May be correlated with the measurement of q0.']);
  assert.deepStrictEqual(say({ measurements: [9, 12], qubits: [1, 4], unmeasured: [2, 3] }), [
    'May be correlated with the measurements of q1, q4.',
    'May still be entangled with unmeasured q2, q3.',
  ]);
  assert.deepStrictEqual(say({ measurements: [], qubits: [], unmeasured: [2] }),
    ['May still be entangled with unmeasured q2.']);
  assert.deepStrictEqual(say(null), []);
});

test('badge sentences', () => {
  const text = QGraph.insightText;
  assert.strictEqual(text.phase(false),
    'Phase only: this gate cannot change a measurement outcome until a non-diagonal gate follows on this wire.');
  assert.ok(text.phase(true).endsWith(' It can still entangle.'));
  assert.strictEqual(text.kickback([0]),
    'Phase kickback: the target is left as it is and the phase lands on control q0.');
  assert.strictEqual(text.kickback([0, 2]),
    'Phase kickback: the target is left as it is and the phase lands on controls q0, q2.');
  assert.strictEqual(text.predetermined(1),
    'This measurement always gives 1: the qubit is provably |1> here.');
  assert.strictEqual(text.noEffect('a control is |0>, so the gate never acts'),
    'No effect here: a control is |0>, so the gate never acts.');
  assert.strictEqual(text.noEffect('it is undone.'), 'No effect here: it is undone.');
});

test('the hover card carries the badges, no effect, correlations and mirrors', () => {
  const g = Object.create(QGraph.prototype);
  const nodes = {
    4: { id: 4, name: 'cz', short: 'cz', qubits: [0, 1] },
    9: { id: 9, name: 'measure', short: 'M', qubits: [0] },
  };
  Object.assign(g, {
    data: { gates: { cz: { phase: true }, measure: {} } },
    byId: nodes,
    insights: QGraph.insightText.normalize({
      no_effect: { 4: 'a control is |0>' },
      predetermined: { 9: 0 },
      correlations: { 9: { measurements: [10], qubits: [1], unmeasured: [3] } },
      mirrors: [[4, 9, 0]],
    }),
    mirrorPairs: [[4, 9, 0]],
    mirrorsByNode: QGraph.mirrors.index([[4, 9, 0]]),
    member: null,
  });
  const cz = g._insightHtml(nodes[4]);
  assert.ok(cz.includes('Phase only') && cz.includes('It can still entangle.'));
  assert.ok(cz.includes('No effect here: a control is |0&gt;.'));
  assert.ok(cz.includes('mirrors #9 (M)'));
  assert.ok(!/cancel/i.test(cz), 'mirror links never claim to cancel');
  const measure = g._insightHtml(nodes[9]);
  assert.ok(measure.includes('always gives 0'));
  assert.ok(measure.includes('May be correlated with the measurement of q1.'));
  assert.ok(measure.includes('May still be entangled with unmeasured q3.'));
  assert.deepStrictEqual([...g._insightMarks(nodes[9]).map((m) => m.text)], ['=0']);
});

test('insights missing from an older response read as nothing found', () => {
  const found = QGraph.insightText.normalize(undefined);
  assert.deepStrictEqual(Object.keys(found).sort(), [
    'correlations', 'guards', 'kickback', 'measurements', 'mirrors', 'no_effect', 'predetermined',
  ]);
  assert.strictEqual(found.mirrors.length, 0);
});

/* -------------------------------------------------- controls and targets -- */

test('roles: leading qubits are controls, the rest get the target the card names', () => {
  const roles = (qubits, gate) => [...QGraph.roles.of({ qubits }, gate)];
  assert.deepStrictEqual(roles([0, 1], { controls: 1, target: 'x' }), ['control', 'target-x']);
  assert.deepStrictEqual(roles([2, 0, 1], { controls: 2, target: 'x' }),
    ['control', 'control', 'target-x']);
  // -1: every qubit but the last is a control (mcx, mcphase).
  assert.deepStrictEqual(roles([0, 1, 2, 3], { controls: -1, target: 'gate' }),
    ['control', 'control', 'control', 'target']);
  assert.deepStrictEqual(roles([0, 1, 2], { controls: 1, target: 'swap' }),
    ['control', 'target-swap', 'target-swap']);
  // No controls: a swap still crosses its wires, anything else is plain.
  assert.deepStrictEqual(roles([0, 1], { controls: 0, target: 'swap' }), ['target-swap', 'target-swap']);
  assert.deepStrictEqual(roles([0, 1], { controls: 0, target: null }), ['plain', 'plain']);
  assert.deepStrictEqual(roles([0, 1], undefined), ['plain', 'plain']);
  // A card that claims more controls than the node has qubits is not believed.
  assert.deepStrictEqual(roles([0], { controls: 1, target: 'x' }), ['plain']);
});
