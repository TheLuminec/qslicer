/* ==========================================================================
   Lint rule selection, shared by the slicer page and the Benchmark page.

   The selection is a list of rule ids ("quick/QS002", "dependence/QD01")
   kept in localStorage, so the rules a user picks on one page are the rules
   the other uses: the benchmark grades exactly what the slicer reports.
   Nothing stored means the server's default selection.
   ========================================================================== */

(function () {
  'use strict';

  var STORAGE_KEY = 'qslicer.lintRules';

  function readStored() {
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      var value = raw ? JSON.parse(raw) : null;
      return Array.isArray(value) ? value : null;
    } catch (error) {
      return null;
    }
  }

  function writeStored(selection) {
    try {
      if (selection === null) window.localStorage.removeItem(STORAGE_KEY);
      else window.localStorage.setItem(STORAGE_KEY, JSON.stringify(selection));
    } catch (error) { /* private mode: the selection lasts for this page only */ }
  }

  function allRuleIds(catalog) {
    var ids = [];
    catalog.linters.forEach(function (linter) {
      linter.rules.forEach(function (rule) { ids.push(rule.id); });
    });
    return ids;
  }

  /* The stored selection, minus ids the server no longer knows; the
     server's default when nothing is stored. Catalog order. */
  function resolve(catalog, stored) {
    if (!stored) return catalog['default'].slice();
    var wanted = {};
    stored.forEach(function (id) { wanted[id] = true; });
    return allRuleIds(catalog).filter(function (id) { return wanted[id]; });
  }

  function isDefault(catalog, selection) {
    var defaults = catalog['default'];
    return defaults.length === selection.length &&
      defaults.every(function (id, i) { return selection[i] === id; });
  }

  function summarize(catalog, selection) {
    var chosen = {};
    selection.forEach(function (id) { chosen[id] = true; });
    var parts = [];
    catalog.linters.forEach(function (linter) {
      var on = linter.rules.filter(function (rule) { return chosen[rule.id]; }).length;
      if (!on) return;
      parts.push(linter.label + (on === linter.rules.length ? '' : ' (' + on + '/' + linter.rules.length + ')'));
    });
    return parts.length ? parts.join(' + ') : 'no rules';
  }

  var catalogPromise = null;

  function load() {
    if (!catalogPromise) {
      catalogPromise = fetch('/api/linters').then(function (response) {
        if (!response.ok) throw new Error('Could not load the linter list.');
        return response.json();
      });
    }
    return catalogPromise;
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* Build the checkbox tree inside `container`. `onChange(selection)` runs
     after every change, once the new selection is stored. */
  function mountPicker(container, catalog, onChange) {
    var selection = resolve(catalog, readStored());

    function chosenSet() {
      var set = {};
      selection.forEach(function (id) { set[id] = true; });
      return set;
    }

    function commit(next) {
      selection = allRuleIds(catalog).filter(function (id) { return next[id]; });
      writeStored(isDefault(catalog, selection) ? null : selection);
      render();
      if (onChange) onChange(selection.slice());
    }

    function render() {
      var chosen = chosenSet();
      var html = '';
      catalog.linters.forEach(function (linter) {
        var on = linter.rules.filter(function (rule) { return chosen[rule.id]; }).length;
        var disabled = !linter.available;
        html += '<fieldset class="lint-linter' + (disabled ? ' is-disabled' : '') + '">' +
          '<legend><label><input type="checkbox" data-linter="' + escapeHtml(linter.id) + '"' +
          (on === linter.rules.length && on ? ' checked' : '') +
          (disabled ? ' disabled' : '') + '> ' + escapeHtml(linter.label) + '</label>' +
          '<span class="lint-count">' + on + '/' + linter.rules.length + '</span></legend>' +
          '<p class="lint-desc">' + escapeHtml(disabled ? linter.unavailable_reason : linter.description) +
          '</p><ul class="lint-rules">';
        linter.rules.forEach(function (rule) {
          html += '<li><label title="' + escapeHtml(rule.description) + '">' +
            '<input type="checkbox" data-rule="' + escapeHtml(rule.id) + '"' +
            (chosen[rule.id] ? ' checked' : '') + (disabled ? ' disabled' : '') + '>' +
            '<span class="lint-code sev-' + escapeHtml(rule.severity) + '">' + escapeHtml(rule.code) +
            '</span> ' + escapeHtml(rule.title) + '</label></li>';
        });
        html += '</ul></fieldset>';
      });
      html += '<div class="lint-actions">' +
        '<button type="button" class="chip" data-action="default">Defaults</button>' +
        '<button type="button" class="chip" data-action="all">All</button>' +
        '<button type="button" class="chip" data-action="none">None</button></div>';
      container.innerHTML = html;
      container.querySelectorAll('input[data-linter]').forEach(function (box) {
        var linter = catalog.linters.filter(function (l) { return l.id === box.dataset.linter; })[0];
        var on = linter.rules.filter(function (rule) { return chosen[rule.id]; }).length;
        box.indeterminate = on > 0 && on < linter.rules.length;
      });
    }

    container.addEventListener('change', function (event) {
      var box = event.target;
      var next = chosenSet();
      if (box.dataset.rule) {
        next[box.dataset.rule] = box.checked;
      } else if (box.dataset.linter) {
        catalog.linters.forEach(function (linter) {
          if (linter.id !== box.dataset.linter) return;
          linter.rules.forEach(function (rule) { next[rule.id] = box.checked; });
        });
      } else {
        return;
      }
      commit(next);
    });

    container.addEventListener('click', function (event) {
      var action = event.target.dataset && event.target.dataset.action;
      if (!action) return;
      var next = {};
      if (action === 'default') {
        writeStored(null);
        catalog['default'].forEach(function (id) { next[id] = true; });
      } else if (action === 'all') {
        catalog.linters.forEach(function (linter) {
          if (!linter.available) return;
          linter.rules.forEach(function (rule) { next[rule.id] = true; });
        });
      }
      commit(next);
    });

    render();
    return {
      selection: function () { return selection.slice(); },
      summary: function () { return summarize(catalog, selection); }
    };
  }

  window.LintRules = {
    STORAGE_KEY: STORAGE_KEY,
    load: load,
    resolve: resolve,
    summarize: summarize,
    current: function (catalog) { return resolve(catalog, readStored()); },
    // What is stored, unresolved: null for the default. Good enough to send
    // before the catalog has loaded, since the server drops unknown ids.
    stored: readStored,
    mountPicker: mountPicker
  };
})();
