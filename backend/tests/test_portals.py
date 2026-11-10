"""Borrower portal and lender dashboard: auth, isolation, upload links, re-upload flow, decisions."""
import json
import logging
from pathlib import Path
import pymupdf
import pytest
from fastapi.testclient import TestClient
from backend.app import app
from backend.tests.conftest import lender_session

PASSWORD = 'borrower-pass-123'


def pdf(text):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 65), text, fontsize=14)
    data = doc.tobytes()
    doc.close()
    return data


ID = lambda name='Ravi Menon': pdf(f'IDENTITY CARD\nFull name: {name}\nDate of birth: 14/08/1994\nID number: ABCDE1234F\nSYNTHETIC SAMPLE')
SLIP = lambda name='Ravi Menon': pdf(f'SALARY SLIP\nEmployee name: {name}\nEmployer: Harbour Tools Pvt Ltd\nGross monthly salary: INR 82,000.00\nSYNTHETIC SAMPLE')
BANK = lambda name='Ravi Menon': pdf(f'BANK STATEMENT\nAccount holder: {name}\nAccount number: 12345678901234\nSalary credit: INR 78,500.00\nSYNTHETIC SAMPLE')


@pytest.fixture(scope='module')
def server():
    with TestClient(app) as client:  # runs the lifespan once (store, analyzer, OCR)
        yield client


_counter = iter(range(10_000))


def borrower(name='Ravi Menon'):
    client = TestClient(app)
    client.headers['x-nb-csrf'] = '1'
    email = f'borrower{next(_counter)}@example.test'
    assert client.post('/api/auth/register', json={'email': email, 'password': PASSWORD, 'name': name}).status_code == 201
    return client


def lender():
    return lender_session(TestClient(app))


def upload(client, slot, data):
    link = client.post('/api/borrower/upload-links', json={'slot': slot})
    assert link.status_code == 201, link.text
    url = link.json()['upload_url']
    response = client.put(url, content=data, headers={'content-type': 'application/octet-stream'})
    return url, response


def application(client):
    return client.get('/api/borrower/application').json()


def submit(client, expect=202):
    response = client.post('/api/borrower/submit')
    assert response.status_code == expect, response.text
    return application(client)


def slot_doc(app_json, slot):
    return next(s for s in app_json['slots'] if s['slot'] == slot)['document']


def app_id_for(lender_client, ref):
    rows = lender_client.get('/api/staff/applications', params={'q': ref}).json()['applications']
    return next(r['id'] for r in rows if r['ref'] == ref)


# ---------------------------------------------------------------- auth & CSRF
def test_register_login_and_csrf(server):
    client = TestClient(app)
    assert client.post('/api/auth/register', json={'email': 'x@example.test', 'password': PASSWORD, 'name': 'X'}).status_code == 403
    client.headers['x-nb-csrf'] = '1'
    assert client.post('/api/auth/register', json={'email': 'short@example.test', 'password': 'short', 'name': 'Ab'}).status_code == 422
    assert client.post('/api/auth/register', json={'email': 'csrf@example.test', 'password': PASSWORD, 'name': 'Asha'}).status_code == 201
    assert client.post('/api/auth/register', json={'email': 'csrf@example.test', 'password': PASSWORD, 'name': 'Asha'}).status_code == 409
    me = client.get('/api/auth/me').json()
    assert me['role'] == 'borrower'
    cookie = client.cookies.get('nb_session')
    assert cookie
    client.post('/api/auth/logout')
    assert client.get('/api/auth/me').status_code == 401
    bad = TestClient(app)
    bad.headers['x-nb-csrf'] = '1'
    for _ in range(5):
        assert bad.post('/api/auth/login', json={'email': 'csrf@example.test', 'password': 'wrong-password'}).status_code == 401
    assert bad.post('/api/auth/login', json={'email': 'csrf@example.test', 'password': PASSWORD}).status_code == 429


def test_unauthenticated_and_role_separation(server):
    anon = TestClient(app)
    assert anon.get('/api/borrower/application').status_code == 401
    assert anon.get('/api/staff/applications').status_code == 401
    b = borrower()
    assert b.get('/api/staff/applications').status_code == 404
    assert b.get('/api/staff/applications/1').status_code == 404
    assert b.post('/v1/extract', files={'files': ('a.pdf', ID(), 'application/pdf')}).status_code == 401
    l = lender()
    assert l.get('/api/borrower/application').status_code == 404


# ---------------------------------------------------------------- borrower flow
def test_full_upload_flow_verifies(server):
    b = borrower()
    draft = application(b)
    assert draft['status'] == 'draft' and draft['ref'].startswith('NB-')
    for slot, data in (('id', ID()), ('slip', SLIP('RAVI MENON.')), ('bank', BANK())):
        _, response = upload(b, slot, data)
        assert response.status_code == 202
    # Uploading only stores the files: nothing is verified until the borrower submits.
    state = application(b)
    assert state['status'] == 'draft' and state['check'] is None and state['can_submit']
    assert all(slot_doc(state, s)['status'] == 'uploaded' for s in ('id', 'slip', 'bank'))
    state = submit(b)
    assert state['status'] == 'in_review' and state['status_label'] == 'Under review'
    assert state['check']['status'] == 'Verified'
    assert all(slot_doc(state, s)['status'] == 'accepted' for s in ('id', 'slip', 'bank'))
    text = json.dumps(state)
    # Borrowers never get confidence scores, masked text or check internals.
    assert 'confidence' not in text and 'masked_text' not in text and 'field_evidence' not in text and 'PERSON_' not in text


def test_upload_link_is_single_use_and_bound_to_owner(server):
    a, b = borrower(), borrower()
    link = a.post('/api/borrower/upload-links', json={'slot': 'id'}).json()['upload_url']
    assert b.put(link, content=ID()).status_code == 404          # someone else's link
    assert a.put(link, content=ID()).status_code == 202
    assert a.put(link, content=ID()).status_code == 404          # already used
    assert a.post('/api/borrower/upload-links', json={'slot': 'passport'}).status_code == 422
    assert a.put('/api/uploads/not-a-real-token', content=ID()).status_code == 404


def test_validation_failures_prompt_reupload(server):
    b = borrower()
    _, bad_bytes = upload(b, 'id', b'<html>not a document</html>')
    assert bad_bytes.status_code == 422
    upload(b, 'id', SLIP())                                        # wrong document in the ID slot
    upload(b, 'slip', pdf('SALARY SLIP\nEmployee name: Ravi Menon\nSYNTHETIC SAMPLE PAGE WITHOUT AMOUNT'))
    state = submit(b)
    assert state['status'] == 'action_needed' and state['status_label'] == 'Action needed'
    assert 'not a id card' in slot_doc(state, 'id')['reason'].lower() or 'id card here' in slot_doc(state, 'id')['reason'].lower()
    assert 'gross monthly salary' in slot_doc(state, 'slip')['reason']
    assert next(s for s in state['slots'] if s['slot'] == 'id')['can_upload']
    assert not state['can_submit']                                 # flagged documents must be replaced first
    upload(b, 'id', ID())                                          # re-upload fixes it
    upload(b, 'slip', SLIP())
    assert application(b)['status'] == 'action_needed'             # still waiting for the resubmit
    state = submit(b)
    assert state['status'] == 'in_review' and slot_doc(state, 'id')['version'] == 2


def test_mismatch_goes_to_review_not_rejection(server):
    b = borrower()
    upload(b, 'id', ID('Ravi Menon'))
    upload(b, 'slip', SLIP('Anil Kumar'))
    state = submit(b)
    assert state['check']['status'] == 'Mismatch' and state['status'] == 'in_review'


def test_incomplete_and_not_applicable(server):
    b = borrower()
    upload(b, 'id', ID())
    assert 'Incomplete' in application(b)['submit_hint']
    state = submit(b)
    assert state['status'] == 'action_needed' and state['check']['status'] == 'Incomplete'
    slip = next(s for s in state['slots'] if s['slot'] == 'slip')
    assert slip['missing'] and slip['can_upload']                  # the missing salary slip can be added
    assert not next(s for s in state['slots'] if s['slot'] == 'id')['can_upload']   # accepted ID is locked
    c = borrower()
    upload(c, 'bank', BANK())
    assert submit(c)['check']['status'] == 'Not applicable'


# ---------------------------------------------------------------- lender flow
def test_lender_queue_detail_reupload_and_decision(server, caplog):
    b = borrower('Meera Iyer')
    upload(b, 'id', ID('Meera Iyer'))
    upload(b, 'slip', SLIP('Meera Iyer'))
    ref = submit(b)['ref']
    l = lender()
    queue = l.get('/api/staff/applications', params={'status': 'in_review'}).json()
    row = next(r for r in queue['applications'] if r['ref'] == ref)
    assert row['borrower'] == 'Meera Iyer' and row['check_status'] == 'Verified'
    detail = l.get(f'/api/staff/applications/{row["id"]}').json()
    assert detail['result']['check']['status'] == 'Verified'
    assert all(doc['page_image'] for doc in detail['result']['documents'])
    assert detail['result']['documents'][0]['fields']['full_name'] == 'Meera Iyer'

    # Lender asks for a clearer salary slip; the borrower sees the reason and re-uploads.
    assert l.post(f'/api/staff/applications/{row["id"]}/reupload-requests',
                  json={'slot': 'slip', 'reason_code': 'unclear', 'message': 'Top edge is cut off.'}).status_code == 200
    state = application(b)
    slip = slot_doc(state, 'slip')
    assert state['status'] == 'action_needed' and slip['requested_by_reviewer'] and 'Top edge is cut off.' in slip['reason']
    assert l.post(f'/api/staff/applications/{row["id"]}/decision', json={'decision': 'approve'}).status_code == 409
    upload(b, 'slip', SLIP('Meera Iyer'))
    assert application(b)['status'] == 'action_needed'
    assert submit(b)['status'] == 'in_review'
    versions = [d for d in l.get(f'/api/staff/applications/{row["id"]}').json()['documents'] if d['slot'] == 'slip']
    assert [d['version'] for d in versions] == [2, 1] and versions[1]['current'] is False   # history kept

    assert l.post(f'/api/staff/applications/{row["id"]}/notes', json={'text': 'Checked employer letterhead.'}).status_code == 201
    assert l.post(f'/api/staff/applications/{row["id"]}/decision', json={'decision': 'reject'}).status_code == 422
    assert l.post(f'/api/staff/applications/{row["id"]}/decision', json={'decision': 'approve', 'reason': ''}).json()['status'] == 'approved'
    final = application(b)
    assert final['status'] == 'approved' and not any(s['can_upload'] for s in final['slots'])
    assert b.post('/api/borrower/upload-links', json={'slot': 'id'}).status_code == 409
    detail = l.get(f'/api/staff/applications/{row["id"]}').json()
    actions = [a['action'] for a in detail['audit']]
    assert {'application.viewed', 'reupload.requested', 'note.added', 'application.approved'} <= set(actions)
    assert detail['notes'][0]['text'] == 'Checked employer letterhead.'


def test_customer_isolation(server):
    a = borrower('Ravi Menon')
    upload(a, 'id', ID())
    b = borrower('Anil Kumar')
    view = application(b)
    assert view['ref'] != application(a)['ref']
    assert all(s['document'] is None for s in view['slots'])          # B sees nothing of A
    assert 'Ravi' not in json.dumps(view)


def test_files_and_results_are_encrypted_at_rest(server):
    b = borrower('Kiran Rao')
    upload(b, 'id', ID('Kiran Rao'))
    st = server.app.state.store
    data_dir = Path(st.dir)
    for blob in (data_dir / 'files').glob('*.bin'):
        raw = blob.read_bytes()
        assert not raw.startswith(b'%PDF') and b'Kiran' not in raw
    db_bytes = (data_dir / 'northbridge.db').read_bytes() + b''.join(p.read_bytes() for p in data_dir.glob('northbridge.db-wal'))
    assert b'ABCDE1234F' not in db_bytes and b'Harbour' not in db_bytes
    audit = json.dumps([dict(r) for r in st.q('SELECT action, detail FROM audit')])
    assert 'Kiran' not in audit and 'ABCDE1234F' not in audit



def test_edit_before_submit_and_lock_after(server):
    b = borrower()
    upload(b, 'id', ID())
    upload(b, 'slip', SLIP())
    upload(b, 'slip', SLIP('Ravi Menon'))                          # replace before submitting
    state = application(b)
    assert slot_doc(state, 'slip')['version'] == 2
    assert b.delete('/api/borrower/documents/bank').status_code == 404
    upload(b, 'bank', BANK())
    assert b.delete('/api/borrower/documents/bank').status_code == 200   # remove before submitting
    assert slot_doc(application(b), 'bank') is None
    submit(b)
    state = application(b)
    assert not any(s['can_upload'] or s['can_remove'] for s in state['slots'])
    assert b.delete('/api/borrower/documents/slip').status_code == 409
    assert b.post('/api/borrower/upload-links', json={'slot': 'slip'}).status_code == 409
    assert b.post('/api/borrower/submit').status_code == 409       # already submitted
    assert submit(borrower(), expect=409)                          # nothing uploaded yet


def test_previews_are_private(server):
    a = borrower()
    upload(a, 'id', ID())
    thumb = a.get('/api/borrower/documents/id/preview')
    assert thumb.status_code == 200 and thumb.headers['content-type'] == 'image/jpeg' and thumb.content[:2] == b'\xff\xd8'
    assert a.get('/api/borrower/documents/id/preview?size=large').status_code == 200
    assert a.get('/api/borrower/documents/slip/preview').status_code == 404
    other = borrower()
    assert other.get('/api/borrower/documents/id/preview').status_code == 404   # B has no ID; A's is unreachable
    assert TestClient(app).get('/api/borrower/documents/id/preview').status_code == 401
    l = lender()
    doc_id = st_doc_id(server, a)
    assert l.get(f'/api/staff/documents/{doc_id}/preview').status_code == 200
    assert a.get(f'/api/staff/documents/{doc_id}/preview').status_code == 404


def st_doc_id(server, client):
    ref = application(client)['ref']
    st = server.app.state.store
    return st.one("SELECT d.id FROM documents d JOIN applications a ON a.id=d.application_id WHERE a.ref=? AND d.current=1", (ref,))['id']
