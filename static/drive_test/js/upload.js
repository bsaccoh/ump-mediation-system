(function () {
  var form = document.getElementById('dt-up-form');
  if (!form) return;

  var $ = function (id) { return document.getElementById(id); };
  var input = $('driveTestFile'), folderInput = $('driveTestFolder'), zone = $('dt-up-zone');
  var emptyEl = $('dt-up-empty'), fileEl = $('dt-up-file');
  var queueEl = $('dt-up-queue'), queueBody = $('dt-up-queue-table').tBodies[0], groupBox = $('dt-up-group');
  var infoPanel = $('dt-up-infopanel');
  var submitBtn = $('dt-up-submit'), actions = $('dt-up-actions');
  var statusPanel = $('dt-up-status'), resultEl = $('dt-up-result');
  var bar = $('dt-up-bar'), barFill = $('dt-up-bar-fill'), statusTitle = $('dt-up-status-title');
  var operatorSel = $('dt-up-operator'), operatorHint = $('dt-up-operator-hint');
  var countersEl = $('dt-up-counters');
  var cntMeas = $('dt-up-cnt-meas'), cntFindings = $('dt-up-cnt-findings');
  var stageLabel = $('dt-up-stage-label'), stageIcon = $('dt-up-stage-icon');
  var typeSel = $('dt-up-type'), dateField = $('dt-up-date');
  var extensions = (form.dataset.extensions || '').split(',').filter(Boolean);
  var previewUrl = form.dataset.previewUrl || '';
  var maxBytes = parseInt(form.dataset.maxBytes || '0', 10);
  var maxLabel = form.dataset.maxLabel || '';
  var listUrl = form.querySelector('#dt-up-cancel').getAttribute('href');

  var queue = [];            // {file, path, size, state, msg, link, statusUrl, sessionRef, meas, misses}
  var skipped = { type: 0, size: 0, empty: 0 };
  var busy = false, uploading = false, pollTimer = null, previewXhr = null, previewed = null;
  var batchSession = '';     // session that grouped files are added to

  // ── utilities ─────────────────────────────────────────────────────
  function fmtSize(n) {
    if (n < 1024) return n + ' B';
    if (n < 1048576) return (n / 1024).toFixed(1) + ' KB';
    if (n < 1073741824) return (n / 1048576).toFixed(1) + ' MB';
    return (n / 1073741824).toFixed(2) + ' GB';
  }
  function fmtNum(n) { return n.toLocaleString(); }
  function extOf(name) { var i = name.lastIndexOf('.'); return i >= 0 ? name.slice(i).toLowerCase() : ''; }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function setInfo(id, value) {
    var node = $(id);
    if (!node) return;
    node.textContent = value || '—';
    node.classList.toggle('dt-upload-info-detected', !!(value && value !== '—'));
  }
  function resetInfo() {
    ['info-name','info-size','info-format','info-parser','info-device','info-tech','info-operator','info-opsource']
      .forEach(function (id) { setInfo(id, ''); });
  }
  function animateCount(node, from, to) {
    if (!node) return;
    var diff = to - from;
    if (diff <= 0) { node.textContent = fmtNum(to); node.dataset.current = to; return; }
    var duration = Math.min(600, Math.max(200, diff * 2)), start = null;
    function step(ts) {
      if (!start) start = ts;
      var pct = Math.min(1, (ts - start) / duration);
      node.textContent = fmtNum(Math.round(from + diff * (1 - Math.pow(1 - pct, 2))));
      if (pct < 1) requestAnimationFrame(step);
      else { node.textContent = fmtNum(to); node.dataset.current = to; }
    }
    requestAnimationFrame(step);
  }

  // ── selection ─────────────────────────────────────────────────────
  function addFiles(list) {
    list.forEach(function (it) {
      var f = it.file, path = it.path || f.name, ext = extOf(f.name);
      if (extensions.length && extensions.indexOf(ext) === -1) { skipped.type++; return; }
      if (f.size === 0) { skipped.empty++; return; }
      if (maxBytes && f.size > maxBytes) { skipped.size++; return; }
      var key = path + '|' + f.size + '|' + f.lastModified;
      if (queue.some(function (q) { return q.key === key; })) return;
      queue.push({ key: key, file: f, path: path, size: f.size, state: 'queued', msg: '' });
    });
    renderSelection();
  }

  function skippedText() {
    var parts = [];
    if (skipped.type) parts.push(skipped.type + ' unsupported type' + (skipped.type > 1 ? 's' : '') + ' (supported: ' + extensions.join(', ') + ')');
    if (skipped.size) parts.push(skipped.size + ' over the ' + maxLabel + ' limit');
    if (skipped.empty) parts.push(skipped.empty + ' empty');
    return parts.length ? 'Skipped: ' + parts.join('; ') + '.' : '';
  }

  var STATE_LABEL = { queued: 'Ready', uploading: 'Uploading…', processing: 'Processing…', done: 'Completed', failed: 'Failed' };

  function renderQueue() {
    queueBody.textContent = '';
    queue.forEach(function (q, i) {
      var tr = el('tr', 'dt-upload-q-' + q.state);
      tr.appendChild(el('td', 'dt-upload-q-name', q.path));
      tr.appendChild(el('td', 'dt-sl-nowrap', fmtSize(q.size)));
      var st = el('td', 'dt-upload-q-status');
      st.appendChild(el('strong', '', STATE_LABEL[q.state] || q.state));
      if (q.msg) st.appendChild(document.createTextNode(' — ' + q.msg));
      if (q.link) { st.appendChild(document.createTextNode(' ')); var a = el('a', 'dt-sl-link', 'View'); a.href = q.link; st.appendChild(a); }
      tr.appendChild(st);
      var rm = el('td');
      if (!busy && q.state !== 'processing') {
        var b = el('button', 'dt-upload-q-rm', '✕');
        b.type = 'button'; b.title = 'Remove'; b.setAttribute('aria-label', 'Remove ' + q.path);
        b.addEventListener('click', function () { queue.splice(i, 1); renderSelection(); });
        rm.appendChild(b);
      }
      tr.appendChild(rm);
      queueBody.appendChild(tr);
    });
  }

  function renderSelection() {
    var n = queue.length, total = queue.reduce(function (a, q) { return a + q.size; }, 0);
    var warn = $('dt-up-file-warn'), skipMsg = skippedText();
    if (!n) {
      emptyEl.hidden = false;
      fileEl.hidden = !skipMsg;
      if (skipMsg) { $('dt-up-file-name').textContent = 'No usable files selected'; $('dt-up-file-size').textContent = ''; $('dt-up-file-ext').textContent = ''; }
      queueEl.hidden = true;
      infoPanel.hidden = false;
      submitBtn.disabled = true;
      if (previewXhr) { previewXhr.abort(); previewXhr = null; }
      previewed = null;
      resetInfo(); clearOperatorDetect();
    } else {
      emptyEl.hidden = true;
      fileEl.hidden = false;
      $('dt-up-file-name').textContent = n === 1 ? queue[0].path : n + ' files selected';
      $('dt-up-file-size').textContent = fmtSize(total) + (n > 1 ? ' total' : '');
      $('dt-up-file-ext').textContent = n === 1 ? (extOf(queue[0].file.name) + ' file') : 'multiple files';
      queueEl.hidden = !(n > 1 || busy || queue.some(function (q) { return q.state !== 'queued'; }));
      $('dt-up-group-wrap').hidden = n < 2;
      infoPanel.hidden = n > 1;
      submitBtn.disabled = busy;
      if (n === 1) { setInfo('info-name', queue[0].path); setInfo('info-size', fmtSize(queue[0].size)); }
      if (previewed !== queue[0].file) { previewed = queue[0].file; runPreview(queue[0].file); }
    }
    warn.hidden = !skipMsg;
    warn.textContent = skipMsg;
    renderQueue();
  }

  function clearAll() {
    if (busy) return;
    queue = []; skipped = { type: 0, size: 0, empty: 0 }; batchSession = '';
    input.value = ''; folderInput.value = '';
    renderSelection();
  }

  function fromInput(inp) {
    var list = Array.prototype.slice.call(inp.files || []).map(function (f) { return { file: f, path: f.webkitRelativePath || f.name }; });
    inp.value = '';
    if (!busy) addFiles(list);
  }

  // Drag-and-drop: walk dropped folders recursively.
  function readAll(reader) {
    return new Promise(function (resolve) {
      var all = [];
      (function next() {
        reader.readEntries(function (batch) {
          if (!batch.length) return resolve(all);
          all = all.concat(Array.prototype.slice.call(batch));
          next();
        }, function () { resolve(all); });
      })();
    });
  }
  function walk(entry) {
    if (entry.isFile) {
      return new Promise(function (resolve) {
        entry.file(function (f) { resolve([{ file: f, path: entry.fullPath.replace(/^\//, '') }]); }, function () { resolve([]); });
      });
    }
    if (entry.isDirectory) {
      return readAll(entry.createReader()).then(function (children) {
        return Promise.all(children.map(walk)).then(function (parts) { return [].concat.apply([], parts); });
      });
    }
    return Promise.resolve([]);
  }
  function takeDrop(dt) {
    if (busy || !dt) return;
    var items = dt.items, entries = [];
    if (items && items.length && items[0].webkitGetAsEntry) {
      for (var i = 0; i < items.length; i++) { var en = items[i].webkitGetAsEntry(); if (en) entries.push(en); }
      Promise.all(entries.map(walk)).then(function (parts) { addFiles([].concat.apply([], parts)); });
    } else {
      addFiles(Array.prototype.slice.call(dt.files || []).map(function (f) { return { file: f, path: f.name }; }));
    }
  }

  $('dt-up-browse').addEventListener('click', function (e) { e.stopPropagation(); input.click(); });
  $('dt-up-browse-folder').addEventListener('click', function (e) { e.stopPropagation(); folderInput.click(); });
  zone.addEventListener('click', function (e) {
    if (fileEl.hidden && e.target.tagName !== 'BUTTON') input.click();
  });
  zone.addEventListener('keydown', function (e) {
    if ((e.key === 'Enter' || e.key === ' ') && fileEl.hidden) { e.preventDefault(); input.click(); }
  });
  input.addEventListener('change', function () { fromInput(input); });
  folderInput.addEventListener('change', function () { fromInput(folderInput); });
  $('dt-up-remove').addEventListener('click', function (e) { e.stopPropagation(); clearAll(); });

  ['dragenter','dragover'].forEach(function (t) {
    zone.addEventListener(t, function (e) { e.preventDefault(); if (!busy) zone.classList.add('is-dragging'); });
  });
  zone.addEventListener('dragleave', function (e) { e.preventDefault(); zone.classList.remove('is-dragging'); });
  zone.addEventListener('drop', function (e) { e.preventDefault(); zone.classList.remove('is-dragging'); takeDrop(e.dataTransfer); });
  window.addEventListener('dragover', function (e) { e.preventDefault(); });
  window.addEventListener('drop', function (e) { e.preventDefault(); });

  // ── operator detect state ─────────────────────────────────────────
  function clearOperatorDetect() {
    operatorSel.classList.remove('is-error');
    operatorHint.classList.remove('is-error', 'is-detected');
    operatorHint.textContent = "Detected from the file's SIM information when available.";
  }
  operatorSel.addEventListener('change', function () {
    operatorSel.classList.remove('is-error');
    operatorHint.classList.remove('is-error');
  });

  // ── preview (auto-detect on the first selected file) ──────────────
  function runPreview(file) {
    if (!previewUrl) return;
    setInfo('info-format', 'Detecting…');
    setInfo('info-parser', 'Detecting…');
    var fd = new FormData();
    fd.append('file', file);
    fd.append('csrfmiddlewaretoken', form.querySelector('[name=csrfmiddlewaretoken]').value);
    if (previewXhr) previewXhr.abort();
    var xhr = previewXhr = new XMLHttpRequest();
    xhr.open('POST', previewUrl);
    xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
    xhr.onload = function () {
      if (previewXhr === xhr) previewXhr = null;
      var r;
      try { r = JSON.parse(xhr.responseText); } catch (e) { r = null; }
      if (!r || !r.ok) { setInfo('info-format', '—'); setInfo('info-parser', '—'); return; }
      if (!r.detected) { setInfo('info-format', 'Unknown'); setInfo('info-parser', '—'); return; }
      setInfo('info-format', r.parser || '—');
      setInfo('info-parser', r.parser || '—');
      setInfo('info-tech', r.technology || '—');
      setInfo('info-device', r.device || '—');
      setInfo('info-operator', r.operator_name || '—');
      setInfo('info-opsource',
        r.operator_source === 'detected' ? 'Auto-detected from SIM' :
        r.operator_source === 'user'     ? 'User selected' : '—');
      if (r.operator_id && operatorSel.value === '' && queue.length === 1) {
        operatorSel.value = String(r.operator_id);
        if (operatorSel.value === String(r.operator_id)) {
          operatorHint.textContent = '✓ Detected: ' + r.operator_name;
          operatorHint.classList.add('is-detected');
        }
      }
      if (r.test_date && dateField && !dateField.value) dateField.value = r.test_date;
      if (r.test_type && typeSel) typeSel.value = r.test_type;
    };
    xhr.onerror = function () { if (previewXhr === xhr) previewXhr = null; setInfo('info-format', '—'); };
    xhr.send(fd);
  }

  // ── status panel helpers ──────────────────────────────────────────
  function button(label, href, primary) {
    var a = el('a', 'dt-sl-btn' + (primary ? ' dt-sl-btn-primary' : ''), label);
    a.href = href;
    return a;
  }
  function lockForm(lock) {
    form.querySelectorAll('input:not([type=hidden]),select,button[type=button]').forEach(function (n) { n.disabled = lock; });
    submitBtn.disabled = true;
  }
  function setStage(text, kind) {
    if (stageLabel) stageLabel.textContent = text;
    if (!stageIcon) return;
    if (kind === 'done') { stageIcon.className = 'dt-upload-stage-icon is-done'; stageIcon.innerHTML = '<i class="bi bi-check-circle-fill"></i>'; }
    else if (kind === 'fail') { stageIcon.className = 'dt-upload-stage-icon is-fail'; stageIcon.innerHTML = '<i class="bi bi-x-circle-fill"></i>'; }
    else { stageIcon.className = 'dt-upload-stage-icon'; stageIcon.innerHTML = '<span class="dt-upload-stage-spin"></span>'; }
  }
  function updateCounters() {
    var meas = 0, sess = {};
    queue.forEach(function (q) {
      meas += q.meas || 0;
      if (q.sessionRef) sess[q.sessionRef] = q.findings || 0;
    });
    var find = Object.keys(sess).reduce(function (a, k) { return a + sess[k]; }, 0);
    animateCount(cntMeas, parseInt(cntMeas.dataset.current || '0', 10), meas);
    animateCount(cntFindings, parseInt(cntFindings.dataset.current || '0', 10), find);
  }

  // ── batch upload ──────────────────────────────────────────────────
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    if (busy || !queue.length) return;
    busy = true; uploading = true;
    queue.forEach(function (q) { if (q.state === 'failed') { q.state = 'queued'; q.msg = ''; q.link = ''; } });
    lockForm(true);
    actions.hidden = true;
    statusPanel.hidden = false;
    statusTitle.textContent = 'Processing Drive Test';
    resultEl.hidden = true; resultEl.textContent = ''; resultEl.className = 'dt-upload-result';
    bar.hidden = false; barFill.style.width = '0';
    countersEl.hidden = false;
    cntMeas.textContent = '0'; cntMeas.dataset.current = '0';
    cntFindings.textContent = '0'; cntFindings.dataset.current = '0';
    renderSelection();
    var todo = queue.filter(function (q) { return q.state === 'queued'; });
    var totalBytes = todo.reduce(function (a, q) { return a + q.size; }, 0), doneBytes = 0;
    var group = groupBox.checked && queue.length > 1;
    if (!group) batchSession = '';
    if (!pollTimer) pollTimer = setTimeout(pollAll, 1500);

    function next(i) {
      if (i >= todo.length) { uploading = false; setStage('Processing…'); return maybeFinish(); }
      var q = todo[i];
      q.state = 'uploading'; renderQueue();
      setStage('Uploading file ' + (i + 1) + ' of ' + todo.length + '…');
      var data = new FormData();
      ['csrfmiddlewaretoken', 'operator', 'test_type', 'title', 'test_date', 'description'].forEach(function (name) {
        var f = form.elements[name];
        if (f) data.append(name, f.value);
      });
      if (group && batchSession) data.append('session_ref', batchSession);
      data.append('drive_test_file', q.file, q.file.name);

      var xhr = new XMLHttpRequest();
      xhr.open('POST', form.getAttribute('action') || window.location.pathname);
      xhr.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
      xhr.setRequestHeader('X-CSRFToken', form.querySelector('[name=csrfmiddlewaretoken]').value);
      xhr.upload.onprogress = function (ev) {
        if (ev.lengthComputable && totalBytes) barFill.style.width = Math.min(100, Math.round((doneBytes + ev.loaded) / totalBytes * 100)) + '%';
      };
      xhr.upload.onload = function () { q.msg = 'validating…'; renderQueue(); };
      function fail(msg, link) { q.state = 'failed'; q.msg = msg; q.link = link || ''; doneBytes += q.size; renderQueue(); next(i + 1); }
      xhr.onerror = function () { fail('the connection to the server was lost'); };
      xhr.onload = function () {
        var r;
        try { r = JSON.parse(xhr.responseText); } catch (err) { r = null; }
        if (!r) {
          return fail(xhr.status === 413 ? 'the server rejected the file as too large (limit ' + maxLabel + ')'
                                         : 'unexpected server response (HTTP ' + xhr.status + ')');
        }
        doneBytes += q.size;
        barFill.style.width = totalBytes ? Math.round(doneBytes / totalBytes * 100) + '%' : '100%';
        if (r.ok) {
          q.state = 'processing'; q.msg = r.parser || ''; q.statusUrl = r.status_url; q.sessionRef = r.session_ref; q.link = '';
          q.misses = 0;
          if (group && !batchSession) batchSession = r.session_ref;
          renderQueue();
          return next(i + 1);
        }
        if (r.code === 'duplicate') return fail('already uploaded' + (r.existing_session ? ' (' + r.existing_session + ')' : ''), r.existing_url);
        if (r.code === 'operator_required') {
          operatorSel.classList.add('is-error'); operatorHint.classList.add('is-error');
          operatorHint.textContent = 'Operator could not be detected for some files. Select one and retry the failed files.';
          return fail('operator not detected — select an operator and retry');
        }
        fail(r.message || 'rejected');
      };
      xhr.send(data);
    }
    next(0);
  });

  function pollAll() {
    pollTimer = null;
    var active = queue.filter(function (q) { return q.state === 'processing'; });
    var pending = active.length;
    if (!pending) { if (uploading) pollTimer = setTimeout(pollAll, 1500); else maybeFinish(); return; }
    active.forEach(function (q) {
      var x = new XMLHttpRequest();
      x.open('GET', q.statusUrl);
      x.setRequestHeader('X-Requested-With', 'XMLHttpRequest');
      function settle() { if (--pending === 0) { updateCounters(); renderQueue(); maybeFinish(); if (busy && !finished && !pollTimer) pollTimer = setTimeout(pollAll, 1500); } }
      function miss() { if (++q.misses > 5) { q.state = 'failed'; q.msg = 'processing status unavailable'; } settle(); }
      x.onload = function () {
        var s;
        try { s = JSON.parse(x.responseText); } catch (err) { s = null; }
        if (!s) return miss();
        q.misses = 0;
        q.meas = s.file_measurements || 0; q.findings = s.findings || 0;
        if (s.file_status === 'COMPLETED') { q.state = 'done'; q.msg = fmtNum(q.meas) + ' measurements'; q.link = s.session_url; }
        else if (s.file_status === 'FAILED') { q.state = 'failed'; q.msg = s.error || 'processing failed'; }
        else { q.msg = ({ PARSING: 'parsing', MATCHING: 'matching cells', NORMALIZING: 'analysing' })[s.file_status] || 'queued'; }
        settle();
      };
      x.onerror = miss;
      x.send();
    });
  }

  var finished = false;
  function maybeFinish() {
    if (uploading || finished) return;
    if (queue.some(function (q) { return q.state === 'processing' || q.state === 'uploading'; })) {
      if (!pollTimer) pollTimer = setTimeout(pollAll, 1500);
      return;
    }
    finished = true;
    busy = false;
    bar.hidden = true;
    updateCounters();
    var done = queue.filter(function (q) { return q.state === 'done'; });
    var failed = queue.filter(function (q) { return q.state === 'failed'; });
    var sessions = {};
    done.forEach(function (q) { sessions[q.sessionRef] = q.link; });
    var refs = Object.keys(sessions);
    statusTitle.textContent = failed.length ? (done.length ? 'Completed with errors' : 'Processing Failed') : 'Processing Complete';
    setStage(failed.length ? (done.length ? 'Completed with errors' : 'Failed') : 'Complete', failed.length && !done.length ? 'fail' : 'done');

    resultEl.hidden = false;
    resultEl.className = 'dt-upload-result is-' + (failed.length ? 'bad' : 'ok');
    resultEl.textContent = '';
    resultEl.appendChild(el('h3', '', done.length + ' of ' + queue.length + ' file' + (queue.length > 1 ? 's' : '') + ' processed'));
    var dl = el('dl');
    [['Sessions', refs.join(', ') || '—'],
     ['Measurements', fmtNum(queue.reduce(function (a, q) { return a + (q.state === 'done' ? q.meas || 0 : 0); }, 0))],
     ['Failed', String(failed.length)]].forEach(function (r) { dl.appendChild(el('dt', '', r[0])); dl.appendChild(el('dd', '', r[1])); });
    resultEl.appendChild(dl);
    var wrap = el('div', 'dt-upload-buttons');
    if (refs.length === 1) wrap.appendChild(button('View Session', sessions[refs[0]], true));
    if (failed.length) {
      var retry = el('button', 'dt-sl-btn' + (refs.length === 1 ? '' : ' dt-sl-btn-primary'), 'Retry Failed');
      retry.type = 'button';
      retry.addEventListener('click', function () {
        queue = queue.filter(function (q) { return q.state === 'failed'; });
        skipped = { type: 0, size: 0, empty: 0 };
        finished = false; statusPanel.hidden = true; countersEl.hidden = true; actions.hidden = false;
        form.querySelectorAll('input,select,button').forEach(function (n) { n.disabled = false; });
        renderSelection();
      });
      wrap.appendChild(retry);
    }
    wrap.appendChild(button(refs.length > 1 || !refs.length ? 'View Sessions' : 'Back to Sessions', listUrl, !refs.length && !failed.length));
    resultEl.appendChild(wrap);
    renderSelection();
  }

  renderSelection();
})();
