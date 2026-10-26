// Pure helpers from swatches.js and graph.js, loaded in a vm with stub globals.
// The DOM-heavy rendering (SVG building, pan/zoom, slicer.js wiring) is not
// covered here. Run: node --test tests/js
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const JS_DIR = path.join(__dirname, '..', '..', 'webapp', 'static', 'js');
const read = (name) => fs.readFileSync(path.join(JS_DIR, name), 'utf8');

function loadPage(files) {
  const listeners = [];
  const context = {
    window: {},
    document: { addEventListener: (type, fn) => listeners.push([type, fn]) },
  };
  vm.createContext(context);
  for (const file of files) vm.runInContext(read(file), context, { filename: file });
  return { window: context.window, listeners };
}

/* ------------------------------------------------------------ swatches -- */

test('DEP_COLORS covers every DependencyType', () => {
  const { window } = loadPage(['swatches.js']);
  assert.deepStrictEqual(Object.keys(window.DEP_COLORS).sort(), [
    'CONTROL_CLASSICAL', 'DATA_CLASSICAL', 'DATA_QUBIT', 'ENTANGLEMENT', 'MEASUREMENT', 'PHASE',
  ]);
  for (const color of Object.values(window.DEP_COLORS)) assert.match(color, /^#[0-9A-Fa-f]{6}$/);
});

test('depColor falls back to a neutral gray for unknown names', () => {
  const { window } = loadPage(['swatches.js']);
  assert.strictEqual(window.depColor('DATA_QUBIT'), window.DEP_COLORS.DATA_QUBIT);
  assert.strictEqual(window.depColor('NOPE'), '#6c7086');
});

test('paintSwatches colors every [data-dep-swatch] and runs on DOMContentLoaded', () => {
  const { window, listeners } = loadPage(['swatches.js']);
  const chip = { dataset: { depSwatch: 'MEASUREMENT' }, style: {} };
  window.paintSwatches({ querySelectorAll: () => [chip] });
  assert.strictEqual(chip.style.background, window.DEP_COLORS.MEASUREMENT);
  assert.deepStrictEqual(listeners.map(([type]) => type), ['DOMContentLoaded']);
});

/* --------------------------------------------------------------- graph -- */

function bareGraph(extra) {
  const { window } = loadPage(['swatches.js', 'mathfmt.js', 'graph.js']);
  const g = Object.create(window.QGraph.prototype);
  return Object.assign(g, { offsets: {}, range: null, activeDeps: null }, extra);
}

const DATA = {
  program: { num_qubits: 2 },
  nodes: [
    { id: 0, label: 'h', lane: 0 },
    { id: 1, label: 'a-much-longer-gate-label', lane: 0.5 },
    { id: 2, label: 'measure', short: 'M', lane: 1 },
  ],
  edges: [],
};

test('graph.js exposes QGraph', () => {
  const { window } = loadPage(['swatches.js', 'graph.js']);
  assert.strictEqual(typeof window.QGraph, 'function');
  assert.ok(window.QGraph.READABLE_SCALE > 0 && window.QGraph.READABLE_SCALE < 1);
});

test('layout runs left to right and places nodes on their lane', () => {
  const g = bareGraph({ data: DATA });
  g._computeLayout();
  const [a, b, c] = [0, 1, 2].map((id) => g.layout[id]);
  assert.ok(a.x < b.x && b.x < c.x);
  assert.ok(b.w > a.w, 'longer labels get wider nodes');
  assert.strictEqual(c.w, a.w, 'short label used when present; both hit the minimum width');
  assert.ok(a.y < b.y && b.y < c.y);
  assert.strictEqual(b.y, (a.y + c.y) / 2);
  assert.ok(g.contentWidth > c.x && g.contentHeight > c.y);
});

test('_pos applies drag offsets', () => {
  const g = bareGraph({ data: DATA });
  g._computeLayout();
  assert.strictEqual(g._pos(0), g.layout[0]);
  g.offsets[0] = { dx: 5, dy: -3 };
  assert.deepStrictEqual(
    { ...g._pos(0) },
    { x: g.layout[0].x + 5, y: g.layout[0].y - 3, w: g.layout[0].w, h: g.layout[0].h });
});

test('_inRange is inclusive and open when no range is set', () => {
  const g = bareGraph();
  assert.ok(g._inRange(99));
  g.range = [2, 4];
  assert.deepStrictEqual([1, 2, 3, 4, 5].map((id) => g._inRange(id)),
    [false, true, true, true, false]);
});

test('_depActive allows everything until a filter is set', () => {
  const g = bareGraph();
  assert.ok(g._depActive('PHASE'));
  g.activeDeps = new Set(['DATA_QUBIT']);
  assert.ok(g._depActive('DATA_QUBIT'));
  assert.ok(!g._depActive('PHASE'));
});

function tooltipNode(overrides) {
  return Object.assign({
    id: 3, name: 'cx', qubits: [0, 1], params: [], clbits: [], condition_clbits: [],
    is_measurement: false,
    gate: { title: 'CNOT', summary: 'Flips $|1>$', detail: '', matrix: null,
            entangling: true, phase: false, self_inverse: true, known: true },
  }, overrides);
}

test('tooltip lists qubits, flags and formatted math', () => {
  const html = bareGraph()._tooltipHtml(tooltipNode());
  assert.ok(html.includes('<h4>CNOT</h4>'));
  assert.ok(html.includes('#3 &middot; cx on q0, q1'));
  assert.ok(html.includes('math-ket'));
  assert.ok(html.includes('flag-ent') && html.includes('flag-inv') && !html.includes('flag-phase'));
});

test('tooltip escapes gate names, titles and params', () => {
  const html = bareGraph()._tooltipHtml(tooltipNode({
    name: '<img onerror=x>', params: ['<b>'],
    gate: { title: '<script>', summary: '', detail: '', known: false },
  }));
  assert.ok(!html.includes('<img') && !html.includes('<script') && !html.includes('<b>'));
  assert.ok(html.includes('No reference entry'));
});

test('tooltip lists the classical bits a measurement writes', () => {
  const node = tooltipNode({ is_measurement: true, clbits: [0] });
  const html = bareGraph()._tooltipHtml(node);
  assert.ok(html.includes('<dt>clbits</dt><dd>0</dd>'));
});

/* -------------------------------------------------------------- slicer -- */

test('slicer.js parses (its logic is DOM wiring, not unit-tested here)', () => {
  assert.doesNotThrow(() => new vm.Script(read('slicer.js'), { filename: 'slicer.js' }));
});
