/* ── Config ──────────────────────────────────────────────────────────────── */
const API = '';
let EXAM_ID = '';
let EXAM_NAME = '';
let EXAM_TYPE = '';   // "capture" | "match"

/* ── API helper — cookie auth ────────────────────────────────────────────── */
async function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json' };
  const res = await fetch(API + path, {
    method: opts.method || 'GET', headers,
    credentials: 'include',
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401) { doLogout(); throw new Error('Session expired'); }
  if (!res.ok) {
    const e = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(e.detail || 'Request failed');
  }
  const ct = res.headers.get('content-type') || '';
  return ct.includes('json') ? res.json() : res.blob();
}

/* ── Boot-time auto-login ────────────────────────────────────────────────── */
(async () => {
  try {
    const d = await api('/supervisor/exam');
    _initApp(d);
  } catch (_) {}
})();

function _initApp(examData) {
  EXAM_ID = examData.exam_id;
  EXAM_NAME = examData.name;
  EXAM_TYPE = examData.type;
  document.getElementById('sidebar-exam-name').textContent = EXAM_NAME;
  document.getElementById('page-exam-name').textContent = EXAM_NAME;
  document.getElementById('login-screen').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');
  loadDashTab();
}

/* ── Auth ────────────────────────────────────────────────────────────────── */
async function doLogin() {
  const phone = document.getElementById('login-phone').value;
  const pw = document.getElementById('login-pass').value;
  const errEl = document.getElementById('login-error');
  errEl.classList.add('hidden');
  try {
    await api('/supervisor/login', { method: 'POST', body: { phone, password: pw } });
    const d = await api('/supervisor/exam');
    _initApp(d);
  } catch (e) {
    errEl.textContent = e.message;
    errEl.classList.remove('hidden');
  }
}

async function doLogout() {
  if (autoRefreshTimer) { clearInterval(autoRefreshTimer); autoRefreshTimer = null; }
  const arCb = document.getElementById('auto-refresh');
  if (arCb) arCb.checked = false;
  EXAM_ID = ''; EXAM_NAME = ''; EXAM_TYPE = '';
  document.getElementById('sidebar-exam-name').textContent = '🔍 Supervisor';
  document.getElementById('page-exam-name').textContent = '';
  const shiftSel = document.getElementById('centers-shift-filter');
  if (shiftSel) shiftSel.innerHTML = '<option value="">All Shifts</option>';
  try { await api('/supervisor/logout', { method: 'POST' }); } catch (_) {}
  document.getElementById('app').classList.add('hidden');
  document.getElementById('login-screen').classList.remove('hidden');
}

/* ── Navigation ──────────────────────────────────────────────────────────── */
function showPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.add('hidden'));
  document.getElementById('page-' + name).classList.remove('hidden');
  document.querySelectorAll('.nav-link').forEach(a => a.classList.remove('active'));
  document.querySelector(`[data-page="${name}"]`)?.classList.add('active');
  document.getElementById('sidebar').classList.remove('open');
}

function toggleSidebar() { document.getElementById('sidebar').classList.toggle('open'); }

/* ── Dashboard tabs ──────────────────────────────────────────────────────── */
let currentDashTab = 'all-centers';
let autoRefreshTimer = null;

function switchDashTab(tab) {
  currentDashTab = tab;
  document.querySelectorAll('#page-dashboard .tab').forEach(t =>
    t.classList.toggle('active', t.dataset.tab === tab)
  );
  ['all-centers', 'matching', 'duplicates'].forEach(t => {
    document.getElementById('dash-tab-' + t).classList.toggle('hidden', t !== tab);
  });
  loadDashTab();
}

async function loadDashTab() {
  if (!EXAM_ID) return;
  if (currentDashTab === 'all-centers') {
    await loadCentersSummary();
  } else if (currentDashTab === 'matching') {
    await loadMatchingTab();
  } else if (currentDashTab === 'duplicates') {
    await loadDuplicatesTab();
  }
}

/* ── Helpers: populate per-tab dropdowns from summary rows ───────────────── */
function _populateTabDropdowns(rows) {
  const centerSelIds = ['att-sup-center', 'match-sup-center', 'dup-sup-center'];
  const shiftSelIds  = ['att-sup-shift',  'match-sup-shift',  'dup-sup-shift'];

  const seenCenters = new Map();
  const seenShifts  = new Map();
  rows.forEach(r => {
    if (!seenCenters.has(r.center_id))
      seenCenters.set(r.center_id, r.center_name || r.center_code);
    if (!seenShifts.has(r.shift_id))
      seenShifts.set(r.shift_id, r.shift_code || r.shift_id.slice(0, 8));
  });

  centerSelIds.forEach(id => {
    const sel = document.getElementById(id);
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = '<option value="">— Select Center —</option>' +
      [...seenCenters].map(([v, l]) =>
        `<option value="${v}"${v === cur ? ' selected' : ''}>${l}</option>`
      ).join('');
  });

  shiftSelIds.forEach(id => {
    const sel = document.getElementById(id);
    if (!sel) return;
    const cur = sel.value;
    sel.innerHTML = '<option value="">— Select Shift —</option>' +
      [...seenShifts].map(([v, l]) =>
        `<option value="${v}"${v === cur ? ' selected' : ''}>${l}</option>`
      ).join('');
  });
}

/* ── All Centers summary ─────────────────────────────────────────────────── */
async function loadCentersSummary() {
  if (!EXAM_ID) return;
  const sid = document.getElementById('centers-shift-filter').value;
  let url = `/supervisor/centers-summary`;
  if (sid) url += `?shift_id=${sid}`;
  try {
    const rows = await api(url);
    const isMatch = EXAM_TYPE === 'match';

    // Shift filter
    const shiftSel = document.getElementById('centers-shift-filter');
    const knownShiftIds = new Set(Array.from(shiftSel.options).map(o => o.value).filter(Boolean));
    rows.forEach(r => {
      if (r.shift_id && !knownShiftIds.has(r.shift_id)) {
        const label = r.shift_code ? `[${r.shift_code}]` : r.shift_id.slice(0, 8);
        const opt = document.createElement('option');
        opt.value = r.shift_id; opt.textContent = label;
        shiftSel.appendChild(opt);
        knownShiftIds.add(r.shift_id);
      }
    });

    // Dynamic thead
    const headers = isMatch
      ? ['Center', 'Code', 'Shift', 'Total', 'Present', 'Absent',
         'Photo ✓', 'Photo ✗', 'FP ✓', 'FP ✗', 'Iris ✓', 'Iris ✗',
         'Devices', 'Supervisor', 'Vendor']
      : ['Center', 'Code', 'Shift', 'Total', 'Present', 'Absent',
         'Matched', 'Mismatched', 'Duplicates', 'Devices', 'Supervisor', 'Vendor'];
    document.getElementById('ac-sup-thead').innerHTML =
      '<tr>' + headers.map(h => `<th>${h}</th>`).join('') + '</tr>';

    // Body rows
    const tbody = document.getElementById('centers-summary-body');
    tbody.innerHTML = rows.map(r => {
      const midCells = isMatch
        ? `<td>${r.matched}</td><td>${r.mismatched}</td>
           <td>${r.fp_matched ?? 0}</td><td>${r.fp_mismatched ?? 0}</td>
           <td>${r.iris_matched ?? 0}</td><td>${r.iris_mismatched ?? 0}</td>`
        : `<td><a href="#" onclick="drillToMatching('${r.center_id}','${r.shift_id}');return false">${r.matched}</a></td>
           <td><a href="#" onclick="drillToMatching('${r.center_id}','${r.shift_id}');return false">${r.mismatched}</a></td>
           <td><a href="#" onclick="drillToDuplicates('${r.center_id}','${r.shift_id}');return false">${r.duplicates}</a></td>`;
      return `<tr>
        <td><a href="#" onclick="drillToMatching('${r.center_id}','${r.shift_id}');return false">${r.center_name || r.center_code}</a></td>
        <td>${r.center_code}</td>
        <td>${r.shift_code || r.shift_id.slice(0, 8)}</td>
        <td>${r.total}</td><td>${r.present}</td><td>${r.absent}</td>
        ${midCells}
        <td>${r.active_devices}</td>
        <td>${r.supervisor_name || '—'}</td>
        <td>${r.vendor_name || '—'}</td>
      </tr>`;
    }).join('');

    // Totals row
    const tot   = rows.reduce((s, r) => s + r.total,   0);
    const pres  = rows.reduce((s, r) => s + r.present, 0);
    const abs   = rows.reduce((s, r) => s + r.absent,  0);
    const match = rows.reduce((s, r) => s + r.matched, 0);
    const mism  = rows.reduce((s, r) => s + r.mismatched, 0);
    const dups  = rows.reduce((s, r) => s + r.duplicates, 0);
    const fpM   = rows.reduce((s, r) => s + (r.fp_matched   ?? 0), 0);
    const fpX   = rows.reduce((s, r) => s + (r.fp_mismatched ?? 0), 0);
    const irisM = rows.reduce((s, r) => s + (r.iris_matched   ?? 0), 0);
    const irisX = rows.reduce((s, r) => s + (r.iris_mismatched ?? 0), 0);
    const devs  = rows.reduce((s, r) => s + r.active_devices, 0);
    const midTotals = isMatch
      ? `<td><strong>${match}</strong></td><td><strong>${mism}</strong></td>
         <td><strong>${fpM}</strong></td><td><strong>${fpX}</strong></td>
         <td><strong>${irisM}</strong></td><td><strong>${irisX}</strong></td>`
      : `<td><strong>${match}</strong></td><td><strong>${mism}</strong></td><td><strong>${dups}</strong></td>`;
    document.getElementById('ac-sup-tfoot').innerHTML =
      `<tr><td colspan="3"><strong>TOTAL</strong></td>
       <td><strong>${tot}</strong></td><td><strong>${pres}</strong></td><td><strong>${abs}</strong></td>
       ${midTotals}
       <td><strong>${devs}</strong></td><td></td><td></td></tr>`;

    // Stat cards
    document.getElementById('ac-sup-total').textContent   = tot;
    document.getElementById('ac-sup-present').textContent = pres;
    document.getElementById('ac-sup-absent').textContent  = abs;
    document.getElementById('ac-sup-stats').classList.remove('hidden');
    document.getElementById('ac-sup-charts').classList.remove('hidden');
    drawPie('ac-sup-chart-att', [
      { label: 'Present', value: pres, color: '#10b981' },
      { label: 'Absent',  value: abs,  color: '#ef4444' },
    ]);
    const photoPend = tot - match - mism;
    drawPie('ac-sup-chart-photo', [
      { label: 'Matched',  value: match,     color: '#10b981' },
      { label: 'Mismatch', value: mism,      color: '#ef4444' },
      { label: 'Pending',  value: photoPend, color: '#f59e0b' },
    ]);

    // Populate per-tab dropdowns from summary data
    _populateTabDropdowns(rows);

  } catch (e) { console.error('[loadCentersSummary]', e); }
}

function drillToMatching(centerId, shiftId) {
  _setTabDropdowns('match-sup-center', 'match-sup-shift', centerId, shiftId);
  switchDashTab('matching');
}

function drillToDuplicates(centerId, shiftId) {
  _setTabDropdowns('dup-sup-center', 'dup-sup-shift', centerId, shiftId);
  switchDashTab('duplicates');
}

function _setTabDropdowns(centerId, shiftId, cval, sval) {
  const cs = document.getElementById(centerId);
  const ss = document.getElementById(shiftId);
  if (cs) cs.value = cval;
  if (ss) ss.value = sval;
}

function toggleAutoRefresh() {
  const on = document.getElementById('auto-refresh').checked;
  if (on) {
    autoRefreshTimer = setInterval(loadCentersSummary, 30000);
  } else {
    clearInterval(autoRefreshTimer);
    autoRefreshTimer = null;
  }
}

/* ── Attendance ──────────────────────────────────────────────────────────── */
async function loadAttendance() {
  const cid = document.getElementById('att-sup-center').value;
  const sid = document.getElementById('att-sup-shift').value;
  const prompt  = document.getElementById('att-sup-prompt');
  const content = document.getElementById('att-sup-content');
  if (!cid || !sid) {
    prompt.classList.remove('hidden');
    content.classList.add('hidden');
    return;
  }
  prompt.classList.add('hidden');
  content.classList.remove('hidden');

  let url = `/supervisor/attendance?exam_id=${EXAM_ID}&center_id=${cid}&shift_id=${sid}`;
  try {
    const data = await api(url);
    const present = data.filter(d => d.attended).length;
    const absent = data.length - present;
    document.getElementById('dash-total').textContent   = data.length;
    document.getElementById('dash-present').textContent = present;
    document.getElementById('dash-absent').textContent  = absent;
    drawPie('dash-chart', [
      { label: 'Present', value: present, color: '#10b981' },
      { label: 'Absent',  value: absent,  color: '#ef4444' },
    ]);
    document.getElementById('dash-body').innerHTML = data.map((d, i) => `<tr>
      <td>${i + 1}</td><td>${d.name}</td><td>${d.roll_no || '—'}</td>
      <td>${d.father_name || '—'}</td><td>${d.dob || '—'}</td>
      <td><span class="badge ${d.attended ? 'badge-yes' : 'badge-no'}">${d.attended ? 'Yes' : 'No'}</span></td>
      <td>${d.center_code}</td>
      <td>${d.shift_code || d.shift_date + ' ' + d.shift_time}</td>
    </tr>`).join('');
  } catch (e) { console.error('[loadAttendance]', e); }
}

/* ── Matching ────────────────────────────────────────────────────────────── */
function _thumb(b64, mime = 'image/jpeg') {
  if (!b64) return '—';
  return `<img src="data:${mime};base64,${b64}" style="height:40px;border-radius:3px;cursor:pointer"
    onclick="window.open('data:${mime};base64,${b64}')">`;
}

async function loadMatchingTab() {
  const cid = document.getElementById('match-sup-center').value;
  const sid = document.getElementById('match-sup-shift').value;
  const prompt  = document.getElementById('match-sup-prompt');
  const content = document.getElementById('match-sup-content');
  if (!cid || !sid) {
    prompt.classList.remove('hidden');
    content.classList.add('hidden');
    return;
  }
  prompt.classList.add('hidden');
  content.classList.remove('hidden');

  const isMatch = EXAM_TYPE === 'match';
  let url = `/supervisor/matching?exam_id=${EXAM_ID}&center_id=${cid}&shift_id=${sid}`;
  try {
    const data = await api(url);
    const matched = data.filter(d => d.photo_match_status === 'match').length;
    const failed  = data.filter(d => d.photo_match_status === 'mismatch').length;
    const pending = data.length - matched - failed;
    document.getElementById('match-matched').textContent = matched;
    document.getElementById('match-failed').textContent  = failed;
    document.getElementById('match-pending').textContent = pending;
    drawPie('match-chart', [
      { label: 'Matched', value: matched, color: '#10b981' },
      { label: 'Failed',  value: failed,  color: '#ef4444' },
      { label: 'Pending', value: pending, color: '#f59e0b' },
    ]);

    // Dynamic headers
    const headers = isMatch
      ? ['No', 'Name', 'Attended', 'Ref Photo', 'Captured',
         'Photo', 'Ref FP', 'Live FP', 'FP', 'Ref Iris', 'Live Iris', 'Iris', 'Center', 'Shift']
      : ['No', 'Name', 'Attended', 'Ref Photo', 'Captured', 'Photo', 'Center', 'Shift'];
    document.getElementById('sup-match-thead').innerHTML =
      '<tr>' + headers.map(h => `<th>${h}</th>`).join('') + '</tr>';

    const mismatches = data.filter(d =>
      d.photo_match_status === 'mismatch' ||
      (isMatch && (d.fingerprint_match_status === 'mismatch' || d.iris_match_status === 'mismatch'))
    );
    document.getElementById('match-body').innerHTML = mismatches.map((d, i) => {
      const midCells = isMatch
        ? [
            _thumb(d.photo_data), _thumb(d.captured_photo_data), badge(d.photo_match_status),
            _thumb(d.fingerprint_data, 'image/bmp'), _thumb(d.live_fingerprint_data, 'image/bmp'), badge(d.fingerprint_match_status),
            _thumb(d.iris_data, 'image/bmp'), _thumb(d.live_iris_data, 'image/bmp'), badge(d.iris_match_status),
          ]
        : [_thumb(d.photo_data), _thumb(d.captured_photo_data), badge(d.photo_match_status)];
      return `<tr>
        <td>${i + 1}</td><td>${d.name}</td>
        <td><span class="badge ${d.attended ? 'badge-yes' : 'badge-no'}">${d.attended ? 'Yes' : 'No'}</span></td>
        ${midCells.map(c => `<td>${c}</td>`).join('')}
        <td>${d.center_code}</td>
        <td>${d.shift_code || ''}</td>
      </tr>`;
    }).join('');
  } catch (e) { console.error('[loadMatchingTab]', e); }
}

/* ── Duplicates ──────────────────────────────────────────────────────────── */
async function loadDuplicatesTab() {
  const cid = document.getElementById('dup-sup-center').value;
  const sid = document.getElementById('dup-sup-shift').value;
  const prompt  = document.getElementById('dup-sup-prompt');
  const content = document.getElementById('dup-sup-content');
  if (!cid || !sid) {
    prompt.classList.remove('hidden');
    content.classList.add('hidden');
    return;
  }
  prompt.classList.add('hidden');
  content.classList.remove('hidden');

  const url = `/supervisor/matching/duplicates?exam_id=${EXAM_ID}&center_id=${cid}&shift_id=${sid}`;
  try {
    const rows = await api(url);
    const emptyEl = document.getElementById('dup-empty');
    const listEl  = document.getElementById('dup-list');
    if (!rows.length) {
      emptyEl.classList.remove('hidden');
      listEl.innerHTML = '';
      return;
    }
    emptyEl.classList.add('hidden');
    // Group flat rows by candidate_no
    const groups = {};
    rows.forEach(r => {
      if (!groups[r.candidate_no]) groups[r.candidate_no] = { name: r.name, entries: [] };
      groups[r.candidate_no].entries.push(r);
    });
    listEl.innerHTML = Object.entries(groups).map(([cno, g]) => `
      <div class="card" style="margin-bottom:1rem">
        <p><strong>${g.name}</strong> (${cno}) — ${g.entries.length} captures</p>
        <table style="width:100%"><thead><tr>
          <th>Ref Photo</th><th>Captured Photo</th><th>Match</th><th>Keep</th>
        </tr></thead><tbody>
        ${g.entries.map(e => `<tr>
          <td>${_thumb(e.photo_data)}</td>
          <td>${_thumb(e.captured_photo_data)}</td>
          <td>${badge(e.photo_match_status)}</td>
          <td><button onclick="resolvedup('${e.capture_id}')">Keep This</button></td>
        </tr>`).join('')}
        </tbody></table>
      </div>
    `).join('');
  } catch (e) { console.error('[loadDuplicatesTab]', e); }
}

async function resolvedup(keepCaptureId) {
  if (!confirm('Keep this capture and delete all others for this candidate?')) return;
  try {
    await api('/supervisor/matching/resolve-duplicate', {
      method: 'POST',
      body: { keep_capture_id: keepCaptureId },
    });
    alert('Duplicate resolved.');
    loadDashTab();
  } catch (e) { alert('Error: ' + e.message); }
}

function badge(val) {
  if (val == null || val === '') return '—';
  const s = String(val).toLowerCase();
  if (s === 'match' || s === 'true')    return '<span class="badge badge-yes">Match</span>';
  if (s === 'mismatch' || s === 'false') return '<span class="badge badge-no">Fail</span>';
  return '<span class="badge badge-pending">Pending</span>';
}

/* ── Downloads ───────────────────────────────────────────────────────────── */
async function download(type, fmt) {
  const centerSel = type === 'attendance' ? 'att-sup-center' : 'match-sup-center';
  const shiftSel  = type === 'attendance' ? 'att-sup-shift'  : 'match-sup-shift';
  const cid = document.getElementById(centerSel)?.value || '';
  const sid = document.getElementById(shiftSel)?.value  || '';
  let url = `/supervisor/${type}/download?exam_id=${EXAM_ID}&format=${fmt}`;
  if (cid) url += `&center_id=${cid}`;
  if (sid) url += `&shift_id=${sid}`;
  try {
    const blob = await api(url);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = type + '.' + fmt; a.click();
    URL.revokeObjectURL(a.href);
  } catch (e) { alert('Download failed: ' + e.message); }
}

/* ── Pie chart ───────────────────────────────────────────────────────────── */
function drawPie(canvasId, slices) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width = canvas.offsetWidth;
  const h = canvas.height = 220;
  ctx.clearRect(0, 0, w, h);
  const total = slices.reduce((a, s) => a + s.value, 0);
  if (total === 0) return;
  const cx = w / 2 - 60, cy = h / 2, r = 80, ir = 45;
  let angle = -Math.PI / 2;
  slices.forEach(s => {
    const sa = (s.value / total) * Math.PI * 2;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(angle) * ir, cy + Math.sin(angle) * ir);
    ctx.arc(cx, cy, r, angle, angle + sa);
    ctx.arc(cx, cy, ir, angle + sa, angle, true);
    ctx.closePath();
    ctx.fillStyle = s.color; ctx.fill();
    angle += sa;
  });
  let ly = 30;
  slices.forEach(s => {
    ctx.fillStyle = s.color;
    ctx.fillRect(w - 120, ly, 14, 14);
    ctx.fillStyle = '#334155';
    ctx.font = '13px sans-serif';
    ctx.fillText(`${s.label}: ${s.value}`, w - 100, ly + 12);
    ly += 24;
  });
}
