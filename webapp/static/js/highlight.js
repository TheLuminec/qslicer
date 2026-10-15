/* ==========================================================================
   A small syntax highlighter for the editor pane.

   Two languages, because the editor accepts two: Python (Qiskit) and
   OpenQASM 3. It is a tokenizer, not a parser -- it knows nothing about scope
   or types, and it is not trying to. Getting a comment, a string, a keyword
   and a gate name apart is most of the readability win.

   Deliberately hand-rolled rather than Prism or highlight.js: the site has no
   build step and no CDN, and this is under 200 lines.

   window.highlightCode(text, language) -> array of HTML strings, one per line.
   Multi-line tokens (triple-quoted strings, block comments) are split across
   the lines they span, so the caller can render one element per line.
   ========================================================================== */

(function () {
  'use strict';

  /* Gate names get their own color: in a circuit file they are the thing you
     are actually reading. Kept in step with webapp/gates.py by hand -- a name
     missing here is simply colored as an ordinary call. */
  var GATE_NAMES = {};
  ('h x y z s sdg t tdg sx sxdg id p u u1 u2 u3 rx ry rz ' +
   'cx cy cz ch cp crx cry crz csx cu cu1 cu3 swap iswap dcx ecr ' +
   'ccx cswap c3x c4x mcx mcp mcry rxx ryy rzz rzx ' +
   'measure measure_all reset barrier initialize').split(' ')
    .forEach(function (name) { GATE_NAMES[name] = true; });

  var PY_KEYWORDS = {};
  ('and as assert async await break class continue def del elif else except ' +
   'finally for from global if import in is lambda nonlocal not or pass raise ' +
   'return try while with yield match case').split(' ')
    .forEach(function (word) { PY_KEYWORDS[word] = true; });

  var PY_CONSTANTS = {};
  ('True False None self cls').split(' ')
    .forEach(function (word) { PY_CONSTANTS[word] = true; });

  var PY_BUILTINS = {};
  ('abs all any bool dict enumerate float format int len list max min print ' +
   'range repr reversed round set sorted str sum tuple type zip isinstance ' +
   'getattr setattr hasattr open super').split(' ')
    .forEach(function (word) { PY_BUILTINS[word] = true; });

  var QASM_KEYWORDS = {};
  ('OPENQASM include qubit qreg bit creg int uint float angle bool duration ' +
   'stretch complex array const let gate def defcal extern if else for in ' +
   'while return break continue measure reset barrier delay box pragma ' +
   'ctrl negctrl inv pow gphase input output readonly mutable').split(' ')
    .forEach(function (word) { QASM_KEYWORDS[word] = true; });

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  /* --------------------------------------------------------- tokenizing -- */

  /* Each rule is [class, sticky regex]. Order matters: whatever can swallow
     another construct has to come first, which is why comments and strings
     lead. */
  var PY_RULES = [
    ['tok-comment', /#[^\n]*/y],
    ['tok-string', /[rRbBuUfF]{0,2}(?:"""[\s\S]*?(?:"""|$)|'''[\s\S]*?(?:'''|$))/y],
    ['tok-string', /[rRbBuUfF]{0,2}(?:"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?)/y],
    ['tok-decorator', /@[A-Za-z_][\w.]*/y],
    ['tok-number', /\b(?:0[xXbBoO][0-9a-fA-F_]+|\d[\d_]*\.?[\d_]*(?:[eE][+-]?\d+)?j?)\b/y],
    ['tok-call', /\.[A-Za-z_]\w*(?=\s*\()/y],
    ['tok-word', /[A-Za-z_]\w*/y],
    ['tok-op', /[+\-*/%=<>!&|^~@:,.;()[\]{}]+/y],
    [null, /\s+/y]
  ];

  var QASM_RULES = [
    ['tok-comment', /\/\/[^\n]*/y],
    ['tok-comment', /\/\*[\s\S]*?(?:\*\/|$)/y],
    ['tok-string', /"(?:\\.|[^"\\\n])*"?/y],
    ['tok-number', /\b\d+(?:\.\d*)?(?:[eE][+-]?\d+)?(?:ms|us|ns|s|dt)?\b/y],
    ['tok-word', /[A-Za-z_$][\w$]*/y],
    ['tok-op', /[+\-*/%=<>!&|^~:,.;()[\]{}@]+/y],
    [null, /\s+/y]
  ];

  function tokenize(text, rules, classify) {
    var tokens = [];
    var index = 0;
    var previousWord = null;

    while (index < text.length) {
      var matched = false;

      for (var i = 0; i < rules.length; i++) {
        var cls = rules[i][0];
        var regex = rules[i][1];
        regex.lastIndex = index;
        var match = regex.exec(text);
        if (!match || match.index !== index) continue;

        var value = match[0];
        var finalClass = cls;

        if (cls === 'tok-word') {
          finalClass = classify(value, previousWord);
          previousWord = value;
        } else if (cls === 'tok-call') {
          // The dot stays plain; only the name after it is colored.
          var name = value.slice(1);
          tokens.push({ cls: null, value: '.' });
          tokens.push({
            cls: GATE_NAMES[name] ? 'tok-gate' : 'tok-call',
            value: name
          });
          index += value.length;
          matched = true;
          break;
        } else if (cls !== null && cls !== 'tok-op') {
          previousWord = null;
        }

        tokens.push({ cls: finalClass, value: value });
        index += value.length;
        matched = true;
        break;
      }

      if (!matched) {                 // nothing claimed it: emit one character
        tokens.push({ cls: null, value: text[index] });
        index++;
      }
    }
    return tokens;
  }

  function classifyPython(word, previousWord) {
    if (previousWord === 'def' || previousWord === 'class') return 'tok-def';
    if (PY_KEYWORDS[word]) return 'tok-keyword';
    if (PY_CONSTANTS[word]) return 'tok-constant';
    if (PY_BUILTINS[word]) return 'tok-builtin';
    if (GATE_NAMES[word]) return 'tok-gate';
    return null;
  }

  function classifyQasm(word, previousWord) {
    if (previousWord === 'gate' || previousWord === 'def') return 'tok-def';
    if (QASM_KEYWORDS[word]) return 'tok-keyword';
    if (GATE_NAMES[word]) return 'tok-gate';
    if (word === 'pi' || word === 'tau' || word === 'euler') return 'tok-constant';
    return null;
  }

  /* ----------------------------------------------------------- emitting -- */

  /* Split the token stream into lines, breaking multi-line tokens so each line
     can be its own element and still look continuous. */
  function highlightCode(text, language) {
    var qasm = language === 'qasm';
    var tokens = tokenize(
      String(text == null ? '' : text),
      qasm ? QASM_RULES : PY_RULES,
      qasm ? classifyQasm : classifyPython
    );

    var lines = [];
    var current = '';

    tokens.forEach(function (token) {
      var pieces = token.value.split('\n');
      pieces.forEach(function (piece, position) {
        if (position > 0) {
          lines.push(current);
          current = '';
        }
        if (!piece) return;
        current += token.cls
          ? '<span class="' + token.cls + '">' + escapeHtml(piece) + '</span>'
          : escapeHtml(piece);
      });
    });
    lines.push(current);
    return lines;
  }

  window.highlightCode = highlightCode;
  window.HIGHLIGHT_GATES = GATE_NAMES;
})();
