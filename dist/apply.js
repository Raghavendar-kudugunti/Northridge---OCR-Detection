import { $, $$, esc, icon, api, toast, fmtDate, fmtDateTime, fmtSize, checkPill, STATUS_TONES, initTheme, logout } from '/common.js';

initTheme();

const SLOT_ICONS = { id: 'id', slip: 'file', bank: 'bank', other1: 'plus', other2: 'plus' };
const DOC_STATUS = {
  checking: ['info', 'clock', 'Checking…'],
  accepted: ['ok', 'check', 'Accepted'],
  reupload: ['warn', 'alert', 'Re-upload needed'],
};
let mode = 'login';
let pickSlot = null;
let activeUploadSlot = 'id';
let pollTimer = null;
let busySlot = null;
let current = null;

// ---------------------------------------------------------------- auth
function setMode(next) {
  mode = next;
  $$('[data-auth]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.auth === mode)));
  $('#name-row').hidden = mode !== 'register';
  $('#pw-hint').hidden = mode !== 'register';
  $('#auth-submit').textContent = mode === 'register' ? 'Create account' : 'Sign in';
  $('#auth-form').password.autocomplete = mode === 'register' ? 'new-password' : 'current-password';
  $('#auth-error').hidden = true;
}
$$('[data-auth]').forEach(b => { b.onclick = () => setMode(b.dataset.auth); });

$('#auth-form').onsubmit = async e => {
  e.preventDefault();
  const form = e.target;
  const body = { email: form.email.value.trim(), password: form.password.value };
  if (mode === 'register') body.name = form.name.value.trim();
  $('#auth-submit').disabled = true;
  try {
    const result = await api('POST', mode === 'register' ? '/api/auth/register' : '/api/auth/login', body);
    if (result.role !== 'borrower') {
      await api('POST', '/api/auth/logout');
      throw new Error('This is a staff account. Staff use the internal staff sign-in, not the borrower portal.');
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

// ---------------------------------------------------------------- dashboard
// Processing pipeline (same stages the backend runs): pre-scan -> OCR -> PII masking -> LLM evaluation -> verification.
const STAGES = [
  ['prescan', 'Pre-scan', 'scan'], ['ocr', 'OCR', 'text'], ['pii', 'PII masking', 'shield'],
  ['llm', 'LLM evaluation', 'sparkles'], ['verify', 'Verification', 'scale'],
];
function pipeline(app) {
  const byKey = Object.fromEntries((app.pipeline || []).map(s => [s.key, s]));
  const hasRun = (app.pipeline || []).length > 0;
  const elapsed = app.status === 'checking' && app.updated
    ? Math.max(0, Math.floor(Date.now() / 1000 - app.updated)) : 0;
  const elapsedText = elapsed < 60 ? `${elapsed}s elapsed` : `${Math.floor(elapsed / 60)}m ${elapsed % 60}s elapsed`;
  $('#pipeline-note').textContent = app.status === 'checking' ? `Processing your documents (${elapsedText})`
    : hasRun ? `Last run ${fmtDateTime(app.updated)}` : 'Runs when you submit your documents for verification.';
  $('#pipeline').innerHTML = STAGES.map(([key, label, glyph]) => {
    const stage = byKey[key];
    let cls = '';
    let detail = '?';
    let mark = '';
    if (stage) {
      cls = stage.status === 'skipped' ? 'skipped' : stage.status;
      detail = stage.detail;
      mark = stage.status === 'done' ? icon('check') : stage.status === 'warn' ? icon('alert') : '';
    } else if (app.status === 'checking') {
      detail = 'waiting for result';
    }
    return `<li class="step ${cls}"><span class="step-icon">${icon(glyph)}</span><div class="step-body"><div class="step-title">${label}<span class="step-state">${mark}</span></div><div class="step-detail" title="${esc(detail)}">${esc(detail)}</div></div></li>`;
  }).join('');
}

function banner(app) {
  const reupload = app.slots.filter(s => s.document?.status === 'reupload').length;
  const missing = app.slots.filter(s => s.missing).length;
  const attention = reupload + missing;
  const messages = {
    draft: ['', 'upload', 'Upload your documents and check each preview. When everything is right, submit for verification.'],
    checking: ['info', 'clock', 'Your documents are being processed locally. The elapsed time shows how long this run has been in progress.'],
    action_needed: attention
      ? ['warn', 'alert', `${attention} document${attention === 1 ? ' needs' : 's need'} your attention. Replace ${attention === 1 ? 'it' : 'them'} below, then resubmit for verification.`]
      : ['info', 'upload', 'You replaced a document. Submit again to update your verification before the final decision.'],
    in_review: ['info', 'shield', 'Your documents are submitted and verified. Our team is reviewing your application.'],
    approved: ['ok', 'check', 'Your documents have been approved.'],
    rejected: ['bad', 'x', `Your documents were not approved.${app.decision_reason ? ` Reason: ${app.decision_reason}` : ''}`],
  };
  const [tone, glyph, text] = messages[app.status];
  $('#banner').className = `banner ${tone}`;
  $('#banner').innerHTML = `${app.status === 'checking' ? '<span class="spinner"></span>' : icon(glyph)}<span>${esc(text)}</span>`;
}

function docStatus(app, d) {
  if (!d) return ['', 'minus', 'Not uploaded'];
  if (d.status === 'uploaded') return app.status === 'draft' ? ['info', 'check', 'Ready to submit'] : ['info', 'upload', 'New copy · resubmit to verify'];
  return DOC_STATUS[d.status] || ['', 'minus', d.status];
}

function docCard(app, slot) {
  const d = slot.document;
  const [tone, glyph, label] = docStatus(app, d);
  const attention = d?.status === 'reupload' || slot.missing;
  const buttons = [];
  if (d?.status === 'accepted') buttons.push(`<button class="btn btn-ghost btn-sm" data-view="${slot.slot}" type="button">Preview</button>`);
  if (slot.can_upload) buttons.push(`<button class="btn btn-outline btn-sm" data-edit="${slot.slot}" type="button" ${busySlot ? 'disabled' : ''}>Edit</button>`);
  if (slot.can_remove) buttons.push(`<button class="btn btn-ghost btn-sm danger-text" data-remove="${slot.slot}" type="button" ${busySlot ? 'disabled' : ''}>Cancel</button>`);
  const thumb = d && d.status !== 'accepted'
    ? `<button class="doc-thumb" data-view="${slot.slot}" title="View ${esc(noun(slot.title))}"><img src="/api/borrower/documents/${slot.slot}/preview?size=thumb&v=${d.version}" alt="Preview of your ${esc(noun(slot.title))}" loading="lazy"></button>`
    : `<span class="doc-row-icon" aria-hidden="true">${icon(d?.status === 'accepted' ? 'check' : SLOT_ICONS[slot.slot])}</span>`;
  const reason = slot.missing
    ? `Required: upload your ${slot.slot === 'id' ? 'ID card' : 'salary slip'}, then resubmit.`
    : d?.reason && d.status === 'reupload' ? d.reason : '';
  return `<article class="doc-row ${attention ? 'attention' : ''}">
    ${thumb}
    <div class="doc-row-body">
      <div class="doc-row-head"><b>${esc(slot.title)}</b><span class="req ${slot.required ? 'required' : ''}">${slot.required ? 'Required' : 'Optional'}</span></div>
      <p class="doc-row-hint">${esc(slot.hint)}</p>
      <div class="doc-row-status"><span class="pill ${tone}">${icon(glyph)}${label}</span>${d ? `<span class="muted doc-meta" title="${d.filename ? esc(d.filename) + ' · ' : ''}${esc(d.kind.toUpperCase())} · ${fmtSize(d.size)} · version ${d.version} · ${fmtDateTime(d.uploaded)}">${d.filename ? `<b>${esc(d.filename)}</b> · ` : ''}${esc(d.kind.toUpperCase())} · ${fmtSize(d.size)} · version ${d.version} · ${fmtDateTime(d.uploaded)}</span>` : ''}</div>
      ${reason ? `<p class="doc-row-reason">${d?.requested_by_reviewer ? '<b>From our review team:</b> ' : ''}${esc(reason)}</p>` : ''}
    </div>
    <div class="doc-row-action">${buttons.join('')}</div>
  </article>`;
}

function renderUploadPanel(slots) {
  const available = slots.filter(s => s.can_upload);
  if (!available.some(s => s.slot === activeUploadSlot)) activeUploadSlot = available[0]?.slot || slots[0]?.slot;
  const active = slots.find(s => s.slot === activeUploadSlot);
  const labels = { id: 'ID card', slip: 'Salary slip', bank: 'Bank statement', other1: 'Other 1', other2: 'Other 2' };
  $('#upload-types').innerHTML = slots.map(s => `<button class="upload-type ${s.slot === activeUploadSlot ? 'selected' : ''}" type="button" data-upload-type="${s.slot}" aria-pressed="${s.slot === activeUploadSlot}" ${!s.can_upload ? 'disabled' : ''}><span class="upload-type-icon">${icon(SLOT_ICONS[s.slot])}</span><span>${labels[s.slot]}${s.required ? '<small>Required</small>' : ''}</span>${s.document ? `<i class="upload-type-check">${icon(s.document.status === 'reupload' ? 'alert' : 'check')}</i>` : ''}</button>`).join('');
  const doc = active?.document;
  $('#drop-title').textContent = doc ? `Replace ${active.title}` : `Drag and drop your ${active?.title || 'document'} here`;
  $('#upload-help').textContent = active?.can_upload
    ? doc ? `This will replace version ${doc.version}. Older versions stay in your application history.`
      : `${active.required ? 'Required document' : 'Optional document'} · Maximum file size: 10 MB.`
    : 'Documents are locked after a final decision.';
  $('#dropzone').disabled = !active?.can_upload || Boolean(busySlot);
  $('#dropzone').setAttribute('aria-label', active?.can_upload ? `Choose a file for ${active.title}` : 'Document upload unavailable');
  $$('[data-upload-type]').forEach(button => { button.onclick = () => { activeUploadSlot = button.dataset.uploadType; renderUploadPanel(slots); }; });
}

function submitBar(app) {
  const bar = $('#submit-bar');
  bar.hidden = !['draft', 'action_needed'].includes(app.status);
  if (bar.hidden) return;
  const again = app.resubmit;
  bar.innerHTML = `<div class="submit-text">
      <b>${again ? 'Ready to resubmit?' : 'Ready to submit?'}</b>
      <p>${esc(app.submit_hint || '')}</p>
      <small>Submitting runs pre-scan, PDF text extraction or OCR, PII masking, optional LLM extraction when fields are missing, and verification. You can replace a document and resubmit before a final decision.</small>
    </div>
    <button class="btn btn-primary" id="submit-btn" ${app.can_submit && !busySlot ? '' : 'disabled'}>${again ? 'Resubmit for verification' : 'Submit for verification'} <span aria-hidden="true">→</span></button>`;
  $('#submit-btn').onclick = submitApplication;
}

function render(app) {
  current = app;
  $('#app-ref').textContent = `Application ${app.ref}`;
  $('#app-dates').textContent = `Started ${fmtDate(app.created)} · last updated ${fmtDateTime(app.updated)}`;
  const badge = $('#app-status');
  badge.className = `status-badge ${STATUS_TONES[app.status] || ''}`;
  badge.textContent = app.status_label;
  $('#app-check').innerHTML = checkPill(app.check?.status);
  $$('#legend [data-check]').forEach(li => li.classList.toggle('active', li.dataset.check === app.check?.status));
  pipeline(app);
  banner(app);
  const main = app.slots.filter(s => !s.slot.startsWith('other'));
  const others = app.slots.filter(s => s.slot.startsWith('other'));
  renderUploadPanel(app.slots);
  $('#doc-list').innerHTML = main.map(s => docCard(app, s)).join('')
    + `<div class="doc-subhead">Other related documents <span class="muted">(optional)</span></div>`
    + others.map(s => docCard(app, s)).join('');
  submitBar(app);
  $('#dropzone').onclick = () => {
    if (!activeUploadSlot) return;
    pickSlot = activeUploadSlot;
    $('#file-input').click();
  };
  $$('[data-remove]').forEach(b => { b.onclick = () => removeDocument(b.dataset.remove); });
  $$('[data-edit]').forEach(b => { b.onclick = () => { pickSlot = b.dataset.edit; $('#file-input').click(); }; });
  $$('[data-view]').forEach(b => { b.onclick = () => viewDocument(b.dataset.view); });
  clearTimeout(pollTimer);
  if (app.status === 'checking') pollTimer = setTimeout(refresh, 1500);
}

async function refresh() {
  try {
    render(await api('GET', '/api/borrower/application'));
  } catch (error) {
    if (error.status === 401) showAuth();
    else toast(error.message);
  }
}

const slotTitle = slot => current?.slots.find(s => s.slot === slot)?.title || 'document';
const noun = title => (title.startsWith('ID') ? title : title.toLowerCase());  // 'ID card' keeps its capitals

// ---- preview before upload ----
async function checkFile(file) {
  if (!file.size || file.size > 10 * 1024 * 1024) return 'Each file must contain data and be no larger than 10 MB.';
  const head = new Uint8Array(await file.slice(0, 8).arrayBuffer());
  const pdf = String.fromCharCode(...head.slice(0, 5)) === '%PDF-';
  const png = [137, 80, 78, 71, 13, 10, 26, 10].every((v, i) => head[i] === v);
  const jpg = head[0] === 255 && head[1] === 216 && head[2] === 255;
  if (!pdf && !png && !jpg) return 'Unsupported file. Choose a PDF, PNG or JPEG.';
  return null;
}

async function confirmUpload(slot, file) {
  const problem = await checkFile(file);
  if (problem) return toast(problem);
  const url = URL.createObjectURL(file);
  const isPdf = file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf');
  const dialog = $('#confirm-dialog');
  $('#confirm-title').textContent = `Upload as your ${noun(slotTitle(slot))}?`;
  $('#confirm-meta').textContent = `${file.name} · ${fmtSize(file.size)}`;
  $('#confirm-preview').innerHTML = isPdf
    ? `<iframe src="${url}#toolbar=0&view=FitH" title="Preview of ${esc(file.name)}"></iframe>`
    : `<img src="${url}" alt="Preview of ${esc(file.name)}">`;
  const close = () => { dialog.close(); $('#confirm-preview').innerHTML = ''; URL.revokeObjectURL(url); };
  $('#confirm-upload').onclick = () => { close(); uploadFile(slot, file); };
  $('#confirm-other').onclick = () => { close(); pickSlot = slot; $('#file-input').click(); };
  $('#confirm-cancel').onclick = close;
  dialog.onclose = () => { $('#confirm-preview').innerHTML = ''; URL.revokeObjectURL(url); };
  dialog.showModal();
}

// ---- larger view of an uploaded document ----
function viewDocument(slot) {
  const d = current?.slots.find(s => s.slot === slot)?.document;
  if (!d) return;
  $('#view-title').textContent = `${slotTitle(slot)} · version ${d.version}`;
  $('#view-body').innerHTML = `<img src="/api/borrower/documents/${slot}/preview?size=large&v=${d.version}" alt="Your ${esc(noun(slotTitle(slot)))}, page 1">`;
  const slotState = current.slots.find(s => s.slot === slot);
  $('#view-cancel').onclick = () => $('#view-dialog').close();
  const edit = $('#view-edit');
  edit.hidden = !slotState?.can_upload;
  edit.onclick = () => {
    $('#view-dialog').close();
    pickSlot = slot;
    $('#file-input').click();
  };
  $('#view-dialog').showModal();
}
$('#view-close').onclick = () => $('#view-dialog').close();

// Secure upload: 1) request a one-time link, 2) PUT the file straight to it. No verification yet.
async function uploadFile(slot, file) {
  busySlot = slot;
  render(current);
  try {
    const link = await api('POST', '/api/borrower/upload-links', { slot });
    const response = await fetch(link.upload_url, { method: link.method, body: file, headers: { 'x-nb-csrf': '1', 'content-type': 'application/octet-stream', 'x-nb-filename': encodeURIComponent(file.name) }, credentials: 'same-origin' });
    if (!response.ok) {
      const failure = await response.json().catch(() => ({}));
      throw new Error(failure.detail || 'Upload failed. Please try again.');
    }
    toast(`${slotTitle(slot)} uploaded securely.`);
  } catch (error) {
    toast(error.message);
  } finally {
    busySlot = null;
    await refresh();
  }
}

async function removeDocument(slot) {
  if (!confirm(`Cancel this ${noun(slotTitle(slot))}? Its current file will be removed from your application.`)) return;
  try {
    await api('DELETE', `/api/borrower/documents/${slot}`);
    toast(`${slotTitle(slot)} removed.`);
  } catch (error) {
    toast(error.message);
  }
  await refresh();
}

async function submitApplication() {
  const again = current?.resubmit;
  if (!confirm(again ? 'Resubmit your documents for verification?' : 'Submit your documents for verification? You can replace a document and resubmit before a final decision.')) return;
  $('#submit-btn').disabled = true;
  try {
    await api('POST', '/api/borrower/submit');
    toast('Submitted. Verifying your documents…');
  } catch (error) {
    toast(error.message);
  }
  await refresh();
}

$('#file-input').onchange = e => {
  const file = e.target.files[0];
  e.target.value = '';
  const slot = pickSlot;
  pickSlot = null;
  if (file && slot) confirmUpload(slot, file);
};

$('#dropzone').addEventListener('dragover', e => { e.preventDefault(); $('#dropzone').classList.add('dragging'); });
$('#dropzone').addEventListener('dragleave', () => $('#dropzone').classList.remove('dragging'));
$('#dropzone').addEventListener('drop', e => {
  e.preventDefault();
  $('#dropzone').classList.remove('dragging');
  const file = e.dataTransfer?.files?.[0];
  if (file && activeUploadSlot) confirmUpload(activeUploadSlot, file);
});

// ---------------------------------------------------------------- boot
function showAuth() {
  clearTimeout(pollTimer);
  $('#dashboard').hidden = true;
  $('#auth').hidden = false;
  $('#user-chip').hidden = true;
  $('#logout-btn').hidden = true;
}

async function start() {
  try {
    const me = await api('GET', '/api/auth/me');
    if (me.role !== 'borrower') {
      await api('POST', '/api/auth/logout');
      showAuth();
      return;
    }
    $('#user-chip').hidden = false;
    $('#user-chip').querySelector('span').textContent = me.name;
    $('#user-chip').classList.add('local');
    $('#logout-btn').hidden = false;
    $('#auth').hidden = true;
    $('#dashboard').hidden = false;
    await refresh();
  } catch {
    showAuth();
  }
}

setMode('login');
start();
