// Northbridge KYC workspace. Buildless ES module; no storage, no third-party requests.
// Served by FastAPI it talks to the local backend; on static hosting it runs synthetic samples only.

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const ICONS = {
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 17h6"/>',
  scan: '<path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2"/><path d="M8 12h8"/>',
  text: '<path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2"/><path d="M8 9h8M8 12h8M8 15h5"/>',
  shield: '<path d="M12 3l7 3v5c0 4.5-3 8.5-7 10-4-1.5-7-5.5-7-10V6z"/><path d="M9.5 12l1.8 1.8L15 10"/>',
  sparkles: '<path d="M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
  scale: '<path d="M12 3v18M7 21h10M5 7h14"/><path d="M5 7l-3 6a3 3 0 0 0 6 0zM19 7l-3 6a3 3 0 0 0 6 0z"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
  eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  minus: '<path d="M6 12h12"/>',
  alert: '<path d="M12 8v5M12 16.5v.5"/><path d="M10.3 3.9L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8v.5"/>',
  id: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="11" r="2.2"/><path d="M5.8 16c.6-1.6 1.8-2.4 3.2-2.4s2.6.8 3.2 2.4M14 10h4M14 13h3"/>',
  bank: '<path d="M3 9l9-5 9 5"/><path d="M5 9v8M9.5 9v8M14.5 9v8M19 9v8M3 20h18"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  upload: '<path d="M7 18a4.5 4.5 0 0 1-.6-8.96A6 6 0 0 1 18 8a4 4 0 0 1 .5 7.97"/><path d="M12 12v8M8.5 15.5L12 12l3.5 3.5"/>',
};
const icon = (name, cls = '') => `<svg class="icon ${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;

const LABELS = { full_name: 'Full name', date_of_birth: 'Date of birth', id_number: 'ID number', employer_name: 'Employer', monthly_salary: 'Gross monthly salary' };
const FIELD_KEYS = Object.keys(LABELS);
const DOC_FIELDS = {
  'ID card': ['full_name', 'date_of_birth', 'id_number'],
  'Salary slip': ['full_name', 'employer_name', 'monthly_salary'],
  'Bank statement, page 1': ['full_name', 'monthly_salary'],
  Unknown: FIELD_KEYS,
};
const ENGINES = { rapidocr: { name: 'RapidOCR', note: 'PP-OCR', icon: 'text' }, tesseract: { name: 'Tesseract', note: 'classic', icon: 'layers' } };
const STAGES = [
  { key: 'prescan', title: 'Pre-scan', icon: 'scan' },
  { key: 'ocr', title: 'OCR', icon: 'text' },
  { key: 'pii', title: 'PII masking', icon: 'shield' },
  { key: 'llm', title: 'LLM evaluation', icon: 'sparkles' },
  { key: 'verify', title: 'Verification', icon: 'scale' },
];
const DEFAULT_THRESHOLDS = { full_name: 0.85, monthly_salary: 0.9 };
const SLOTS = [
  { key: 'id', title: 'ID card', type: 'ID card', hint: 'Name, date of birth, ID number. Synthetic only.', required: true, max: 1, icon: 'id' },
  { key: 'slip', title: 'Salary slip', type: 'Salary slip', hint: 'Employee name, employer, gross monthly salary.', required: true, max: 1, icon: 'file' },
  { key: 'bank', title: 'Bank statement', type: 'Bank statement, page 1', hint: 'Page 1 only. Account holder and salary credit; account number is masked and dropped.', required: false, max: 1, icon: 'bank' },
  { key: 'other', title: 'Other related documents', type: null, hint: 'e.g. a second payslip or another statement. Classified from content.', required: false, max: 2, icon: 'plus' },
];
const fieldLabel = (d, key) => (key === 'monthly_salary' && d?.document_type === 'Bank statement, page 1' ? 'Salary credit (net)' : LABELS[key]);

const state = {
  review: null, health: null, local: false, engine: 'rapidocr', uploads: [], uploadSlots: [], previewURLs: [],
  slotFiles: { id: [], slip: [], bank: [], other: [] }, pickSlot: null,
  result: null, docIndex: 0, tab: 'fields', hover: null, pinned: null, mask: true,
  compare: null, comparing: false, busy: false, generation: 0, history: [], timers: [],
};

// ---------- helpers ----------
const doc = () => state.result?.documents?.[state.docIndex];
const thresholds = () => state.health?.review_thresholds || DEFAULT_THRESHOLDS;
const fmtMs = ms => (ms == null ? '—' : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`);
const pct = v => `${Math.round((v || 0) * 100)}%`;
const engineShort = label => /docling/i.test(label) ? 'Docling layout' : /rapidocr/i.test(label) ? 'RapidOCR' : /tesseract/i.test(label) ? 'Tesseract' : /pymupdf/i.test(label) ? 'PDF text' : 'Synthetic';
const isOCR = d => d?.pipeline?.confidence != null;
const usedEngineKey = d => /rapidocr/i.test(d?.pipeline?.ocr_engine || '') ? 'rapidocr' : /tesseract/i.test(d?.pipeline?.ocr_engine || '') ? 'tesseract' : null;
function formatValue(key, value) {
  if (value == null) return null;
  if (key === 'monthly_salary') return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 }).format(value);
  return String(value);
}
function toast(message) {
  const el = $('#toast');
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { el.hidden = true; }, 3200);
}
function clearTimers() { state.timers.forEach(clearTimeout); state.timers = []; }
function revokePreviews() { state.previewURLs.forEach(url => url && URL.revokeObjectURL(url)); state.previewURLs = []; }
function isPdf(file) { return file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf'); }

// ---------- landing: engine + files ----------
function renderEngineToggle() {
  const installed = state.health?.ocr_engines || [];
  $('#engine-toggle').innerHTML = Object.entries(ENGINES).map(([key, e]) => {
    const available = !state.local || installed.includes(key);
    const title = !state.local ? 'Engines run in the local app' : available ? `${e.name} (${e.note})` : `${e.name} is not installed`;
    return `<button role="radio" data-engine="${key}" aria-checked="${state.engine === key}" ${available && state.local ? '' : 'disabled'} title="${esc(title)}">${icon(e.icon)}${e.name}<small>${e.note}</small></button>`;
  }).join('');
  $$('#engine-toggle [data-engine]').forEach(button => {
    button.onclick = () => { state.engine = button.dataset.engine; renderEngineToggle(); };
  });
}

const slotList = () => SLOTS.flatMap(slot => state.slotFiles[slot.key].map(file => ({ file, slot })));

function renderFiles() {
  $('#slots').innerHTML = SLOTS.map(slot => {
    const files = state.slotFiles[slot.key];
    const canAdd = files.length < slot.max;
    return `<div class="slot${files.length ? ' filled' : ''}" data-slot="${slot.key}">
      <div class="slot-head"><span class="slot-icon">${icon(slot.icon)}</span><b>${slot.title}</b><span class="req ${slot.required ? 'required' : ''}">${slot.required ? 'Required for check' : 'Optional'}</span></div>
      <p class="slot-desc">${esc(slot.hint)}</p>
      ${files.map((file, i) => `<div class="slot-file"><span class="file-type">${esc(isPdf(file) ? 'PDF' : file.name.split('.').pop().toUpperCase().slice(0, 4))}</span><span class="file-meta"><b>${esc(file.name)}</b><small>${(file.size / 1024).toFixed(0)} KB</small></span><button class="icon-btn" data-remove="${slot.key}:${i}" aria-label="Remove ${esc(file.name)} from ${slot.title}">${icon('x')}</button></div>`).join('')}
      ${canAdd ? `<button class="slot-drop" data-pick="${slot.key}">${icon('upload')}<span>${files.length ? 'Add another' : 'Drop file or <u>browse</u>'}</span></button>` : ''}
    </div>`;
  }).join('');
  $$('#slots [data-remove]').forEach(button => {
    button.onclick = () => {
      const [key, index] = button.dataset.remove.split(':');
      state.slotFiles[key].splice(Number(index), 1);
      renderFiles();
    };
  });
  $$('#slots [data-pick]').forEach(button => {
    button.onclick = () => {
      if (state.busy) return;
      state.pickSlot = button.dataset.pick;
      $('#file-input').multiple = SLOTS.find(slot => slot.key === state.pickSlot).max - state.slotFiles[state.pickSlot].length > 1;
      $('#file-input').click();
    };
  });
  $$('#slots [data-slot]').forEach(card => {
    const key = card.dataset.slot;
    ['dragenter', 'dragover'].forEach(type => card.addEventListener(type, e => { e.preventDefault(); card.classList.add('dragging'); }));
    ['dragleave', 'drop'].forEach(type => card.addEventListener(type, e => {
      e.preventDefault();
      card.classList.remove('dragging');
      if (type === 'drop' && !state.busy) addFiles([...e.dataTransfer.files], key);
    }));
  });
  const files = slotList();
  const pair = state.slotFiles.id.length && state.slotFiles.slip.length;
  $('#run-btn').disabled = !files.length || state.busy;
  $('#run-btn').innerHTML = `${pair || !files.length ? 'Run verification' : 'Extract fields'} <span aria-hidden="true">→</span>`;
  $('#run-btn').title = pair || !files.length ? '' : 'Without both an ID card and a salary slip the result will be Incomplete (or Not applicable for a bank statement alone); fields are still extracted.';
}

function intakeError(message = '') {
  $('#intake-error').textContent = message;
  $('#intake-error').hidden = !message;
}

async function addFiles(files, slotKey) {
  intakeError();
  const slot = SLOTS.find(item => item.key === slotKey);
  if (!slot || !files.length) return;
  if (state.slotFiles[slotKey].length + files.length > slot.max) return intakeError(`${slot.title}: up to ${slot.max} file${slot.max > 1 ? 's' : ''}.`);
  for (const file of files) {
    if (!file.size || file.size > 10 * 1024 * 1024) return intakeError('Each file must contain data and be no larger than 10 MB.');
    const bytes = new Uint8Array(await file.slice(0, 8).arrayBuffer());
    const pdf = String.fromCharCode(...bytes.slice(0, 5)) === '%PDF-';
    const png = [137, 80, 78, 71, 13, 10, 26, 10].every((v, i) => bytes[i] === v);
    const jpg = bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255;
    if (!pdf && !png && !jpg) return intakeError('Unsupported file. Choose a valid PDF, PNG or JPEG.');
  }
  state.slotFiles[slotKey].push(...files);
  renderFiles();
}

function renderHistory() {
  const box = $('#history');
  box.hidden = !state.history.length;
  box.innerHTML = `<h2>This session</h2>${state.history.map(h => `<div class="history-row"><span>${esc(h.time)} · ${esc(h.mode)}</span><code>${esc(h.id.slice(0, 8))}</code><b>${esc(h.status)}${h.review ? ' · review' : ''}</b></div>`).join('')}`;
}

// ---------- workspace shell ----------
function showWorkspace() {
  $('#landing').hidden = true;
  $('#workspace').hidden = false;
  window.scrollTo({ top: 0 });
}

function showLanding() {
  $('#workspace').hidden = true;
  $('#landing').hidden = false;
  renderFiles();
  renderHistory();
}

function resetAll(message) {
  state.generation++;
  clearTimers();
  revokePreviews();
  state.slotFiles = { id: [], slip: [], bank: [], other: [] };
  Object.assign(state, { uploads: [], uploadSlots: [], result: null, docIndex: 0, tab: 'fields', hover: null, pinned: null, compare: null, comparing: false, busy: false });
  $('#file-input').value = '';
  intakeError();
  showLanding();
  if (message) toast(message);
}

function setBusy(busy) {
  state.busy = busy;
  $('#run-btn').disabled = busy || !slotList().length;
  $$('.sample').forEach(b => { b.disabled = busy; });
}

// ---------- stepper ----------
function renderStepper(statuses) {
  $('#stepper').innerHTML = STAGES.map((stage, i) => {
    const s = statuses[i] || { state: 'pending', detail: '—' };
    const mark = s.state === 'running' ? '<span class="spinner" aria-label="running"></span>'
      : s.state === 'done' ? icon('check') : s.state === 'warn' ? icon('alert') : s.state === 'fail' ? icon('x') : '';
    return `<li class="step ${s.state}"><span class="step-icon">${icon(stage.icon)}</span><div class="step-body"><div class="step-title">${stage.title}<span class="step-state">${mark}</span></div><div class="step-detail" title="${esc(s.detail)}">${esc(s.detail)}</div></div></li>`;
  }).join('');
}

function runningStepper(activeIndex) {
  renderStepper(STAGES.map((_, i) => i < activeIndex ? { state: 'done', detail: 'done' } : i === activeIndex ? { state: 'running', detail: 'running…' } : { state: 'pending', detail: '—' }));
}

function resultStepper(result) {
  const docs = result.documents;
  if (result.stages?.length) {
    // The backend's own stage summary (same five stages the borrower sees).
    return renderStepper(result.stages.map(stage => ({
      state: stage.status === 'skipped' ? 'pending' : stage.status,
      detail: `${stage.ms ? `${fmtMs(stage.ms)} · ` : ''}${stage.detail}`,
    })));
  }
  if (result.mode === 'demo') {
    return renderStepper([
      { state: 'done', detail: 'pass · synthetic' }, { state: 'done', detail: 'synthetic fixture' },
      { state: 'done', detail: `${docs.reduce((n, d) => n + Object.values(d.pii.entity_counts).reduce((a, b) => a + b, 0), 0)} values masked` },
      { state: 'done', detail: 'labeled parser' }, decideStep(result),
    ]);
  }
  const sum = key => docs.reduce((n, d) => n + (d.pipeline.timings_ms[key] || 0), 0);
  const scanWarn = docs.some(d => isOCR(d) && d.pipeline.warnings.some(w => w.startsWith('Low ')));
  const engines = [...new Set(docs.map(d => engineShort(d.pipeline.ocr_engine)))].join(' + ');
  const masked = docs.reduce((n, d) => n + Object.values(d.pii.entity_counts).reduce((a, b) => a + b, 0), 0);
  const llmUsed = docs.some(d => d.pipeline.llm_used);
  const guardrail = (result.check.review_reasons || []).some(r => /LLM|guardrail/.test(r));
  renderStepper([
    { state: scanWarn ? 'warn' : 'done', detail: scanWarn ? 'review scan quality' : 'pass' },
    { state: 'done', detail: `${fmtMs(sum('ocr'))} · ${engines}` },
    { state: 'done', detail: `${fmtMs(sum('pii'))} · ${masked} values masked` },
    { state: guardrail ? 'warn' : 'done', detail: llmUsed ? `${fmtMs(sum('llm'))} · parser + LLM` : 'labeled parser' },
    decideStep(result),
  ]);
}

function decideStep(result) {
  const { status, review_required: review } = result.check;
  const stateName = status === 'Verified' && !review ? 'done' : status === 'Mismatch' ? 'fail' : 'warn';
  return { state: status === 'Not applicable' ? 'done' : stateName, detail: `${status}${review ? ' · review' : ''}` };
}

// ---------- documents header ----------
function renderDocChips(pendingFiles) {
  const items = pendingFiles
    ? pendingFiles.map(f => ({ title: f.name, sub: [isPdf(f) ? 'PDF' : 'Image', 'reading…'] }))
    : state.result.documents.map((d, i) => {
      const slot = state.uploadSlots[i];
      const misfiled = slot?.type && slot.type !== d.document_type;
      return {
        title: d.document_type,
        sub: [state.result.mode === 'demo' ? 'Synthetic' : d.error ? 'Unreadable' : engineShort(d.pipeline.ocr_engine), state.uploads[i]?.name || slot?.title || '1 page'],
        warn: misfiled ? `uploaded as ${slot.title}` : '',
      };
    });
  $('#doc-chips').innerHTML = items.map((item, i) => `
    <button class="doc-chip" role="tab" data-doc="${i}" aria-selected="${i === state.docIndex}">
      <span class="doc-icon">${icon('file')}</span>
      <span><b>${esc(item.title)}</b>${item.sub.map(s => `<small>${esc(s)}</small>`).join(' ')}${item.warn ? ` <small class="misfiled">${esc(item.warn)}</small>` : ''}</span>
    </button>`).join('');
  $$('#doc-chips [data-doc]').forEach(button => {
    button.onclick = () => selectDoc(Number(button.dataset.doc));
  });
}

function selectDoc(index) {
  state.docIndex = index;
  state.hover = null;
  state.pinned = null;
  if (state.result) renderDocChips();
  else $$('#doc-chips [data-doc]').forEach(b => b.setAttribute('aria-selected', String(Number(b.dataset.doc) === index)));
  renderViewer();
  renderPanel();
}

// ---------- quality ----------
function renderQuality(result) {
  if (!result) {
    $('#quality-badge').className = 'badge';
    $('#quality-badge').textContent = 'checking…';
    $('#quality-body').innerHTML = '';
    return;
  }
  const docs = result.documents;
  const anyWarn = docs.some(d => isOCR(d) && d.pipeline.warnings.some(w => w.startsWith('Low ')));
  $('#quality-badge').className = `badge ${anyWarn ? 'warn' : 'pass'}`;
  $('#quality-badge').textContent = anyWarn ? 'review' : 'pass';
  const q = (d, k) => d.pipeline.quality?.[k];
  $('#quality-body').innerHTML = `<table class="grid"><thead><tr><th>Document</th><th>Reader</th><th>OCR confidence</th><th>Sharpness</th><th>Contrast</th><th>Brightness</th><th>Notes</th></tr></thead><tbody>${docs.map(d => `
    <tr><td>${esc(d.document_type)}</td><td>${esc(d.pipeline.ocr_engine)}</td>
    <td class="num">${d.pipeline.confidence != null ? `${d.pipeline.confidence}%` : 'n/a (text layer)'}</td>
    <td class="num">${esc(q(d, 'sharpness') ?? '—')}</td><td class="num">${esc(q(d, 'contrast') ?? '—')}</td><td class="num">${esc(q(d, 'brightness') ?? '—')}</td>
    <td>${esc((isOCR(d) ? d.pipeline.warnings : d.pipeline.warnings.filter(w => !w.startsWith('Low '))).join(' ') || '—')}</td></tr>`).join('')}</tbody></table>
    <p class="panel-note">Scan-quality warnings only gate documents that needed OCR; PDFs with a text layer are read exactly.</p>`;
}

// ---------- viewer ----------
function pageSource(index) {
  const d = state.result?.documents?.[index];
  if (d?.page_image) return { kind: 'img', src: d.page_image };
  const file = state.uploads[index];
  if (!file) return null;
  if (!state.previewURLs[index]) state.previewURLs[index] = URL.createObjectURL(file);
  return { kind: isPdf(file) ? 'pdf' : 'img', src: state.previewURLs[index] };
}

function renderViewer() {
  const d = doc();
  const total = state.result?.documents?.length || state.uploads.length;
  $('#viewer-title').textContent = `Document ${state.docIndex + 1} of ${total}${d ? ` · ${d.document_type}` : ''}`;
  const source = pageSource(state.docIndex);
  if (!source) {
    $('#page-wrap').innerHTML = '<div class="placeholder-page">No preview</div>';
  } else if (source.kind === 'pdf') {
    $('#page-wrap').innerHTML = `<div class="page"><iframe src="${source.src}" title="Original PDF preview"></iframe></div>`;
  } else {
    $('#page-wrap').innerHTML = `<div class="page"><img src="${source.src}" alt="Page 1 of document ${state.docIndex + 1}"><div class="overlay" id="overlay"></div></div>`;
  }
  renderOverlay();
}

function renderOverlay() {
  const overlay = $('#overlay');
  const d = doc();
  const hint = $('#viewer-hint');
  if (!overlay || !d) {
    hint.textContent = state.result ? '' : 'Reading the document…';
    return;
  }
  const rect = (b, cls, extra = '') => `<span class="${cls}" style="left:${b.x * 100}%;top:${b.y * 100}%;width:${b.w * 100}%;height:${b.h * 100}%">${extra}</span>`;
  const field = state.hover || state.pinned;
  const evidence = field ? d.field_evidence?.[field] : null;
  let html = state.mask ? (d.redactions || []).map(b => rect(b, 'redaction')).join('') : '';
  if (evidence?.boxes?.length) {
    const low = evidence.needs_review ? ' low' : '';
    const first = evidence.boxes[0];
    html += evidence.boxes.map(b => rect(b, `highlight${low}`)).join('');
    html += `<span class="hl-tag${low}" style="left:${first.x * 100}%;top:${first.y * 100}%">${esc(fieldLabel(d, field))} · ${pct(evidence.confidence)}</span>`;
  }
  overlay.innerHTML = html;
  if (field && evidence && !evidence.boxes?.length) hint.textContent = `${fieldLabel(d, field)} was not located on the page.`;
  else if (field) hint.textContent = `${fieldLabel(d, field)} · source ${evidence?.source === 'llm' ? 'LLM (masked text)' : 'labeled parser'}${state.pinned ? ' · click the field again to unpin' : ''}`;
  else hint.textContent = `Hover or select a field to see where it came from.${state.mask ? ' Detected PII is masked; switch off "Mask PII" to inspect it.' : ''}`;
}

// ---------- inspector ----------
function setTab(tab) {
  state.tab = tab;
  $$('.tabs [data-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === tab)));
  renderPanel();
}

function renderPanel() {
  const panel = $('#panel');
  if (state.review && (state.tab === 'actions' || state.tab === 'history')) return (state.tab === 'actions' ? renderActions : renderHistoryTab)();
  if (!state.result) {
    panel.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div><p class="panel-note">Reading, masking and structuring the documents locally…</p>';
    return;
  }
  ({ fields: renderFields, decision: renderDecision, masked: renderMasked, compare: renderCompare, telemetry: renderTelemetry, actions: renderActions, history: renderHistoryTab })[state.tab]();
}

function fieldCard(d, key) {
  const value = formatValue(key, d.fields[key]);
  const ev = value != null ? d.field_evidence?.[key] : null;
  const locatable = !!ev?.boxes?.length;
  const active = state.pinned === key;
  const source = ev ? (ev.source === 'llm' ? '<span class="src llm">LLM · masked</span>' : '<span class="src">Label</span>') : '';
  return `<button class="field${locatable ? ' locatable' : ''}${active ? ' active' : ''}" data-field="${key}" ${locatable ? '' : 'aria-disabled="true"'}>
    <div class="field-head"><span class="field-label">${esc(fieldLabel(d, key))}</span>${source}</div>
    <div class="field-value${value == null ? ' muted' : ''}">${value == null ? 'Not found on this document' : esc(value)}</div>
    ${ev ? `<div class="conf"><span class="conf-track"><span class="conf-fill${ev.needs_review ? ' low' : ''}" style="width:${Math.round(ev.confidence * 100)}%"></span></span><span class="conf-num">${pct(ev.confidence)}</span></div>` : ''}
    ${ev && !ev.grounded ? '<div class="field-note">Not located on the page — check manually.</div>' : ''}
    ${ev?.needs_review && ev.grounded ? '<div class="field-note">Below the review threshold.</div>' : ''}
  </button>`;
}

function renderFields() {
  const d = doc();
  if (d.error) {
    $('#panel').innerHTML = `<div class="review"><b>This file could not be read.</b> ${esc(d.error)} The borrower was asked to upload it again.</div>`;
    return;
  }
  const primary = DOC_FIELDS[d.document_type] || FIELD_KEYS;
  const others = FIELD_KEYS.filter(k => !primary.includes(k) && d.fields[k] != null);
  const masked = Object.values(d.pii.entity_counts).reduce((a, b) => a + b, 0);
  const slot = state.uploadSlots[state.docIndex];
  const misfiled = slot?.type && slot.type !== d.document_type
    ? `<div class="review">Uploaded in the <b>${esc(slot.title)}</b> slot, but its content reads as <b>${esc(d.document_type)}</b>. The check uses the detected type.</div>` : '';
  const bankNote = d.document_type === 'Bank statement, page 1'
    ? '<p class="panel-note">Bank statement: page 1 only. Account number and joint-holder names are masked and dropped. The salary credit is net pay, not gross. Bank statements are not part of the name check.</p>' : '';
  $('#panel').innerHTML = `${misfiled}${bankNote}
    <p class="panel-note">${esc(d.pipeline.ocr_engine)}${d.pipeline.llm_used ? ' + LLM on masked text' : ''} · ${masked} sensitive values masked</p>
    <div class="field-list">${primary.map(k => fieldCard(d, k)).join('')}</div>
    ${others.length ? `<div class="subhead">Also found</div><div class="field-list">${others.map(k => fieldCard(d, k)).join('')}</div>` : ''}
    ${d.pipeline.warnings.length ? `<p class="field-hint">${esc(d.pipeline.warnings.join(' '))}</p>` : ''}`;
  $$('#panel [data-field]').forEach(card => {
    const key = card.dataset.field;
    if (card.getAttribute('aria-disabled') === 'true') return;
    const enter = () => { state.hover = key; renderOverlay(); };
    const leave = () => { state.hover = null; renderOverlay(); };
    card.addEventListener('mouseenter', enter);
    card.addEventListener('mouseleave', leave);
    card.addEventListener('focus', enter);
    card.addEventListener('blur', leave);
    card.addEventListener('click', () => {
      state.pinned = state.pinned === key ? null : key;
      $$('#panel [data-field]').forEach(c => c.classList.toggle('active', c.dataset.field === state.pinned));
      renderOverlay();
    });
  });
}

function traceItems(result) {
  const docs = result.documents;
  const ids = docs.filter(d => d.document_type === 'ID card');
  const slips = docs.filter(d => d.document_type === 'Salary slip');
  const t = thresholds();
  const items = [];
  const add = (status, text, note = '') => items.push({ status, text, note });
  if (result.check.status === 'Not applicable') {
    add('na', 'Bank statements are not part of the name comparison', 'Upload an ID card and a salary slip to run the check.');
    return items;
  }
  add(ids.length === 1 ? 'pass' : 'fail', 'Exactly one ID card', `${ids.length} found`);
  add(slips.length === 1 ? 'pass' : 'fail', 'Exactly one salary slip', `${slips.length} found`);
  const pair = ids.length === 1 && slips.length === 1;
  const namesFound = pair && ids[0].fields.full_name && slips[0].fields.full_name;
  add(!pair ? 'na' : namesFound ? 'pass' : 'fail', 'Name extracted from both documents');
  add(result.check.status === 'Verified' ? 'pass' : result.check.status === 'Mismatch' ? 'fail' : 'na', 'Names match after normalization', 'Case, spacing and Unicode width are normalized; the comparison runs on request-scoped tokens.');
  for (const d of [...ids, ...slips]) {
    const ev = d.field_evidence?.full_name;
    if (ev && ev.source !== 'none') add(ev.needs_review ? 'warn' : 'pass', `${d.document_type}: name confidence ≥ ${pct(t.full_name)}`, `measured ${pct(ev.confidence)}${ev.grounded ? '' : ' · not located on page'}`);
  }
  for (const d of slips) {
    const ev = d.field_evidence?.monthly_salary;
    if (ev && ev.source !== 'none') add(ev.needs_review ? 'warn' : 'pass', `Salary confidence ≥ ${pct(t.monthly_salary)}`, `measured ${pct(ev.confidence)}`);
  }
  const llm = docs.some(d => d.pipeline.llm_used);
  const llmIssues = (result.check.review_reasons || []).filter(r => /LLM|guardrail/.test(r));
  add(!llm ? 'na' : llmIssues.length ? 'warn' : 'pass', 'LLM output passed placeholder guardrails', llm ? `${llmIssues.length} issue(s)` : 'LLM off — labeled parser only');
  const scan = docs.filter(isOCR);
  const lowScan = scan.some(d => d.pipeline.warnings.some(w => w.startsWith('Low ')));
  add(!scan.length ? 'na' : lowScan ? 'warn' : 'pass', 'Scan quality acceptable', scan.length ? `${scan.length} document(s) read by OCR` : 'No OCR needed (text-layer PDFs)');
  return items;
}

function renderDecision() {
  const { check } = state.result;
  const review = check.review_required;
  const tone = check.status === 'Verified' && !review ? 'pass' : check.status === 'Mismatch' ? 'fail' : check.status === 'Not applicable' ? 'neutral' : 'warn';
  const mark = tone === 'pass' ? 'check' : tone === 'fail' ? 'x' : tone === 'neutral' ? 'minus' : 'alert';
  const reasons = check.review_reasons || [];
  $('#panel').innerHTML = `
    <div class="decision ${tone}"><span class="decision-icon">${icon(mark)}</span><div><h3>${esc(check.status)}</h3><p>${esc(check.reason)}</p></div></div>
    ${review ? `<div class="review"><b>Human review required before approval</b><ul>${(reasons.length ? reasons : [check.reason]).map(r => `<li>${esc(r)}</li>`).join('')}</ul></div>` : ''}
    <div class="subhead">Check trace</div>
    <ul class="trace">${traceItems(state.result).map(item => `<li class="${item.status}"><span class="t-icon">${icon(item.status === 'pass' ? 'check' : item.status === 'fail' ? 'x' : item.status === 'warn' ? 'alert' : 'minus')}</span><span class="t-text">${esc(item.text)}${item.note ? `<small>${esc(item.note)}</small>` : ''}</span></li>`).join('')}</ul>
    <p class="field-hint">Rules run in code. Complete applications with a Verified result are approved automatically. Mismatches and incomplete checks stay in the lender review queue.</p>`;
}

function renderMasked() {
  const d = doc();
  const external = state.health?.external_calls;
  const text = esc(d.masked_text).replace(/&lt;([A-Z_]+_\d+)&gt;/g, '<span class="tok">&lt;$1&gt;</span>');
  $('#panel').innerHTML = `<p class="panel-note">This is the only text an LLM would see. Detected values are replaced by request-scoped placeholders; unlabeled PII may remain. ${external ? 'It is sent to the configured LLM endpoint.' : 'Nothing is sent externally.'}</p><pre class="code">${text}</pre>`;
}

function renderCompare() {
  const d = doc();
  if (state.review) {
    $('#panel').innerHTML = `<div class="empty"><div>${icon('layers')}<p>Engine comparison runs on files uploaded in the quick verify tool.<br>Stored applications keep the result of the automatic check.</p></div></div>`;
    return;
  }
  const panel = $('#panel');
  if (state.result.mode === 'demo') {
    panel.innerHTML = `<div class="empty"><div>${icon('layers')}<p>Engine comparison runs in the local app on uploaded scans.</p></div></div>`;
    return;
  }
  const used = usedEngineKey(d);
  if (!used) {
    panel.innerHTML = `<div class="empty"><div>${icon('info')}<p>This PDF has a text layer, so it was read exactly and no OCR engine was needed.<br>Engine comparison applies to scans and photos.</p></div></div>`;
    return;
  }
  const other = used === 'rapidocr' ? 'tesseract' : 'rapidocr';
  if (!(state.health?.ocr_engines || []).includes(other)) {
    panel.innerHTML = `<div class="empty"><p>${ENGINES[other].name} is not installed on this machine.</p></div>`;
    return;
  }
  if (state.comparing) {
    panel.innerHTML = `<div class="empty"><div><span class="spinner"></span><p>Running ${ENGINES[other].name} on the same documents…</p></div></div>`;
    return;
  }
  if (!state.compare) {
    panel.innerHTML = `<div class="compare-head"><p class="panel-note">Re-read the same uploads with ${ENGINES[other].name} and compare every field with ${ENGINES[used].name}. Disagreements on a name or salary are a strong signal for manual review.</p><button class="btn btn-outline" id="compare-btn">Run ${ENGINES[other].name}</button></div>`;
    $('#compare-btn').onclick = () => runCompare(other);
    return;
  }
  const alt = state.compare.result.documents[state.docIndex];
  const keys = DOC_FIELDS[d.document_type] || FIELD_KEYS;
  const cell = (docItem, key) => {
    const value = formatValue(key, docItem?.fields?.[key]);
    const ev = docItem?.field_evidence?.[key];
    return `${value == null ? '<span class="cell-conf">not found</span>' : esc(value)}${value != null && ev ? `<span class="cell-conf">${pct(ev.confidence)}</span>` : ''}`;
  };
  const same = key => String(d.fields[key] ?? '').trim().toLowerCase() === String(alt?.fields?.[key] ?? '').trim().toLowerCase();
  panel.innerHTML = `
    <table class="grid"><thead><tr><th>Field</th><th>${ENGINES[used].name}</th><th>${ENGINES[state.compare.engine].name}</th><th></th></tr></thead><tbody>
    ${keys.map(k => `<tr><td>${esc(fieldLabel(d, k))}</td><td>${cell(d, k)}</td><td>${cell(alt, k)}</td><td>${same(k) ? `<span class="agree" title="Agree">${icon('check')}</span>` : `<span class="disagree" title="Differ">≠</span>`}</td></tr>`).join('')}
    <tr><td>OCR time</td><td class="num">${fmtMs(d.pipeline.timings_ms.ocr)}</td><td class="num">${fmtMs(alt?.pipeline?.timings_ms?.ocr)}</td><td></td></tr>
    <tr><td>Mean OCR confidence</td><td class="num">${d.pipeline.confidence ?? '—'}%</td><td class="num">${alt?.pipeline?.confidence ?? '—'}%</td><td></td></tr>
    <tr><td>Decision</td><td>${esc(state.result.check.status)}</td><td>${esc(state.compare.result.check.status)}</td><td>${state.result.check.status === state.compare.result.check.status ? `<span class="agree">${icon('check')}</span>` : '<span class="disagree">≠</span>'}</td></tr>
    </tbody></table>
    <p class="field-hint">The workspace keeps the ${ENGINES[used].name} result. Run a new verification with the other engine selected to switch.</p>`;
}

function renderTelemetry() {
  $('#panel').innerHTML = `<p class="panel-note">Allowlisted metadata only — what the audit log receives. No filenames, OCR text or field values.</p><h3>Request summary</h3><pre class="code">${esc(JSON.stringify(state.result.metrics || {}, null, 2))}</pre><h3>Stage trace</h3><pre class="code">${esc(JSON.stringify(state.result.telemetry, null, 2))}</pre>`;
}

// ---------- runs ----------
async function postDocuments(engine) {
  const form = new FormData();
  state.uploads.forEach(file => form.append('files', file));
  const response = await fetch(`/v1/extract?engine=${encodeURIComponent(engine)}`, { method: 'POST', body: form, headers: { 'x-nb-csrf': '1' }, credentials: 'same-origin', signal: AbortSignal.timeout(180000) });
  if (!response.ok) {
    const failure = await response.json().catch(() => ({}));
    throw new Error(failure.detail || 'Could not process these documents. Please try again.');
  }
  return response.json();
}

function beginRun(pendingFiles) {
  clearTimers();
  Object.assign(state, { result: null, docIndex: 0, tab: 'fields', hover: null, pinned: null, compare: null, comparing: false });
  $$('.tabs [data-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === 'fields')));
  $('#export-btn').hidden = true;
  showWorkspace();
  renderDocChips(pendingFiles);
  renderQuality(null);
  runningStepper(0);
  renderViewer();
  renderPanel();
}

async function runUploads() {
  const list = slotList();
  if (state.busy || !list.length) return;
  intakeError();
  if (!state.local) {
    intakeError('This hosted demo cannot read real documents. Run the included local app to extract them, or try a synthetic application below.');
    return;
  }
  const generation = ++state.generation;
  revokePreviews();
  state.uploads = list.map(item => item.file);
  state.uploadSlots = list.map(item => item.slot);
  setBusy(true);
  beginRun(state.uploads);
  state.timers.push(setTimeout(() => runningStepper(1), 350));
  try {
    const result = await postDocuments(state.engine);
    if (generation !== state.generation) return;
    finishRun(result);
  } catch (error) {
    if (generation !== state.generation) return;
    clearTimers();
    showLanding();
    intakeError(error.name === 'TimeoutError' ? 'Processing timed out. Try a smaller or clearer document.' : error.message);
  } finally {
    if (generation === state.generation) setBusy(false);
  }
}

function finishRun(result) {
  clearTimers();
  state.result = result;
  state.docIndex = 0;
  resultStepper(result);
  renderDocChips();
  renderQuality(result);
  renderViewer();
  setTab(result.check.review_required || result.check.status !== 'Verified' ? 'decision' : 'fields');
  $('#export-btn').hidden = false;
  state.history.unshift({ id: result.request_id, status: result.check.status, review: result.check.review_required, mode: result.mode, time: new Date().toLocaleTimeString() });
  state.history = state.history.slice(0, 8);
}

async function runCompare(engine) {
  const generation = state.generation;
  state.comparing = true;
  renderPanel();
  try {
    const result = await postDocuments(engine);
    if (generation !== state.generation) return;
    state.compare = { engine, result };
  } catch (error) {
    if (generation !== state.generation) return;
    toast(error.message);
  } finally {
    if (generation === state.generation) {
      state.comparing = false;
      if (state.tab === 'compare') renderPanel();
    }
  }
}

// ---------- synthetic samples (browser only; no OCR) ----------
const SAMPLES = {
  full: [['ID card', 'Ravi Menon'], ['Salary slip', 'Ravi Menon'], ['Bank statement, page 1', 'Ravi Menon']],
  match: [['ID card', 'Ravi Menon'], ['Salary slip', 'RAVI MENON']],
  mismatch: [['ID card', 'Ravi Menon'], ['Salary slip', 'Anil Kumar']],
  incomplete: [['ID card', 'Ravi Menon']],
  bank: [['Bank statement, page 1', 'Ravi Menon']],
};

function synthDocument(type, name, personToken, requestId) {
  const W = 850, H = 1100;
  const canvas = document.createElement('canvas');
  canvas.width = W; canvas.height = H;
  const g = canvas.getContext('2d');
  const font = (weight, size) => `${weight} ${size}px system-ui, -apple-system, "Segoe UI", Arial, sans-serif`;
  g.fillStyle = '#fff'; g.fillRect(0, 0, W, H);
  g.fillStyle = '#1f2a37'; g.fillRect(60, 60, 250, 50);
  g.fillStyle = '#fff'; g.font = font(600, 15);
  g.fillText(type === 'ID card' ? 'DEMO IDENTITY AUTHORITY' : type === 'Salary slip' ? 'HARBOUR TOOLS PVT LTD' : 'DEMO SAVINGS BANK', 76, 91);
  g.fillStyle = '#111'; g.font = font(700, 34);
  g.fillText(type === 'ID card' ? 'IDENTITY CARD' : type === 'Salary slip' ? 'SALARY SLIP' : 'BANK STATEMENT', 60, 180);
  g.fillStyle = '#d4d4d8'; g.fillRect(60, 200, W - 120, 2);
  const rows = type === 'ID card'
    ? [['Full name', name, 'full_name', personToken], ['Date of birth', '14/08/1994', 'date_of_birth', '<DATE_TIME_1>'], ['ID number', 'DEMO1234X', 'id_number', '<ID_NUM_1>']]
    : type === 'Salary slip'
      ? [['Employee name', name, 'full_name', personToken], ['Employer', 'Harbour Tools Pvt Ltd', 'employer_name', '<ORGANIZATION_1>'], ['Gross monthly salary', 'INR 82,000.00', 'monthly_salary', null]]
      : [['Account holder', name, 'full_name', personToken], ['Account number', '00001234567890', null, '<ACCOUNT_NUMBER_1>'], ['Salary credit · HARBOUR TOOLS', 'INR 78,500.00', 'monthly_salary', null]];
  const evidence = {};
  const redactions = [];
  const maskedLines = [type === 'Bank statement, page 1' ? 'BANK STATEMENT' : type.toUpperCase()];
  let y = 260;
  for (const [label, value, key, token] of rows) {
    g.fillStyle = '#6b7280'; g.font = font(500, 15); g.fillText(label, 60, y);
    g.fillStyle = '#111'; g.font = font(600, 24); g.fillText(value, 60, y + 34);
    const width = g.measureText(value).width;
    const box = { x: 58 / W, y: (y + 12) / H, w: (width + 4) / W, h: 30 / H };
    if (key) evidence[key] = { confidence: 1, source: 'labeled_parser', grounded: true, needs_review: false, boxes: [box] };
    if (token && key !== 'employer_name') redactions.push(box);
    maskedLines.push(`${label}: ${token || value}`);
    y += 92;
  }
  g.fillStyle = '#b91c1c'; g.font = font(700, 14);
  g.fillText('SYNTHETIC SAMPLE · NOT A VALID DOCUMENT', 60, H - 70);
  maskedLines.push('SYNTHETIC SAMPLE · NOT A VALID DOCUMENT');
  for (const k of FIELD_KEYS) if (!evidence[k]) evidence[k] = { confidence: 0, source: 'none', grounded: false, needs_review: false, boxes: [] };
  const counts = { PERSON: 1, ...(type === 'ID card' ? { DATE_TIME: 1, ID_NUM: 1 } : type === 'Salary slip' ? { ORGANIZATION: 1 } : { ACCOUNT_NUMBER: 1 }) };
  return {
    request_id: requestId, document_type: type,
    fields: {
      full_name: name,
      date_of_birth: type === 'ID card' ? '1994-08-14' : null,
      id_number: type === 'ID card' ? 'DEMO1234X' : null,
      employer_name: type === 'Salary slip' ? 'Harbour Tools Pvt Ltd' : null,
      monthly_salary: type === 'Salary slip' ? 82000 : type === 'Bank statement, page 1' ? 78500 : null,
    },
    field_evidence: evidence, redactions, page_image: canvas.toDataURL('image/png'),
    masked_text: maskedLines.join('\n'),
    pii: { masked_before_llm: true, entity_counts: counts },
    pipeline: { ocr_engine: 'Synthetic fixture (no OCR)', used_vision_fallback: false, llm_used: false, llm_provider: 'none', timings_ms: { ocr: 0, pii: 0, llm: 0, rule: 0 }, warnings: ['Synthetic example; no extraction was performed.'], quality: {}, confidence: null },
  };
}

function sampleResult(key) {
  const requestId = crypto.randomUUID ? crypto.randomUUID() : String(Date.now());
  const names = [];
  const documents = SAMPLES[key].map(([type, name]) => {
    const normalized = name.toLowerCase();
    if (!names.includes(normalized)) names.push(normalized);
    return synthDocument(type, name, `<PERSON_${names.indexOf(normalized) + 1}>`, requestId);
  });
  const check = {
    full: { status: 'Verified', reason: 'Normalized names match across the ID card and salary slip.', review_required: false, review_reasons: [] },
    match: { status: 'Verified', reason: 'Normalized names match across the ID card and salary slip.', review_required: false, review_reasons: [] },
    mismatch: { status: 'Mismatch', reason: 'The ID card and salary slip contain different names. Manual review is required.', review_required: true, review_reasons: [] },
    incomplete: { status: 'Incomplete', reason: 'Provide exactly one ID card and one salary slip for an unambiguous comparison.', review_required: false, review_reasons: [] },
    bank: { status: 'Not applicable', reason: 'Bank statements do not participate in the name comparison.', review_required: false, review_reasons: [] },
  }[key];
  documents.forEach(d => { d.check = d.document_type === 'Bank statement, page 1' ? { status: 'Not applicable', reason: 'Bank statements do not participate in the name comparison.', review_required: false, review_reasons: [] } : check; });
  return { request_id: requestId, check, documents, mode: 'demo', telemetry: documents.map(d => ({ request_id: requestId, stage: 'demo.fixture', doc_type: d.document_type, status: check.status, synthetic: true })) };
}

async function runSample(key) {
  if (state.busy) return;
  const generation = ++state.generation;
  intakeError();
  revokePreviews();
  state.uploads = [];
  state.uploadSlots = [];
  setBusy(true);
  beginRun(SAMPLES[key].map(([type]) => ({ name: type, type: 'application/pdf' })));
  for (let i = 0; i < STAGES.length; i++) {
    runningStepper(i);
    await new Promise(resolve => state.timers.push(setTimeout(resolve, 220)));
    if (generation !== state.generation) return;
  }
  finishRun(sampleResult(key));
  setBusy(false);
}

// ---------- export ----------
function exportMetadata() {
  if (!state.result) return;
  const r = state.result;
  const safe = {
    request_id: r.request_id, mode: r.mode, check: r.check, telemetry: r.telemetry,
    documents: r.documents.map(d => ({
      document_type: d.document_type, check: d.check, pii: d.pii, pipeline: d.pipeline,
      field_confidence: Object.fromEntries(Object.entries(d.field_evidence || {}).map(([k, v]) => [k, { confidence: v.confidence, source: v.source, grounded: v.grounded, needs_review: v.needs_review }])),
    })),
  };
  const url = URL.createObjectURL(new Blob([JSON.stringify(safe, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'northbridge-review-metadata.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast('Exported metadata only. Field values and document text were excluded.');
}

// ---------- wiring ----------
$('#file-input').onchange = e => {
  const key = state.pickSlot;
  state.pickSlot = null;
  if (key) addFiles([...e.target.files], key);
  e.target.value = '';
};
$('#run-btn').onclick = runUploads;
$$('.sample').forEach(button => { button.onclick = () => runSample(button.dataset.sample); });
$('#new-btn').onclick = () => (state.review ? (location.href = '/staff') : resetAll('Ready for a new verification. Previous document data was cleared.'));
$('#export-btn').onclick = exportMetadata;
$('#mask-toggle').onchange = e => { state.mask = e.target.checked; renderOverlay(); };
$$('.tabs [data-tab]').forEach(button => { button.onclick = () => (state.result || state.review) && setTab(button.dataset.tab); });
window.addEventListener('pagehide', () => { revokePreviews(); state.uploads = []; state.result = null; state.history = []; });

// ---------- theme: follows the system until the user picks one (kept for this page only;
// the app promises no browser storage) ----------
const darkQuery = matchMedia('(prefers-color-scheme: dark)');
let themeChoice = null;
function applyTheme() {
  const dark = themeChoice ? themeChoice === 'dark' : darkQuery.matches;
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  const toggle = $('#theme-toggle');
  toggle.setAttribute('aria-pressed', String(dark));
  toggle.setAttribute('aria-label', dark ? 'Switch to light mode' : 'Switch to dark mode');
  toggle.title = toggle.getAttribute('aria-label');
}
$('#theme-toggle').onclick = () => {
  themeChoice = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  applyTheme();
};
darkQuery.addEventListener('change', () => { if (!themeChoice) applyTheme(); });
applyTheme();

renderEngineToggle();
renderFiles();
renderHistory();

try {
  const response = await fetch('/v1/health', { signal: AbortSignal.timeout(2500) });
  const health = response.ok ? await response.json() : null;
  if (health?.mode === 'local') {
    state.health = health;
    state.local = true;
    state.engine = health.ocr_engines?.includes('rapidocr') ? 'rapidocr' : (health.ocr_engines?.[0] || 'rapidocr');
    const chip = $('#mode-chip');
    chip.classList.add('local');
    chip.querySelector('span').textContent = health.external_calls ? 'Local backend · LLM endpoint is remote' : 'Local backend';
    const llm = health.llm && health.llm !== 'none';
    $('#llm-pill').innerHTML = `${icon('sparkles')}${llm ? `Parser + ${esc(health.llm === 'mock' ? 'mock LLM' : 'LLM')}` : 'Labeled parser'}`;
    $('#llm-pill').title = llm ? 'The LLM only sees masked text and can only fill fields the parser left empty.' : 'Deterministic labeled-field parser';
    $('#privacy-note').textContent = health.external_calls
      ? 'Documents are read on this machine. Only masked text is sent to the configured LLM endpoint.'
      : 'Documents are processed in memory on this machine. Nothing is stored or sent out.';
    renderEngineToggle();
    renderFiles();
  }
} catch {
  // Static hosting: synthetic samples only.
}
if (!state.local) {
  $('#llm-pill').innerHTML = `${icon('sparkles')}Labeled parser`;
  $('#privacy-note').textContent = 'Hosted demo: synthetic samples run in your browser. Real documents are processed only by the local app.';
}

// ---------- lender review mode: /staff/applications/{id} ----------
// Reuses the workspace above for a stored application and adds Actions and History tabs.
const reviewMatch = location.pathname.match(/^\/staff\/applications\/(\d+)\/?$/);
const REASON_LABELS = { unclear: 'Scan is not clear', wrong_document: 'Wrong document', name_mismatch: 'Name does not match', expired: 'Out of date', incomplete: 'Page cut off or incomplete' };
const ACTION_LABELS = {
  'application.created': 'Application created', 'upload_link.issued': 'Secure upload link issued', 'document.uploaded': 'Document uploaded',
  'check.completed': 'Automatic check completed', 'check.failed': 'Automatic check failed', 'application.viewed': 'Application viewed',
  'reupload.requested': 'Re-upload requested', 'note.added': 'Internal note added', 'application.approved': 'Application approved',
  'application.auto_approved': 'Automatically approved after verification',
  'application.rejected': 'Application rejected', 'application.submitted': 'Submitted for verification',
  'application.resubmitted': 'Resubmitted for verification', 'document.removed': 'Document removed before submitting',
  'document.viewed': 'Document version viewed',
};
const STATUS_TONE = { draft: '', checking: 'info', action_needed: 'warn', in_review: 'info', approved: 'ok', rejected: 'bad' };
const CHECK_TONE = { Verified: 'ok', Mismatch: 'bad', Incomplete: 'warn' };
const fmtTime = ts => (ts ? new Date(ts * 1000).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—');
const slotTitle = slot => state.review?.slots?.[slot]?.title || slot;

async function lenderApi(method, url, body) {
  const options = { method, headers: { 'x-nb-csrf': '1' }, credentials: 'same-origin' };
  if (body !== undefined) { options.headers['content-type'] = 'application/json'; options.body = JSON.stringify(body); }
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(typeof data.detail === 'string' ? data.detail : 'Something went wrong.');
    error.status = response.status;
    throw error;
  }
  return data;
}

function renderReviewBar(data) {
  const a = data.application;
  const bar = $('#review-bar');
  bar.hidden = false;
  bar.innerHTML = `<div>
      <a class="back-link" href="/staff">← Review queue</a>
      <h1>${esc(a.borrower.name)} · ${esc(a.ref)}</h1>
      <span class="muted">${esc(a.borrower.email)} · submitted ${a.submitted ? fmtTime(a.submitted) : '—'} · updated ${fmtTime(a.updated)}</span>
    </div>
    <div class="review-bar-statuses">
      <span class="status-badge ${STATUS_TONE[a.status] || ''}">${esc(a.status_label)}</span>
      ${a.check_status ? `<span class="pill ${CHECK_TONE[a.check_status] || ''}">${esc(a.check_status)}</span>` : ''}
      ${a.review_required ? '<span class="pill warn">Needs human review</span>' : ''}
    </div>`;
}

async function loadReview(keepTab) {
  let data;
  try {
    data = await lenderApi('GET', `/api/staff/applications/${reviewMatch[1]}`);
  } catch (error) {
    if (error.status === 401 || error.status === 404) { location.href = '/staff'; return; }
    toast(error.message);
    return;
  }
  state.review = data;
  $('#landing').hidden = true;
  $('#workspace').hidden = false;
  $$('.tabs [data-tab="actions"], .tabs [data-tab="history"]').forEach(b => { b.hidden = false; });
  $('.tabs [data-tab="compare"]').hidden = true;  // needs the original uploads; stored applications keep the automatic result
  $('#new-btn').textContent = '← Back to queue';
  document.title = `${data.application.ref} — Lender review`;
  renderReviewBar(data);
  state.uploads = [];
  state.uploadSlots = (data.result_slots || []).map(slot => ({ slot, title: data.slots[slot].title, type: data.slots[slot].expected }));
  if (!data.result) {
    state.result = null;
    $('#doc-chips').innerHTML = '';
    renderStepper([]);
    $('#quality').hidden = true;
    $('#page-wrap').innerHTML = '<div class="placeholder-page">No documents uploaded yet</div>';
    setTab('history');
    return;
  }
  const autoApproved = data.application.status === 'approved' && data.application.check_status === 'Verified';
  const check = autoApproved ? {
    ...data.result.check,
    review_required: false,
    reason: data.application.decision_reason || 'Required documents were accepted and verification passed. Automatically approved.',
  } : data.result.check;
  const stages = autoApproved ? data.result.stages.map(stage => stage.key === 'verify'
    ? { ...stage, status: 'done', detail: 'Verified · approved automatically' } : stage) : data.result.stages;
  state.result = { ...data.result, check, stages, mode: 'local' };
  state.docIndex = Math.min(state.docIndex, state.result.documents.length - 1);
  resultStepper(state.result);
  renderDocChips();
  renderQuality(state.result);
  renderViewer();
  $('#export-btn').hidden = false;
  const a = data.application;
  setTab(keepTab || (a.status === 'in_review' ? 'decision' : 'fields'));
}

function renderActions() {
  const data = state.review;
  const a = data.application;
  const decided = a.status === 'approved' || a.status === 'rejected';
  const canDecide = a.status === 'in_review';
  const current = data.documents.filter(d => d.current);
  const firstReason = Object.keys(data.reupload_reasons)[0];
  $('#panel').innerHTML = `<div class="actions-grid">
    <section class="action-box">
      <h4>Decision</h4>
      ${decided
        ? `<div class="decision ${a.status === 'approved' ? 'pass' : 'fail'}"><span class="decision-icon">${icon(a.status === 'approved' ? 'check' : 'x')}</span><div><h3>${esc(a.status_label)}</h3><p>${esc(a.decision_reason || 'No reason recorded.')}</p></div></div>`
        : `<p class="panel-note">${canDecide ? 'Check the OCR results and the Decision trace first. The borrower sees the outcome and your reason.' : 'Available once every document is in and no re-upload is pending.'}</p>
           <label class="field-input">Reason for the borrower <small>(required to reject)</small><textarea id="decision-reason" maxlength="500"></textarea></label>
           <div class="row"><button class="btn btn-primary" id="approve-btn" ${canDecide ? '' : 'disabled'}>Approve</button><button class="btn btn-danger" id="reject-btn" ${canDecide ? '' : 'disabled'}>Reject</button></div>`}
    </section>
    <section class="action-box">
      <h4>Request a re-upload</h4>
      <p class="panel-note">The borrower sees the reason next to the document and can upload a new version. Older versions are kept.</p>
      ${current.length ? `
      <label class="field-input">Document<select id="ru-slot">${current.map(d => `<option value="${esc(d.slot)}">${esc(slotTitle(d.slot))} · version ${d.version}${d.status === 'reupload' ? ' (re-upload pending)' : ''}</option>`).join('')}</select></label>
      <label class="field-input">Reason<select id="ru-reason">${Object.keys(data.reupload_reasons).map(k => `<option value="${esc(k)}">${esc(REASON_LABELS[k] || k)}</option>`).join('')}</select></label>
      <p class="panel-note" id="ru-preview">Borrower sees: “${esc(data.reupload_reasons[firstReason])}”</p>
      <label class="field-input">Extra note for the borrower <small>(optional)</small><textarea id="ru-msg" maxlength="300"></textarea></label>
      <div class="row"><button class="btn btn-outline" id="ru-btn" ${decided ? 'disabled' : ''}>Send re-upload request</button></div>` : '<p class="panel-note">No documents uploaded yet.</p>'}
    </section>
    <section class="action-box">
      <h4>Internal notes</h4>
      <p class="panel-note">Visible to Northbridge staff only. Never shown to the borrower.</p>
      ${data.notes.map(n => `<div class="note">${esc(n.text)}<small>${esc(n.author)} · ${fmtTime(n.created)}</small></div>`).join('')}
      <label class="field-input">Add a note<textarea id="note-text" maxlength="2000"></textarea></label>
      <div class="row"><button class="btn btn-outline" id="note-btn">Add note</button></div>
    </section>
  </div>`;
  const id = a.id;
  const act = async (button, call, message) => {
    button.disabled = true;
    try { await call(); toast(message); await loadReview('actions'); } catch (error) { toast(error.message); button.disabled = false; }
  };
  $('#ru-reason')?.addEventListener('change', e => { $('#ru-preview').textContent = `Borrower sees: “${data.reupload_reasons[e.target.value]}”`; });
  $('#approve-btn')?.addEventListener('click', e => {
    if (!confirm(`Approve ${a.ref} for ${a.borrower.name}?`)) return;
    act(e.currentTarget, () => lenderApi('POST', `/api/staff/applications/${id}/decision`, { decision: 'approve', reason: $('#decision-reason').value }), 'Application approved.');
  });
  $('#reject-btn')?.addEventListener('click', e => {
    if (!$('#decision-reason').value.trim()) { toast('Give the borrower a reason before rejecting.'); $('#decision-reason').focus(); return; }
    if (!confirm(`Reject ${a.ref}? The borrower will see your reason.`)) return;
    act(e.currentTarget, () => lenderApi('POST', `/api/staff/applications/${id}/decision`, { decision: 'reject', reason: $('#decision-reason').value }), 'Application rejected.');
  });
  $('#ru-btn')?.addEventListener('click', e => act(e.currentTarget, () => lenderApi('POST', `/api/staff/applications/${id}/reupload-requests`,
    { slot: $('#ru-slot').value, reason_code: $('#ru-reason').value, message: $('#ru-msg').value }), 'Re-upload requested. The borrower will see it in their portal.'));
  $('#note-btn').addEventListener('click', e => {
    if (!$('#note-text').value.trim()) return;
    act(e.currentTarget, () => lenderApi('POST', `/api/staff/applications/${id}/notes`, { text: $('#note-text').value }), 'Note added.');
  });
}

function renderHistoryTab() {
  const data = state.review;
  const bySlot = {};
  data.documents.forEach(d => { (bySlot[d.slot] = bySlot[d.slot] || []).push(d); });
  const versions = Object.entries(bySlot).map(([slot, list]) => `
    <div class="subhead">${esc(slotTitle(slot))}</div>
    <ul class="version-list">${list.map(d => `<li class="${d.current ? '' : 'old'}"><span>Version ${d.version} · ${esc(d.kind.toUpperCase())} · ${Math.max(1, Math.round(d.size / 1024))} KB${d.detected_type ? ` · read as ${esc(d.detected_type)}` : ''}</span><span>${d.current ? esc(d.status === 'reupload' ? 'Re-upload requested' : d.status === 'accepted' ? 'Accepted' : d.status === 'uploaded' ? 'Uploaded, not yet verified' : 'Checking') : 'Replaced'} · ${fmtTime(d.created)} · <a href="/api/staff/documents/${d.id}/preview" target="_blank" rel="noopener">View</a></span></li>`).join('')}</ul>`).join('');
  const detail = e => [e.detail.slot ? slotTitle(e.detail.slot) : '', e.detail.version ? `v${e.detail.version}` : '', e.detail.status || '', e.detail.reason ? (REASON_LABELS[e.detail.reason] || e.detail.reason) : ''].filter(Boolean).join(' · ');
  $('#panel').innerHTML = `
    ${versions || '<p class="panel-note">No documents uploaded yet.</p>'}
    <div class="subhead">Audit trail</div>
    <p class="panel-note">IDs and actions only. Document contents are never written to the audit log.</p>
    <ul class="timeline">${data.audit.map(e => `<li><time>${fmtTime(e.ts)}</time><span><b>${esc(e.actor)}</b> ${esc(ACTION_LABELS[e.action] || e.action)}${detail(e) ? ` <span class="muted">· ${esc(detail(e))}</span>` : ''}</span></li>`).join('')}</ul>`;
}

if (reviewMatch) loadReview();
