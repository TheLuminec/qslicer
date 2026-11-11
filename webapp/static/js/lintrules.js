/* ==========================================================================
   Lint rule selection, shared by the Slicer, Lint and Benchmark pages.

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

  /* The linters this server can run. One it cannot (LintQ through CodeQL,
     unless CodeQL is installed) is left out of every picker, and kept in
     ``unavailable`` for the Lint page to mention. */
  function load() {
    if (!catalogPromise) {
      catalogPromise = fetch('/api/linters').then(function (response) {
        if (!response.ok) throw new Error('Could not load the linter list.');
        return response.json();
      }).then(function (catalog) {
        catalog.unavailable = catalog.linters.filter(function (l) { return !l.available; });
        catalog.linters = catalog.linters.filter(function (l) { return l.available; });
        return catalog;
      });
    }
    return catalogPromise;
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  /* --------------------------------------------------- rule explanations -- */

  var docCache = {};      // rule id -> /api/linters/rules/<id> body, or a promise of it

  function loadDoc(ruleId) {
    if (!docCache[ruleId]) {
      docCache[ruleId] = fetch('/api/linters/rules/' + ruleId).then(function (response) {
        if (!response.ok) throw new Error('Could not load the explanation of ' + ruleId + '.');
        return response.json();
      }).then(function (body) {
        docCache[ruleId] = body;
        return body;
      }, function (error) {
        delete docCache[ruleId];
        throw error;
      });
    }
    return Promise.resolve(docCache[ruleId]);
  }

  /* A program with line numbers, the lines the rule flags highlighted and its
     findings under them. */
  function sourceBlock(source, findings) {
    var byLine = {};
    (findings || []).forEach(function (f) { (byLine[f.line] = byLine[f.line] || []).push(f); });
    var html = '<pre class="source-view lint-doc-source">';
    source.replace(/\n$/, '').split('\n').forEach(function (text, index) {
      var n = index + 1;
      html += '<span class="src-line' + (byLine[n] ? ' is-fault' : '') + '"><span class="src-no">' +
        n + '</span>' + escapeHtml(text) + '</span>';
      (byLine[n] || []).forEach(function (f) {
        // A finding that names its linter links to the rule on the Lint page.
        var code = f.tool ? '<a class="src-rule" href="/lint#' + escapeHtml(f.tool + '/' + f.code) +
          '">' + escapeHtml(f.code) + '</a>' : escapeHtml(f.code);
        html += '<span class="src-warning sev-' + escapeHtml(f.severity || 'warning') + '">' +
          code + ' ' + escapeHtml(f.message) + (f.hint ? '\n' + escapeHtml(f.hint) : '') + '</span>';
      });
    });
    return html + '</pre>';
  }

  function renderDoc(doc) {
    var html = '<div class="lint-doc-head"><span class="lint-code sev-' + escapeHtml(doc.severity) + '">' +
      escapeHtml(doc.code) + '</span><strong>' + escapeHtml(doc.title) + '</strong>' +
      '<span class="lint-doc-meta">' + escapeHtml(doc.linter + ' · ' + doc.severity) + '</span>' +
      '<button type="button" class="lint-doc-close" data-doc-close aria-label="Close the explanation">×</button></div>' +
      '<p>' + escapeHtml(doc.explain || doc.description) + '</p>';
    if (doc.limits) {
      html += '<p class="lint-doc-limits"><strong>Limits.</strong> ' + escapeHtml(doc.limits) + '</p>';
    }
    if (doc.example) {
      var found = doc.findings || {};
      html += '<div class="lint-doc-examples"><div><h4>Example</h4>' +
        sourceBlock(doc.example, found.example) + '</div><div><h4>Fixed</h4>' +
        sourceBlock(doc.fixed, found.fixed) +
        '<p class="lint-doc-note">' + (doc.findings
          ? (found.fixed && found.fixed.length ? 'The rule still fires here.' : 'No warning: the rule is satisfied.')
          : 'Not run: this linter is unavailable here.') + '</p></div></div>';
      if (doc.run_with && doc.run_with !== doc.linter_id) {
        html += '<p class="lint-doc-note">Shown with the LintQ port, which gives the original\'s warnings.</p>';
      }
    } else {
      html += '<p class="lint-doc-note">No example: this rule is about the size of a whole program.</p>';
    }
    return html;
  }

  /* Build the checkbox tree inside `container`. `onChange(selection)` runs
     after every change, once the new selection is stored. Each rule has a
     "?" button that opens its explanation, with a worked example: below the
     tree, or with ``options.inlineDocs`` right under the rule (the Lint page). */
  function mountPicker(container, catalog, onChange, options) {
    var inline = !!(options && options.inlineDocs);
    var selection = resolve(catalog, readStored());
    var openDoc = null;                 // the rule id whose explanation is shown

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
      var html = '<div class="lint-actions">' +
        '<button type="button" class="chip" data-action="default">Defaults</button>' +
        '<button type="button" class="chip" data-action="all">All</button>' +
        '<button type="button" class="chip" data-action="none">None</button></div>';
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
          var open = openDoc === rule.id;
          html += '<li id="' + (inline ? 'rule-' + escapeHtml(rule.id.replace('/', '-')) : '') + '"' +
            (open ? ' class="is-open"' : '') + '><label title="' + escapeHtml(rule.description) + '">' +
            '<input type="checkbox" data-rule="' + escapeHtml(rule.id) + '"' +
            (chosen[rule.id] ? ' checked' : '') + (disabled ? ' disabled' : '') + '>' +
            '<span class="lint-code sev-' + escapeHtml(rule.severity) + '">' + escapeHtml(rule.code) +
            '</span> ' + escapeHtml(rule.title) + '</label>' +
            '<button type="button" class="lint-more' + (open ? ' is-open' : '') + '" data-doc="' +
            escapeHtml(rule.id) + '" aria-expanded="' + open + '" title="Explain ' +
            escapeHtml(rule.code) + ', with an example">?</button></li>';
          if (inline && open) html += '<li class="lint-doc-row"><section class="lint-doc" aria-live="polite"></section></li>';
        });
        html += '</ul></fieldset>';
      });
      if (!inline) {
        html += '<section class="lint-doc" aria-live="polite"' + (openDoc ? '' : ' hidden') + '></section>';
      }
      container.innerHTML = html;
      fillDoc();
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

    /* The explanation panel's content, from the cache or once it arrives. */
    function fillDoc() {
      var panel = container.querySelector('.lint-doc');
      if (!panel || !openDoc) return;
      var wanted = openDoc;
      var cached = docCache[wanted];
      if (cached && !cached.then) {
        panel.innerHTML = renderDoc(cached);
        return;
      }
      panel.innerHTML = '<p class="lint-doc-note">Loading the explanation…</p>';
      loadDoc(wanted).then(function (doc) {
        if (openDoc === wanted) panel.innerHTML = renderDoc(doc);
      }, function (error) {
        if (openDoc === wanted) panel.innerHTML = '<p class="lint-doc-note">' + escapeHtml(error.message) + '</p>';
      });
    }

    function showDoc(ruleId) {
      openDoc = openDoc === ruleId ? null : ruleId;
      render();
      if (options && options.onDoc) options.onDoc(openDoc);
      var panel = container.querySelector('.lint-doc');
      if (!openDoc || !panel) return;
      if (container.closest && container.closest('.lint-dropdown')) {
        container.scrollTop = panel.offsetTop - 8;     // scroll the dropdown, not the page
      } else if (panel.scrollIntoView) {
        panel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      }
    }

    container.addEventListener('click', function (event) {
      var target = event.target;
      if (target.closest && target.closest('[data-doc-close]')) {
        showDoc(openDoc);
        return;
      }
      var docButton = target.closest && target.closest('[data-doc]');
      if (docButton) {
        event.preventDefault();
        showDoc(docButton.dataset.doc);
        return;
      }
      var action = target.dataset && target.dataset.action;
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
      summary: function () { return summarize(catalog, selection); },
      // Open one rule's explanation (a link to /lint#quick/QS015) and bring
      // its rule to the top of the view. False for a rule it does not list.
      openRule: function (ruleId) {
        var known = catalog.linters.some(function (linter) {
          return linter.rules.some(function (rule) { return rule.id === ruleId; });
        });
        if (!known) return false;
        openDoc = ruleId;
        render();
        var row = container.querySelector('[data-doc="' + ruleId + '"]');
        if (row && row.scrollIntoView) row.closest('li').scrollIntoView({ block: 'start' });
        return true;
      }
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
    mountPicker: mountPicker,
    sourceBlock: sourceBlock
  };
})();
