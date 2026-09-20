/**
 * Service Control — Live refresh, action dispatch, clipboard, modals.
 * Polls /services/api/ every 15 seconds for status updates.
 */
document.addEventListener('DOMContentLoaded', function () {
    const page = document.getElementById('serviceControlPage');
    if (!page) return;

    const API_URL    = page.dataset.apiUrl;
    const ACTION_URL = page.dataset.actionUrl;

    const refreshBtn  = document.getElementById('scRefreshBtn');
    const refreshIcon = document.getElementById('scRefreshIcon');
    const feedbackEl  = document.getElementById('scFeedback');
    const csrfInput   = document.querySelector('#scCsrfForm input[name="csrfmiddlewaretoken"]');
    const csrf        = () => csrfInput ? csrfInput.value : '';

    // Bootstrap Modals
    const confirmModalEl = document.getElementById('scConfirmModal');
    const confirmModal   = confirmModalEl ? new bootstrap.Modal(confirmModalEl) : null;
    const confirmBtn     = document.getElementById('scConfirmBtn');
    const confirmTitle   = document.getElementById('scConfirmTitle');
    const confirmDesc    = document.getElementById('scConfirmDesc');
    const confirmIcon    = document.getElementById('scConfirmIcon');

    const configModalEl  = document.getElementById('scConfigModal');
    const configModal    = configModalEl ? new bootstrap.Modal(configModalEl) : null;

    let pendingAction = null;

    // ── Prefix map: service id → template prefix used in element IDs ──────────
    const ID_PREFIX = {
        'mediation-collector':   'collection',
        'mediation-decoder':     'decoding',
        'mediation-distributor': 'distribution',
    };

    // ── Toast / feedback ───────────────────────────────────────────────────────
    function showFeedback(msg, type) {
        if (!feedbackEl) return;
        const cls  = type === 'success' ? 'alert-success' : (type === 'warning' ? 'alert-warning' : 'alert-danger');
        const icon = type === 'success' ? 'bi-check-circle-fill' : (type === 'warning' ? 'bi-exclamation-triangle-fill' : 'bi-x-circle-fill');
        feedbackEl.innerHTML = `<div class="alert ${cls} alert-dismissible fade show d-flex align-items-center gap-2 mb-4 shadow-sm" role="alert" style="border-radius:8px"><i class="bi ${icon} fs-5"></i><div class="small fw-semibold">${msg}</div><button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>`;
        setTimeout(() => {
            const a = feedbackEl.querySelector('.alert');
            if (a) { a.classList.remove('show'); setTimeout(() => a.remove(), 250); }
        }, 6000);
    }

    // ── Clipboard copy ─────────────────────────────────────────────────────────
    document.querySelectorAll('.sc-copy-btn').forEach(btn => {
        btn.addEventListener('click', async function (e) {
            e.preventDefault();
            const text = this.dataset.copy;
            if (!text) return;
            try {
                if (navigator.clipboard && window.isSecureContext) {
                    await navigator.clipboard.writeText(text);
                } else {
                    const ta = document.createElement('textarea');
                    ta.value = text; ta.style.cssText = 'position:fixed;left:-9999px';
                    document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove();
                }
                const orig = this.innerHTML;
                this.innerHTML = '<i class="bi bi-check2 text-success" style="font-size:14px"></i>';
                setTimeout(() => { this.innerHTML = orig; }, 1500);
            } catch (err) { console.error('Copy failed', err); }
        });
    });

    // ── Live refresh ───────────────────────────────────────────────────────────
    async function refreshAll() {
        if (!API_URL) return;
        if (refreshIcon) refreshIcon.classList.add('sc-spinning');
        if (refreshBtn) refreshBtn.disabled = true;

        try {
            const resp = await fetch(API_URL, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
            if (!resp.ok) throw new Error('API ' + resp.status);
            const data = await resp.json();

            // Header: timestamp + status dot
            const dot = document.getElementById('scStatusDot');
            const ts  = document.getElementById('scLastUpdated');
            if (ts && data.last_updated) ts.textContent = data.last_updated;
            if (dot) dot.style.background = data.all_healthy ? '#16A34A' : '#F4B400';

            // Per-service updates
            if (Array.isArray(data.services)) {
                data.services.forEach(svc => {
                    const pfx = ID_PREFIX[svc.id];
                    if (!pfx) return;

                    // Status pill
                    const pill = document.getElementById(pfx + 'Health');
                    if (pill) {
                        pill.className = `sc-status-pill ${svc.status_color}`;
                        const t = pill.querySelector('.sc-pill-text');
                        if (t) t.textContent = svc.status_label;
                    }

                    // Uptime
                    const uptime = document.getElementById(pfx + 'Uptime');
                    if (uptime && svc.uptime) uptime.textContent = svc.uptime;

                    // Last activity
                    const act = document.getElementById(pfx + 'Activity');
                    if (act && svc.last_activity) act.textContent = svc.last_activity;

                    // Context value
                    const ctxV = document.getElementById(pfx + 'CtxValue');
                    if (ctxV && svc.context_value) ctxV.textContent = svc.context_value;

                    // Metrics panel
                    const metrics = document.getElementById(pfx + 'Metrics');
                    if (metrics && Array.isArray(svc.metrics)) {
                        metrics.innerHTML = svc.metrics.map(m =>
                            `<div class="sc-metric-row">` +
                            `<div class="sc-metric-icon ${m.tone || ''}"><i class="bi ${m.icon}"></i></div>` +
                            `<div><span class="sc-metric-label">${m.label}</span><span class="sc-metric-value">${m.value}</span></div>` +
                            `</div>`
                        ).join('');
                    }

                    // Button states
                    const card = document.getElementById(pfx + 'Card');
                    if (card) {
                        const startBtn   = card.querySelector('.sc-btn-start');
                        const stopBtn    = card.querySelector('.sc-btn-stop');
                        const restartBtn = card.querySelector('.sc-btn-restart');
                        if (startBtn)   startBtn.disabled   = svc.is_running || !svc.is_controllable;
                        if (stopBtn)    stopBtn.disabled    = !svc.is_running || !svc.is_controllable;
                        if (restartBtn) restartBtn.disabled = !svc.is_controllable;
                    }
                });
            }
        } catch (err) {
            console.error('Refresh failed:', err);
            showFeedback('Unable to refresh service status.', 'danger');
        } finally {
            if (refreshIcon) refreshIcon.classList.remove('sc-spinning');
            if (refreshBtn) refreshBtn.disabled = false;
        }
    }

    if (refreshBtn) refreshBtn.addEventListener('click', e => { e.preventDefault(); refreshAll(); });
    setInterval(() => { if (!document.hidden) refreshAll(); }, 15000);

    // ── Execute backend action ─────────────────────────────────────────────────
    async function executeAction(serviceId, action, btnEl) {
        let origHtml = '';
        if (btnEl) {
            origHtml = btnEl.innerHTML;
            btnEl.disabled = true;
            btnEl.innerHTML = '<span class="spinner-border spinner-border-sm" role="status"></span>';
        }
        try {
            const resp = await fetch(ACTION_URL, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf(), 'X-Requested-With': 'XMLHttpRequest' },
                body: JSON.stringify({ service: serviceId, action: action }),
            });
            const res = await resp.json();
            if (resp.ok && res.success) {
                showFeedback(res.message || `${action} completed.`, 'success');
                await refreshAll();
            } else {
                showFeedback(res.error || res.message || `${action} failed.`, 'danger');
            }
        } catch (err) {
            console.error('Action error:', err);
            showFeedback('Network error executing service action.', 'danger');
        } finally {
            if (btnEl) { btnEl.innerHTML = origHtml; btnEl.disabled = false; }
        }
    }

    // ── Individual service action buttons ──────────────────────────────────────
    document.querySelectorAll('.sc-action-btn').forEach(btn => {
        btn.addEventListener('click', function (e) {
            e.preventDefault();
            if (this.disabled) return;
            const svcId   = this.dataset.service;
            const svcName = this.dataset.serviceName || 'Service';
            const action  = this.dataset.action;

            if (action === 'start') {
                executeAction(svcId, action, this);
            } else if (action === 'stop') {
                pendingAction = { serviceId: svcId, action, btnEl: this };
                if (confirmTitle) confirmTitle.textContent = `Stop ${svcName}?`;
                if (confirmDesc)  confirmDesc.textContent  = `This will stop CDR processing for ${svcName} until restarted.`;
                if (confirmIcon)  confirmIcon.className    = 'd-inline-flex align-items-center justify-content-center mb-3 bg-danger-subtle text-danger';
                if (confirmBtn)   { confirmBtn.className = 'btn px-4 fw-semibold text-white'; confirmBtn.style.background = '#DC2626'; confirmBtn.textContent = 'Stop Service'; }
                if (confirmModal) confirmModal.show();
            } else if (action === 'restart') {
                pendingAction = { serviceId: svcId, action, btnEl: this };
                if (confirmTitle) confirmTitle.textContent = `Restart ${svcName}?`;
                if (confirmDesc)  confirmDesc.textContent  = `This will restart ${svcName}. Active operations will resume automatically.`;
                if (confirmIcon)  confirmIcon.className    = 'd-inline-flex align-items-center justify-content-center mb-3 bg-primary-subtle text-primary';
                if (confirmBtn)   { confirmBtn.className = 'btn px-4 fw-semibold text-white'; confirmBtn.style.background = '#1677EE'; confirmBtn.textContent = 'Restart Service'; }
                if (confirmModal) confirmModal.show();
            }
        });
    });

    // ── Bulk action buttons ────────────────────────────────────────────────────
    document.querySelectorAll('.sc-bulk-action-btn').forEach(btn => {
        btn.addEventListener('click', function (e) {
            e.preventDefault();
            const action = this.dataset.action;
            if (action === 'start') {
                executeAction('all', action, this);
            } else if (action === 'stop') {
                pendingAction = { serviceId: 'all', action, btnEl: this };
                if (confirmTitle) confirmTitle.textContent = 'Stop All Services?';
                if (confirmDesc)  confirmDesc.textContent  = 'This halts collection, decoding and distribution. CDR ingestion will pause.';
                if (confirmIcon)  confirmIcon.className    = 'd-inline-flex align-items-center justify-content-center mb-3 bg-danger-subtle text-danger';
                if (confirmBtn)   { confirmBtn.className = 'btn px-4 fw-semibold text-white'; confirmBtn.style.background = '#DC2626'; confirmBtn.textContent = 'Stop All'; }
                if (confirmModal) confirmModal.show();
            } else if (action === 'restart') {
                pendingAction = { serviceId: 'all', action, btnEl: this };
                if (confirmTitle) confirmTitle.textContent = 'Restart All Services?';
                if (confirmDesc)  confirmDesc.textContent  = 'Sequentially restarts collector, decoder, and distributor services.';
                if (confirmIcon)  confirmIcon.className    = 'd-inline-flex align-items-center justify-content-center mb-3 bg-warning-subtle text-warning';
                if (confirmBtn)   { confirmBtn.className = 'btn px-4 fw-semibold'; confirmBtn.style.background = '#F4B400'; confirmBtn.textContent = 'Restart All'; }
                if (confirmModal) confirmModal.show();
            }
        });
    });

    // ── Confirm modal execute ──────────────────────────────────────────────────
    if (confirmBtn) {
        confirmBtn.addEventListener('click', function () {
            if (confirmModal) confirmModal.hide();
            if (pendingAction) {
                const { serviceId, action, btnEl } = pendingAction;
                pendingAction = null;
                executeAction(serviceId, action, btnEl);
            }
        });
    }

    // ── Configure modal ────────────────────────────────────────────────────────
    document.querySelectorAll('.sc-config-btn').forEach(btn => {
        btn.addEventListener('click', function (e) {
            e.preventDefault();
            const svcName  = this.dataset.serviceName || 'Service';
            const unitName = this.dataset.unit || '';
            const status   = this.dataset.status || '';
            const desc     = this.dataset.desc || '';
            const link     = this.dataset.configLink || '#';
            const linkText = this.dataset.configLinkText || 'Configure';

            const el = (id) => document.getElementById(id);
            if (el('scCfgUnit'))  el('scCfgUnit').value       = unitName;
            if (el('scCfgDesc'))  el('scCfgDesc').textContent = desc;
            if (el('scCfgState')) {
                el('scCfgState').textContent = status;
                el('scCfgState').className   = status.toLowerCase() === 'running' ? 'badge bg-success-subtle text-success fw-semibold' : 'badge bg-danger-subtle text-danger fw-semibold';
            }
            if (el('scCfgTitle'))    el('scCfgTitle').textContent    = `${svcName} Settings`;
            if (el('scCfgDeepLink')) { el('scCfgDeepLink').href = link; el('scCfgDeepLink').innerHTML = `${linkText} <i class="bi bi-arrow-right ms-1"></i>`; }
            if (el('scConfigModalLabel')) el('scConfigModalLabel').textContent = `${svcName} Configuration`;
            if (configModal) configModal.show();
        });
    });
});
