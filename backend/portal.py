"""Borrower portal and lender dashboard API.

Flow (borrower): register/log in -> request a secure upload link for a document slot ->
PUT the file directly to that link -> it is encrypted into storage and recorded as a new
document version (no verification yet; the borrower can preview, replace or remove it) ->
"Submit for verification" runs the pipeline over all current documents -> each document is
accepted or gets a re-upload prompt, and the application gets its check status
(Verified / Mismatch / Incomplete / Not applicable). Verified complete applications are
approved automatically; borrowers can replace documents only before final approval.

Flow (lender): log in -> review exception queue -> open an application (every view is audited) ->
review mismatches/incomplete results -> request a re-upload, add notes, approve or reject.

Isolation: borrower endpoints take no application or document IDs; they always resolve the
signed-in borrower's own application. Lender endpoints require the lender role. Objects that
are not visible to the caller return 404.
"""
import hashlib
import json
import logging
import re
import secrets
import threading
import time
import io
import urllib.parse
import pymupdf
from PIL import Image, ImageOps
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from .pipeline import DocumentError, MAX_FILE, normalize_name, process, validate
from .store import token_hash, verify_password

COOKIE = 'nb_session'
UPLOAD_TTL = 600
SLOTS = {
    'id': {'title': 'ID card', 'expected': 'ID card', 'required': True, 'fields': ['full_name'],
           'hint': 'Aadhaar, PAN card or another government ID. Synthetic test documents only.'},
    'slip': {'title': 'Salary slip', 'expected': 'Salary slip', 'required': True, 'fields': ['full_name', 'monthly_salary'],
             'hint': 'Your latest payslip showing name, employer and gross monthly salary.'},
    'bank': {'title': 'Bank statement', 'expected': 'Bank statement, page 1', 'required': False, 'fields': ['full_name'],
             'hint': 'Page 1 only. Your account number is masked and never stored as a field.'},
    'other1': {'title': 'Other document', 'expected': None, 'required': False, 'fields': [],
               'hint': 'Any related document, e.g. a second payslip.'},
    'other2': {'title': 'Other document', 'expected': None, 'required': False, 'fields': [],
               'hint': 'Any related document.'},
}
FIELD_WORDS = {'full_name': 'your name', 'monthly_salary': 'the gross monthly salary'}
STATUS_LABELS = {
    'draft': 'Not submitted', 'checking': 'Checking your documents', 'action_needed': 'Action needed',
    'in_review': 'Under review', 'approved': 'Approved', 'rejected': 'Not approved',
}
REUPLOAD_REASONS = {
    'unclear': 'The scan is not clear enough to read. Please upload a sharper photo or the original PDF.',
    'wrong_document': 'This is not the requested document. Please upload the correct one.',
    'name_mismatch': 'The name on this document does not match your other documents. Please upload a document in your name.',
    'expired': 'This document appears to be out of date. Please upload a current one.',
    'incomplete': 'Part of the document is missing or cut off. Please upload the full page.',
}
WORK_LOCK = threading.Lock()
audit_log = logging.getLogger('northbridge.audit')
_login_failures = {}

router = APIRouter(prefix='/api')


# ---------------------------------------------------------------- helpers
def store(request: Request):
    return request.app.state.store


def current_user(request: Request):
    user = store(request).session_user(request.cookies.get(COOKIE))
    if user is None:
        raise HTTPException(401, 'Please sign in.')
    return user


def borrower(user=Depends(current_user)):
    if user['role'] != 'borrower':
        raise HTTPException(404, 'Not found.')
    return user


def lender(user=Depends(current_user)):
    if user['role'] != 'lender':
        raise HTTPException(404, 'Not found.')
    return user


def new_ref(st):
    while True:
        ref = f'NB-{time.strftime("%Y")}-{secrets.randbelow(900000) + 100000}'
        if not st.one('SELECT 1 FROM applications WHERE ref=?', (ref,)):
            return ref


def own_application(st, user):
    """The borrower's active application, created on first use."""
    row = st.one('SELECT * FROM applications WHERE borrower_id=? ORDER BY id DESC LIMIT 1', (user['id'],))
    if row is None:
        now = time.time()
        app_id = st.run('INSERT INTO applications(ref,borrower_id,status,created,updated) VALUES(?,?,?,?,?)',
                        (new_ref(st), user['id'], 'draft', now, now))
        st.audit('application.created', user['id'], app_id)
        row = st.one('SELECT * FROM applications WHERE id=?', (app_id,))
    return row


def current_docs(st, app_id):
    rows = st.q('SELECT * FROM documents WHERE application_id=? AND current=1', (app_id,))
    order = list(SLOTS)
    return sorted(rows, key=lambda r: order.index(r['slot']))


def identity_candidates_from_documents(documents):
    """Build keyed-match inputs from parsed identifiers, never from raw document bytes."""
    candidates = set()
    for doc in documents:
        fields = getattr(doc, 'fields', None) or doc.get('fields') or {}
        get = fields.get if isinstance(fields, dict) else lambda key: getattr(fields, key, None)
        person = normalize_name(get('full_name') or '')
        if not person:
            continue
        identifier = re.sub(r'[^A-Z0-9]', '', (get('id_number') or '').upper())
        if identifier:
            candidates.add(('identity_id', f'{person}|{identifier}'))
        employee_id = re.sub(r'[^A-Z0-9]', '', (get('employee_id') or '').upper())
        employer = normalize_name(get('employer_name') or '')
        # Employee IDs can be reused by unrelated employers; require employer and name context.
        if employee_id and employer:
            candidates.add(('employee_id', f'{person}|{employer}|{employee_id}'))
    return sorted(candidates)


def verified_identity_candidates(result):
    return identity_candidates_from_documents(result.documents)


def backfill_verified_identity_keys(st):
    """Index existing approved, verified applications after deploying the fingerprint table."""
    rows = st.q("SELECT id, borrower_id, result_blob FROM applications WHERE status='approved' AND check_status='Verified' AND result_blob IS NOT NULL")
    for row in rows:
        try:
            payload = json.loads(st.decrypt(row['result_blob'], f'result:{row["id"]}'))
            candidates = identity_candidates_from_documents(payload.get('documents', []))
            st.check_and_register_verified_identity_keys(row['id'], row['borrower_id'], candidates, register=True)
        except Exception:
            # A legacy result that cannot be decrypted or decoded is left out of the index.
            continue


def decided(app_row):
    return app_row['status'] in ('approved', 'rejected')


REQUIRED = ('id', 'slip')


def promote_verified_applications(st):
    """Move already-checked complete Verified applications out of the review queue."""
    with WORK_LOCK:
        rows = st.q("SELECT id FROM applications WHERE status='in_review' AND check_status='Verified'")
        for row in rows:
            docs = current_docs(st, row['id'])
            slots = {doc['slot'] for doc in docs}
            if any(doc['status'] != 'accepted' for doc in docs) or not set(REQUIRED) <= slots:
                continue
            now = time.time()
            reason = 'Automatically approved: all required documents were accepted and the verification check passed.'
            st.run("UPDATE applications SET status='approved', review_required=0, decision_reason=?, updated=? WHERE id=?",
                   (reason, now, row['id']))
            st.audit('application.auto_approved', None, row['id'],
                     detail=json.dumps({'check_status': 'Verified', 'migrated_existing_check': True}))
        backfill_verified_identity_keys(st)


def can_upload(app_row, doc, slot=None):
    """Allow draft/re-upload changes and let borrowers correct name mismatches immediately."""
    mismatch_slot = app_row['check_status'] == 'Mismatch' and slot in ('id', 'slip')
    if app_row['status'] == 'draft':
        return True
    if app_row['status'] == 'action_needed':
        return ((doc is None and slot in REQUIRED) or (doc is not None and doc['status'] == 'reupload')
                or mismatch_slot)
    if app_row['status'] == 'in_review':
        return (doc is not None and doc['status'] == 'reupload') or (
            mismatch_slot)
    return False


def can_remove(app_row, doc):
    return doc is not None and app_row['status'] == 'draft'


def submit_state(app_row, docs):
    """(can_submit, hint) for the borrower's Submit button."""
    if app_row['status'] not in ('draft', 'action_needed'):
        return False, None
    if not docs:
        return False, 'Upload your documents first.'
    if any(d['status'] == 'reupload' for d in docs):
        return False, 'Replace the documents marked "Re-upload needed" first.'
    missing = [SLOTS[s]['title'] for s in REQUIRED if s not in {d['slot'] for d in docs}]
    if missing:
        return True, f'You can submit now, but without your {" and ".join(m.lower() if not m.startswith("ID") else m for m in missing)} the result will be Incomplete.'
    return True, 'All required documents are attached.'


# ---------------------------------------------------------------- check worker
def doc_noun(title):
    """'ID card' stays as is; 'Salary slip' -> 'salary slip'; 'Bank statement, page 1' -> 'bank statement'."""
    title = title.split(',')[0]
    return title if title.startswith('ID') else title[0].lower() + title[1:]


def with_article(noun):
    return f'an {noun}' if noun[0] in 'aeiouAEIOU' or noun.startswith('ID') else f'a {noun}'


def doc_outcome(slot, result_doc):
    """Automatic validation for one document: (status, detected_type, customer-facing reason)."""
    spec = SLOTS[slot]
    if result_doc.error:
        return 'reupload', 'Unknown', 'We could not read this file. Please upload a clearer scan or the original PDF.'
    detected = result_doc.document_type
    expected = spec['expected']
    if expected and detected != expected:
        what = 'a document we could not recognise' if detected == 'Unknown' else with_article(doc_noun(detected))
        wanted = doc_noun(spec['title'])
        return 'reupload', detected, f'This looks like {what}, not {with_article(wanted)}. Please upload your {wanted} here.'
    if slot == 'id' and result_doc.fields.is_pan_card and not result_doc.fields.pan_number:
        return 'reupload', detected, 'We could not read the PAN number on this card. Upload a clearer PAN card image.'
    required_fields = spec['fields']
    if slot == 'id' and result_doc.fields.is_pan_card and result_doc.fields.pan_number:
        # A PAN number provides a reliable link to the matching number on the payslip,
        # even when OCR cannot recover the PAN card's printed name line.
        required_fields = [field for field in required_fields if field != 'full_name']
    missing = [FIELD_WORDS[f] for f in required_fields if getattr(result_doc.fields, f) is None]
    if missing:
        return 'reupload', detected, f'We could not find {" or ".join(missing)} on this document. Please upload a clearer or complete copy.'
    return 'accepted', detected, None


def run_check(st, app_id, analyzer, llm):
    """Re-run the pipeline over the application's current documents and update statuses."""
    with WORK_LOCK:
        app_row = st.one('SELECT * FROM applications WHERE id=?', (app_id,))
        docs = current_docs(st, app_id)
        if app_row is None or decided(app_row):
            return
        if not docs:
            st.run('UPDATE applications SET status=?, updated=? WHERE id=?', ('draft', time.time(), app_id))
            return
        try:
            payload = [st.get_file(d['storage_key']) for d in docs]
            result = process(payload, analyzer, llm, 'auto', tolerant=True, preview_images=True)
        except Exception:
            # Fail closed: every document gets a re-upload prompt, nothing is echoed.
            for d in docs:
                st.run('UPDATE documents SET status=?, reason=? WHERE id=?',
                       ('reupload', 'We could not process this file. Please upload it again.', d['id']))
            st.run('UPDATE applications SET status=?, updated=? WHERE id=?', ('action_needed', time.time(), app_id))
            st.audit('check.failed', None, app_id)
            return
        finally:
            payload = None
        for d, r in zip(docs, result.documents):
            if d['status'] == 'reupload' and d['requested_by']:
                continue  # A lender's re-upload request stands until the borrower uploads a new version.
            status, detected, reason = doc_outcome(d['slot'], r)
            st.run('UPDATE documents SET status=?, detected_type=?, reason=? WHERE id=?', (status, detected, reason, d['id']))
        pan_card_present = any(r.document_type == 'ID card' and r.fields.is_pan_card for r in result.documents)
        if pan_card_present:
            slip_doc = next((d for d, r in zip(docs, result.documents) if d['slot'] == 'slip' and not r.error), None)
            if slip_doc:
                slip_result = next(r for d, r in zip(docs, result.documents) if d['id'] == slip_doc['id'])
                if not slip_result.fields.pan_number:
                    st.run('UPDATE documents SET status=?, reason=? WHERE id=?',
                           ('reupload', 'We could not find a PAN number on this salary slip. Upload a copy that shows the PAN number.', slip_doc['id']))
        docs = current_docs(st, app_id)
        slots = {d['slot'] for d in docs}
        check = result.check
        llm_name_used = any(
            doc.document_type in ('ID card', 'Salary slip')
            and doc.field_evidence.get('full_name') is not None
            and doc.field_evidence['full_name'].source == 'llm'
            for doc in result.documents
        )
        if check.status == 'Verified' and llm_name_used:
            reason = 'An LLM supplied a name used in the match; lender confirmation is required.'
            check = check.model_copy(update={
                'review_required': True,
                'review_reasons': list(dict.fromkeys([*check.review_reasons, reason])),
            })
            result.check = check
            for doc in result.documents:
                if doc.document_type in ('ID card', 'Salary slip'):
                    doc.check = check
            for stage in result.stages:
                if stage.get('key') == 'verify':
                    stage.update(status='warn', detail='Verified · LLM-supplied name needs confirmation')
        identity_candidates = verified_identity_candidates(result)
        eligible_for_registration = (
            check.status == 'Verified' and not llm_name_used
            and not any(d['status'] == 'reupload' for d in docs)
            and set(REQUIRED) <= slots
        )
        reused = st.check_and_register_verified_identity_keys(
            app_id, app_row['borrower_id'], identity_candidates,
            register=eligible_for_registration)
        if reused:
            if 'identity_id' in reused:
                reason = ('Not applicable: an identity number and name on these documents match a document '
                          'already used on a verified account. If this is your document, contact support.')
            else:
                reason = ('Not applicable: this employee ID, employer and name match a payslip already used '
                          'on a verified account. If this is your document, contact support.')
            check = check.model_copy(update={
                'status': 'Not applicable', 'reason': reason, 'review_required': True,
                'review_reasons': [reason],
            })
            result.check = check
            for doc in result.documents:
                doc.check = check
            for stage in result.stages:
                if stage.get('key') == 'verify':
                    stage.update(status='warn', detail='Not applicable Â· verified identity match found')
        if any(d['status'] == 'reupload' for d in docs) or not set(REQUIRED) <= slots:
            status = 'action_needed'
        elif check.status == 'Verified' and not llm_name_used:
            # A complete, verified document set is approved automatically; the lender
            # queue is reserved for mismatches, incomplete checks, and LLM-sourced names.
            status = 'approved'
        else:
            status = 'in_review'
        blob = st.encrypt(result.model_dump_json().encode(), f'result:{app_id}')
        st.run('UPDATE applications SET status=?, check_status=?, check_reason=?, review_required=?, review_reasons=?, '
               "result_blob=?, result_slots=?, stages=?, decision_reason=?, updated=?, submitted=COALESCE(submitted, CASE WHEN ? IN ('in_review','approved') THEN ? END) WHERE id=?",
               (status, check.status, check.reason, int(check.review_required and status != 'approved'), json.dumps(check.review_reasons),
                blob, json.dumps([d['slot'] for d in docs]), json.dumps(result.stages),
                'Automatically approved: all required documents were accepted and the verification check passed.' if status == 'approved' else None,
                time.time(), status, time.time(), app_id))
        for event in result.telemetry:  # Verification engine: sanitized logs only
            audit_log.info(json.dumps({**event, 'application_id': app_id}))
        st.audit('check.completed', None, app_id, detail=json.dumps({
            'status': check.status, 'review_required': check.review_required and status != 'approved',
            'application_status': status, 'auto_approved': status == 'approved'}))
        if status == 'approved':
            st.audit('application.auto_approved', None, app_id, detail=json.dumps({'check_status': check.status}))


# ---------------------------------------------------------------- auth
class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class Registration(Credentials):
    name: str = Field(min_length=2, max_length=80)


def _set_session(response, request, token):
    secure = request.app.state.cookie_secure
    response.set_cookie(COOKIE, token, httponly=True, samesite='strict', secure=secure, max_age=8 * 3600, path='/')


@router.post('/auth/register', status_code=201)
def register(body: Registration, request: Request, response: Response):
    st = store(request)
    email = body.email.strip().lower()
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email):
        raise HTTPException(422, 'Enter a valid email address.')
    if len(body.password) < 10:
        raise HTTPException(422, 'Use a password of at least 10 characters.')
    if st.one('SELECT 1 FROM users WHERE email=?', (email,)):
        raise HTTPException(409, 'An account with this email already exists. Sign in instead.')
    user_id = st.create_user(email, body.name.strip(), body.password, 'borrower')
    st.audit('user.registered', user_id)
    _set_session(response, request, st.new_session(user_id))
    return {'role': 'borrower'}


@router.post('/auth/login')
def login(body: Credentials, request: Request, response: Response):
    st = store(request)
    email = body.email.strip().lower()
    key = hashlib.sha256(email.encode()).hexdigest()
    fails = [t for t in _login_failures.get(key, []) if t > time.time() - 300]
    if len(fails) >= 5:
        raise HTTPException(429, 'Too many attempts. Try again in a few minutes.')
    user = st.one('SELECT * FROM users WHERE email=?', (email,))
    if user is None or not verify_password(body.password, user['pw_hash']):
        _login_failures[key] = fails + [time.time()]
        raise HTTPException(401, 'Email or password is incorrect.')
    _login_failures.pop(key, None)
    _set_session(response, request, st.new_session(user['id']))
    st.audit('user.login', user['id'])
    return {'role': user['role']}


@router.post('/auth/logout')
def logout(request: Request, response: Response):
    store(request).end_session(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path='/')
    return {'ok': True}


@router.get('/auth/me')
def me(user=Depends(current_user)):
    return {'name': user['name'], 'email': user['email'], 'role': user['role']}


# ---------------------------------------------------------------- borrower
def borrower_view(st, app_row):
    docs = {d['slot']: d for d in current_docs(st, app_row['id'])}
    slots = []
    for key, spec in SLOTS.items():
        d = docs.get(key)
        slots.append({
            'slot': key, 'title': spec['title'], 'required': spec['required'], 'hint': spec['hint'],
            'can_upload': can_upload(app_row, d, key),
            'can_remove': can_remove(app_row, d),
            'missing': d is None and key in REQUIRED and app_row['status'] == 'action_needed',
            'document': None if d is None else {
                'status': d['status'], 'reason': d['reason'], 'version': d['version'], 'kind': d['kind'],
                'size': d['size'], 'uploaded': d['created'], 'requested_by_reviewer': bool(d['requested_by']),
                'filename': st.decrypt(d['filename_blob'], f'document-name:{d["id"]}').decode('utf-8') if d['filename_blob'] else None,
            },
        })
    show_check = app_row['check_status'] and app_row['status'] not in ('checking', 'draft')
    check_reason = app_row['check_reason']
    if (show_check and app_row['check_status'] == 'Mismatch' and app_row['result_blob']
            and not (check_reason or '').startswith('The PAN number')):
        # Keep extracted names inside encrypted result storage; construct this detail only
        # for the authenticated borrower's response instead of persisting names in plaintext.
        try:
            result = json.loads(st.decrypt(app_row['result_blob'], f'result:{app_row["id"]}'))
            names = {
                doc.get('document_type'): (doc.get('fields') or {}).get('full_name')
                for doc in result.get('documents', [])
            }
            id_name, slip_name = names.get('ID card'), names.get('Salary slip')
            if id_name and slip_name:
                check_reason = f'The ID card shows "{id_name}", while the salary slip shows "{slip_name}".'
        except (TypeError, ValueError, KeyError):
            pass
    can_submit, submit_hint = submit_state(app_row, list(docs.values()))
    pipeline = [{k: stage[k] for k in ('key', 'label', 'status', 'detail')}
                for stage in json.loads(app_row['stages'] or '[]')]
    if app_row['status'] == 'approved':
        for stage in pipeline:
            if stage['key'] == 'verify':
                stage.update(status='done', detail='Verified · approved automatically')
    return {
        'can_submit': can_submit, 'submit_hint': submit_hint, 'resubmit': bool(app_row['submitted']),
        'ref': app_row['ref'], 'status': app_row['status'], 'status_label': STATUS_LABELS[app_row['status']],
        'check': {'status': app_row['check_status'], 'reason': check_reason} if show_check else None,
        'decision_reason': app_row['decision_reason'] if decided(app_row) else None,
        'created': app_row['created'], 'updated': app_row['updated'], 'slots': slots,
        'pipeline': pipeline,
    }


@router.get('/borrower/application')
def borrower_application(request: Request, user=Depends(borrower)):
    st = store(request)
    return borrower_view(st, own_application(st, user))


class LinkRequest(BaseModel):
    slot: str


@router.post('/borrower/upload-links', status_code=201)
def upload_link(body: LinkRequest, request: Request, user=Depends(borrower)):
    st = store(request)
    if body.slot not in SLOTS:
        raise HTTPException(422, 'Unknown document slot.')
    app_row = own_application(st, user)
    doc = st.one('SELECT * FROM documents WHERE application_id=? AND slot=? AND current=1', (app_row['id'], body.slot))
    if not can_upload(app_row, doc, body.slot):
        raise HTTPException(409, 'This document cannot be replaced right now.')
    token = secrets.token_urlsafe(32)
    st.run('INSERT INTO upload_links(token_hash,application_id,slot,user_id,expires) VALUES(?,?,?,?,?)',
           (token_hash(token), app_row['id'], body.slot, user['id'], time.time() + UPLOAD_TTL))
    st.audit('upload_link.issued', user['id'], app_row['id'], detail=json.dumps({'slot': body.slot}))
    return {'upload_url': f'/api/uploads/{token}', 'method': 'PUT', 'expires_in': UPLOAD_TTL, 'max_bytes': MAX_FILE}


@router.put('/uploads/{token}', status_code=202)
async def direct_upload(token: str, request: Request, background: BackgroundTasks, user=Depends(borrower)):
    st = store(request)
    link = st.one('SELECT * FROM upload_links WHERE token_hash=?', (token_hash(token),))
    if link is None or link['user_id'] != user['id'] or link['used'] or link['expires'] < time.time():
        raise HTTPException(404, 'This upload link is invalid or has expired. Request a new one.')
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_FILE:
            raise HTTPException(413, 'Each file must be no larger than 10 MB.')
        body.extend(chunk)
    data = bytes(body)
    try:
        kind = validate(data)
    except DocumentError as exc:
        raise HTTPException(422, str(exc))
    st.run('UPDATE upload_links SET used=1 WHERE token_hash=?', (token_hash(token),))
    app_row = st.one('SELECT * FROM applications WHERE id=?', (link['application_id'],))
    prev = st.one('SELECT * FROM documents WHERE application_id=? AND slot=? AND current=1', (app_row['id'], link['slot']))
    if not can_upload(app_row, prev, link['slot']):
        raise HTTPException(409, 'This document cannot be replaced right now.')
    key = st.put_file(data)
    st.run('UPDATE documents SET current=0 WHERE application_id=? AND slot=?', (app_row['id'], link['slot']))
    doc_id = st.run('INSERT INTO documents(application_id,slot,version,current,status,storage_key,sha256,size,kind,created) '
                    'VALUES(?,?,?,?,?,?,?,?,?,?)',
                    (app_row['id'], link['slot'], (prev['version'] + 1) if prev else 1, 1, 'uploaded', key,
                     hashlib.sha256(data).hexdigest(), len(data), kind, time.time()))
    encoded_name = request.headers.get('x-nb-filename', '')
    try:
        filename = urllib.parse.unquote(encoded_name, errors='replace')
    except Exception:
        filename = ''
    filename = filename.replace('\\', '/').split('/')[-1]
    filename = ''.join(ch for ch in filename if ch.isprintable()).strip()[:255]
    if filename:
        st.run('UPDATE documents SET filename_blob=? WHERE id=?',
               (st.encrypt(filename.encode('utf-8'), f'document-name:{doc_id}'), doc_id))
    next_status = 'action_needed' if app_row['status'] == 'in_review' else app_row['status']
    st.run('UPDATE applications SET status=?, updated=? WHERE id=?', (next_status, time.time(), app_row['id']))
    st.audit('document.uploaded', user['id'], app_row['id'], doc_id, json.dumps({'slot': link['slot'], 'version': (prev['version'] + 1) if prev else 1}))
    data = None
    body.clear()
    return {'status': 'uploaded'}


@router.delete('/borrower/documents/{slot}')
def remove_document(slot: str, request: Request, user=Depends(borrower)):
    """Before submitting, a borrower can take a document out. The encrypted version is kept for the audit trail."""
    st = store(request)
    app_row = own_application(st, user)
    doc = st.one('SELECT * FROM documents WHERE application_id=? AND slot=? AND current=1', (app_row['id'], slot))
    if doc is None:
        raise HTTPException(404, 'Not found.')
    if not can_remove(app_row, doc):
        raise HTTPException(409, 'Documents can only be removed before you submit.')
    st.run('UPDATE documents SET current=0 WHERE id=?', (doc['id'],))
    st.run('UPDATE applications SET updated=? WHERE id=?', (time.time(), app_row['id']))
    st.audit('document.removed', user['id'], app_row['id'], doc['id'], json.dumps({'slot': slot}))
    return {'ok': True}


@router.post('/borrower/submit', status_code=202)
def submit(request: Request, background: BackgroundTasks, user=Depends(borrower)):
    """Run verification (pre-scan, OCR, PII masking, LLM evaluation, verification) on all current documents."""
    st = store(request)
    app_row = own_application(st, user)
    can, hint = submit_state(app_row, current_docs(st, app_row['id']))
    if not can:
        raise HTTPException(409, hint or 'This application has already been submitted.')
    now = time.time()
    st.run("UPDATE documents SET status='checking' WHERE application_id=? AND current=1 AND status='uploaded'", (app_row['id'],))
    st.run("UPDATE applications SET status=?, updated=?, stages='[]', submitted=COALESCE(submitted, ?) WHERE id=?", ('checking', now, now, app_row['id']))
    st.audit('application.resubmitted' if app_row['submitted'] else 'application.submitted', user['id'], app_row['id'])
    background.add_task(run_check, st, app_row['id'], request.app.state.analyzer, request.app.state.llm)
    return {'status': 'checking'}


def render_preview(data, kind, max_side):
    """Page 1 as a JPEG, rendered on request from the decrypted file (nothing cached on disk)."""
    if kind == 'pdf':
        with pymupdf.open(stream=data, filetype='pdf') as pdf:
            page = pdf[0]
            scale = min(3.0, max_side / max(page.rect.width, page.rect.height))
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            image = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
    else:
        with Image.open(io.BytesIO(data)) as opened:
            image = ImageOps.exif_transpose(opened).convert('RGB')
        image.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', quality=82)
    return buffer.getvalue()


def preview_response(st, doc, size):
    try:
        content = render_preview(st.get_file(doc['storage_key']), doc['kind'], 1600 if size == 'large' else 420)
    except Exception:
        raise HTTPException(422, 'Preview is not available for this file.')
    return Response(content, media_type='image/jpeg', headers={'Cache-Control': 'no-store'})


@router.get('/borrower/documents/{slot}/preview')
def borrower_preview(slot: str, request: Request, size: str = 'thumb', user=Depends(borrower)):
    st = store(request)
    app_row = own_application(st, user)
    doc = st.one('SELECT * FROM documents WHERE application_id=? AND slot=? AND current=1', (app_row['id'], slot))
    if doc is None:
        raise HTTPException(404, 'Not found.')
    return preview_response(st, doc, size)


@router.get('/staff/documents/{doc_id}/preview')
def staff_preview(doc_id: int, request: Request, size: str = 'large', user=Depends(lender)):
    """Any stored version, for staff review. Every view is audited."""
    st = store(request)
    doc = st.one('SELECT * FROM documents WHERE id=?', (doc_id,))
    if doc is None:
        raise HTTPException(404, 'Not found.')
    st.audit('document.viewed', user['id'], doc['application_id'], doc['id'])
    return preview_response(st, doc, size)


# ---------------------------------------------------------------- lender
@router.get('/staff/applications')
def queue(request: Request, status: str = '', q: str = '', user=Depends(lender)):
    st = store(request)
    sql = ('SELECT a.*, u.name AS borrower_name, u.email AS borrower_email, '
           '(SELECT COUNT(*) FROM documents d WHERE d.application_id=a.id AND d.current=1) AS doc_count, '
           "(SELECT COUNT(*) FROM documents d WHERE d.application_id=a.id AND d.current=1 AND d.status='reupload') AS reupload_count "
           'FROM applications a JOIN users u ON u.id=a.borrower_id WHERE 1=1')
    args = []
    if status:
        sql += ' AND a.status=?'
        args.append(status)
    if q.strip():
        sql += ' AND (a.ref LIKE ? OR u.name LIKE ? OR u.email LIKE ?)'
        like = f'%{q.strip()}%'
        args += [like, like, like]
    sql += ' ORDER BY a.updated DESC LIMIT 500'
    rows = st.q(sql, args)
    counts = {r['status']: r['n'] for r in st.q('SELECT status, COUNT(*) AS n FROM applications GROUP BY status')}
    return {'counts': counts, 'applications': [{
        'id': r['id'], 'ref': r['ref'], 'borrower': r['borrower_name'], 'email': r['borrower_email'],
        'status': r['status'], 'status_label': STATUS_LABELS[r['status']], 'check_status': r['check_status'],
        'review_required': bool(r['review_required']), 'documents': r['doc_count'], 'reuploads': r['reupload_count'],
        'created': r['created'], 'submitted': r['submitted'], 'updated': r['updated'],
    } for r in rows]}


def lender_app(st, app_id):
    row = st.one('SELECT a.*, u.name AS borrower_name, u.email AS borrower_email FROM applications a '
                 'JOIN users u ON u.id=a.borrower_id WHERE a.id=?', (app_id,))
    if row is None:
        raise HTTPException(404, 'Not found.')
    return row


@router.get('/staff/applications/{app_id}')
def application_detail(app_id: int, request: Request, user=Depends(lender)):
    st = store(request)
    row = lender_app(st, app_id)
    st.audit('application.viewed', user['id'], app_id)
    result = json.loads(st.decrypt(row['result_blob'], f'result:{app_id}')) if row['result_blob'] else None
    docs = st.q('SELECT * FROM documents WHERE application_id=? ORDER BY slot, version DESC', (app_id,))
    notes = st.q('SELECT n.*, u.name AS author FROM notes n JOIN users u ON u.id=n.author_id WHERE application_id=? ORDER BY created', (app_id,))
    audit = st.q('SELECT a.*, u.name AS actor, u.role AS actor_role FROM audit a LEFT JOIN users u ON u.id=a.actor_id '
                 'WHERE application_id=? ORDER BY ts DESC LIMIT 200', (app_id,))
    return {
        'application': {
            'id': row['id'], 'ref': row['ref'], 'status': row['status'], 'status_label': STATUS_LABELS[row['status']],
            'check_status': row['check_status'], 'review_required': bool(row['review_required']),
            'review_reasons': json.loads(row['review_reasons']), 'decision_reason': row['decision_reason'],
            'created': row['created'], 'submitted': row['submitted'], 'updated': row['updated'],
            'borrower': {'name': row['borrower_name'], 'email': row['borrower_email']},
        },
        'result': result,
        'result_slots': json.loads(row['result_slots']),
        'stages': json.loads(row['stages'] or '[]'),
        'slots': {k: {'title': v['title'], 'expected': v['expected']} for k, v in SLOTS.items()},
        'reupload_reasons': REUPLOAD_REASONS,
        'documents': [{'id': d['id'], 'slot': d['slot'], 'version': d['version'], 'current': bool(d['current']),
                       'status': d['status'], 'detected_type': d['detected_type'], 'reason': d['reason'],
                       'requested_by_reviewer': bool(d['requested_by']), 'kind': d['kind'], 'size': d['size'],
                       'created': d['created']} for d in docs],
        'notes': [{'author': n['author'], 'created': n['created'],
                   'text': st.decrypt(n['body'], f'note:{n["id"]}:{app_id}').decode()} for n in notes],
        'audit': [{'ts': a['ts'], 'action': a['action'], 'actor': a['actor'] or 'System', 'actor_role': a['actor_role'] or 'system',
                   'document_id': a['document_id'], 'detail': json.loads(a['detail'] or '{}')} for a in audit],
    }


class ReuploadRequest(BaseModel):
    slot: str
    reason_code: str = 'unclear'
    message: str = Field(default='', max_length=300)


@router.post('/staff/applications/{app_id}/reupload-requests')
def request_reupload(app_id: int, body: ReuploadRequest, request: Request, user=Depends(lender)):
    st = store(request)
    row = lender_app(st, app_id)
    if decided(row):
        raise HTTPException(409, 'This application has already been decided.')
    if body.reason_code not in REUPLOAD_REASONS:
        raise HTTPException(422, 'Unknown reason.')
    doc = st.one('SELECT * FROM documents WHERE application_id=? AND slot=? AND current=1', (app_id, body.slot))
    if doc is None:
        raise HTTPException(404, 'No document in that slot.')
    reason = REUPLOAD_REASONS[body.reason_code] + (f' Note from the reviewer: {body.message.strip()}' if body.message.strip() else '')
    st.run('UPDATE documents SET status=?, reason=?, requested_by=? WHERE id=?', ('reupload', reason, user['id'], doc['id']))
    st.run('UPDATE applications SET status=?, updated=? WHERE id=?', ('action_needed', time.time(), app_id))
    st.audit('reupload.requested', user['id'], app_id, doc['id'], json.dumps({'slot': body.slot, 'reason': body.reason_code}))
    return {'status': 'action_needed'}


class Decision(BaseModel):
    decision: str
    reason: str = Field(default='', max_length=500)


@router.post('/staff/applications/{app_id}/decision')
def decide(app_id: int, body: Decision, request: Request, user=Depends(lender)):
    st = store(request)
    row = lender_app(st, app_id)
    if body.decision not in ('approve', 'reject'):
        raise HTTPException(422, 'Decision must be approve or reject.')
    if row['status'] != 'in_review':
        raise HTTPException(409, 'Only applications under review can be decided.')
    if body.decision == 'reject' and not body.reason.strip():
        raise HTTPException(422, 'Give the borrower a reason for the rejection.')
    if body.decision == 'approve' and row['check_status'] == 'Verified' and row['result_blob']:
        payload = json.loads(st.decrypt(row['result_blob'], f'result:{app_id}'))
        candidates = identity_candidates_from_documents(payload.get('documents', []))
        reused = st.check_and_register_verified_identity_keys(
            app_id, row['borrower_id'], candidates, register=True)
        if reused:
            raise HTTPException(409, 'A matching verified identity is already registered. Review this application before approving it.')
    status = 'approved' if body.decision == 'approve' else 'rejected'
    st.run('UPDATE applications SET status=?, decision_reason=?, updated=? WHERE id=?', (status, body.reason.strip() or None, time.time(), app_id))
    st.audit(f'application.{status}', user['id'], app_id, detail=json.dumps({'check_status': row['check_status'], 'review_required': bool(row['review_required'])}))
    return {'status': status}


class Note(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post('/staff/applications/{app_id}/notes', status_code=201)
def add_note(app_id: int, body: Note, request: Request, user=Depends(lender)):
    st = store(request)
    lender_app(st, app_id)
    note_id = st.run('INSERT INTO notes(application_id,author_id,body,created) VALUES(?,?,?,?)', (app_id, user['id'], b'', time.time()))
    st.run('UPDATE notes SET body=? WHERE id=?', (st.encrypt(body.text.strip().encode(), f'note:{note_id}:{app_id}'), note_id))
    st.audit('note.added', user['id'], app_id)
    return {'ok': True}
