import { $, $$, esc, icon, api, toast, fmtDate, fmtDateTime, checkPill, STATUS_TONES, initTheme, logout } from '/common.js';

initTheme();

const FILTERS = [
  ['in_review', 'Under review'], ['action_needed', 'Action needed'], ['checking', 'Checking'],
  ['draft', 'Documents needed'], ['approved', 'Approved'], ['rejected', 'Not approved'], ['', 'All'],
];
let filter = 'in_review';
let search = '';
let searchTimer = null;

function renderTiles(counts) {
  const tiles = [['in_review', 'Needs review', 'shield'], ['action_needed', 'Waiting on borrower', 'alert'], ['approved', 'Approved', 'check'], ['rejected', 'Not approved', 'x']];
  $('#tiles').innerHTML = tiles.map(([key, label, glyph]) => `<button class="card stat-tile ${STATUS_TONES[key]}" data-tile="${key}"><span class="stat-icon">${icon(glyph)}</span><span><b>${counts[key] || 0}</b><small>${label}</small></span></button>`).join('');
  $$('[data-tile]').forEach(b => { b.onclick = () => { filter = b.dataset.tile; load(); }; });
  $('#filters').innerHTML = FILTERS.map(([key, label]) => {
    const n = key ? (counts[key] || 0) : Object.values(counts).reduce((a, b) => a + b, 0);
    return `<button role="tab" data-filter="${key}" aria-selected="${filter === key}">${label}<span class="count">${n}</span></button>`;
  }).join('');
  $$('[data-filter]').forEach(b => { b.onclick = () => { filter = b.dataset.filter; load(); }; });
}

function renderRows(rows) {
  $('#queue-empty').hidden = rows.length > 0;
  $('#queue-body').innerHTML = rows.map(r => `
    <tr data-open="${r.id}" tabindex="0" role="link" aria-label="Open application ${esc(r.ref)}">
      <td><b>${esc(r.borrower)}</b><small class="cell-sub">${esc(r.email)}</small></td>
      <td class="num">${esc(r.ref)}</td>
      <td><span class="status-badge ${STATUS_TONES[r.status] || ''}">${esc(r.status_label)}</span></td>
      <td>${checkPill(r.check_status)}${r.review_required ? `<small class="cell-flag">${icon('alert')}Needs human review</small>` : ''}</td>
      <td>${r.documents}${r.reuploads ? ` <small class="cell-flag">${r.reuploads} re-upload</small>` : ''}</td>
      <td>${fmtDate(r.submitted)}</td>
      <td>${fmtDateTime(r.updated)}</td>
    </tr>`).join('');
  $$('[data-open]').forEach(tr => {
    const go = () => { location.href = `/staff/applications/${tr.dataset.open}`; };
    tr.onclick = go;
    tr.onkeydown = e => { if (e.key === 'Enter') go(); };
  });
}

async function load() {
  try {
    const params = new URLSearchParams({ status: filter, q: search });
    const data = await api('GET', `/api/staff/applications?${params}`);
    renderTiles(data.counts);
    renderRows(data.applications);
  } catch (error) {
    if (error.status === 401 || error.status === 404) showAuth();
    else toast(error.message);
  }
}

$('#search').oninput = e => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { search = e.target.value; load(); }, 250);
};

$('#auth-form').onsubmit = async e => {
  e.preventDefault();
  const form = e.target;
  $('#auth-submit').disabled = true;
  try {
    const result = await api('POST', '/api/auth/login', { email: form.email.value.trim(), password: form.password.value });
    if (result.role !== 'lender') {
      await api('POST', '/api/auth/logout');
      throw new Error('This account is not a staff account. Borrowers sign in at the borrower portal.');
    }
    form.reset();
    await start();
  } catch (error) {
    $('#auth-error').textContent = error.message;
    $('#auth-error').hidden = false;
  } finally {
    $('#auth-submit').disabled = false;
  }
};
$('#logout-btn').onclick = logout;

function showAuth() {
  $('#queue-view').hidden = true;
  $('#auth').hidden = false;
  ['#user-chip', '#logout-btn', '#verify-link'].forEach(s => { $(s).hidden = true; });
}

async function start() {
  try {
    const me = await api('GET', '/api/auth/me');
    if (me.role !== 'lender') {
      showAuth();
      $('#auth-error').textContent = 'You are signed in as a borrower. Sign out first to use a staff account.';
      $('#auth-error').hidden = false;
      return;
    }
    $('#user-chip').querySelector('span').textContent = me.name;
    $('#user-chip').classList.add('local');
    ['#user-chip', '#logout-btn', '#verify-link'].forEach(s => { $(s).hidden = false; });
    $('#auth').hidden = true;
    $('#queue-view').hidden = false;
    await load();
  } catch {
    showAuth();
  }
}

start();
