/* ==========================================================================
   Lint page: pick the rules (with each one explained in place) and lint a
   pasted program with them. The selection is the one the Slicer and the
   Benchmark use (lintrules.js).
   ========================================================================== */

(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  var state = { picker: null, busy: false };

  document.addEventListener('DOMContentLoaded', function () {
    $('btn-lint').addEventListener('click', run);
    $('lint-source').addEventListener('keydown', function (event) {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) run();
    });

    window.LintRules.load()
      .then(function (catalog) {
        state.picker = window.LintRules.mountPicker($('lint-picker'), catalog, function (selection) {
          setStatus(selection.length ? 'Selection changed: lint again to see it.'
            : 'Select at least one rule.');
        }, {
          inlineDocs: true,
          // The open rule goes into the address, so it can be linked to.
          onDoc: function (ruleId) {
            history.replaceState(null, '', ruleId ? '#' + ruleId : location.pathname);
          }
        });
        showUnavailable(catalog.unavailable || []);
        openFromHash();
        window.addEventListener('hashchange', openFromHash);
      })
      .catch(function (error) {
        $('lint-picker').innerHTML = '<p class="placeholder">' + escapeHtml(error.message) + '</p>';
      });
  });

  /* /lint#dependence/QD07 opens that rule's explanation. */
  function openFromHash() {
    var ruleId = decodeURIComponent(location.hash.replace(/^#/, ''));
    if (ruleId && state.picker) state.picker.openRule(ruleId);
  }

  function showUnavailable(linters) {
    var note = $('lint-unavailable');
    if (!linters.length) return;
    note.textContent = 'Not available here: ' + linters.map(function (linter) {
      return linter.label + (linter.unavailable_reason ? ' (' + linter.unavailable_reason + ')' : '');
    }).join('; ');
    note.hidden = false;
  }

  function setStatus(text) { $('lint-status').textContent = text; }

  function run() {
    if (state.busy) return;
    var source = $('lint-source').value;
    if (!source.trim()) {
      setStatus('Nothing to lint yet.');
      return;
    }
    var selection = state.picker ? state.picker.selection() : window.LintRules.stored();
    if (selection && !selection.length) {
      setStatus('Select at least one rule.');
      return;
    }
    state.busy = true;
    $('btn-lint').disabled = true;
    setStatus('Linting…');
    fetch('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // The static reader: the program is read, never run.
      body: JSON.stringify({ source: source, filename: 'program.py', language: 'qiskit-static',
        lint_rules: selection })
    })
      .then(function (response) {
        return response.json().then(function (body) {
          // A refused request (too large, bad input) says why; a program the
          // parser could not rebuild still comes back with its findings.
          if (!response.ok && !(body.diagnostics || []).length) {
            setStatus((body.error && body.error.message) || 'The lint request failed.');
            return;
          }
          render(source, body.diagnostics || []);
        });
      })
      .catch(function (error) { setStatus('The lint request failed: ' + error); })
      .finally(function () {
        state.busy = false;
        $('btn-lint').disabled = false;
      });
  }

  /* The program with its findings under their lines; findings about no line
     (a skipped linter) are listed above it. Each code links to its rule. */
  function render(source, findings) {
    var lines = source.replace(/\n$/, '').split('\n').length;
    var placed = findings.filter(function (f) { return f.line >= 1 && f.line <= lines; });
    var loose = findings.filter(function (f) { return placed.indexOf(f) === -1; });
    var html = '';
    if (loose.length) {
      html += '<ul class="lint-loose">' + loose.map(function (f) {
        return '<li class="sev-' + escapeHtml(f.severity || 'info') + '"><strong>' +
          escapeHtml(f.code) + '</strong> ' + escapeHtml(f.message) + '</li>';
      }).join('') + '</ul>';
    }
    html += window.LintRules.sourceBlock(source, placed);
    $('lint-result').innerHTML = html;

    var errors = findings.filter(function (f) { return f.severity === 'error'; }).length;
    var warnings = findings.filter(function (f) { return f.severity === 'warning'; }).length;
    var notes = findings.length - errors - warnings;
    setStatus(findings.length
      ? [errors && errors + ' error' + (errors === 1 ? '' : 's'),
         warnings && warnings + ' warning' + (warnings === 1 ? '' : 's'),
         notes && notes + ' note' + (notes === 1 ? '' : 's')].filter(Boolean).join(', ') + '.'
      : 'No findings from the selected rules.');
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
})();
