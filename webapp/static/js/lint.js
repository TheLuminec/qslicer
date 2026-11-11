/* ==========================================================================
   Lint page: pick the rules, each one explained in place. The selection is
   the one the Slicer and the Benchmark use (lintrules.js).
   ========================================================================== */

(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };

  var state = { picker: null };

  document.addEventListener('DOMContentLoaded', function () {
    window.LintRules.load()
      .then(function (catalog) {
        state.picker = window.LintRules.mountPicker($('lint-picker'), catalog, null, {
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

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
})();
