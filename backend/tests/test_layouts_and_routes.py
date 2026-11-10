"""Real-world label layouts, classification, NER masking, pipeline stages, and the public/staff split."""
import pymupdf
import pytest
from fastapi.testclient import TestClient
from backend.app import app
from backend.llm import get_provider
from backend.pipeline import build_analyzer, classify, extract_fields, process

# Synthetic layouts like the ones borrowers actually upload: no colons, values on the next line.
ID_CARD = """National SAMPLE Identity ID
Specimen card for testing only issued by government test
SPECIMEN
Name Ravi Kumar Menon
Date of Birth 14/08/1994
Mobile 9876 543210
Address 12 Lake View Road Kochi 682001
Holder signature: specimen
National ID Number
4321 8765 2109"""
PAYSLIP = """Harbour Tools Pvt Ltd
Finance Department, Kochi
PAYSLIP FOR THE MONTH OF October 2026
Employee Name
Ravi Kumar Menon
Employee Code
HT2291
Designation
Engineer
Paid Days
31
Earnings
Amount (Rs.)
Basic Pay
45,000
Gross Salary
82,000
Total Deductions
9,800
Net Pay: Rs. 72,200
Synthetic payslip for testing only"""
STATEMENT = """Statement of Account Demo Savings Bank
Account Number 123456789012
Account Holder Ravi Kumar Menon
Joint holder Anita Menon
IFSC DEMO0001234
Branch Kochi Main
Period 01/10/2026 to 31/10/2026
Date Description Debit Credit Balance
01/10/2026 OPENING BALANCE 1,20,000.00
05/10/2026 NEFT SALARY HARBOUR TOOLS 72,200.00 1,92,200.00
Synthetic statement for testing only"""


def pdf(text):
    doc = pymupdf.open()
    doc.new_page().insert_text((40, 50), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture(scope='module')
def analyzer():
    return build_analyzer()


def test_classifier_handles_real_layouts():
    assert classify(ID_CARD) == 'ID card'
    assert classify(PAYSLIP) == 'Salary slip'
    assert classify(STATEMENT) == 'Bank statement, page 1'
    assert classify('Hello world, this is a letter about nothing in particular.') == 'Unknown'


def test_labels_without_colon_and_on_next_line():
    card = extract_fields(ID_CARD, 'ID card')
    assert card.full_name == 'Ravi Kumar Menon' and card.date_of_birth == '14/08/1994' and card.id_number == '4321 8765 2109'
    slip = extract_fields(PAYSLIP, 'Salary slip')
    assert slip.full_name == 'Ravi Kumar Menon' and slip.monthly_salary == 82000
    bank = extract_fields(STATEMENT, 'Bank statement, page 1')
    assert bank.full_name == 'Ravi Kumar Menon' and bank.monthly_salary == 72200
    # A label line followed by another label is never taken as a value.
    assert extract_fields('SALARY SLIP\nEmployee Name\nEmployee Code\nHT1', 'Salary slip').full_name is None


def test_real_layouts_verify_and_mask(analyzer):
    result = process([pdf(ID_CARD), pdf(PAYSLIP), pdf(STATEMENT)], analyzer)
    assert [d.document_type for d in result.documents] == ['ID card', 'Salary slip', 'Bank statement, page 1']
    assert result.check.status == 'Verified'
    assert result.documents[0].fields.date_of_birth == '1994-08-14'
    for doc in result.documents:
        for secret in ('Ravi', 'Menon', '9876 543210', 'Lake View', '4321 8765 2109', '123456789012', 'Anita'):
            assert secret not in doc.masked_text, secret


def test_ner_masks_unlabeled_names(analyzer):
    if not analyzer.nb_ner:
        pytest.skip('spaCy model en_core_web_sm is not installed')
    text = 'SALARY SLIP\nEmployee Name: Ravi Menon\nApproved by Sunita Sharma of HR\nGross Salary: 82,000\nSYNTHETIC'
    masked = process([pdf(text)], analyzer).documents[0].masked_text
    assert 'Sunita' not in masked and 'Sharma' not in masked


def test_stage_summary_is_sanitized(analyzer):
    result = process([pdf(ID_CARD), pdf(PAYSLIP)], analyzer)
    assert [s['key'] for s in result.stages] == ['prescan', 'ocr', 'pii', 'llm', 'verify']
    assert result.stages[3]['status'] == 'skipped' and result.stages[4]['detail'].startswith('Verified')
    text = str(result.stages)
    assert 'Ravi' not in text and '4321' not in text


def test_policy_and_roll_numbers_are_masked(analyzer):
    text = ('SALARY SLIP\nEmployee name: Ravi Menon\nEmployer: Harbour Tools Pvt Ltd\n'
            'Gross monthly salary: INR 82,000.00\nPolicy Number: POL-AB123456\nRoll No: ROLL-2026-18\nSYNTHETIC')
    masked = process([pdf(text)], analyzer).documents[0].masked_text
    assert 'POL-AB123456' not in masked and 'ROLL-2026-18' not in masked


def test_public_site_has_no_staff_links():
    with TestClient(app) as client:
        home = client.get('/')
        assert home.status_code == 200 and 'Borrower portal' in home.text
        for page in (home.text, client.get('/apply.js').text, client.get('/common.js').text):
            assert '/staff' not in page and 'lender' not in page.lower()
        assert client.get('/lender').status_code == 404
        assert client.get('/lender.html').status_code == 404       # staff files are not in the public folder
        assert client.get('/apply', follow_redirects=False).headers['location'] == '/'
        staff = client.get('/staff')
        assert staff.status_code == 200 and 'Staff sign-in' in staff.text
        assert client.get('/staff/assets/lender.js').status_code == 200


def test_openrouter_provider_needs_key(monkeypatch):
    monkeypatch.setenv('NB_LLM_PROVIDER', 'openrouter')
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    monkeypatch.delenv('NB_LLM_API_KEY', raising=False)
    with pytest.raises(ValueError):
        get_provider()
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-key-not-real')
    llm = get_provider()
    assert llm.model == 'deepseek/deepseek-v4.1-flash' and llm.label == 'DeepSeek V4.1 Flash' and llm.external
