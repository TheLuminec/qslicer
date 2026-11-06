/* Dependency colors, shared by the graph, the legend chips and the reference
   tables. qslicer/viz/model.py (DEP_COLORS) has the same palette, so a screenshot
   from the web view and one from the desktop viewer, `qslicer viz`, can sit side
   by side: change both. */

window.DEP_COLORS = {
  DATA_QUBIT: '#2196F3',
  ENTANGLEMENT: '#E91E63',
  MEASUREMENT: '#4CAF50',
  DATA_CLASSICAL: '#FF9800',
  CONTROL_CLASSICAL: '#795548',
  PHASE: '#8D1EAC'
};

window.depColor = function depColor(name) {
  return window.DEP_COLORS[name] || '#6c7086';
};

/* Paint every <span class="swatch" data-dep-swatch="DATA_QUBIT"> on the page. */
window.paintSwatches = function paintSwatches(root) {
  var scope = root || document;
  scope.querySelectorAll('[data-dep-swatch]').forEach(function (el) {
    el.style.background = window.depColor(el.dataset.depSwatch);
  });
};

document.addEventListener('DOMContentLoaded', function () {
  window.paintSwatches(document);
});
