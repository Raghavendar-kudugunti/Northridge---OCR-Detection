"""Local-only pipeline. No raw documents, prompts or extracted values are logged."""
import base64
import io
import importlib.util
import os
import re
import time
import unicodedata
from datetime import date
from collections import Counter
from contextlib import contextmanager
from threading import Lock
from uuid import uuid4
import cv2
import pymupdf as fitz
import numpy as np
import spacy
from PIL import Image, ImageOps
from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern, RecognizerRegistry
from presidio_analyzer.nlp_engine import SpacyNlpEngine
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig
from .models import *
from .ocr import ENGINE_LABELS, RUNNERS as OCR_RUNNERS, resolve_engine, tesseract_available
from .metrics import record as record_metrics

MAX_FILE = 10 * 1024 * 1024
MAX_PIXELS = 20_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
PREVIEW_MAX_SIDE = 1400
_DOCLING_LOCK = Lock()
_DOCLING_CONVERTER = None
_DOCLING_OCR_CONVERTER = None
# Review gates (plan section 2.2). Field confidence below these values forces human review.
SALARY_MIN = float(os.environ.get('NB_SALARY_CONFIDENCE_MIN', '0.90'))
NAME_MIN = float(os.environ.get('NB_NAME_CONFIDENCE_MIN', '0.85'))
FIELD_MIN = {'full_name': NAME_MIN, 'monthly_salary': SALARY_MIN}
DEFAULT_FIELD_MIN = 0.60
LLM_FACTOR = 0.9       # LLM-sourced values are trusted slightly less than labeled ones.
UNGROUNDED = 0.5       # A value that cannot be located on the page.
LABELS = {'full_name': 'Full name', 'date_of_birth': 'Date of birth', 'id_number': 'ID number',
          'pan_number': 'PAN number',
          'employer_name': 'Employer', 'monthly_salary': 'Gross monthly salary'}

class DocumentError(ValueError):
    pass

class LocalNlp(SpacyNlpEngine):
    """No model download or network access at request time.

    Uses a locally installed spaCy NER model (``NB_SPACY_MODEL``, default ``en_core_web_sm``)
    so Presidio can mask person names anywhere in the text, not only next to labels. If the
    model is not installed it falls back to blank tokenization (labeled and pattern masking only).
    """
    ner = False

    def load(self):
        name = os.environ.get('NB_SPACY_MODEL', 'en_core_web_sm')
        try:
            self.nlp = {'en': spacy.load(name, disable=['parser', 'lemmatizer'])}
            self.ner = True
        except (OSError, ImportError):
            self.nlp = {'en': spacy.blank('en')}
            self.ner = False


def build_analyzer():
    engine = LocalNlp(models=[{'lang_code': 'en', 'model_name': 'en'}])
    engine.load()
    registry = RecognizerRegistry(supported_languages=['en'], recognizers=[PatternRecognizer(supported_entity='EMAIL_ADDRESS', patterns=[Pattern('email', r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', .9)])])
    analyzer = AnalyzerEngine(nlp_engine=engine, registry=registry, supported_languages=['en'])
    recognizers = {
        'AADHAAR': r'(?<!\d)\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)',
        'PAN': r'\b[A-Z]{5}\d{4}[A-Z]\b',
        'PHONE_NUMBER': r'(?<!\d)(?:\+91[ -]?)?[6-9]\d{9}(?!\d)',
        'ACCOUNT_NUMBER': r'(?<!\d)\d{9,18}(?!\d)',
        'DATE_TIME': r'\b\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b',
        'EMAIL_ADDRESS': r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}',
        'POLICY_NUMBER': r'(?i)\bpolicy\s*(?:number|no\.?)?\s*[:#-]?\s*[A-Z0-9][A-Z0-9/-]{5,25}\b',
        'ROLL_NUMBER': r'(?i)\broll\s*(?:number|no\.?|id|#)?\s*[:#-]?\s*[A-Z0-9][A-Z0-9/-]{2,20}\b',
    }
    for entity, regex in recognizers.items():
        analyzer.registry.add_recognizer(PatternRecognizer(supported_entity=entity, patterns=[Pattern(entity, regex, .9)]))
    if engine.ner:
        from presidio_analyzer.predefined_recognizers import SpacyRecognizer
        analyzer.registry.add_recognizer(SpacyRecognizer(supported_language='en', supported_entities=['PERSON']))
    analyzer.nb_ner = engine.ner
    return analyzer


def normalize_name(value):
    """Spec N(x): uppercase, strip punctuation, collapse whitespace (after NFKC)."""
    text = unicodedata.normalize('NFKC', value).upper()
    text = ''.join(ch for ch in text if not unicodedata.category(ch).startswith(('P', 'S')))
    return ' '.join(text.replace('_', '').split())


_MONTHS = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}


def iso_date(value):
    """ISO YYYY-MM-DD when the date parses (day-first, Indian convention); otherwise unchanged."""
    if not value:
        return value
    raw = value.strip()
    candidates = []
    m = re.fullmatch(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', raw)
    if m: candidates.append((int(m[1]), int(m[2]), int(m[3])))
    m = re.fullmatch(r'(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{4})', raw)
    if m: candidates.append((int(m[3]), int(m[2]), int(m[1])))
    m = re.fullmatch(r'(\d{1,2})[-/ ]?([A-Za-z]{3,9})\.?[-/ ,]*(\d{4})', raw)
    if m and m[2][:3].lower() in _MONTHS: candidates.append((int(m[3]), _MONTHS[m[2][:3].lower()], int(m[1])))
    for year, month, day in candidates:
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            continue
    return value

class Vault:
    def __init__(self):
        self.lookup = {}
        self.originals = {}
        self.counts = Counter()

    def token(self, kind, value):
        key = (kind, normalize_name(value))
        if key not in self.lookup:
            self.counts[kind] += 1
            token = f'<{kind}_{self.counts[kind]}>'
            self.lookup[key] = token
            self.originals[token] = value
        return self.lookup[key]

    def clear(self):
        self.lookup.clear()
        self.originals.clear()
        self.counts.clear()

@contextmanager
def request_vault():
    vault = Vault()
    try:
        yield vault
    finally:
        vault.clear()


def validate(data):
    if not data or len(data) > MAX_FILE:
        raise DocumentError('Each file must contain data and be no larger than 10 MB.')
    if data.startswith(b'%PDF-'): return 'pdf'
    if data.startswith(b'\xff\xd8\xff'): return 'jpeg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return 'png'
    raise DocumentError('Unsupported document. Use a valid PDF, PNG or JPEG.')


def preview_data_url(image):
    preview = image.copy()
    preview.thumbnail((PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE))
    buffer = io.BytesIO()
    preview.save(buffer, format='JPEG', quality=80)
    return 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


def docling_pdf_text(data, full_page_ocr=False):
    """Extract PDF text with Docling's local layout-aware document model."""
    global _DOCLING_CONVERTER, _DOCLING_OCR_CONVERTER
    from docling.datamodel.base_models import DocumentStream, InputFormat
    from docling.datamodel.pipeline_options import OcrMode, PdfPipelineOptions, RapidOcrOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    artifacts_path = os.environ.get('DOCLING_ARTIFACTS_PATH')
    if not artifacts_path:
        local_cache = os.path.join(os.path.expanduser('~'), '.cache', 'docling', 'models')
        artifacts_path = local_cache if os.path.isdir(local_cache) else None

    with _DOCLING_LOCK:
        if full_page_ocr:
            if _DOCLING_OCR_CONVERTER is None:
                options = PdfPipelineOptions(artifacts_path=artifacts_path)
                options.do_ocr = True
                options.ocr_options = RapidOcrOptions(mode=OcrMode.FULL_PAGE)
                _DOCLING_OCR_CONVERTER = DocumentConverter(
                    format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
            converter = _DOCLING_OCR_CONVERTER
        else:
            if _DOCLING_CONVERTER is None:
                options = PdfPipelineOptions(artifacts_path=artifacts_path)
                # The application OCR fallback handles pages where layout text
                # misses required values; keep Docling focused on layout parsing.
                options.do_ocr = False
                _DOCLING_CONVERTER = DocumentConverter(
                    format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
            converter = _DOCLING_CONVERTER
        source = DocumentStream(name='upload.pdf', stream=io.BytesIO(data))
        result = converter.convert(source, max_num_pages=1, max_file_size=MAX_FILE)
    try:
        markdown = result.document.export_to_markdown()
    finally:
        result.input.release()

    # Keep columns distinct when converting Docling's Markdown tables to parser text.
    lines = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith('|') and stripped.endswith('|'):
            cells = [re.sub(r'\*\*(.*?)\*\*|`([^`]*)`', lambda m: m.group(1) or m.group(2), cell.strip())
                     for cell in stripped.strip('|').split('|')]
            if cells and all(re.fullmatch(r':?-{2,}:?', cell) for cell in cells):
                continue
            lines.append('\t'.join(cells))
        else:
            clean = re.sub(r'^\s*#{1,6}\s*', '', line)
            clean = re.sub(r'\*\*(.*?)\*\*|`([^`]*)`', lambda m: m.group(1) or m.group(2), clean)
            lines.append(clean)
    return '\n'.join(lines)


def read_document(data, engine='auto', preview_images=False):
    kind = validate(data)
    embedded = ''
    embedded_engine = 'Docling layout text'
    warnings = []
    if kind == 'pdf':
        with fitz.open(stream=data, filetype='pdf') as pdf:
            if pdf.needs_pass or len(pdf) == 0:
                raise DocumentError('Could not read this document.')
            # First page only, including bank statements. Keep rendering bounded.
            page = pdf[0]
            if page.rect.width <= 0 or page.rect.height <= 0:
                raise DocumentError('Could not read this document.')
            scale = min(2, 2400 / max(page.rect.width, page.rect.height))
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            image = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
            words = page.get_text('words')
            # Use the PDF's existing text layer first; it is fast and preserves line
            # breaks on ordinary digital PDFs. Spend time on Docling layout inference
            # only when classification or required fields are missing.
            fast_text = page.get_text('text', sort=True).strip()
            fast_type = classify(fast_text) if len(re.sub(r'\W', '', fast_text)) >= 40 else 'Unknown'
            fast_fields = extract_fields(fast_text, fast_type)
            fast_missing = (
                fast_type == 'Unknown' or
                (fast_type == 'ID card' and not fast_fields.full_name and not fast_fields.pan_number) or
                (fast_type == 'Salary slip' and (not fast_fields.full_name or fast_fields.monthly_salary is None)) or
                (fast_type == 'Bank statement, page 1' and not fast_fields.full_name)
            )
            if not fast_missing:
                embedded = fast_text
                embedded_engine = 'PyMuPDF text'
            else:
                try:
                    embedded = docling_pdf_text(data)
                except Exception:
                    embedded = ''
                    warnings.append('Docling layout parsing failed; OCR fallback was used.')
            boxes = [{'text': w[4], 'x': w[0]/page.rect.width, 'y': w[1]/page.rect.height,
                      'w': (w[2]-w[0])/page.rect.width, 'h': (w[3]-w[1])/page.rect.height, 'conf': 100.0}
                     for w in words]
        page_image = preview_data_url(image)
    else:
        with Image.open(io.BytesIO(data)) as opened:
            if opened.width * opened.height > MAX_PIXELS:
                raise DocumentError('Image exceeds the 20 megapixel processing limit.')
            image = ImageOps.exif_transpose(opened).convert('RGB')
        boxes = []
        # The browser usually holds the original image; stored applications need a render.
        page_image = preview_data_url(image) if preview_images else None
    prescan_start = time.perf_counter()
    gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
    quality = {'sharpness': round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 2),
               'contrast': round(float(gray.std()), 2), 'brightness': round(float(gray.mean()), 2),
               'dpi': None, 'skew_degrees': None}
    quality['prescan_ms'] = round((time.perf_counter() - prescan_start) * 1000)
    if quality['sharpness'] < 70: warnings.append('Low sharpness: review scan quality.')
    if quality['contrast'] < 25: warnings.append('Low contrast: review scan quality.')
    if kind == 'pdf': warnings.append('Only the first page is processed.')
    if len(re.sub(r'\W', '', embedded)) >= 40:
        embedded_type = classify(embedded)
        embedded_fields = extract_fields(embedded, embedded_type)
        required_missing = (
            (embedded_type == 'ID card' and not embedded_fields.full_name and not embedded_fields.pan_number) or
            (embedded_type == 'Salary slip' and (not embedded_fields.full_name or embedded_fields.monthly_salary is None)) or
            (embedded_type == 'Bank statement, page 1' and not embedded_fields.full_name)
        )
        if not required_missing:
            return embedded, boxes, quality, warnings, None, embedded_engine, page_image
        warnings.append('Docling text lacked required fields; local OCR fallback was used.')
    try:
        engine_name, fallback_note = resolve_engine(engine)
    except (ValueError, RuntimeError) as exc:
        raise DocumentError(str(exc) if isinstance(exc, ValueError) else 'Could not read this document. No OCR engine is available.')
    if fallback_note: warnings.append(fallback_note)
    try:
        text, boxes, confidence = OCR_RUNNERS[engine_name](image)
    except Exception:
        if engine_name == 'rapidocr' and tesseract_available():
            # RapidOCR failed to start or to read this page: use Tesseract rather than rejecting the file.
            warnings.append('RapidOCR was unavailable for this page; Tesseract was used.')
            engine_name = 'tesseract'
            try:
                text, boxes, confidence = OCR_RUNNERS[engine_name](image)
            except Exception:
                raise DocumentError('Could not read this document. Upload a clearer scan.')
        else:
            # Engine failures fail closed and never echo engine output.
            raise DocumentError('Could not read this document. Upload a clearer scan.')
    if len(re.sub(r'\W', '', text)) < 40 or confidence < 60:
        # No hosted vision fallback: an unreadable document must fail closed.
        raise DocumentError('Could not read this document. Upload a clearer scan.')
    return text, boxes, quality, warnings, round(confidence, 1), ENGINE_LABELS[engine_name], page_image


# Weighted signals per document type. Real layouts rarely say "Identity card" or use
# "Label: value", so several weaker signals together also identify a document.
CLASS_SIGNALS = {
    'Salary slip': [
        (r'salary\s*slip|pay\s*slip|earnings\s*statement', 3), (r'gross\s*(?:monthly\s*)?(?:salary|pay|earnings)', 2),
        (r'net\s*(?:pay|salary)', 2), (r'basic\s*(?:pay|salary)', 1), (r'deductions?', 1),
        (r'employee\s*(?:name|code|id)', 1), (r'paid\s*days|designation', 1)],
    'Bank statement, page 1': [
        (r'bank\s*statement|account\s*statement|statement\s+of\s+(?:account|[a-z ]{0,30}bank)', 3),
        (r'opening\s*balance|closing\s*balance', 2), (r'account\s*holder', 1), (r'ifsc', 1),
        (r'debit', 1), (r'credit', 1), (r'balance', 1)],
    'ID card': [
        (r'aadhaa?r|permanent\s*account\s*number|income\s*tax\s*department|identity\s*card|id\s*card|voter|driving\s*licen[cs]e|passport', 3),
        (r'id\s*(?:number|no)', 2), (r'date\s*of\s*birth|dob|year\s*of\s*birth', 1), (r'government', 1),
        (r'gender|male|female', 1), (r'holder\s*signature', 1)],
}


def classify(text):
    scores = {kind: sum(weight for pattern, weight in signals if re.search(r'(?<![a-z])(?:' + pattern + r')(?![a-z])', text, re.I))
              for kind, signals in CLASS_SIGNALS.items()}
    best = max(scores, key=lambda kind: scores[kind])  # ties keep dict order: slip, bank, ID
    return best if scores[best] >= 3 else 'Unknown'


FIELD_LABELS = {
    'full_name': r'full\s*name|employee\s*name|name\s*of\s*(?:the\s*)?(?:employee|card\s*holder|holder)|name|account\s*holder(?:\s*name)?',
    'date_of_birth': r'date\s*of\s*birth|dob|birth\s*date',
    'id_number': r'id\s*(?:number|no\.?)|aadhaa?r\s*(?:number|no\.?)?|pan\s*(?:number|no\.?)?|card\s*(?:number|no\.?)',
    'employee_id': r'employee\s*(?:id|code|number|no\.?)',
    'employer_name': r'employer(?:\s*name)?|company(?:\s*name)?|organi[sz]ation',
    'monthly_salary': r'gross\s*(?:monthly\s*)?salary|monthly\s*(?:gross\s*)?salary|gross\s*pay|gross\s*earnings|total\s*earnings',
}
# Other labels seen on these documents: a line that is one of these is never taken as a value.
OTHER_LABELS = (r'designation|department|paid\s*days|earnings|deductions|amount(?:\s*\(rs\.?\))?|'
                r'basic\s*pay|net\s*pay|total\s*deductions|mobile|phone|address|gender|sex|issue\s*date|valid\s*(?:until|till)|'
                r'expiry(?:\s*date)?|ifsc|branch|payment\s*date|currency|document\s*type|pay\s*period|period|account\s*(?:number|no\.?|type)|holder\s*signature|signature|'
                r"father'?s?\s*name|mother'?s?\s*name|spouse\s*name|nationality|blood\s*group")
_ANY_LABEL = re.compile(r'^(?:' + '|'.join(list(FIELD_LABELS.values()) + [OTHER_LABELS]) + r')(?![A-Za-z0-9])\s*[:\-]?\s*$', re.I)
_NEXT_LABELED_FIELD = re.compile(
    r'\s+(?=(?:' + '|'.join(list(FIELD_LABELS.values()) + [OTHER_LABELS]) + r')(?![A-Za-z0-9])\s*[:\-])', re.I)


def labeled(text, labels, lead_words=0):
    """Value for a label written as "Label: value", "Label value", or "Label" with the value on the next line.

    ``lead_words`` allows a few words before the label (e.g. "National ID Number").
    """
    lines = [line.strip() for line in text.splitlines()]
    lead = r'(?:[A-Za-z]+\s+){0,%d}' % lead_words if lead_words else ''
    pattern = re.compile(r'^' + lead + r'(?:' + labels + r')(?![A-Za-z0-9])\s*[:\-\u2013]?\s*(.*)$', re.I)
    for i, line in enumerate(lines):
        match = pattern.match(line)
        if not match:
            continue
        value = match.group(1).strip(' :-\u2013')
        # Layout-preserved tables can put a neighboring column heading after the
        # target label. Ignore that heading and take the value from the same column
        # on the next row instead.
        if value and '\t' in value:
            first = value.split('\t', 1)[0].strip()
            if first and not _ANY_LABEL.match(first):
                value = first
            else:
                value = ''
        if value:
            # PDF text extraction can place adjacent columns on the same line, e.g.
            # "Employee Name: Tara Bose Payment Date: 31 Oct 2026". Stop the field
            # value at the next recognized label instead of absorbing that column.
            value = _NEXT_LABELED_FIELD.split(value, maxsplit=1)[0].strip()
        if value and not _ANY_LABEL.match(value):
            if labels == FIELD_LABELS['full_name']:
                value = re.split(r'\b\d{1,2}\s+[A-Za-z]{3,9}\s*[-\u2013]\s*\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}\b', value, maxsplit=1, flags=re.I)[0].strip()
            if value:
                return value
        following = next((l for l in lines[i + 1:i + 4] if l and not _ANY_LABEL.match(l)), None)
        if following:
            return following.split('\t', 1)[0].strip()
    return None


def labeled_column(text, labels):
    """Read a labeled value that appears in a neighboring PDF table column."""
    pattern = re.compile(r'(?<![A-Za-z0-9])(?:' + labels + r')(?![A-Za-z0-9])\s*[:\-\u2013]\s*(.*)$', re.I)
    for line in text.splitlines():
        match = pattern.search(line)
        if not match:
            continue
        value = _NEXT_LABELED_FIELD.split(match.group(1), maxsplit=1)[0].strip()
        if value and not _ANY_LABEL.match(value):
            return value
    return None


_AMOUNT = re.compile(r'(?<![\d/.\-])((?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d{1,2})?)(?![\d/\-])')


def salary_credit(text):
    """First amount after the word 'salary' on a credit line of a bank statement (net pay)."""
    for line in text.splitlines():
        m = re.search(r'\bsal(?:ary)?\b', line, re.I)
        if not m or re.search(r'\b(debit|dr|withdrawal)\b', line, re.I):
            continue
        for amount in _AMOUNT.findall(line[m.end():]):
            value = float(amount.replace(',', ''))
            if value >= 100:  # Skip day numbers and short references.
                return value
    return None


_LEADING_AMOUNT = re.compile(r'(?:INR|Rs\.?|₹)?\s*(\d[\d,]*(?:\.\d{1,2})?)(?:\s*/-)?(?=\s|$)', re.I)


def id_card_name(text):
    """Recover unlabeled names commonly printed as a standalone line on Aadhaar cards."""
    lines = [line.strip(' \t|:;') for line in text.splitlines()]
    candidates = []
    blocked = NOT_NAME_WORDS | {
        'my', 'male', 'female', 'transgender', 'of', 'the', 'sample', 'synthetic', 'specimen',
        'test', 'data', 'record', 'fictional', 'valid', 'government', 'india', 'department',
    }
    for index, line in enumerate(lines):
        if not line or len(line) > 64 or re.search(r'\d', line):
            continue
        if not re.fullmatch(r"[A-Za-z][A-Za-z.'’\-]*(?:\s+[A-Za-z][A-Za-z.'’\-]*){1,4}", line):
            continue
        words = re.findall(r'[A-Za-z]+', line.casefold())
        if any(word in blocked for word in words):
            continue
        context = ' '.join(lines[max(0, index - 1):min(len(lines), index + 4)])
        score = 0
        if re.search(r'\b(?:male|female|transgender)\b', context, re.I):
            score += 2
        if re.search(r'\b(?:dob|date\s*of\s*birth|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4})\b', context, re.I):
            score += 2
        nearby = ' '.join(lines[max(0, index - 3):min(len(lines), index + 5)])
        if re.search(r'\b(?:aadhaa?r|identity|id\s*(?:number|no\.?))\b', nearby, re.I):
            score += 1
        candidates.append((score, index, line))
    if not candidates:
        return None
    # Prefer a candidate with nearby identity details; ties retain document reading order.
    score, _, name = max(candidates, key=lambda candidate: (candidate[0], -candidate[1]))
    return name if score >= 2 else None


def id_card_number(text):
    """Recover common Aadhaar/PAN identifiers printed without a text label."""
    aadhaar = re.search(r'(?<!\d)\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)', text)
    if aadhaar:
        return aadhaar.group(0)
    pan = re.search(r'(?<![A-Z0-9])[A-Z]{5}\d{4}[A-Z](?![A-Z0-9])', text, re.I)
    return pan.group(0).upper() if pan else None


_PAN_VALUE = re.compile(r'(?<![A-Z0-9])[A-Z]{5}\d{4}[A-Z](?![A-Z0-9])', re.I)


def extract_fields(text, doc_type=None):
    salary = labeled(text, FIELD_LABELS['monthly_salary'])
    salary_match = _LEADING_AMOUNT.match(salary or '')
    if salary_match is None:
        # Table rows flattened by the PDF text layer: "Gross Salary 82,000 Total Deductions 9,800".
        salary_match = re.search(r'(?<![A-Za-z])(?:' + FIELD_LABELS['monthly_salary'] + r')\s*[:\-]?\s*(?:INR|Rs\.?|₹)?\s*(\d[\d,]*(?:\.\d{1,2})?)(?![\d,])', text, re.I)
    full_name = labeled(text, FIELD_LABELS['full_name'])
    id_number = labeled(text, FIELD_LABELS['id_number'], lead_words=2)
    pan_match = _PAN_VALUE.search(text)
    pan_number = pan_match.group(0).upper() if pan_match else None
    if not full_name and doc_type == 'ID card' and not pan_number:
        full_name = id_card_name(text)
    if not id_number and doc_type == 'ID card':
        id_number = id_card_number(text)
    if not id_number and pan_number:
        id_number = pan_number
    return NorthbridgeFields(
        full_name=full_name,
        date_of_birth=labeled(text, FIELD_LABELS['date_of_birth']),
        id_number=id_number,
        pan_number=pan_number,
        is_pan_card=bool(doc_type == 'ID card' and (pan_number or re.search(r'permanent\s+account\s+number|income\s+tax\s+department', text, re.I))),
        employee_id=labeled(text, FIELD_LABELS['employee_id']),
        employer_name=labeled(text, FIELD_LABELS['employer_name']) or labeled_column(text, FIELD_LABELS['employer_name']),
        monthly_salary=(float(salary_match.group(1).replace(',', '')) if salary_match
                        else salary_credit(text) if doc_type == 'Bank statement, page 1' else None))


PRIVATE_LABELS = re.compile(
    r'^[ \t]*(?:address|phone(?:\s*(?:number|no\.?))?|mobile(?:\s*(?:number|no\.?))?|(?:holder\s*)?signature|'
    r'policy\s*(?:number|no\.?)|account\s*(?:number|no\.?)|a/c\s*(?:number|no\.?)|joint\s*(?:account\s*)?holder|'
    r"second\s*holder|nominee|father'?s?\s*name|mother'?s?\s*name|spouse\s*name)(?![A-Za-z0-9])[ \t]*[:\-]?[ \t]*", re.I | re.M)


def private_spans(text):
    """Values of address/phone/account/relative labels: never output fields, always masked."""
    spans = []
    for m in PRIVATE_LABELS.finditer(text):
        line_end = text.find('\n', m.end())
        line_end = len(text) if line_end < 0 else line_end
        if text[m.end():line_end].strip():
            spans.append((m.end(), line_end, 'PRIVATE'))
        else:  # value on the next non-empty line
            nxt = line_end + 1
            while nxt < len(text) and text[nxt] == '\n':
                nxt += 1
            nxt_end = text.find('\n', nxt)
            nxt_end = len(text) if nxt_end < 0 else nxt_end
            if nxt < len(text) and text[nxt:nxt_end].strip() and not _ANY_LABEL.match(text[nxt:nxt_end].strip()):
                spans.append((nxt, nxt_end, 'PRIVATE'))
    return spans


# Words that are document furniture, never part of a person's name. NER on all-caps headings
# ("SALARY SLIP Employee") and across line breaks produces such false "names".
NOT_NAME_WORDS = {
    'salary', 'slip', 'payslip', 'employee', 'employer', 'name', 'bank', 'statement', 'account', 'holder', 'card',
    'identity', 'id', 'number', 'date', 'birth', 'gross', 'net', 'pay', 'basic', 'monthly', 'synthetic', 'sample',
    'specimen', 'department', 'designation', 'code', 'earnings', 'deductions', 'total', 'amount', 'balance', 'opening',
    'closing', 'credit', 'debit', 'branch', 'ifsc', 'address', 'mobile', 'phone', 'signature', 'government', 'india',
    'income', 'tax', 'permanent', 'aadhaar', 'aadhar', 'pan', 'period', 'month', 'company', 'ltd', 'pvt', 'limited',
}


def ner_person_spans(text, results):
    """Clean spaCy PERSON detections: split at line breaks, drop pieces containing label words."""
    spans = []
    for r in results:
        if r.entity_type != 'PERSON':
            continue
        offset = r.start
        for piece in text[r.start:r.end].split('\n'):
            stripped = piece.strip(' \t:-')
            words = re.findall(r'[A-Za-z]+', stripped)
            if len(stripped) >= 3 and words and not any(w.lower() in NOT_NAME_WORDS for w in words):
                start = offset + piece.find(stripped)
                spans.append((start, start + len(stripped), 'PERSON'))
            offset += len(piece) + 1
    return spans


def redact(text, fields, vault, analyzer):
    detected = analyzer.analyze(text=text, language='en')
    # Pattern detections (IDs, phones, accounts, dates, emails) and cleaned NER names: priority 1.
    spans = [(x.start, x.end, x.entity_type, 1) for x in detected if x.entity_type != 'PERSON']
    spans += [(s, e, k, 1) for s, e, k in ner_person_spans(text, detected)]
    # Explicit labeled field values and private-label values take priority (0) over any detection.
    for key, kind in [('full_name','PERSON'), ('date_of_birth','DATE_TIME'), ('id_number','ID_NUM'), ('pan_number','ID_NUM'),
                      ('employee_id','EMPLOYEE_ID'), ('employer_name','ORGANIZATION')]:
        value = getattr(fields, key)
        if value:
            spans.extend((m.start(), m.end(), kind, 0) for m in re.finditer(re.escape(value), text, re.I))
    spans.extend((s, e, k, 0) for s, e, k in private_spans(text))
    chosen = []
    # Labeled values first, then the longest detection; overlapping spans are skipped.
    for start, end, kind, _ in sorted(spans, key=lambda s: (s[3], -(s[1] - s[0]))):
        if not any(start < e and end > s for s, e, _ in chosen): chosen.append((start, end, kind))
    counts = Counter()
    masked = text
    for start,end,kind in sorted(chosen, reverse=True):
        token = vault.token(kind, text[start:end])
        # Presidio's anonymizer performs each approved replacement.
        from presidio_analyzer import RecognizerResult
        masked = AnonymizerEngine().anonymize(text=masked,
            analyzer_results=[RecognizerResult(kind,start,end,1.0)],
            operators={kind:OperatorConfig('replace', {'new_value':token})}).text
        counts[kind] += 1
    redacted_values = [text[s:e] for s,e,_ in chosen]
    return masked, tokenize_fields(fields, vault), dict(counts), redacted_values


def tokenize_fields(fields, vault):
    """Request-scoped tokens for the comparison; same normalized name -> same token."""
    token_fields = fields.model_copy()
    if fields.full_name: token_fields.full_name = vault.token('PERSON', fields.full_name)
    if fields.id_number: token_fields.id_number = vault.token('ID_NUM', fields.id_number)
    if fields.pan_number: token_fields.pan_number = vault.token('ID_NUM', fields.pan_number)
    if fields.employee_id: token_fields.employee_id = vault.token('EMPLOYEE_ID', fields.employee_id)
    if fields.date_of_birth: token_fields.date_of_birth = vault.token('DATE_TIME', fields.date_of_birth)
    if fields.employer_name: token_fields.employer_name = vault.token('ORGANIZATION', fields.employer_name)
    return token_fields


# --- per-field confidence + visual grounding ---------------------------------

def _word(value):
    return re.sub(r'\W', '', unicodedata.normalize('NFKC', str(value)).casefold())


def _as_number(word):
    digits = re.sub(r'[^\d.]', '', word.replace(',', ''))
    try:
        return float(digits) if re.search(r'\d', digits) else None
    except ValueError:
        return None


def locate(value, boxes):
    """Indices of the word boxes that carry ``value`` (consecutive normalized-word run)."""
    if value is None or not boxes:
        return []
    if isinstance(value, float):
        return next(([i] for i, b in enumerate(boxes) if _as_number(b['text']) == value), [])
    target = [w for w in (_word(t) for t in str(value).split()) if w]
    words = [_word(b['text']) for b in boxes]
    n = len(target)
    for i in range(len(words) - n + 1):
        if n and words[i:i + n] == target:
            return list(range(i, i + n))
    return []


def field_evidence(fields, sources, boxes):
    """Confidence = OCR confidence of the grounded words x source factor (computed in code)."""
    evidence = {}
    for key in FIELD_NAMES:
        value = getattr(fields, key)
        if value is None:
            evidence[key] = FieldEvidence(confidence=0, source='none', grounded=False)
            continue
        source = sources.get(key, 'labeled_parser')
        factor = LLM_FACTOR if source == 'llm' else 1.0
        hits = locate(value, boxes)
        if hits:
            confidence = sum(boxes[i].get('conf', 100.0) for i in hits) / len(hits) / 100 * factor
        else:
            confidence = UNGROUNDED * factor
        confidence = round(min(max(confidence, 0.0), 1.0), 3)
        evidence[key] = FieldEvidence(
            confidence=confidence, source=source, grounded=bool(hits),
            needs_review=confidence < FIELD_MIN.get(key, DEFAULT_FIELD_MIN),
            boxes=[Box(x=boxes[i]['x'], y=boxes[i]['y'], w=boxes[i]['w'], h=boxes[i]['h']) for i in hits])
    return evidence


# --- optional LLM extraction over masked text, with guardrails ----------------

_TOKEN_KINDS = {'full_name': 'PERSON', 'date_of_birth': 'DATE_TIME', 'id_number': 'ID_NUM|PAN|AADHAAR'}
# Fields each document type can carry (spec "Document types"). The LLM may not fill others,
# e.g. a transaction date on a bank statement is never a date of birth.
LLM_FIELDS = {'ID card': {'full_name', 'date_of_birth', 'id_number'},
              'Salary slip': {'full_name', 'employer_name', 'monthly_salary'},
              'Bank statement, page 1': {'full_name', 'monthly_salary'},
              'Unknown': set(FIELD_NAMES)}


def leaks_known_pii(masked, vault):
    """True if any value already in the vault still appears unmasked in this text."""
    folded = masked.casefold()
    return any(len(v.strip()) >= 3 and v.strip().casefold() in folded for v in vault.originals.values())


def _same(a, b):
    if isinstance(a, float) or isinstance(b, float):
        return a == b
    return normalize_name(a) == normalize_name(b)


def apply_llm(out, masked, fields, doc_type, vault):
    """Validate LLM output, un-mask accepted tokens, and merge with the labeled parser.

    Code wins: a labeled-parser value is never replaced; the LLM only fills gaps.
    PII fields must come back as placeholders that occur in THIS document's masked text;
    anything else (a raw name, a token from another file, a hallucinated value) is rejected.
    Returns (fields, sources, doc_type, review_reasons, rejected_field_names).
    """
    present = set(re.findall(r'<[A-Z_]+_\d+>', masked))
    accepted, rejected = {}, []

    def token(value, kinds):
        value = (value or '').strip()
        if re.fullmatch(rf'<(?:{kinds})_\d+>', value) and value in present and value in vault.originals:
            return vault.originals[value]
        return None

    for key, kinds in _TOKEN_KINDS.items():
        value = getattr(out, key)
        if value is None:
            continue
        original = token(value, kinds)
        if original is None: rejected.append(key)
        else: accepted[key] = original
    if out.employer_name:
        original = token(out.employer_name, 'ORGANIZATION')
        literal = out.employer_name.strip()
        if original is not None:
            accepted['employer_name'] = original
        elif literal and '<' not in literal and literal.casefold() in masked.casefold():
            accepted['employer_name'] = literal
        else:
            rejected.append('employer_name')
    if out.monthly_salary is not None:
        numbers = {_as_number(n) for n in re.findall(r'\d[\d,]*(?:\.\d+)?', masked)}
        if out.monthly_salary > 0 and out.monthly_salary in numbers:
            accepted['monthly_salary'] = float(out.monthly_salary)
        else:
            rejected.append('monthly_salary')

    effective_type = out.document_type if doc_type == 'Unknown' and out.document_type else doc_type
    allowed = LLM_FIELDS.get(effective_type, set(FIELD_NAMES))
    accepted = {k: v for k, v in accepted.items() if k in allowed}
    rejected = [k for k in rejected if k in allowed]

    merged = fields.model_copy()
    sources = {k: 'labeled_parser' for k in FIELD_NAMES if getattr(fields, k) is not None}
    reasons = []
    for key, value in accepted.items():
        current = getattr(fields, key)
        if current is None:
            setattr(merged, key, value)
            sources[key] = 'llm'
        elif not _same(current, value):
            reasons.append(f'{doc_type}: {LABELS[key]} differs between the labeled parser and the LLM; parser value kept.')
    for key in rejected:
        reasons.append(f'{doc_type}: LLM output for {LABELS[key]} failed the placeholder guardrail and was discarded.')
    if out.document_type and out.document_type != 'Unknown':
        if doc_type == 'Unknown':
            doc_type = out.document_type
            reasons.append(f'{doc_type}: document type was inferred by the LLM, not by document labels.')
        elif out.document_type != doc_type:
            reasons.append(f'{doc_type}: LLM classification disagrees with the document labels; label-based type kept.')
    return merged, sources, doc_type, reasons, rejected


def evaluate(items):
    ids = [x for x in items if x[0] == 'ID card']
    slips = [x for x in items if x[0] == 'Salary slip']
    if items and all(x[0] == 'Bank statement, page 1' for x in items):
        return VerificationCheck(status='Not applicable', reason='Bank statements do not participate in the name comparison.')
    if len(ids) != 1 or len(slips) != 1:
        return VerificationCheck(status='Incomplete', reason='Provide exactly one ID card and one salary slip for an unambiguous comparison.')
    id_fields, slip_fields = ids[0][1], slips[0][1]
    if id_fields.is_pan_card:
        if not id_fields.pan_number:
            return VerificationCheck(status='Incomplete', reason='A PAN card was uploaded, but its PAN number could not be read.')
        if not slip_fields.pan_number:
            return VerificationCheck(status='Incomplete', reason='The PAN card was read, but a PAN number could not be found on the salary slip.')
        if id_fields.pan_number != slip_fields.pan_number:
            return VerificationCheck(status='Mismatch', reason='The PAN number on the PAN card does not match the PAN number on the salary slip.')
        if id_fields.full_name and slip_fields.full_name and id_fields.full_name != slip_fields.full_name:
            return VerificationCheck(status='Mismatch', reason='The PAN numbers match, but the names on the PAN card and salary slip differ.')
        return VerificationCheck(status='Verified', reason='The PAN number matches across the PAN card and salary slip.')
    if not id_fields.full_name or not slip_fields.full_name:
        return VerificationCheck(status='Incomplete', reason='A required name could not be extracted. Review the document labels and scan quality.')
    matched = id_fields.full_name == slip_fields.full_name
    return VerificationCheck(status='Verified' if matched else 'Mismatch', reason='Normalized names match across the ID card and salary slip.' if matched else 'The ID card and salary slip contain different names. Manual review is required.')


GATED_FIELDS = {'ID card': ('full_name', 'pan_number'), 'Salary slip': ('full_name', 'monthly_salary', 'pan_number')}


def review_gate(check, docs):
    """Attach human-review requirements. Never changes the deterministic status.

    ``docs``: (doc_type, field_evidence, extra_reasons, quality_warnings) per document.
    """
    if check.status == 'Not applicable':
        return check
    reasons = []
    for doc_type, evidence, extra, quality_warnings in docs:
        if doc_type not in GATED_FIELDS:
            continue
        for key in GATED_FIELDS[doc_type]:
            ev = evidence.get(key)
            if ev is None or ev.source == 'none':
                continue
            if ev.needs_review:
                minimum = FIELD_MIN.get(key, DEFAULT_FIELD_MIN)
                reasons.append(f'{doc_type}: {LABELS[key]} confidence {ev.confidence:.2f} is below {minimum:.2f}.')
            if not ev.grounded:
                reasons.append(f'{doc_type}: {LABELS[key]} could not be located on the page.')
        reasons.extend(extra)
        reasons.extend(f'{doc_type}: {w}' for w in quality_warnings if w.startswith('Low '))
    reasons = list(dict.fromkeys(reasons))
    return check.model_copy(update={'review_required': bool(reasons) or check.status == 'Mismatch',
                                    'review_reasons': reasons})


def process(documents, analyzer, llm=None, engine='auto', tolerant=False, preview_images=False):
    """Run the pipeline over one application's files.

    ``tolerant``: an unreadable file becomes an error entry (and is left out of the check)
    instead of failing the whole batch. Used by the portal worker for per-document re-upload prompts.
    """
    request_id = str(uuid4())
    output, tokens, telemetry, gate_inputs = [], [], [], []
    with request_vault() as vault:
        for data in documents:
            start = time.perf_counter()
            try:
                text, boxes, quality, warnings, confidence, engine_label, page_image = read_document(data, engine, preview_images)
            except DocumentError as exc:
                if not tolerant:
                    raise
                output.append(ExtractionResponse(request_id=request_id, document_type='Unknown', fields=NorthbridgeFields(),
                    check=VerificationCheck(status='Incomplete', reason='Unreadable document.'),
                    pii=PIIMetadata(masked_before_llm=True, entity_counts={}), error=str(exc),
                    pipeline=PipelineMetadata(ocr_engine='not read', timings_ms=PipelineTimings())))
                telemetry.append({'request_id': request_id, 'stage': 'ocr.read', 'error': 'unreadable'})
                continue
            ocr_ms = round((time.perf_counter()-start)*1000)
            doc_type = classify(text)
            extension = 'pdf' if data.startswith(b'%PDF-') else 'png' if data.startswith(b'\x89PNG\r\n\x1a\n') else 'jpeg'
            telemetry.append({'request_id': request_id, 'stage': 'ocr.read', 'doc_type': doc_type,
                'page_count': 1, 'file_extension': extension, 'ocr_engine': engine_label,
                'character_count': len(re.sub(r'\W', '', text)), 'mean_confidence': confidence,
                'fallback': 'fallback' in ' '.join(warnings).lower(), 'duration_ms': ocr_ms})
            fields = extract_fields(text, doc_type)
            full_page_ocr_used = False
            required_missing = (
                (doc_type == 'ID card' and not fields.full_name and not fields.pan_number) or
                (doc_type == 'Salary slip' and (not fields.full_name or fields.monthly_salary is None)) or
                (doc_type == 'Bank statement, page 1' and not fields.full_name)
            )
            if data.startswith(b'%PDF-') and required_missing and importlib.util.find_spec('onnxruntime'):
                try:
                    retry_text = docling_pdf_text(data, full_page_ocr=True)
                    retry_type = classify(retry_text)
                    retry_fields = extract_fields(retry_text, retry_type)
                    recovered = False
                    for key in FIELD_NAMES:
                        if getattr(fields, key) is None and getattr(retry_fields, key) is not None:
                            setattr(fields, key, getattr(retry_fields, key))
                            recovered = True
                    if recovered:
                        full_page_ocr_used = True
                        text = f'{text}\n{retry_text}'
                        if retry_type != 'Unknown':
                            doc_type = retry_type
                        warnings.append('Docling full-page OCR fallback was used to recover missing fields.')
                except Exception:
                    warnings.append('Docling full-page OCR retry failed; required fields may be missing.')
            elif data.startswith(b'%PDF-') and required_missing:
                warnings.append('Docling full-page OCR retry was unavailable (onnxruntime is not installed).')
            start = time.perf_counter()
            masked, token_fields, counts, values = redact(text, fields, vault, analyzer)
            pii_ms = round((time.perf_counter()-start)*1000)
            sources = {k: 'labeled_parser' for k in FIELD_NAMES if getattr(fields, k) is not None}
            extra_reasons = ([f'{doc_type}: Docling layout parsing failed; OCR fallback was used.']
                             if any(w.startswith('Docling layout parsing failed') for w in warnings) else [])
            if full_page_ocr_used:
                extra_reasons.append(f'{doc_type}: Full-page OCR recovered missing fields; human review is required.')
            if any(w.startswith('Docling text lacked required fields') for w in warnings):
                extra_reasons.append(f'{doc_type}: Layout text missed required fields; local OCR was needed. Please review extracted values.')
            rejected, llm_ms, llm_used, llm_attempted, llm_error = [], 0, False, False, None
            llm_metadata = {'model': getattr(llm, 'model', getattr(llm, 'label', llm.name)) if llm else 'none', 'token_counts': {}}
            if llm is not None:
                start = time.perf_counter()
                llm_fields = LLM_FIELDS.get(doc_type, set(FIELD_NAMES))
                missing_llm_fields = doc_type == 'Unknown' or any(getattr(fields, key) is None for key in llm_fields)
                if not missing_llm_fields:
                    warnings.append('LLM skipped: the parser already extracted all supported fields.')
                elif leaks_known_pii(masked, vault):
                    warnings.append('LLM skipped: a known personal value was still visible after masking.')
                    extra_reasons.append(f'{doc_type}: masking was incomplete, so LLM extraction was skipped.')
                else:
                    llm_attempted = True
                    try:
                        if hasattr(llm, 'extract_with_metadata'):
                            out, llm_metadata = llm.extract_with_metadata(masked)
                        else:
                            out = llm.extract(masked)
                        fields, sources, doc_type, llm_reasons, rejected = apply_llm(out, masked, fields, doc_type, vault)
                        extra_reasons.extend(llm_reasons)
                        token_fields = tokenize_fields(fields, vault)
                        llm_used = True
                    except Exception as exc:
                        # Keep provider messages private; expose only a safe failure category.
                        status_code = getattr(getattr(exc, 'response', None), 'status_code', None)
                        if exc.__class__.__name__ in ('TimeoutException', 'ConnectTimeout', 'ReadTimeout', 'WriteTimeout', 'PoolTimeout'):
                            llm_error = 'timeout'
                            warnings.append('LLM request timed out; parser results only.')
                        elif exc.__class__.__name__ in ('ConnectError', 'NetworkError'):
                            llm_error = 'connection'
                            warnings.append('LLM server could not be reached; parser results only.')
                        elif status_code:
                            llm_error = f'http_{status_code}'
                            warnings.append(f'LLM request failed (HTTP {status_code}); parser results only.')
                        else:
                            llm_error = 'invalid_response'
                            warnings.append('LLM response was invalid; parser results only.')
                llm_ms = round((time.perf_counter()-start)*1000)
                telemetry.append({'request_id':request_id,'stage':'llm.extract','provider':llm.name,
                                  'model':llm_metadata['model'],
                                  'prompt_version':'northbridge-kyc-v1','token_counts':llm_metadata['token_counts'],'used':llm_used,
                                  'attempted':llm_attempted,'error':llm_error,
                                  'filled':sorted(k for k,v in sources.items() if v=='llm'),
                                  'rejected':sorted(rejected),'duration_ms':llm_ms})
            evidence = field_evidence(fields, sources, boxes)
            fields.date_of_birth = iso_date(fields.date_of_birth)
            tokens.append((doc_type, token_fields))
            # Scan-quality warnings only matter when the text came from OCR, not embedded PDF text.
            gate_inputs.append((doc_type, evidence, extra_reasons, warnings if confidence is not None else []))
            redactions = [{k:v for k,v in b.items() if k not in ('text', 'conf')} for b in boxes
                          if any(normalize_name(b['text']) in normalize_name(v).split() or b['text'] == v for v in values)]
            output.append(ExtractionResponse(request_id=request_id, document_type=doc_type, fields=fields,
                field_evidence=evidence,
                check=VerificationCheck(status='Incomplete',reason='Pending pair comparison.'),
                pii=PIIMetadata(masked_before_llm=True,entity_counts=counts), masked_text=masked, redactions=redactions,
                page_image=page_image,
                pipeline=PipelineMetadata(ocr_engine=engine_label,timings_ms=PipelineTimings(ocr=ocr_ms,pii=pii_ms,llm=llm_ms),
                    quality=quality,warnings=warnings,confidence=confidence,llm_used=llm_used,
                    llm_provider=llm.name if llm is not None else 'none')))
            telemetry.append({'request_id':request_id,'stage':'pii.detect','entity_counts':counts,'doc_type':doc_type,'duration_ms':pii_ms})
        start = time.perf_counter()
        check = review_gate(evaluate(tokens), gate_inputs)
        elapsed = round((time.perf_counter()-start)*1000)
        for doc in output:
            doc.check = VerificationCheck(status='Not applicable',reason='Bank statements do not participate in the name comparison.') if doc.document_type == 'Bank statement, page 1' else check
            doc.pipeline.timings_ms.rule = elapsed
        telemetry.append({'request_id':request_id,'stage':'rule.evaluate','status':check.status,
                          'review_required':check.review_required,'review_reasons':len(check.review_reasons),'duration_ms':elapsed})
        stages = stage_summary(output, check, llm, getattr(analyzer, 'nb_ner', False), elapsed)
        telemetry.append({'request_id':request_id,'stage':'request.complete',
            'outcome':'error' if any(d.error for d in output) else 'success',
            'doc_type_counts':dict(Counter(d.document_type for d in output)),
            'entity_counts':dict(sum((Counter(d.pii.entity_counts) for d in output), Counter())),
            'check_status':check.status,
            'stage_durations_ms':{k:sum(getattr(d.pipeline.timings_ms,k) for d in output) for k in ('ocr','pii','llm','rule')}})
        request_event = telemetry[-1]
        aggregate_metrics = record_metrics(request_event['check_status'], request_event['outcome'],
            request_event['entity_counts'], request_event['stage_durations_ms'],
            sum(e.get('fallback',False) for e in telemetry))
        return BatchResponse(request_id=request_id,check=check,documents=output,telemetry=telemetry,stages=stages,
            metrics=aggregate_metrics)


def stage_summary(docs, check, llm, ner, rule_ms):
    """The diagram's pipeline as five stages, for the borrower and staff views. No document values."""
    read = [d for d in docs if not d.error]
    scanned = [d for d in read if d.pipeline.confidence is not None]
    prescan_ms = sum(int(d.pipeline.quality.get('prescan_ms') or 0) for d in read)
    ocr_ms = max(sum(d.pipeline.timings_ms.ocr for d in docs) - prescan_ms, 0)
    low_quality = any(w.startswith('Low ') for d in scanned for w in d.pipeline.warnings)
    engines = sorted({d.pipeline.ocr_engine if d.pipeline.confidence is None else d.pipeline.ocr_engine.split(' (')[0].replace(' local OCR', '') for d in read})
    unreadable = len(docs) - len(read)
    masked = sum(sum(d.pii.entity_counts.values()) for d in read)
    llm_used = any(d.pipeline.llm_used for d in docs)
    llm_filled = any(ev.source == 'llm' for d in read for ev in d.field_evidence.values())
    llm_empty = llm_used and not llm_filled
    llm_issue = any(('LLM' in r or 'guardrail' in r) for r in check.review_reasons)
    llm_failed = any(any(w.startswith(('LLM request timed out', 'LLM server could not be reached',
                                       'LLM request failed', 'LLM response was invalid'))
                         for w in d.pipeline.warnings) for d in read)
    llm_not_needed = bool(read) and llm is not None and all(
        any(w.startswith('LLM skipped: the parser already extracted all supported fields')
            for w in d.pipeline.warnings) for d in read)
    verified = check.status == 'Verified' and not check.review_required
    if llm is None:
        llm_status, llm_detail = 'skipped', 'not configured'
    elif llm_not_needed:
        llm_status, llm_detail = 'skipped', 'not needed; parser found all supported fields'
    elif llm_failed:
        llm_status, llm_detail = 'warn', 'failed; parser results retained'
    elif llm_empty:
        llm_status, llm_detail = 'warn', 'returned no accepted fields; parser results retained'
    else:
        llm_status = ('warn' if llm_issue else 'done') if llm_used else 'skipped'
        llm_detail = (getattr(llm, 'label', 'LLM') + ' · masked text only') if llm_used else 'skipped for privacy'
    return [
        {'key': 'prescan', 'label': 'Pre-scan', 'status': 'warn' if low_quality else 'done', 'ms': prescan_ms,
         'detail': f'{len(docs)} file{"s" if len(docs) != 1 else ""} checked' + (' · scan quality warning' if low_quality else '')},
        {'key': 'ocr', 'label': 'OCR', 'status': 'warn' if unreadable else 'done', 'ms': ocr_ms,
         'detail': ' + '.join(engines) + (f' · {unreadable} unreadable' if unreadable else '') if engines else 'nothing readable'},
        {'key': 'pii', 'label': 'PII masking', 'status': 'done', 'ms': sum(d.pipeline.timings_ms.pii for d in read),
         'detail': f'{masked} values masked' + (' · Presidio + NER' if ner else ' · Presidio')},
        {'key': 'llm', 'label': 'LLM evaluation', 'status': llm_status,
         'ms': sum(d.pipeline.timings_ms.llm for d in docs),
         'detail': llm_detail},
        {'key': 'verify', 'label': 'Verification', 'status': 'done' if verified or check.status == 'Not applicable' else 'warn',
         'ms': rule_ms, 'detail': check.status + (' · needs review' if check.review_required else '')},
    ]
