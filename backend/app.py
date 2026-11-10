import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from email.parser import BytesParser
from email.policy import default
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from .models import BatchResponse
from .pipeline import build_analyzer, process, DocumentError, MAX_FILE, SALARY_MIN, NAME_MIN
from .llm import get_provider
from .ocr import available_engines, get_rapidocr, rapidocr_available, ENGINE_LABELS
from .portal import router as portal_router, COOKIE, promote_verified_applications
from .store import Store

DIST = Path(__file__).resolve().parent.parent / 'dist'

logger = logging.getLogger('northbridge.audit')
MAX_FILES = 5
MAX_BODY = MAX_FILES * MAX_FILE + 65536
lock = asyncio.Semaphore(1)

@asynccontextmanager
async def lifespan(app):
    app.state.analyzer = build_analyzer()
    app.state.llm = get_provider()
    app.state.store = Store(os.environ.get('NB_DATA_DIR', str(Path(__file__).resolve().parent.parent / 'data')))
    promote_verified_applications(app.state.store)
    app.state.cookie_secure = os.environ.get('NB_COOKIE_SECURE', '0') == '1'
    if rapidocr_available():
        try:
            await run_in_threadpool(get_rapidocr)  # Load ONNX models once, before the first request.
        except Exception:
            pass  # Logged in ocr.py; Tesseract is used instead.
    yield

app = FastAPI(title='Northbridge KYC', version='1.0.0', lifespan=lifespan)

@app.middleware('http')
async def privacy_headers(request, call_next):
    # CSRF: state-changing API calls must carry a custom header. Browsers cannot add it
    # cross-site without a CORS preflight, which this app never allows. Cookies are also SameSite=Strict.
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and (request.url.path.startswith('/api/') or request.url.path.startswith('/v1/')):
        if request.headers.get('x-nb-csrf') != '1':
            return JSONResponse({'detail': 'Missing request header.'}, status_code=403, headers={'Cache-Control': 'no-store'})
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; frame-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'self'"
    return response

@app.get('/v1/health')
def health(request: Request):
    llm = request.app.state.llm
    return {'status':'ok','mode':'local','ocr':'PyMuPDF text, then '+' / '.join(ENGINE_LABELS[e] for e in available_engines()),
            'ocr_engines':available_engines(),'default_engine':(available_engines() or ['none'])[0],'pii':'Presidio + explicit recognizers',
            'llm':llm.name if llm else 'none','llm_model':getattr(llm,'label',None) if llm else None,'llm_input':'masked text only',
            'external_calls':bool(llm and llm.external),'vision_fallback':False,'pages_per_file':1,
            'review_thresholds':{'monthly_salary':SALARY_MIN,'full_name':NAME_MIN}}

@app.post('/v1/extract', response_model=BatchResponse, openapi_extra={'requestBody': {'required': True, 'content': {'multipart/form-data': {'schema': {'type': 'object', 'required': ['files'], 'properties': {'files': {'type': 'array', 'maxItems': 5, 'items': {'type': 'string', 'format': 'binary'}}}}}}}})
async def extract(request: Request):
    # Quick-verify tool for lender staff only.
    user = request.app.state.store.session_user(request.cookies.get(COOKIE))
    if user is None or user['role'] != 'lender':
        return JSONResponse({'detail': 'Sign in to the lender dashboard to use the verification tool.'}, status_code=401)
    # Do not use UploadFile: its spooling can persist PII on disk.
    content_type = request.headers.get('content-type','')
    if not content_type.startswith('multipart/form-data') or '\r' in content_type or '\n' in content_type:
        return JSONResponse({'detail':'Expected multipart document files.'},status_code=415)
    if lock.locked():
        return JSONResponse({'detail':'Another request is processing. Please retry shortly.'},status_code=429)
    async with lock:
        body = bytearray()
        files = []
        try:
            async for chunk in request.stream():
                if len(body)+len(chunk)>MAX_BODY:
                    return JSONResponse({'detail':'The request exceeds the 50 MB total limit.'},status_code=413)
                body.extend(chunk)
            message = BytesParser(policy=default).parsebytes(('Content-Type: '+content_type+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+bytes(body))
            if not message.is_multipart(): raise DocumentError('Invalid multipart request.')
            for part in message.iter_parts():
                if part.get_content_disposition() != 'form-data' or part.get_param('name',header='content-disposition') != 'files':
                    raise DocumentError('Use the files form field for each document.')
                data = part.get_payload(decode=True)
                if not isinstance(data,bytes): raise DocumentError('Could not read this document.')
                if len(data)>MAX_FILE: return JSONResponse({'detail':'Each file must be no larger than 10 MB.'},status_code=413)
                files.append(data)
                if len(files)>MAX_FILES: raise DocumentError('Upload up to five documents per request.')
            if not files: raise DocumentError('Upload at least one document.')
            engine = request.query_params.get('engine', 'auto')
            if engine not in ('auto', 'rapidocr', 'tesseract'): raise DocumentError('Unknown OCR engine. Use auto, rapidocr or tesseract.')
            result = await run_in_threadpool(process, files, request.app.state.analyzer, request.app.state.llm, engine)
            for event in result.telemetry: logger.info(json.dumps(event))
            return result
        except DocumentError as exc:
            return JSONResponse({'detail':str(exc)},status_code=422)
        except Exception:
            # Never echo parser/OCR exceptions, filenames, or extracted text.
            return JSONResponse({'detail':'Could not read this document.'},status_code=422)
        finally:
            for i in range(len(body)): body[i] = 0
            files.clear()
            body.clear()

app.include_router(portal_router)


STAFF = Path(__file__).resolve().parent.parent / 'staff'


def page(folder, name):
    return FileResponse(folder / name, headers={'Cache-Control': 'no-store'})


# Public site: the borrower portal only. Staff pages live in a separate folder that the public
# static mount cannot serve, at an unlinked /staff URL behind a staff sign-in.
@app.get('/apply', include_in_schema=False)
def apply_redirect():
    return RedirectResponse('/', status_code=308)


@app.get('/staff', include_in_schema=False)
def staff_page():
    return page(STAFF, 'lender.html')


@app.get('/staff/verify', include_in_schema=False)
def staff_verify_page():
    return page(STAFF, 'verify.html')


@app.get('/staff/applications/{app_id}', include_in_schema=False)
def staff_review_page(app_id: int):
    return page(STAFF, 'verify.html')


app.mount('/staff/assets', StaticFiles(directory=STAFF), name='staff-assets')
app.mount('/', StaticFiles(directory=Path(__file__).resolve().parent.parent/'dist',html=True),name='workspace')
