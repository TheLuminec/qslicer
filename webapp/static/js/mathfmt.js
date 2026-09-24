/* ==========================================================================
   A very small math formatter.

   Handles the notation this site actually uses -- kets, square roots,
   fractions, superscripts, Greek letters and 2x2/4x4 matrices -- and nothing
   else. KaTeX would do it better, but it is ~300 KB plus fonts and would have
   to be vendored, and this site is meant to work offline with no build step.

   Markup, a LaTeX-shaped subset:

     |0>              ket           ->  |0>  with proper angle bracket
     <0|              bra
     \sqrt{2}         radical with the bar drawn over the radicand
     \frac{1}{2}      stacked fraction with a rule between
     ^{2}  _{k}       super- and subscript (braces optional for one character)
     \pi \theta ...   Greek letters
     \otimes \dagger  symbols; see SYMBOLS below
     \i               the imaginary unit, set in italics
     \matrix{a & b \\ c & d}   matrix with square brackets

   In prose, wrap math in dollar signs: "Maps $|0>$ to $\frac{1}{\sqrt{2}}$".
   Call formatText() for those strings and formatMath() for a bare expression.
   ========================================================================== */

(function () {
  'use strict';

  var SYMBOLS = {
    alpha: 'α', beta: 'β', gamma: 'γ', delta: 'δ',
    theta: 'θ', lambda: 'λ', mu: 'μ', pi: 'π',
    phi: 'φ', varphi: 'ϕ', psi: 'ψ', omega: 'ω',
    Omega: 'Ω', Sigma: 'Σ',
    pm: '±', mp: '∓', times: '×', cdot: '·',
    approx: '≈', ne: '≠', le: '≤', ge: '≥',
    to: '→', mapsto: '↦', infty: '∞',
    otimes: '⊗', oplus: '⊕', dagger: '†',
    langle: '⟨', rangle: '⟩', half: '½'
  };

  // Function names are set upright, the way they are in printed maths.
  var FUNCTIONS = ['cos', 'sin', 'tan', 'exp', 'log', 'det', 'diag', 'Tr', 'max', 'min'];

  var LKET = '|';        // vertical bar
  var RKET = '⟩';        // mathematical right angle bracket
  var LBRA = '⟨';

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /* Read a {...} group starting at src[index] (which must be '{'), honouring
     nesting. Returns [contents, indexAfterClosingBrace]. */
  function readGroup(src, index) {
    if (src[index] !== '{') {
      return [src[index] || '', index + 1];   // bare single character
    }
    var depth = 0;
    for (var i = index; i < src.length; i++) {
      if (src[i] === '{') depth++;
      else if (src[i] === '}') {
        depth--;
        if (depth === 0) return [src.slice(index + 1, i), i + 1];
      }
    }
    return [src.slice(index + 1), src.length];   // unbalanced; take the rest
  }

  function renderMatrix(body) {
    var rows = body.split('\\\\').map(function (row) {
      return row.split('&').map(function (cell) { return formatMath(cell.trim()); });
    });
    // The brackets are empty spans drawn with CSS borders, so they stretch to
    // whatever height the grid ends up being.
    var html = '<span class="math-matrix"><span class="math-bracket math-bracket-left"></span>' +
      '<span class="math-grid" style="grid-template-columns: repeat(' +
      rows[0].length + ', auto)">';
    rows.forEach(function (row) {
      row.forEach(function (cell) { html += '<span class="math-cell">' + cell + '</span>'; });
    });
    return html + '</span><span class="math-bracket math-bracket-right"></span></span>';
  }

  /* Format one expression (no dollar signs). */
  function formatMath(src) {
    src = String(src == null ? '' : src);
    var out = '';
    var i = 0;

    while (i < src.length) {
      var ch = src[i];

      if (ch === '\\') {
        var match = /^[a-zA-Z]+/.exec(src.slice(i + 1));
        var name = match ? match[0] : '';
        var after = i + 1 + name.length;

        if (name === 'frac') {
          var numerator = readGroup(src, after);
          var denominator = readGroup(src, numerator[1]);
          out += '<span class="math-frac"><span class="math-num">' +
            formatMath(numerator[0]) + '</span><span class="math-den">' +
            formatMath(denominator[0]) + '</span></span>';
          i = denominator[1];
          continue;
        }
        if (name === 'sqrt') {
          var radicand = readGroup(src, after);
          out += '<span class="math-sqrt">√<span class="math-radicand">' +
            formatMath(radicand[0]) + '</span></span>';
          i = radicand[1];
          continue;
        }
        if (name === 'matrix') {
          var body = readGroup(src, after);
          out += renderMatrix(body[0]);
          i = body[1];
          continue;
        }
        if (name === 'i') {
          out += '<i>i</i>';
          i = after;
          continue;
        }
        if (name === 'text') {
          var text = readGroup(src, after);
          out += '<span class="math-text">' + escapeHtml(text[0]) + '</span>';
          i = text[1];
          continue;
        }
        if (Object.prototype.hasOwnProperty.call(SYMBOLS, name)) {
          out += SYMBOLS[name];
          i = after;
          continue;
        }
        if (FUNCTIONS.indexOf(name) !== -1) {
          out += '<span class="math-text">' + name + '</span>';
          i = after;
          continue;
        }
        if (name === '') {           // an escaped character, e.g. \{
          out += escapeHtml(src[i + 1] || '');
          i += 2;
          continue;
        }
        out += escapeHtml('\\' + name);   // unknown command: show it as written
        i = after;
        continue;
      }

      if (ch === '^' || ch === '_') {
        var script = readGroup(src, i + 1);
        out += (ch === '^' ? '<sup>' : '<sub>') + formatMath(script[0]) +
          (ch === '^' ? '</sup>' : '</sub>');
        i = script[1];
        continue;
      }

      // |psi> and <psi| -- only when the bracket closes on the same expression.
      if (ch === '|') {
        var close = src.indexOf('>', i + 1);
        if (close !== -1 && src.slice(i + 1, close).indexOf('|') === -1) {
          out += '<span class="math-ket">' + LKET +
            formatMath(src.slice(i + 1, close)) + RKET + '</span>';
          i = close + 1;
          continue;
        }
      }
      if (ch === '<') {
        var bar = src.indexOf('|', i + 1);
        if (bar !== -1 && src.slice(i + 1, bar).indexOf('<') === -1) {
          out += '<span class="math-ket">' + LBRA +
            formatMath(src.slice(i + 1, bar)) + LKET + '</span>';
          i = bar + 1;
          continue;
        }
      }

      out += escapeHtml(ch);
      i++;
    }

    return '<span class="math">' + out + '</span>';
  }

  /* Format prose containing $...$ spans. Everything outside them is escaped. */
  function formatText(src) {
    var text = String(src == null ? '' : src);
    var out = '';
    var parts = text.split('$');
    for (var i = 0; i < parts.length; i++) {
      out += (i % 2 === 1) ? formatMath(parts[i]) : escapeHtml(parts[i]);
    }
    return out;
  }

  /* Replace the contents of every [data-math] element on the page.
     data-math="text" treats the content as prose with $...$ spans. */
  function renderMathIn(root) {
    (root || document).querySelectorAll('[data-math]').forEach(function (node) {
      if (node.dataset.mathDone === '1') return;
      var source = node.textContent;
      node.innerHTML = node.dataset.math === 'text' ? formatText(source) : formatMath(source);
      node.dataset.mathDone = '1';
    });
  }

  var api = { formatMath: formatMath, formatText: formatText, renderMathIn: renderMathIn };

  if (typeof window !== 'undefined') {
    window.formatMath = formatMath;
    window.formatText = formatText;
    window.renderMathIn = renderMathIn;
    document.addEventListener('DOMContentLoaded', function () { renderMathIn(document); });
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;   // so the formatter can be unit-tested with node
  }
})();
