/* Rules & Thresholds — keeps the active tab across a full page reload (filter form
   submits). Server-rendered page; no client-side rule/threshold logic lives here. */
(function () {
  var root = document.getElementById('dt-rl');
  if (!root) return;

  var tabInputs = [
    document.getElementById('rl-tab-input-r'),
    document.getElementById('rl-tab-input-t'),
  ].filter(Boolean);

  document.querySelectorAll('#dt-rl-tabs button[data-tab]').forEach(function (btn) {
    btn.addEventListener('shown.bs.tab', function () {
      tabInputs.forEach(function (input) { input.value = btn.dataset.tab; });
    });
  });
})();
