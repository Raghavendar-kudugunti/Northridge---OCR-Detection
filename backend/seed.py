"""Development seed: one lender account plus synthetic borrowers in different states.

    python -m backend.seed            # uses NB_DATA_DIR (default ./data)

Development credentials (override with NB_SEED_LENDER_EMAIL / NB_SEED_LENDER_PASSWORD /
NB_SEED_BORROWER_PASSWORD). They are test values for a local build only.
"""
import os
import hashlib
import time
from pathlib import Path
import pymupdf
from .pipeline import build_analyzer
from .portal import new_ref, run_check
from .store import Store

LENDER_EMAIL = os.environ.get('NB_SEED_LENDER_EMAIL', 'lender@northbridge.test')
LENDER_PASSWORD = os.environ.get('NB_SEED_LENDER_PASSWORD', 'lender-dev-pass-2026')
BORROWER_PASSWORD = os.environ.get('NB_SEED_BORROWER_PASSWORD', 'borrower-dev-pass-2026')


def pdf(text):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 65), text, fontsize=14)
    data = doc.tobytes()
    doc.close()
    return data


def id_card(name):
    return pdf(f'IDENTITY CARD\nFull name: {name}\nDate of birth: 14/08/1994\nID number: ABCDE1234F\nSYNTHETIC SAMPLE - NOT A VALID DOCUMENT')


def salary_slip(name):
    return pdf(f'SALARY SLIP\nEmployee name: {name}\nEmployer: Harbour Tools Pvt Ltd\nGross monthly salary: INR 82,000.00\nSYNTHETIC SAMPLE - NOT A VALID DOCUMENT')


def bank_statement(name):
    return pdf(f'BANK STATEMENT\nAccount holder: {name}\nAccount number: 12345678901234\n05/10/2026 NEFT SALARY HARBOUR TOOLS 78,500.00\nSYNTHETIC SAMPLE - NOT A VALID DOCUMENT')


BORROWERS = [
    ('Ravi Menon', 'ravi.menon@example.test', [('id', id_card('Ravi Menon')), ('slip', salary_slip('RAVI MENON')), ('bank', bank_statement('Ravi Menon'))]),
    ('Anil Kumar', 'anil.kumar@example.test', [('id', id_card('Anil Kumar')), ('slip', salary_slip('Anil K Sharma'))]),
    ('Meera Iyer', 'meera.iyer@example.test', [('id', salary_slip('Meera Iyer')), ('slip', salary_slip('Meera Iyer'))]),
    ('Kiran Rao', 'kiran.rao@example.test', [('id', id_card('Kiran Rao'))]),
]


def main():
    data_dir = os.environ.get('NB_DATA_DIR', str(Path(__file__).resolve().parent.parent / 'data'))
    st = Store(data_dir)
    if st.one('SELECT 1 FROM users WHERE email=?', (LENDER_EMAIL,)) is None:
        st.create_user(LENDER_EMAIL, 'Priya Nair (Credit officer)', LENDER_PASSWORD, 'lender')
        print(f'Created lender account {LENDER_EMAIL} (password: see backend/seed.py)')
    analyzer = build_analyzer()
    for name, email, files in BORROWERS:
        if st.one('SELECT 1 FROM users WHERE email=?', (email,)):
            continue
        user_id = st.create_user(email, name, BORROWER_PASSWORD, 'borrower')
        now = time.time()
        app_id = st.run('INSERT INTO applications(ref,borrower_id,status,created,updated,submitted) VALUES(?,?,?,?,?,?)',
                        (new_ref(st), user_id, 'checking', now, now, now))
        for slot, data in files:
            st.run('INSERT INTO documents(application_id,slot,version,current,status,storage_key,sha256,size,kind,created) '
                   'VALUES(?,?,?,?,?,?,?,?,?,?)', (app_id, slot, 1, 1, 'checking', st.put_file(data),
                                                 hashlib.sha256(data).hexdigest(), len(data), 'pdf', now))
        st.audit('application.created', user_id, app_id)
        run_check(st, app_id, analyzer, None)
        row = st.one('SELECT ref, status, check_status FROM applications WHERE id=?', (app_id,))
        print(f'Borrower {email}: {row["ref"]} -> {row["status"]} / {row["check_status"]}')
    print(f'Data directory: {Path(data_dir).resolve()}')


if __name__ == '__main__':
    main()
