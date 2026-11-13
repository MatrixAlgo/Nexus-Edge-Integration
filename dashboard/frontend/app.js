/* D5 Edge Sync Console - vanilla JS. Every number on screen comes from the live APIs (no hardcoded counters). */
'use strict';
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const USER = 'office-admin';
const TYPES = ['PR', 'GRN', 'MAINTENANCE', 'EMERGENCY_MAINTENANCE'];
const PRIOS = ['CRITICAL', 'NORMAL', 'LOW'];
const FIELDS = {
  PR: { item: 'Hydraulic Pump', quantity: 2, uom: 'EA', requiredBy: '2026-12-15', justification: 'Onboard stock below minimum', status: 'SUBMITTED' },
  GRN: { poNumber: 'PO-74120', item: 'Fuel Injector Nozzle', receivedQuantity: 6, uom: 'EA', receivedBy: 'Chief Officer', condition: 'GOOD', status: 'RECEIVED' },
  MAINTENANCE: { equipment: 'Main Engine', task: '500h service', runningHours: 12480, status: 'IN_PROGRESS', workPerformed: '', technicianRemarks: '' },
  EMERGENCY_MAINTENANCE: { equipment: 'Fire Pump', fault: 'Loss of pressure', safetyImpact: 'HIGH', status: 'OPEN', technicianRemarks: 'Pump isolated' },
};
let VESSELS = [];

async function api(path, opts = {}) {
  const o = { method: opts.method || 'GET', headers: { 'X-D5-User': opts.user || USER } };
  if (opts.body !== undefined) { o.body = JSON.stringify(opts.body); o.headers['Content-Type'] = 'application/json'; }
  const r = await fetch(path, o);
  let data = null; try { data = await r.json(); } catch (e) { /* empty */ }
  if (!r.ok) { const m = (data && (data.error + (data.detail ? ': ' + JSON.stringify(data.detail) : ''))) || r.statusText; throw new Error(m); }
  return data;
}
function toast(msg, err) { const t = $('#toast'); t.textContent = msg; t.className = err ? 'err' : ''; t.hidden = false; clearTimeout(t._h); t._h = setTimeout(() => t.hidden = true, err ? 6000 : 3000); }
const tm = iso => iso ? new Date(iso).toLocaleTimeString() : '—';
const dt = iso => iso ? new Date(iso).toLocaleString() : '—';
const secs = iso => iso ? Math.max(0, (Date.now() - new Date(iso)) / 1000) : null;
const dur = s => s == null ? '—' : s < 60 ? `${s.toFixed(0)}s` : s < 3600 ? `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s` : `${(s / 3600).toFixed(1)}h`;
const prio = p => `<span class="prio ${esc(p)}">${esc(p)}</span>`;
const short = id => id ? `<span class="mono" title="${esc(id)}">${esc(id.slice(0, 8))}…</span>` : '—';
function pill(text, kind) { return `<span class="pill ${kind}"><span class="dot"></span>${esc(text)}</span>`; }
function statusPill(s) {
  const k = { ACKNOWLEDGED: 'ok', PROCESSED: 'ok', APPLIED: 'ok', ACTIVE: 'ok', RESOLVED: 'ok', AUTO_RESOLVED: 'ok', SYNCED: 'ok', ONLINE: 'ok', CONNECTED: 'ok', RUNNING: 'ok',
    PENDING: 'warn', SENDING: 'info', ACCEPTED: 'info', STAGED: 'info', DOWNLOADING: 'info', INTERMITTENT: 'warn', FROM_OFFICE: 'info', CONFLICT_REVIEW: 'bad', OPEN: 'bad',
    FAILED: 'bad', REJECTED: 'bad', OFFLINE: 'bad', DISCONNECTED: 'bad', ROLLED_BACK: 'warn', SUPERSEDED: 'grey', INACTIVE: 'grey', DOWN: 'bad' }[s] || 'grey';
  return pill(s || '—', k);
}
function table(cols, rows, empty = 'No data') {
  if (!rows.length) return `<div class="empty">${esc(empty)}</div>`;
  return `<div class="tbl"><table><thead><tr>${cols.map(c => `<th>${c[0]}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${cols.map(c => `<td>${c[1](r)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}
function vesselOptions(all = true) { return (all ? '<option value="">All vessels</option>' : '') + VESSELS.map(v => `<option value="${esc(v.vesselId)}">${esc(v.vesselId)} · ${esc(v.name)}</option>`).join(''); }
function openDrawer(html) { $('#drawer-content').innerHTML = html; $('#drawer').hidden = false; }
function closeDrawer() { $('#drawer').hidden = true; }
function formData(form) {
  const o = {};
  $$('[name]', form).forEach(i => {
    if (i.type === 'checkbox') { o[i.name] = i.checked; return; }
    if (i.type === 'radio') { if (i.checked) o[i.name] = i.value; return; }
    o[i.name] = i.value;
  });
  return o;
}
const typed = (v, like) => (typeof like === 'number' && v !== '' && !isNaN(+v)) ? +v : v;

/* ------------------------------------------------------------------ top bar: satellite links */
async function refreshTop() {
  try {
    const [links, cf] = await Promise.all([api('/api/network'), api('/api/conflicts')]);
    $('#links').innerHTML = Object.values(links).map(l => {
      const up = l.state === 'CONNECTED';
      return `<div class="linkpill"><span class="sat">🛰</span><b>${esc(l.vesselId)}</b>${statusPill(l.state)}
        <button class="sm ${up ? 'danger' : 'ok'}" data-act="link" data-v="${esc(l.vesselId)}" data-state="${up ? 'DISCONNECTED' : 'CONNECTED'}">${up ? 'Disconnect' : 'Reconnect'}</button></div>`;
    }).join('') || '<span class="muted">network simulator not reachable</span>';
    const open = cf.filter(c => c.status === 'OPEN').length;
    const b = $('#cf-badge'); b.hidden = !open; b.textContent = open;
  } catch (e) { $('#links').innerHTML = `<span class="pill bad">${esc(e.message)}</span>`; }
  $('#clock').textContent = new Date().toLocaleTimeString();
}
async function refreshPlatform() {
  try {
    const p = await api('/api/platform');
    const q = p.queues || {};
    $('#platform-foot').innerHTML = `<b style="color:#fff">IBM platform</b><br>MQ ${esc(p.queueManager)}: ${esc(p.mqStatus)}<br>ACE ${esc(p.ace.integrationServer)}: ${esc(p.ace.status)}<br>
      flows running: ${(p.ace.flows || []).filter(f => f.running).length}/${(p.ace.flows || []).length}<br>
      IN C/N/L: ${q['D5.IN.CRITICAL'] ?? '?'}/${q['D5.IN.NORMAL'] ?? '?'}/${q['D5.IN.LOW'] ?? '?'} · ERR ${q['D5.ERROR'] ?? '?'}`;
  } catch (e) { $('#platform-foot').textContent = 'platform: ' + e.message; }
}

/* ------------------------------------------------------------------ pages */
const PAGES = {};

PAGES.overview = {
  title: 'Executive overview',
  mount(el) {
    el.innerHTML = `<div class="grid g4" id="kpis"></div><h2>Fleet</h2><div class="grid g2" id="vcards"></div>
      <h2>IBM integration platform (live)</h2><div class="grid g2"><div class="card" id="mq"></div><div class="card" id="ace"></div></div>`;
  },
  async refresh() {
    const o = await api('/api/overview');
    const k = (v, l, s, cls = '') => `<div class="card kpi ${cls}"><div class="v">${esc(v)}</div><div class="l">${l}</div>${s ? `<div class="s">${s}</div>` : ''}</div>`;
    $('#kpis').innerHTML = [
      k(o.totalVessels, 'Total vessels', 'registered edge nodes'),
      k(o.onlineVessels, 'Online vessels', 'satellite link connected', 'ok'),
      k(o.offlineVessels, 'Offline vessels', 'disconnected / intermittent', o.offlineVessels ? 'bad' : ''),
      k(o.queuedEvents, 'Queued events', 'in vessel outboxes, not yet accepted', o.queuedEvents ? 'warn' : ''),
      k(o.synchronizedEvents, 'Synchronized events', `${o.businessTransactions} business transactions`, 'ok'),
      k(o.failedEvents, 'Failed events', `${o.centralLedger.rejected} rejected centrally`, o.failedEvents ? 'bad' : ''),
      k(o.pendingAcknowledgements, 'Pending acknowledgements', `in MQ / awaiting business ack`, o.pendingAcknowledgements ? 'warn' : ''),
      k(`${o.conflicts.open} / ${o.conflicts.total}`, 'Conflicts open / total', `${o.conflicts.autoResolved} auto · ${o.conflicts.resolved} manual`, o.conflicts.open ? 'bad' : ''),
      k(o.centralLedger.duplicateDeliveries, 'Duplicates suppressed', 'redeliveries absorbed by eventId ledger'),
    ].join('');
    $('#vcards').innerHTML = o.vessels.map(vesselCard).join('');
    const p = o.platform; const q = p.queues || {};
    $('#mq').innerHTML = `<h3>IBM MQ · ${esc(p.queueManager)} ${statusPill(p.mqStatus.startsWith('RUNNING') ? 'RUNNING' : 'DOWN')}</h3>` +
      table([['Queue', r => `<span class="mono">${esc(r[0])}</span>`], ['Current depth', r => `<b>${esc(r[1])}</b>`], ['Purpose', r => `<span class="muted small">${esc(r[2])}</span>`]],
        [['D5.IN.CRITICAL', q['D5.IN.CRITICAL'], 'inbound, MQ priority 9'], ['D5.IN.NORMAL', q['D5.IN.NORMAL'], 'inbound, MQ priority 5'], ['D5.IN.LOW', q['D5.IN.LOW'], 'inbound, MQ priority 1'],
          ['D5.OUT.ACK', q['D5.OUT.ACK'], 'business acknowledgements'], ['D5.ERROR', q['D5.ERROR'], 'rejected / poison (backout)'], ['D5.DLQ', q['D5.DLQ'], 'dead-letter queue']]);
    $('#ace').innerHTML = `<h3>IBM ACE · ${esc(p.ace.integrationServer)} ${statusPill(p.ace.status)}</h3><div class="muted small" style="margin-bottom:8px">Application ${esc(p.ace.application || '—')}</div>` +
      table([['Message flow', f => `<span class="mono">${esc(f.name)}</span>`], ['State', f => statusPill(f.running ? 'RUNNING' : 'DOWN')]], p.ace.flows || [], 'not deployed');
  },
};

function vesselCard(v) {
  const obs = v.observer || {}; const q = obs.queue || {}; const rep = v.reported || {};
  const open = (q.openCritical || 0) + (q.openNormal || 0) + (q.openLow || 0);
  const w = n => open ? (100 * n / open) : 0;
  const up = v.link.state === 'CONNECTED';
  return `<div class="vcard"><div class="hd"><div><b>${esc(v.vesselId)}</b> <span class="muted">${esc(v.name)} · ${esc(v.group)}</span></div>${statusPill(v.connectivity)}</div>
    <div class="stat">
      <span>Edge agent</span><span>${v.agentReachable ? `v${esc(obs.agentVersion)} ${pill('running', 'ok')}` : pill('agent down', 'bad')}</span>
      <span>Config version</span><span>v${esc(obs.configVersion ?? '—')}</span>
      <span>Open events (C / N / L)</span><span><b>${open}</b> &nbsp;<span class="prio CRITICAL">${q.openCritical || 0}</span> / <span class="prio NORMAL">${q.openNormal || 0}</span> / <span class="prio LOW">${q.openLow || 0}</span></span>
      <span>Acknowledged · failed</span><span>${q.acknowledged || 0} · ${q.failed || 0}</span>
      <span>Last synchronisation</span><span>${tm(obs.lastSyncAt)}</span>
      <span>Edge sees link</span><span>${statusPill(obs.linkObserved === 'DOWN' ? 'OFFLINE' : 'ONLINE')} ${obs.offlineSince ? `<span class="muted small">for ${dur(secs(obs.offlineSince))}</span>` : ''}</span>
      <span>Last heartbeat at office</span><span>${rep.lastHeartbeatAt ? dur(rep.heartbeatAgeSec) + ' ago' : 'never'} ${rep.stale ? pill('stale', 'warn') : ''}</span>
    </div>
    <div class="bar" style="margin:10px 0 12px" title="open events by priority"><i style="width:${w(q.openCritical || 0)}%;background:var(--crit)"></i><i style="width:${w(q.openNormal || 0)}%;background:var(--norm)"></i><i style="width:${w(q.openLow || 0)}%;background:var(--low)"></i></div>
    <div class="row"><button class="${up ? 'danger' : 'ok'}" data-act="link" data-v="${esc(v.vesselId)}" data-state="${up ? 'DISCONNECTED' : 'CONNECTED'}">${up ? 'Disconnect satellite' : 'Reconnect satellite'}</button>
      <a class="btn" href="#onboard" data-goto-vessel="${esc(v.vesselId)}">Open onboard AMOS</a></div></div>`;
}

PAGES.vessels = {
  title: 'Vessel management & satellite link',
  mount(el) {
    el.innerHTML = `<div class="note">CPU / memory / disk are <b>measured</b> from each edge-agent process (psutil + cgroup v2). <b>Configured limits</b> are read back from the cgroup the agent runs in (systemd CPUQuota / MemoryMax). <b>Targets</b> are the business requirement (1 vCPU · 512 MB · 2 GB) and are not measurements.</div>
      <div class="card"><h3>Vessel nodes</h3><div id="vt"></div></div><h2>Satellite link simulator</h2><div class="grid g2" id="lc"></div>`;
    this.linksMounted = false;
  },
  async refresh() {
    const vs = await api('/api/vessels');
    $('#vt').innerHTML = table([
      ['Vessel', v => `<b>${esc(v.vesselId)}</b><div class="muted small">${esc(v.name)}</div>`],
      ['Connectivity', v => statusPill(v.connectivity) + `<div class="muted small">${esc(v.link.latencyMs ?? '?')} ms${v.link.bandwidthKbps ? ' · ' + v.link.bandwidthKbps + ' kbit/s' : ''}${v.link.lossPercent ? ' · ' + v.link.lossPercent + '% loss' : ''}</div>`],
      ['Edge agent', v => v.observer ? `v${esc(v.observer.agentVersion)}<div class="muted small">prev ${esc(v.observer.previousAgentVersion || '—')}</div>` : pill('down', 'bad')],
      ['CPU (measured / limit)', v => res(v, 'cpu')], ['Memory (measured / limit / target)', v => res(v, 'mem')], ['Disk (used / target)', v => res(v, 'disk')],
      ['Queue depth', v => v.observer ? `<b>${(v.observer.queue.pending || 0) + (v.observer.queue.inFlight || 0) + (v.observer.queue.awaitingAck || 0)}</b><div class="muted small">${v.observer.queue.pending} pending · ${v.observer.queue.awaitingAck} awaiting ack</div>` : '—'],
      ['Last sync', v => tm(v.observer && v.observer.lastSyncAt)],
      ['Heartbeat (via satellite)', v => v.reported.lastHeartbeatAt ? `${dur(v.reported.heartbeatAgeSec)} ago ${v.reported.stale ? pill('stale', 'warn') : ''}` : 'never'],
    ], vs);
    if (!this.linksMounted) {
      this.linksMounted = true;
      $('#lc').innerHTML = vs.map(v => { const l = v.link; return `<form class="card" data-linkform="${esc(v.vesselId)}"><h3>${esc(v.vesselId)} link <span data-linkstate></span></h3>
        <div class="row" style="margin-bottom:12px">${['CONNECTED', 'DISCONNECTED', 'INTERMITTENT'].map(s => `<label class="row" style="color:var(--ink);margin:0"><input type="radio" name="state" value="${s}" ${l.state === s ? 'checked' : ''}> ${s}</label>`).join('')}</div>
        <div class="form">${[['latencyMs', 'Latency (ms)'], ['jitterMs', 'Jitter (ms)'], ['bandwidthKbps', 'Bandwidth kbit/s (0 = unlimited)'], ['lossPercent', 'Packet loss %'], ['intermittentUpSec', 'Intermittent: up (s)'], ['intermittentDownSec', 'Intermittent: down (s)']]
          .map(([k, lbl]) => `<div><label>${lbl}</label><input name="${k}" type="number" step="any" value="${esc(l[k])}"></div>`).join('')}
        <div><button class="primary" type="submit">Apply link settings</button></div></div>
        <div class="muted small" style="margin-top:8px">Relay port ${esc(l.relayPort)} · forwarded ${esc(l.stats.forwarded)} · blocked ${esc(l.stats.blocked)}</div></form>`; }).join('');
    }
    vs.forEach(v => { const f = $(`[data-linkform="${v.vesselId}"] [data-linkstate]`); if (f) f.innerHTML = statusPill(v.link.state) + (v.link.state === 'INTERMITTENT' ? ' ' + pill(v.link.effectiveUp ? 'currently up' : 'currently down', v.link.effectiveUp ? 'ok' : 'bad') : ''); });
  },
};
function res(v, kind) {
  const r = v.observer && v.observer.resources; if (!r) return '—';
  const m = r.measured, lim = r.configuredLimits, t = r.targets || {};
  if (kind === 'cpu') return `<b>${esc(m.cpuPercent)}%</b> of 1 vCPU<div class="muted small">limit ${lim.cpuVcpu ?? 'none'} vCPU · target ${t.cpuVcpu} vCPU</div>`;
  if (kind === 'mem') return `<b>${esc(m.rssMB)} MB</b> RSS<div class="muted small">cgroup ${esc(m.cgroupMemoryMB ?? '—')} MB · limit ${lim.memoryMB ?? 'none'} MB · target ${t.memoryMB} MB</div>`;
  return `<b>${esc(m.diskUsedMB)} MB</b><div class="muted small">target ${t.diskMB} MB</div>`;
}

PAGES.events = {
  title: 'Event monitoring',
  mount(el) {
    el.innerHTML = `<div class="card"><div class="row" style="margin-bottom:12px"><select id="ev-v">${vesselOptions()}</select>
      <select id="ev-s"><option value="">All delivery states</option>${['PENDING', 'SENDING', 'ACCEPTED', 'ACKNOWLEDGED', 'FAILED'].map(s => `<option>${s}</option>`).join('')}</select>
      <span class="muted small">ACCEPTED = persisted on IBM MQ, waiting for the central business acknowledgement. Only ACKNOWLEDGED is final.</span></div><div id="ev-t"></div></div>`;
    $('#ev-v').onchange = $('#ev-s').onchange = () => this.refresh();
  },
  async refresh() {
    const v = $('#ev-v').value, s = $('#ev-s').value;
    const rows = await api(`/api/events?limit=400${v ? '&vessel=' + v : ''}${s ? '&status=' + s : ''}`);
    $('#ev-t').innerHTML = table([
      ['Event ID', e => `<a href="#" data-act="event" data-v="${esc(e.vesselId)}" data-id="${esc(e.eventId)}">${short(e.eventId)}</a>`],
      ['Record', e => `<b>${esc(e.recordId)}</b> <span class="muted small">v${esc(e.version)}</span>`], ['Vessel', e => esc(e.vesselId)],
      ['Event type', e => `${esc(e.recordType)}<div class="muted small">${esc(e.eventType)}</div>`], ['Priority', e => prio(e.priority)],
      ['Seq', e => esc(e.sequenceNumber)], ['Created', e => tm(e.createdAt)], ['Delivery status', e => statusPill(e.status) + (e.lastError && e.status !== 'ACKNOWLEDGED' ? `<div class="muted small" title="${esc(e.lastError)}">${esc(e.lastError.slice(0, 60))}</div>` : '')],
      ['Retries', e => esc(e.retryCount) + (e.replayCount ? ` <span class="pill info">replayed ×${e.replayCount}</span>` : '')], ['MQ queue', e => `<span class="mono">${esc(e.mqQueue || '—')}</span>`],
      ['Processing ack', e => e.central ? `${statusPill(e.central.status)}<div class="muted small">${esc(e.central.outcome || '')} ${esc(e.central.transaction_id || '')}${e.central.received_count > 1 ? ' · received ×' + e.central.received_count : ''}</div><div class="muted small">ack ${e.central.ack_recorded_at ? '✓ ' + tm(e.central.ack_recorded_at) : 'pending'}</div>` : '<span class="muted">not at central</span>'],
      ['', e => ['ACKNOWLEDGED', 'FAILED'].includes(e.status) ? `<button class="sm" data-act="replay" data-v="${esc(e.vesselId)}" data-id="${esc(e.eventId)}" title="re-send the same eventId to prove duplicate-safe processing">Replay</button>` : ''],
    ], rows, 'No events yet');
  },
};

PAGES.sync = {
  title: 'Synchronization monitoring',
  mount(el) { el.innerHTML = `<div class="note">Delivery is strict priority per vessel (CRITICAL → NORMAL → LOW, FIFO within a priority, one event in flight). Vessels are independent: no global ordering across vessels is claimed.</div><div class="grid g2" id="sy"></div>`; },
  async refresh() {
    const all = await api('/api/sync');
    $('#sy').innerHTML = all.map(v => {
      const e = v.edge || {}; const q = e.queue || {}; const s = (e.sessions || [])[0];
      const prog = s ? Math.round(100 * Math.min(s.delivered, s.backlog_at_start) / Math.max(1, s.backlog_at_start)) : null;
      const offline = e.linkObserved === 'DOWN';
      return `<div class="card"><h3>${esc(v.vesselId)} · ${esc(v.name)} ${statusPill(v.link.state || 'UNKNOWN')}</h3>
        <div class="stat"><span>Edge link observation</span><span>${statusPill(offline ? 'OFFLINE' : 'ONLINE')}</span>
          <span>Offline duration</span><span>${offline ? `<b>${dur(secs(e.offlineSince))}</b> (since ${tm(e.offlineSince)})` : s && s.offline_duration_sec != null ? `last outage ${dur(s.offline_duration_sec)}` : '—'}</span>
          <span>Pending messages</span><span><b>${(q.pending || 0) + (q.inFlight || 0) + (q.awaitingAck || 0)}</b> · <span class="prio CRITICAL">C ${q.openCritical || 0}</span> <span class="prio NORMAL">N ${q.openNormal || 0}</span> <span class="prio LOW">L ${q.openLow || 0}</span></span>
          <span>Successful / failed deliveries</span><span>${q.acknowledged || 0} acknowledged · ${q.failed || 0} failed · ${e.sendAttempts ? e.sendAttempts.failed : 0} failed send attempts</span>
          <span>Last synchronisation</span><span>${tm(e.lastSyncAt)}</span></div>
        <h2 style="margin-top:16px">Current / last reconnect session</h2>
        ${s ? `<div class="muted small">Reconnected ${tm(s.reconnected_at)} · backlog ${s.backlog_at_start} · delivered ${s.delivered} · failed ${s.failed} · ${s.drained_at ? 'drained ' + tm(s.drained_at) : 'draining…'}</div>
          <div class="bar" style="margin:8px 0"><i style="width:${prog}%;background:var(--ok)"></i></div><div class="muted small" style="margin-bottom:6px">${prog}% synchronised</div>
          <div class="seq">${s.delivery_sequence.map((x, i) => `<span class="it ${esc(x.priority)}" title="${esc(x.eventId)} seq ${x.sequenceNumber}"><span class="n">#${i + 1}</span>${esc(x.recordId)} <span class="prio ${esc(x.priority)}">${esc(x.priority[0])}</span></span>`).join('') || '<span class="muted">nothing delivered yet</span>'}</div>`
          : '<div class="muted">No outage/reconnect cycle recorded yet.</div>'}
        <h2>Central processing order (latest first)</h2>
        <div class="seq">${v.centralDeliverySequence.slice(0, 24).map(x => `<span class="it ${esc(x.priority)}" title="${esc(x.event_id)}"><span class="n">${x.processing_order}</span>${esc(x.record_id)} <span class="muted small">${esc(x.outcome)}</span></span>`).join('') || '<span class="muted">none</span>'}</div></div>`;
    }).join('');
  },
};

PAGES.onboard = {
  title: 'Vessel AMOS · onboard console',
  vessel: null,
  mount(el) {
    this.vessel = this.vessel || (VESSELS[0] && VESSELS[0].vesselId);
    el.innerHTML = `<div class="note">This is the crew's local AMOS console on the vessel LAN. It works whether or not the satellite link is up — records are committed to the vessel's SQLite store together with an outbound event (transactional outbox).</div>
      <div class="tabs" id="vtabs">${VESSELS.map(v => `<button data-vtab="${esc(v.vesselId)}">${esc(v.vesselId)} · ${esc(v.name)}</button>`).join('')}</div>
      <div class="grid g2"><form class="card" id="newrec"><h3>Create operational record <span id="obs-link"></span></h3>
        <div class="form"><div><label>Record type</label><select name="recordType">${TYPES.map(t => `<option>${t}</option>`).join('')}</select></div>
        <div><label>Record ID (blank = auto)</label><input name="recordId" placeholder="e.g. PR-1001"></div>
        <div><label>Priority (blank = rule)</label><select name="priority"><option value="">(priority rule)</option>${PRIOS.map(p => `<option>${p}</option>`).join('')}</select></div></div>
        <div class="form" id="pf" style="margin-top:10px"></div>
        <div class="row" style="margin-top:12px"><button class="primary" type="submit">Save onboard</button></div></form>
        <div class="card"><h3>Bulk test data</h3><div class="form"><div><label>Number of records</label><input id="gen-n" type="number" value="10" min="1" max="200"></div>
          <div><label>Mix</label><select id="gen-mix"><option value="mixed">Mixed priorities (C/N/L)</option><option value="random">Random record types</option></select></div>
          <div><button id="gen-go">Generate records</button></div></div>
          <h3 style="margin-top:18px">Local outbox</h3><div id="obq"></div></div></div>
      <div class="card" style="margin-top:14px"><h3>Onboard records</h3><div id="rt"></div></div>`;
    $$('[data-vtab]').forEach(b => b.onclick = () => { this.vessel = b.dataset.vtab; this.tabs(); this.refresh(); });
    const f = $('#newrec'); const pf = () => { $('#pf').innerHTML = Object.entries(FIELDS[f.recordType.value]).map(([k, v]) => `<div><label>${k}</label><input data-pf="${k}" value="${esc(v)}"></div>`).join(''); };
    f.recordType.onchange = pf; pf();
    f.onsubmit = async ev => {
      ev.preventDefault(); const d = formData(f); const payload = {};
      $$('[data-pf]', f).forEach(i => payload[i.dataset.pf] = typed(i.value, FIELDS[d.recordType][i.dataset.pf]));
      try { const r = await api(`/api/vessel/${this.vessel}/records`, { method: 'POST', body: { recordType: d.recordType, recordId: d.recordId || undefined, priority: d.priority || undefined, payload } });
        toast(`${r.recordId} saved onboard (${r.priority}) · event seq ${r.sequenceNumber}`); f.recordId.value = ''; this.refresh(); } catch (e) { toast(e.message, true); }
    };
    $('#gen-go').onclick = async () => {
      const n = +$('#gen-n').value; const body = { count: n }; if ($('#gen-mix').value === 'mixed') body.priorities = ['LOW', 'NORMAL', 'CRITICAL'];
      try { const r = await api(`/api/vessel/${this.vessel}/demo/generate`, { method: 'POST', body }); toast(`${r.length} records created onboard ${this.vessel}`); this.refresh(); } catch (e) { toast(e.message, true); }
    };
    this.tabs();
  },
  tabs() { $$('[data-vtab]').forEach(b => b.classList.toggle('on', b.dataset.vtab === this.vessel)); },
  async refresh() {
    const v = this.vessel; const [recs, st] = await Promise.all([api(`/api/vessel/${v}/records`), api(`/api/vessel/${v}/status`)]);
    $('#obs-link').innerHTML = statusPill(st.linkObserved === 'DOWN' ? 'OFFLINE' : 'ONLINE');
    const q = st.queue;
    $('#obq').innerHTML = `<div class="qbars">${[['Pending', q.pending, 'var(--warn)'], ['In flight', q.inFlight, 'var(--info)'], ['Awaiting business ack', q.awaitingAck, 'var(--info)'], ['Acknowledged', q.acknowledged, 'var(--ok)'], ['Failed', q.failed, 'var(--bad)']]
      .map(([l, n, c]) => `<div class="qrow"><span>${l}</span><div class="bar"><i style="width:${q.total ? 100 * n / q.total : 0}%;background:${c}"></i></div><b>${n}</b></div>`).join('')}</div>`;
    $('#rt').innerHTML = table([
      ['Record', r => `<b>${esc(r.record_id)}</b>`], ['Type', r => esc(r.record_type)], ['Priority', r => prio(r.priority)], ['Version', r => 'v' + esc(r.version)],
      ['Key data', r => `<span class="small">${esc(summary(r.payload))}</span>`], ['Status', r => esc(r.payload.status || '')], ['Updated', r => `${tm(r.updated_at)}<div class="muted small">${esc(r.updated_by)}</div>`],
      ['Sync', r => statusPill(r.sync_status)], ['', r => `<button class="sm" data-act="edit-onboard" data-v="${esc(v)}" data-id="${esc(r.record_id)}">Edit</button>`],
    ], recs, 'No records onboard yet');
  },
};
function summary(p) { return ['item', 'equipment', 'quantity', 'receivedQuantity', 'fault', 'task', 'runningHours'].filter(k => p[k] !== undefined && p[k] !== '').map(k => `${k}: ${p[k]}`).join(' · '); }

PAGES.office = {
  title: 'Office AMOS · central records',
  mount(el) {
    el.innerHTML = `<div class="note">Central office view of every operational record (central SQLite). Office edits create a new version and travel to the vessel over the satellite link (downlink feed) — a vessel that is offline receives them after reconnect.</div>
      <div class="card"><div class="row" style="margin-bottom:12px"><select id="of-v">${vesselOptions()}</select></div><div id="of-t"></div></div>`;
    $('#of-v').onchange = () => this.refresh();
  },
  async refresh() {
    const v = $('#of-v').value; const rows = await api('/api/central/records' + (v ? '?vessel=' + v : ''));
    $('#of-t').innerHTML = table([
      ['Record', r => `<b>${esc(r.record_id)}</b>`], ['Vessel', r => esc(r.vessel_id)], ['Type', r => esc(r.record_type)], ['Priority', r => prio(r.priority)],
      ['Version', r => 'v' + esc(r.version)], ['Key data', r => `<span class="small">${esc(summary(r.payload))}</span>`], ['Status', r => esc(r.payload.status || '')],
      ['Source', r => esc(r.source_system)], ['Updated', r => `${tm(r.updated_at)}<div class="muted small">${esc(r.updated_by)}</div>`],
      ['', r => (r.conflict_open ? pill('conflict', 'bad') + ' ' : '') + `<button class="sm" data-act="office-record" data-id="${esc(r.record_id)}">Open</button>`],
    ], rows, 'No records have reached the office yet');
  },
};

PAGES.conflicts = {
  title: 'Conflict management',
  sel: null,
  mount(el) {
    el.innerHTML = `<div class="note">Policy: fields changed on one side only are merged automatically; a field changed on both sides is decided by its owner (VESSEL / OFFICE) or, for critical fields such as <b>status</b>, held for manual review. No blanket last-write-wins. Policy file: central-office/config/conflict-policy.json</div>
      <div class="card"><h3>Detected conflicts</h3><div id="cf-t"></div></div><div id="cf-d" style="margin-top:14px"></div>`;
    const m = location.hash.match(/[?&]id=([^&]+)/); if (m) this.sel = decodeURIComponent(m[1]);
  },
  async refresh() {
    const rows = await api('/api/conflicts'); this.rows = rows;
    $('#cf-t').innerHTML = table([
      ['Conflict', c => `<b>${esc(c.conflict_id)}</b>`], ['Record', c => esc(c.record_id)], ['Vessel', c => esc(c.vessel_id)], ['Detected', c => dt(c.detected_at)],
      ['Versions', c => `base v${c.base_version} · office v${c.office_version} · vessel v${c.vessel_version}`],
      ['Conflicting fields', c => c.field_decisions.filter(d => !['AUTO_MERGE', 'IDENTICAL'].includes(d.decision)).map(d => `<span class="mono">${esc(d.field)}</span>`).join(', ') || '—'],
      ['Status', c => statusPill(c.status)], ['', c => `<button class="sm ${c.status === 'OPEN' ? 'primary' : ''}" data-act="conflict" data-id="${esc(c.conflict_id)}">${c.status === 'OPEN' ? 'Review' : 'View'}</button>`],
    ], rows, 'No conflicts detected');
    if (this.sel && !$('#cf-d form[data-resolving]')) this.detail(this.sel);
  },
  detail(id) {
    this.sel = id; const c = (this.rows || []).find(x => x.conflict_id === id); if (!c) return;
    const fields = [...new Set([...Object.keys(c.base_payload), ...Object.keys(c.office_payload), ...Object.keys(c.vessel_payload)])].sort();
    const dec = Object.fromEntries(c.field_decisions.map(d => [d.field, d]));
    const final = c.final_payload || c.merged_payload;
    const col = (title, obj, mark) => `<div><h4>${title}</h4><pre>${fields.map(f => { const cls = mark(f); return `<span class="${cls}">${esc(f)}: ${esc(JSON.stringify(obj[f]))}</span>`; }).join('\n')}</pre></div>`;
    const conflicted = f => dec[f] && !['AUTO_MERGE', 'IDENTICAL'].includes(dec[f].decision);
    const manual = c.field_decisions.filter(d => d.decision === 'MANUAL_REVIEW');
    $('#cf-d').innerHTML = `<div class="card"><h3>${esc(c.conflict_id)} · ${esc(c.record_id)} ${statusPill(c.status)}</h3>
      <div class="cmp">${col(`Original (base v${c.base_version})`, c.base_payload, () => '')}${col(`Vessel version (v${c.vessel_version})`, c.vessel_payload, f => conflicted(f) ? 'hl-bad' : (c.vessel_payload[f] !== c.base_payload[f] ? 'hl-ok' : ''))}
      ${col(`Office version (v${c.office_version})`, c.office_payload, f => conflicted(f) ? 'hl-bad' : (c.office_payload[f] !== c.base_payload[f] ? 'hl-ok' : ''))}${col(c.status === 'OPEN' ? 'Merged so far (pending review)' : `Final record (v${c.final_version})`, final, f => conflicted(f) ? 'hl-ok' : '')}</div>
      <h2>Field decisions</h2>${table([['Field', d => `<span class="mono">${esc(d.field)}</span>`], ['Base', d => esc(JSON.stringify(d.base))], ['Vessel', d => esc(JSON.stringify(d.vessel))], ['Office', d => esc(JSON.stringify(d.office))],
        ['Decision', d => statusPill(d.decision === 'MANUAL_REVIEW' ? 'OPEN' : d.decision === 'MANUAL_RESOLVED' ? 'RESOLVED' : 'APPLIED') + ' ' + esc(d.decision)], ['Winner', d => esc(d.winner || '—') + (d.resolvedBy ? `<div class="muted small">by ${esc(d.resolvedBy)}</div>` : '')], ['Rule', d => `<span class="muted small">${esc(d.rule)}</span>`]], c.field_decisions)}
      ${c.status === 'OPEN' ? `<form id="resolve" data-resolving style="margin-top:14px"><h2>Manual resolution</h2>${manual.map(d => `<div class="card" style="margin-bottom:8px"><b class="mono">${esc(d.field)}</b>
          <div class="row" style="margin-top:6px"><label class="row" style="color:var(--ink);margin:0"><input type="radio" name="f_${esc(d.field)}" value="VESSEL"> Vessel: <b>${esc(JSON.stringify(d.vessel))}</b></label>
          <label class="row" style="color:var(--ink);margin:0"><input type="radio" name="f_${esc(d.field)}" value="OFFICE"> Office: <b>${esc(JSON.stringify(d.office))}</b></label>
          <label class="row" style="color:var(--ink);margin:0"><input type="radio" name="f_${esc(d.field)}" value="CUSTOM"> Custom <input name="c_${esc(d.field)}" placeholder="value"></label></div></div>`).join('')}
          <div class="form"><div><label>Resolved by</label><input name="actor" value="technical-superintendent"></div><div class="wide"><label>Resolution note</label><input name="note" value="Verified with vessel by phone"></div>
          <div><button class="primary" type="submit">Apply resolution</button></div></div></form>`
        : `<div class="muted" style="margin-top:10px">Resolved ${dt(c.resolved_at)} by <b>${esc(c.resolved_by)}</b>${c.resolution_note ? ' — ' + esc(c.resolution_note) : ''}</div>`}</div>`;
    const f = $('#resolve');
    if (f) f.onsubmit = async ev => {
      ev.preventDefault(); const d = formData(f); const decisions = {};
      for (const m of manual) { const ch = d['f_' + m.field]; if (!ch) return toast('choose a value for ' + m.field, true); decisions[m.field] = ch === 'CUSTOM' ? { custom: d['c_' + m.field] } : ch; }
      try { const r = await api(`/api/conflicts/${c.conflict_id}/resolve`, { method: 'POST', body: { decisions, actor: d.actor, note: d.note } }); toast(`${c.conflict_id} resolved · record v${r.finalVersion} sent to ${c.vessel_id}`); f.removeAttribute('data-resolving'); this.refresh(); } catch (e) { toast(e.message, true); }
    };
  },
};

function targetPicker(name) {
  return `<div class="wide"><label>Target</label><div class="row">${VESSELS.map(v => `<label class="row" style="color:var(--ink);margin:0"><input type="checkbox" name="${name}_${esc(v.vesselId)}" checked> ${esc(v.vesselId)}</label>`).join('')}
    <span class="muted">or group</span><select name="${name}_group"><option value="">—</option><option>ALL</option>${[...new Set(VESSELS.map(v => v.group))].map(g => `<option>${esc(g)}</option>`).join('')}</select></div></div>`;
}
function readTarget(d, name) { const g = d[name + '_group']; const vessels = VESSELS.map(v => v.vesselId).filter(id => d[name + '_' + id]); return g ? { group: g, vessels } : { vessels }; }

PAGES.config = {
  title: 'Edge configuration management',
  async mount(el) {
    const st = await api('/api/config'); const cur = st.versions[0].config;
    el.innerHTML = `<form class="card" id="cfgf"><h3>Publish configuration version ${st.versions[0].version + 1}</h3>
      <div class="form">${targetPicker('t')}
        <div><label>Synchronisation enabled</label><select name="syncEnabled"><option value="true" ${cur.syncEnabled ? 'selected' : ''}>true</option><option value="false" ${!cur.syncEnabled ? 'selected' : ''}>false</option></select></div>
        ${['dispatchIntervalSec', 'ackPollIntervalSec', 'ackTimeoutSec', 'retryBaseSec', 'retryMaxSec', 'heartbeatSec', 'requestTimeoutSec'].map(k => `<div><label>${k}</label><input name="${k}" type="number" step="any" value="${esc(cur[k])}"></div>`).join('')}
        <div class="wide"><label>Priority rules (record type → priority)</label><div class="row">${TYPES.map(t => `<label style="margin:0">${t} <select name="pr_${t}">${PRIOS.map(p => `<option ${cur.priorityRules[t] === p ? 'selected' : ''}>${p}</option>`).join('')}</select></label>`).join('')}</div></div>
        <div class="wide"><label>Release notes</label><input name="notes" value="Configuration v${st.versions[0].version + 1}"></div><div><button class="primary" type="submit">Publish configuration</button></div></div>
      <div class="muted small" style="margin-top:8px">Validated centrally and again on each edge before activation. Offline vessels stay PENDING until their link returns.</div></form>
      <div class="grid g2" style="margin-top:14px"><div class="card"><h3>Active configuration per vessel</h3><div id="cfg-v"></div></div><div class="card"><h3>Deployment status</h3><div id="cfg-d"></div></div></div>
      <div class="card" style="margin-top:14px"><h3>Configuration versions</h3><div id="cfg-h"></div></div>`;
    const f = $('#cfgf');
    f.onsubmit = async ev => {
      ev.preventDefault(); const d = formData(f); const config = { syncEnabled: d.syncEnabled === 'true', priorityRules: {} };
      ['dispatchIntervalSec', 'ackPollIntervalSec', 'ackTimeoutSec', 'retryBaseSec', 'retryMaxSec', 'heartbeatSec', 'requestTimeoutSec'].forEach(k => config[k] = +d[k]);
      TYPES.forEach(t => config.priorityRules[t] = d['pr_' + t]);
      try { const r = await api('/api/config/publish', { method: 'POST', body: { config, target: readTarget(d, 't'), notes: d.notes } }); toast(`configuration v${r.configVersion} published to ${r.vessels.join(', ')}`); route(); } catch (e) { toast(e.message, true); }
    };
  },
  async refresh() {
    const st = await api('/api/config');
    $('#cfg-v').innerHTML = table([['Vessel', v => `<b>${esc(v.vessel_id)}</b> <span class="muted small">${esc(v.vessel_group)}</span>`], ['Active on edge', v => v.activeConfigVersion ? 'v' + v.activeConfigVersion : pill('agent down', 'bad')], ['Last reported to office', v => v.config_version ? 'v' + v.config_version : '—']], st.vessels);
    $('#cfg-d').innerHTML = table([['Version', d => 'v' + d.config_version], ['Vessel', d => esc(d.vessel_id)], ['Status', d => statusPill(d.status)], ['Updated', d => tm(d.updated_at)], ['Detail', d => `<span class="muted small">${esc(d.detail || '')}</span>`]], st.deployments, 'No deployments yet');
    $('#cfg-h').innerHTML = table([['Version', c => `<b>v${c.version}</b>`], ['Created', c => dt(c.created_at)], ['By', c => esc(c.created_by)], ['Target', c => `<span class="mono small">${esc(c.target)}</span>`], ['Notes', c => esc(c.notes || '')],
      ['Settings', c => `<span class="mono small">${esc(JSON.stringify(c.config))}</span>`]], st.versions);
  },
};

PAGES.versions = {
  title: 'Edge version management',
  mount(el) {
    el.innerHTML = `<div class="note">Controlled update: the vessel downloads the package over the satellite link, verifies its SHA-256 against the published checksum, stages it, activates it, runs a health check and rolls back automatically on failure. The central console never sends shell commands. In this POC the package is a mock (manifest + feature flags): the active version and features change; the Python code itself is not replaced.</div>
      <div class="grid g2"><form class="card" id="verf"><h3>Publish edge-agent version</h3><div class="form">
        <div><label>Version</label><input name="version" value="2.0.0"></div>${targetPicker('t')}
        <div class="wide"><label>Release notes</label><input name="notes" value="Delta compression for low-bandwidth links"></div>
        <div class="wide"><label class="row" style="color:var(--ink)"><input type="checkbox" name="corrupt"> Simulate corrupted package (checksum mismatch)</label>
        <label class="row" style="color:var(--ink)"><input type="checkbox" name="failHealthCheck"> Simulate failing post-update health check (automatic rollback)</label></div>
        <div><button class="primary" type="submit">Publish &amp; deploy</button></div></div></form>
      <form class="card" id="rbf"><h3>Roll back to previous version</h3><div class="form">${targetPicker('r')}<div><button class="danger" type="submit">Roll back</button></div></div>
        <h3 style="margin-top:18px">Fleet versions</h3><div id="ver-v"></div></form></div>
      <div class="card" style="margin-top:14px"><h3>Deployment status</h3><div id="ver-d"></div></div>
      <div class="card" style="margin-top:14px"><h3>Published packages</h3><div id="ver-p"></div></div>`;
    $('#verf').onsubmit = async ev => {
      ev.preventDefault(); const d = formData(ev.target);
      try { const r = await api('/api/versions/publish', { method: 'POST', body: { version: d.version, notes: d.notes, target: readTarget(d, 't'), corrupt: d.corrupt, failHealthCheck: d.failHealthCheck } }); toast(`version ${r.version} published (sha256 ${r.sha256.slice(0, 12)}…)`); this.refresh(); } catch (e) { toast(e.message, true); }
    };
    $('#rbf').onsubmit = async ev => {
      ev.preventDefault(); const d = formData(ev.target);
      try { const r = await api('/api/versions/rollback', { method: 'POST', body: { target: readTarget(d, 'r') } }); toast('rollback requested for ' + r.vessels.join(', ')); this.refresh(); } catch (e) { toast(e.message, true); }
    };
  },
  async refresh() {
    const st = await api('/api/versions');
    $('#ver-v').innerHTML = table([['Vessel', v => `<b>${esc(v.vessel_id)}</b>`], ['Active', v => v.activeVersion ? `<b>${esc(v.activeVersion)}</b>` : pill('agent down', 'bad')], ['Previous (rollback target)', v => esc(v.previousVersion || '—')]], st.vessels);
    $('#ver-d').innerHTML = table([['#', d => d.id], ['Vessel', d => esc(d.vessel_id)], ['Action', d => esc(d.action)], ['Target', d => esc(d.target_version || 'previous')], ['From', d => esc(d.from_version || '—')], ['Status', d => statusPill(d.status)],
      ['Detail', d => `<span class="small">${esc(d.detail ? (d.detail.error || JSON.stringify(d.detail)) : '')}</span>`], ['Updated', d => tm(d.updated_at)]], st.deployments, 'No deployments yet');
    $('#ver-p').innerHTML = table([['Version', p => `<b>${esc(p.version)}</b>`], ['SHA-256 (published)', p => `<span class="mono">${esc(p.sha256.slice(0, 24))}…</span>`], ['Size', p => p.size + ' B'], ['Features', p => esc((p.manifest.features || []).join(', '))],
      ['Health check', p => esc(p.manifest.healthCheck)], ['Created', p => dt(p.created_at)], ['Notes', p => esc(p.notes || '')]], st.packages);
  },
};

PAGES.audit = {
  title: 'Audit & synchronisation history',
  mount(el) {
    el.innerHTML = `<div class="card"><h3>Record history (end-to-end)</h3><div class="row"><input id="rh-id" placeholder="Record ID, e.g. MNT-2001"><button id="rh-go" class="primary">Show history</button></div><div id="rh"></div></div>
      <div class="card" style="margin-top:14px"><h3>Audit log</h3><div class="row" style="margin-bottom:10px"><select id="au-a"><option value="">All actions</option>${['EVENT_PROCESSED', 'DUPLICATE_IGNORED', 'EVENT_REJECTED', 'CONFLICT_DETECTED', 'CONFLICT_RESOLVED', 'OFFICE_UPDATED', 'OFFICE_CREATED', 'SATELLITE_LINK_CHANGED', 'CONFIG_PUBLISHED', 'VERSION_PUBLISHED', 'ROLLBACK_REQUESTED'].map(a => `<option>${a}</option>`).join('')}</select></div><div id="au"></div></div>`;
    $('#au-a').onchange = () => this.refresh();
    $('#rh-go').onclick = () => this.history($('#rh-id').value.trim());
  },
  async history(id) {
    if (!id) return;
    try {
      const r = await api('/api/central/records/' + encodeURIComponent(id));
      let edge = null; try { edge = await api(`/api/vessel/${r.vessel_id}/records/${encodeURIComponent(id)}`); } catch (e) { /* vessel down */ }
      $('#rh').innerHTML = `<h2>Central versions</h2>${table([['Version', v => 'v' + v.version], ['Source', v => esc(v.change_source)], ['By', v => esc(v.changed_by)], ['At', v => dt(v.created_at)], ['Event', v => short(v.event_id)], ['Payload', v => `<span class="mono small">${esc(JSON.stringify(v.payload))}</span>`]], r.versions)}
        <h2>Business transactions</h2>${table([['Transaction', t => esc(t.transaction_id)], ['Event', t => short(t.event_id)], ['Action', t => esc(t.action)], ['Version after', t => esc(t.version_after)], ['At', t => dt(t.created_at)]], r.transactions)}
        <h2>Vessel outbound events (${esc(r.vessel_id)})</h2>${edge ? table([['Event', e => short(e.event_id)], ['Type', e => esc(e.event_type)], ['Version', e => 'v' + e.version], ['Status', e => statusPill(e.status)], ['Created', e => dt(e.created_at)], ['Acked', e => dt(e.acked_at)], ['Central outcome', e => esc(e.central_outcome || '')]], edge.events) : '<div class="muted">vessel agent not reachable</div>'}
        <h2>Audit entries</h2>${auditTable(r.audit)}`;
    } catch (e) { $('#rh').innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  },
  async refresh() { const a = $('#au-a').value; $('#au').innerHTML = auditTable(await api('/api/audit?limit=300' + (a ? '&action=' + a : ''))); },
};
function auditTable(rows) { return table([['Time', a => dt(a.ts)], ['Actor', a => esc(a.actor)], ['Action', a => `<b>${esc(a.action)}</b>`], ['Entity', a => `${esc(a.entity)} <span class="mono">${esc(a.entity_id)}</span>`], ['Detail', a => `<span class="mono small">${esc(a.detail || '')}</span>`]], rows, 'No audit entries'); }

/* ------------------------------------------------------------------ actions */
document.addEventListener('click', async ev => {
  const t = ev.target.closest('[data-act],[data-goto-vessel]'); if (!t) return;
  if (t.dataset.gotoVessel) { PAGES.onboard.vessel = t.dataset.gotoVessel; return; }
  const a = t.dataset.act; ev.preventDefault();
  try {
    if (a === 'close-drawer') closeDrawer();
    if (a === 'link') { await api('/api/network/' + t.dataset.v, { method: 'POST', body: { state: t.dataset.state } }); toast(`${t.dataset.v} satellite ${t.dataset.state === 'CONNECTED' ? 'reconnected' : 'disconnected'}`); refreshTop(); current && current.refresh(); }
    if (a === 'replay') { const r = await api(`/api/vessel/${t.dataset.v}/events/${t.dataset.id}/replay`, { method: 'POST' }); toast('re-sending the same eventId ' + r.eventId.slice(0, 8) + '… (duplicate test)'); }
    if (a === 'conflict') PAGES.conflicts.detail(t.dataset.id);
    if (a === 'event') {
      const e = await api(`/api/vessel/${t.dataset.v}/events/${t.dataset.id}`);
      openDrawer(`<h2>Event ${esc(e.event_id)}</h2><div class="stat"><span>Status</span><span>${statusPill(e.status)}</span><span>Record</span><span>${esc(e.record_id)} v${e.version} · ${prio(e.priority)}</span><span>Sequence</span><span>${e.sequence_number}</span>
        <span>MQ queue</span><span class="mono">${esc(e.mq_queue || '—')}</span><span>Central outcome</span><span>${esc(e.central_outcome || '—')} ${esc(e.transaction_id || '')}</span><span>Accepted (MQ) / acknowledged</span><span>${dt(e.accepted_at)} / ${dt(e.acked_at)}</span></div>
        <h2>Delivery attempts</h2>${table([['#', x => x.attempt_no], ['Phase', x => esc(x.phase)], ['At', x => tm(x.started_at)], ['ms', x => esc(x.duration_ms)], ['Result', x => statusPill(x.result === 'OK' ? 'ACCEPTED' : x.result)], ['HTTP', x => esc(x.http_status || '')], ['Detail', x => `<span class="mono small">${esc((x.detail || '').slice(0, 300))}</span>`]], e.attemptsLog)}
        <h2>Event payload</h2><pre class="mono" style="white-space:pre-wrap">${esc(JSON.stringify(e.event, null, 2))}</pre>`);
    }
    if (a === 'edit-onboard') {
      const r = await api(`/api/vessel/${t.dataset.v}/records/${t.dataset.id}`);
      openDrawer(`<h2>Edit ${esc(r.record_id)} onboard ${esc(t.dataset.v)}</h2><div class="muted">v${r.version} · ${esc(r.record_type)} · sync ${esc(r.sync_status)}</div>
        <form id="ed" class="form" style="margin-top:12px">${Object.entries(r.payload).map(([k, v]) => `<div><label>${esc(k)}</label><input data-k="${esc(k)}" data-num="${typeof v === 'number'}" value="${esc(v)}"></div>`).join('')}
        <div><label>Priority</label><select name="priority">${PRIOS.map(p => `<option ${p === r.priority ? 'selected' : ''}>${p}</option>`).join('')}</select></div><div class="wide"><button class="primary" type="submit">Save change onboard</button></div></form>`);
      $('#ed').onsubmit = async e2 => {
        e2.preventDefault(); const changes = {};
        $$('[data-k]', e2.target).forEach(i => { const v = i.dataset.num === 'true' && i.value !== '' ? +i.value : i.value; if (v !== r.payload[i.dataset.k]) changes[i.dataset.k] = v; });
        const pr = e2.target.priority.value;
        try { const res = await api(`/api/vessel/${t.dataset.v}/records/${r.record_id}`, { method: 'PUT', body: { changes, priority: pr !== r.priority ? pr : undefined } }); toast(`${r.record_id} v${res.version} saved onboard · changed ${Object.keys(res.changedFields).join(', ')}`); closeDrawer(); current.refresh(); } catch (er) { toast(er.message, true); }
      };
    }
    if (a === 'office-record') {
      const r = await api('/api/central/records/' + t.dataset.id);
      openDrawer(`<h2>${esc(r.record_id)} · Office AMOS</h2><div class="muted">${esc(r.vessel_id)} · v${r.version} · ${esc(r.record_type)} ${r.conflict_open ? pill('open conflict', 'bad') : ''}</div>
        <form id="oe" class="form" style="margin-top:12px">${Object.entries(r.payload).map(([k, v]) => `<div><label>${esc(k)}</label><input data-k="${esc(k)}" data-num="${typeof v === 'number'}" value="${esc(v)}"></div>`).join('')}
        <div><label>Office user</label><input name="user" value="technical-superintendent"></div><div class="wide"><button class="primary" type="submit">Save office update</button></div></form>
        <h2>Version history</h2>${table([['Version', v => 'v' + v.version], ['Source', v => esc(v.change_source)], ['By', v => esc(v.changed_by)], ['At', v => tm(v.created_at)]], r.versions)}`);
      $('#oe').onsubmit = async e2 => {
        e2.preventDefault(); const changes = {};
        $$('[data-k]', e2.target).forEach(i => { const v = i.dataset.num === 'true' && i.value !== '' ? +i.value : i.value; if (v !== r.payload[i.dataset.k]) changes[i.dataset.k] = v; });
        if (!Object.keys(changes).length) return toast('nothing changed', true);
        try { const res = await api('/api/office/records', { method: 'POST', user: e2.target.user.value, body: { recordId: r.record_id, changes } }); toast(`${r.record_id} v${res.version} saved in office · queued for ${r.vessel_id}`); closeDrawer(); current.refresh(); } catch (er) { toast(er.message, true); }
      };
    }
  } catch (e) { toast(e.message, true); }
});
$('#drawer').addEventListener('click', e => { if (e.target.id === 'drawer') closeDrawer(); });

/* ------------------------------------------------------------------ router + polling */
let current = null, busy = false;
async function route() {
  const name = (location.hash || '#overview').slice(1).split('?')[0];
  const page = PAGES[name] || PAGES.overview;
  $$('nav a').forEach(a => a.classList.toggle('on', a.dataset.page === name));
  $('#page-title').textContent = page.title; current = page; closeDrawer();
  try { await page.mount($('#page')); await page.refresh(); } catch (e) { $('#page').innerHTML = `<div class="card empty">Could not load: ${esc(e.message)}</div>`; }
}
async function tick() {
  if (busy) return; busy = true;
  try { await refreshTop(); if (current && $('#drawer').hidden) await current.refresh(); } catch (e) { /* shown in-page */ } finally { busy = false; }
}
(async function init() {
  try { VESSELS = (await api('/api/vessels')).map(v => ({ vesselId: v.vesselId, name: v.name, group: v.group })); } catch (e) { toast('central office API not reachable', true); }
  window.addEventListener('hashchange', route); await route(); refreshTop(); refreshPlatform();
  setInterval(tick, 2000); setInterval(refreshPlatform, 5000);
})();
