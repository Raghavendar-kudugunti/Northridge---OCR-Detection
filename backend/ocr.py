"""Local OCR engines. Document images never leave the process or touch the disk.

* ``rapidocr`` (default) - PaddleOCR PP-OCR models on ONNX Runtime: the bundled PP-OCRv6
  detector plus the PP-OCRv5 English recognizer. The bundled Chinese-first recognizer drops
  spaces on degraded English scans ("name:RaviMenon"), which breaks label parsing. The English
  model (~8 MB) is fetched once by RapidOCR and verified against the SHA-256 pinned in its
  config; ``app`` loads it at startup (Docker downloads it at build time), never per request.
  Returns per-word text, score and box.
* ``tesseract`` - the system Tesseract binary, piped over stdin/stdout (no temp files).

Both return ``(text, boxes, mean_confidence_0_to_100)`` where each box is
``{'text', 'x', 'y', 'w', 'h', 'conf'}`` with coordinates normalized to the page (0-1).
"""
import csv
import io
import logging
import os
import shutil
import subprocess
import threading
import cv2
import numpy as np

ENGINE_LABELS = {'rapidocr': 'RapidOCR (PP-OCR, local)', 'tesseract': 'Tesseract local OCR'}

_rapid = None
_rapid_failed = False
_rapid_lock = threading.Lock()
log = logging.getLogger('northbridge.ocr')


def rapidocr_available():
    if _rapid_failed:
        return False
    try:
        import rapidocr  # noqa: F401
        return True
    except ImportError:
        return False


def tesseract_available():
    return shutil.which('tesseract') is not None


def available_engines():
    return [name for name, ok in (('rapidocr', rapidocr_available()), ('tesseract', tesseract_available())) if ok]


def _quiet_rapidocr_logs():
    for name in ('RapidOCR', 'rapidocr'):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_rapidocr():
    """One shared engine; loading the ONNX models takes a few seconds."""
    global _rapid, _rapid_failed
    with _rapid_lock:
        if _rapid is None:
            if _rapid_failed:
                raise RuntimeError('RapidOCR is unavailable')
            _quiet_rapidocr_logs()
            try:
                _rapid = _build_rapidocr()
            except Exception:
                # e.g. the English model could not be downloaded on first start: Tesseract takes over.
                _rapid_failed = True
                log.warning('RapidOCR could not start; Tesseract will be used where installed.')
                raise
            _quiet_rapidocr_logs()
    return _rapid


def _build_rapidocr():
    from rapidocr import RapidOCR
    from rapidocr.utils.typings import LangRec, ModelType, OCRVersion
    # Fixed thread count avoids oversubscribing the CPU alongside the API worker.
    threads = int(os.environ.get('NB_OCR_THREADS', str(min(4, os.cpu_count() or 1))))
    return RapidOCR(params={'Global.log_level': 'warning',
                            'Rec.ocr_version': OCRVersion.PPOCRV5,
                            'Rec.model_type': ModelType.MOBILE,
                            'Rec.lang_type': LangRec.EN,
                            'EngineConfig.onnxruntime.intra_op_num_threads': threads,
                            'EngineConfig.onnxruntime.inter_op_num_threads': 1})


def run_rapidocr(image):
    width, height = image.size
    output = get_rapidocr()(cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR), return_word_box=True)
    lines, boxes, scores = [], [], []
    for line in getattr(output, 'word_results', None) or ():
        words = []
        if not isinstance(line, (list, tuple)):
            continue
        for item in line:
            if not isinstance(item, (list, tuple)) or len(item) < 3 or item[2] is None:
                continue
            text, score, quad = item[0], float(item[1]), item[2]
            text = str(text).strip()
            if not text:
                continue
            xs = [float(p[0]) for p in quad]
            ys = [float(p[1]) for p in quad]
            boxes.append({'text': text, 'x': max(min(xs), 0) / width, 'y': max(min(ys), 0) / height,
                          'w': (max(xs) - min(xs)) / width, 'h': (max(ys) - min(ys)) / height,
                          'conf': max(min(score, 1.0), 0.0) * 100})
            scores.append(score * 100)
            words.append(text)
        if words:
            lines.append(' '.join(words))
    return '\n'.join(lines), boxes, (sum(scores) / len(scores) if scores else 0.0)


def run_tesseract(image):
    payload = io.BytesIO()
    image.save(payload, format='PNG')
    completed = subprocess.run(['tesseract', 'stdin', 'stdout', '-l', 'eng', '--psm', '6', 'tsv'],
        input=payload.getvalue(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        check=True, timeout=30)
    rows = csv.DictReader(io.StringIO(completed.stdout.decode('utf-8')), delimiter='\t', quoting=csv.QUOTE_NONE)
    words, confidences, boxes = [], [], []
    prior_line = None
    for row in rows:
        text = row.get('text', '')
        if not text.strip():
            continue
        conf = float(row['conf'])
        line = (row['block_num'], row['par_num'], row['line_num'])
        if prior_line is not None and line != prior_line:
            words.append('\n')
        words.append(text)
        prior_line = line
        if conf >= 0:
            confidences.append(conf)
        boxes.append({'text': text, 'x': int(row['left']) / image.width, 'y': int(row['top']) / image.height,
                      'w': int(row['width']) / image.width, 'h': int(row['height']) / image.height,
                      'conf': max(conf, 0.0)})
    text = ' '.join(words).replace(' \n ', '\n')
    return text, boxes, (sum(confidences) / len(confidences) if confidences else 0.0)


RUNNERS = {'rapidocr': run_rapidocr, 'tesseract': run_tesseract}


def resolve_engine(requested):
    """Map auto/rapidocr/tesseract to an installed engine. Returns (engine, warning_or_None)."""
    requested = (requested or 'auto').lower()
    if requested not in ('auto', 'rapidocr', 'tesseract'):
        raise ValueError('Unknown OCR engine. Use auto, rapidocr or tesseract.')
    installed = available_engines()
    preferred = 'rapidocr' if requested == 'auto' else requested
    if preferred in installed:
        return preferred, None
    if installed:
        return installed[0], f'{ENGINE_LABELS[preferred]} is not installed; used {ENGINE_LABELS[installed[0]]}.'
    raise RuntimeError('No OCR engine is installed.')
