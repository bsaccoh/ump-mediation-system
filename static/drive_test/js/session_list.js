(function () {
  var all = document.getElementById('dt-sl-check-all');
  if (!all) return;
  var rows = Array.prototype.slice.call(document.querySelectorAll('.dt-sl-row-check'));
  var badge = document.getElementById('dt-sl-selected');
  var count = document.getElementById('dt-sl-selected-n');

  function sync() {
    var n = rows.filter(function (r) { return r.checked; }).length;
    rows.forEach(function (r) { r.closest('tr').classList.toggle('is-selected', r.checked); });
    all.checked = n > 0 && n === rows.length;
    all.indeterminate = n > 0 && n < rows.length;
    count.textContent = n;
    badge.hidden = n === 0;
  }
  all.addEventListener('change', function () {
    rows.forEach(function (r) { r.checked = all.checked; });
    sync();
  });
  rows.forEach(function (r) { r.addEventListener('change', sync); });
  sync();
})();
