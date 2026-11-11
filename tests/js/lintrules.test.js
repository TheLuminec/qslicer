// Pure helpers from lintrules.js (the rule selection shared by the slicer and
// Benchmark pages), loaded in a vm with a stub window. Run: node --test tests/js
'use strict';

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE = fs.readFileSync(
  path.join(__dirname, '..', '..', 'webapp', 'static', 'js', 'lintrules.js'), 'utf8');

function load(stored) {
  const store = new Map(stored === undefined ? [] : [['qslicer.lintRules', stored]]);
  const context = {
    window: {
      localStorage: {
        getItem: (key) => (store.has(key) ? store.get(key) : null),
        setItem: (key, value) => store.set(key, value),
        removeItem: (key) => store.delete(key),
      },
    },
  };
  vm.createContext(context);
  vm.runInContext(SOURCE, context, { filename: 'lintrules.js' });
  return context.window.LintRules;
}

const CATALOG = {
  default: ['quick/QS001', 'quick/QS002'],
  linters: [
    { id: 'quick', label: 'Quick checks', rules: [{ id: 'quick/QS001' }, { id: 'quick/QS002' }] },
    { id: 'dependence', label: 'Dependence checks', rules: [{ id: 'dependence/QD01' }] },
  ],
};

test('nothing stored resolves to the server default', () => {
  const rules = load();
  assert.deepStrictEqual(Array.from(rules.current(CATALOG)), CATALOG.default);
  assert.strictEqual(rules.stored(), null);
});

test('a stored selection keeps catalog order and drops unknown ids', () => {
  const rules = load(JSON.stringify(['dependence/QD01', 'gone/X', 'quick/QS002']));
  assert.deepStrictEqual(Array.from(rules.current(CATALOG)), ['quick/QS002', 'dependence/QD01']);
});

test('corrupt storage falls back to the default', () => {
  const rules = load('{not json');
  assert.deepStrictEqual(Array.from(rules.current(CATALOG)), CATALOG.default);
});

test('summary names each linter and partial selections', () => {
  const rules = load();
  assert.strictEqual(rules.summarize(CATALOG, ['quick/QS001', 'quick/QS002', 'dependence/QD01']),
    'Quick checks + Dependence checks');
  assert.strictEqual(rules.summarize(CATALOG, ['quick/QS002']), 'Quick checks (1/2)');
  assert.strictEqual(rules.summarize(CATALOG, []), 'no rules');
});
