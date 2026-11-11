# Northbridge Finance — KYC verification workspace

Team Beta · Runnable local MVP + private, browser-only synthetic demonstration.

## Local setup and workflow

Python 3.12 is required. The steps below use Windows PowerShell; run them from the repository folder. PyMuPDF reads digital PDF text, Docling handles layout fallback, and RapidOCR processes scans and images. Tesseract is optional.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
python -m backend.seed
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000 --no-access-log
```

Open **http://127.0.0.1:8000** for the borrower portal. The staff dashboard is **http://127.0.0.1:8000/staff**. Seeded local demo credentials and the four synthetic borrower accounts are listed in `backend/seed.py`; override the defaults with `NB_SEED_LENDER_EMAIL`, `NB_SEED_LENDER_PASSWORD`, and `NB_SEED_BORROWER_PASSWORD`. Seed only once for a fresh local database. Press **Ctrl+C** in the server terminal to stop the app; restart with the uvicorn command after changing backend code.

macOS/Linux: create and activate `.venv` with the platform's normal Python 3.12 commands, then run the same `python -m pip`, `python -m backend.seed`, and `python -m uvicorn` commands. Alternatively, with Docker Desktop, run `docker compose up --build`; configure the required `NB_ENCRYPTION_KEY` first.

### Borrower verification

1. Register or sign in at `/`, then upload an ID card and salary slip. A PAN card is supported as the ID document; a bank statement and up to two other documents are optional.
2. Submit the application. The app extracts text locally, checks that each file matches its selected document type, and reports specific missing-field or comparison reasons. If asked to replace a document, use the reason shown beside it, upload a corrected file, and resubmit.
3. A regular ID card is checked by comparing its holder name with the employee name on the salary slip. For a PAN card, the PAN number must also be present on the salary slip and match the PAN card. Missing values produce **Incomplete**; differing values produce **Mismatch**.
4. Complete, matching documents can be approved automatically. A mismatch, incomplete extraction, or duplicate verified identity is flagged for staff review. A verified identity value already associated with another borrower is marked **Not applicable** with a notice to the new applicant.

### Staff review

1. Open `/staff` and sign in with the seeded lender account (role `lender`).
2. Use the queue to find an application and open it to inspect its check result, reason, extracted fields, document versions, and audit history.
3. Resolve cases needing staff attention: request a replacement with a clear reason, add an internal note, then approve or reject when appropriate.

The development seed credentials are for local synthetic data only. Do not expose this local-only service to the public internet or use real identity documents without a reviewed deployment and data-retention policy.

## Backend processing pipeline

Every upload runs the same five stages over the application's current documents. Borrowers see them as a progress rail (stage names, status and a short non-identifying detail); staff also see timings.

| Stage | What happens | Where |
| --- | --- | --- |
| **Secure storage** | File type checked from its bytes; stored encrypted (AES-256-GCM); a new document version is recorded | `store.py`, `portal.py` |
| **Pre-scan** | Sharpness, contrast and brightness of each page | `pipeline.read_document` |
| **PDF text and layout parsing** | PyMuPDF extracts an existing PDF text layer first; local Docling layout parsing is the fallback when classification or required fields are missing. PyMuPDF also renders page previews. | `pipeline.py` |
| **OCR** | Local RapidOCR (or Tesseract) reads image uploads; unreadable files fail closed → re-upload prompt | `ocr.py` |
| **PII masking** | Presidio with spaCy NER (`en_core_web_sm`, local) plus Indian ID/phone/account patterns and labeled values. Names, IDs, dates of birth, addresses, phones, account numbers and relatives' names become request-scoped tokens such as `<PERSON_1>` | `pipeline.redact` |
| **LLM evaluation** | When configured and supported fields are missing, DeepSeek V4.1 Flash (OpenRouter, provider data collection denied) or the configured local model reads **masked text only** and may fill parser gaps. Placeholder guardrails discard raw names or foreign tokens; the stage reports failures and empty/rejected output. Skipped when no supported fields are missing or no LLM is configured. | `llm.py`, `pipeline.apply_llm` |
| **Verification** | Deterministic: compare ID-card holder name with salary-slip employee name; for PAN cards, also require a matching PAN number on the payslip. Missing or conflicting values become Incomplete or Mismatch. Previously verified identity identifiers are checked for another borrower and reported as Not applicable. | `pipeline.evaluate`, `review_gate`, `store.py` |

The LLM cannot decide the result: the verification rules compare parsed values deterministically. Names are normalized before comparison; PAN numbers are compared when the ID document is a PAN card. Because the LLM only sees placeholders, it can help identify which token represents a field, but the verification engine performs the comparison.

**Real-world layouts.** Labels are read whether written as `Name: value`, `Name value`, or `Name` with the value on the next line (e.g. `Employee Name` ↵ `Ravi Menon`, `Gross Salary` ↵ `82,000`). Documents are classified by weighted signals (e.g. *payslip*, *gross salary*, *net pay* → salary slip; *statement of … bank*, *opening balance*, *IFSC* → bank statement; *ID number*, *date of birth*, *government* → ID card), so cards that never print "Identity card" or "Aadhaar" are still recognised.

To enable the LLM stage (PowerShell):

```powershell
$env:NB_LLM_PROVIDER = "openrouter"
$env:OPENROUTER_API_KEY = "<your OpenRouter key>"
```

With a remote LLM, residual risk remains: masking is best effort, so a name that neither a label nor NER recognises could be sent in masked text. Review the Masked text tab on real-looking test documents before enabling it for production data.

## Borrower portal and staff dashboard

```
Borrower portal (/)                              Staff dashboard (/staff, unlinked)
1. Register / sign in                            1. Staff sign-in (role: lender)
2. Document slots: ID card, salary slip,         2. Review queue: borrower, application ID,
   bank statement, 2 other documents                status, document check, dates
3. Request a one-time secure upload link  ──►    3. Open an application:
   PUT the file to it ──► encrypted storage         • viewer + field highlights + decision trace
   ──► database ──► automatic OCR check             • request a re-upload (reason shown to borrower)
   validation fails ──► re-upload prompt            • internal notes · approve / reject
Status: Verified · Mismatch · Incomplete ·       History: every document version + audit trail
        Not applicable
```

**Borrower flow.** Uploading only stores the file (encrypted) — nothing is verified yet. Choosing a file first opens a **preview** ("Upload this file" / "Choose another file"); after upload each document shows its filename and a page-1 **thumbnail** that opens a larger view. Before submitting, the borrower can **replace** or **remove** any document. **Submit for verification** runs the full pipeline once on all documents. Before a final staff decision, the borrower can replace any current document and **resubmit**; replacing a document while under review moves the application to **Action needed**.

**Application statuses:** Not submitted → Checking your documents → Action needed (replace flagged documents, then resubmit), Approved (required documents accepted and deterministic checks pass), or Under review (Mismatch, Incomplete, duplicate verified identity, or an LLM-supplied matching name) → Approved / Not approved by staff. A model-supplied name is never enough to auto-approve.

**Automatic re-upload prompts** (per document, shown to the borrower with a plain reason): the file cannot be read; its content is a different document type than its slot (e.g. a salary slip in the ID card slot); or a required field is missing (name on the ID card; name and gross salary on the salary slip; PAN number on a PAN-card applicant's salary slip; account holder on the bank statement). A lender can also request a re-upload with a reason. Re-uploading creates a new version; older versions are kept for the audit trail.

**Isolation and security**

- Borrower endpoints take no application or document IDs; they always resolve the signed-in borrower's own application. Lender endpoints require the `lender` role. Anything not visible to the caller returns **404**.
- Upload links are random, single-use, expire after 10 minutes, are bound to one borrower and one slot, and still require that borrower's session.
- Documents, extraction results and staff notes are encrypted with **AES-256-GCM** (bound to their record) before they are written. SQLite holds metadata only. Key: `NB_ENCRYPTION_KEY` (base64, 32 bytes). Without it a development key file is created in the data folder with a warning — never do that in production.
- Sessions: random tokens (stored hashed), HttpOnly, SameSite=Strict cookies; `NB_COOKIE_SECURE=1` behind HTTPS. Every state-changing API call must carry the `x-nb-csrf: 1` header. Passwords use scrypt; 5 failed sign-ins lock an email for 5 minutes.
- The audit log records actions and IDs only: uploads, link issues, automatic checks, every lender view of an application, re-upload requests, notes and decisions.
- Borrowers see statuses and reasons only — never confidence scores, masked text, telemetry or internal notes.
- The quick verify tool (`/staff/verify`, `POST /v1/extract`) is limited to signed-in staff. Staff pages live in `staff/`, outside the public `dist/` folder, so the public site cannot serve them; a staff account signing in on the borrower page is signed out and told to use the staff sign-in.

**Configuration:** `NB_DATA_DIR` (default `./data`), `NB_ENCRYPTION_KEY`, `NB_COOKIE_SECURE`, plus the OCR/LLM variables below. Docker Compose requires `NB_ENCRYPTION_KEY` and stores data in the `northbridge-data` volume.

**Portal API**

| Method | Path | Who |
| --- | --- | --- |
| POST | `/api/auth/register`, `/api/auth/login`, `/api/auth/logout` · GET `/api/auth/me` | anyone |
| GET | `/api/borrower/application` | borrower (own application only) |
| POST | `/api/borrower/upload-links` `{slot}` → `{upload_url, method: "PUT", expires_in}` | borrower |
| DELETE | `/api/borrower/documents/{slot}` (before submitting) | borrower |
| POST | `/api/borrower/submit` (runs verification) | borrower |
| GET | `/api/borrower/documents/{slot}/preview?size=thumb\|large` (page 1 as JPEG, own documents only) | borrower |
| GET | `/api/staff/documents/{id}/preview` (any stored version; audited) | staff |
| PUT | `/api/uploads/{token}` (raw file body) | borrower who owns the link |
| GET | `/api/staff/applications?status=&q=` · `/api/staff/applications/{id}` | staff (role `lender`) |
| POST | `/api/staff/applications/{id}/reupload-requests` · `/decision` · `/notes` | staff (role `lender`) |

**Not built yet:** OIDC / single sign-on, staff account management UI, email/SMS notifications, a retention job, field corrections by officers, four-eyes approval.

## Included functionality

- Responsive, dependency-free HTML/CSS/JavaScript workspace (no frontend build): engine picker and dropzone, a five-stage stepper with timings, a pre-scan quality panel, a page viewer with PII masking and field highlights, and an inspector with Fields, Decision (check trace), Masked text, Compare and Telemetry tabs.
- Upload slots per document type: **ID card** and **Salary slip** (both needed for the name check), **Bank statement** (page 1, optional) and **Other related documents** (up to 2, optional). Drag-and-drop or browse per slot, byte-signature checks, 10 MiB/file, 5 files and 50 MiB per request. Every file is classified from its content; if it reads as a different type than its slot, the UI says so and the check uses the detected type.
- FastAPI `POST /v1/extract`, Pydantic v2 response models, same-origin frontend.
- In-memory PDF first-page layout and text extraction with local Docling; PyMuPDF is retained for rendering previews and locating text on the page. Image uploads and PDF fallback OCR use a local engine chosen per request (`?engine=auto|rapidocr|tesseract`): **RapidOCR** or **Tesseract** (piped over stdin/stdout). Neither sends document content to a remote service.
- Compare tab: re-reads the same uploads with the other engine and shows field-by-field agreement, confidence and OCR time.
- Pillow/OpenCV sharpness, brightness and contrast checks; image orientation corrected using EXIF; bounded 20 MP image input and 2400-pixel PDF renders.
- Microsoft Presidio analyzer/anonymizer with explicit Indian financial recognizers and labeled-field spans. Blank spaCy tokenization avoids runtime model downloads.
- Name normalization per the spec: NFKC, **uppercase, strip punctuation, collapse spaces** (`Ravi Menon.` = `RAVI  MENON`). Request-local PERSON tokens built on that key are shared across files, so the ID and slip are compared without sending raw names. Maps clear in `finally` even on failures.
- Deterministic name comparison for ID card and salary slip; PAN cards additionally require matching PAN numbers on the card and payslip. Missing required values result in Incomplete; conflicts result in Mismatch. Bank statements and other documents do not change the identity comparison.
- After an application is verified, keyed fingerprints of supported identity values are retained for cross-borrower duplicate detection. If another borrower submits the same identifying value, the check reports Not applicable for staff review. Raw identifiers are not stored in the fingerprint index.
- Per-field evidence: every field carries `confidence` (OCR word confidence × source factor), `source` (`labeled_parser` / `llm`), and page `boxes`. In the UI, select a field to highlight where it came from (PDF page 1 is rendered for this).
- Check diagnostics: name confidence below 0.85, salary confidence below 0.90 (plan §2.2), a value not found on the page, a low-quality OCR scan, an LLM guardrail rejection or a parser/LLM disagreement is recorded in `check.review_required` and non-PII `review_reasons`. The lender portal auto-approves complete applications with a Verified parser/OCR name match; Mismatch, Incomplete and LLM-supplied name matches stay in review.
- Optional LLM extraction on masked text only (off by default; see below). Code wins: the labeled parser's values are never replaced, and the LLM cannot change the name result.
- Original field display, masked-text inspection, image redaction overlays, original PDF preview, safe JSON telemetry and metadata-only export.
- No external model calls by default and no browser storage. The quick verify tool keeps uploads in memory only; the portals store documents **encrypted** (see below). No original filenames or document content in logs.
- 67 automated tests (including portal tests for auth, isolation, upload links, re-uploads, decisions, encryption at rest, real-world label layouts and the public/staff split) (offline; LLM tests use mock/fake providers; image OCR tested on both engines) and a reproducible 20-document synthetic evaluation.

## Submission corpus and test sheet

`samples/documents/` contains **64 designed synthetic PDFs**: 12 synthetic identity cards, 12 Aadhaar-style samples, 12 PAN-style samples, 14 salary slips and 13 bank statement first pages. Each person has one Aadhaar-style and one PAN-style sample. The identity samples are prominently marked invalid, use placeholder identifiers and omit government seals and security features. Every file has a distinct, formatted card, payroll or statement layout with invented holder details, amounts and transactions. A visible synthetic-sample label appears on every page; the examples contain no real financial or identity data. Salary slips include both gross and net amounts; extraction is expected to return gross monthly salary. `samples/documents/manifest.csv` describes each file.

`samples/20-document-test-sheet.csv` is generated by `python -m backend.evaluate`. It records each input's expected type, expected fields, extracted fields and expected/actual rule result. The 20 documents cover four matching and four mismatching pairs, ID-only and salary-only incomplete cases, and bank-only not-applicable cases. The brief's case-only example conflicts with its list of mismatch examples: behavior follows the explicit normalization rule, so `Ravi Menon` and `RAVI MENON` match.
## Hosted vs local

The private hosted site contains the frontend only. Its four scenarios use clearly labeled synthetic fixtures. Uploads on that site are previewed in the browser and **are not extracted**. It does not connect from a remote site to localhost.

The same frontend served by FastAPI automatically detects `/v1/health` and sends uploads to the local backend for real extraction. Server responses contain original field values for display to the operator; they must never be sent to shared logs or exposed through a public reverse proxy.

## Optional LLM extraction (masked text only)

| Variable | Default | Notes |
| --- | --- | --- |
| `NB_LLM_PROVIDER` | `none` | `none` · `mock` (offline) · **`openrouter`** (DeepSeek V4.1 Flash) · `openai_compatible` |
| `OPENROUTER_API_KEY` | — | Required for `openrouter`. Model defaults to `deepseek/deepseek-v4.1-flash` (override with `NB_LLM_MODEL`). |
| `NB_LLM_BASE_URL` | — | e.g. `http://127.0.0.1:11434/v1` (Ollama). Prefer a local endpoint. |
| `NB_LLM_MODEL` | — | Model name at that endpoint. |
| `NB_LLM_API_KEY` | — | Only if the endpoint needs one. |
| `NB_LLM_TIMEOUT_S` | `30` | |
| `NB_SALARY_CONFIDENCE_MIN` / `NB_NAME_CONFIDENCE_MIN` | `0.90` / `0.85` | Review thresholds. |

How it is contained:

1. Only Presidio-masked text is sent, wrapped in `<sanitized_ocr_text>` delimiters (control characters and delimiter spoofing stripped, 8,000-character cap). If any value already in the token vault is still visible, the call is skipped.
2. Output is validated by Pydantic, then guardrails: name, date of birth and ID number must be placeholders that occur in *this* document's masked text; employer must be a placeholder or text present on the page; salary must be a number present on the page. Anything else is discarded and forces review.
3. The LLM only fills fields the labeled parser left empty. Disagreements keep the parser value and force review. The name comparison stays deterministic code.
4. Provider errors are never echoed; the request falls back to parser-only results.
5. For OpenRouter, requests set `provider.data_collection = "deny"`. `/v1/health` reports `external_calls: true` for any non-local endpoint.

Limitation: there is no general NER model (blank spaCy), so a name without a recognised label is **not** masked. With a remote endpoint such a name would leave the machine, and the LLM cannot fill `full_name` from it (raw names are rejected). Use a local endpoint, or add a locally installed spaCy NER model, before relying on LLM name extraction.

## API

`GET /v1/health` reports processing capabilities.

`POST /v1/extract` accepts repeated multipart fields named `files`:

```bash
curl -X POST http://127.0.0.1:8000/v1/extract \
  -F 'files=@identity.pdf' \
  -F 'files=@salary.pdf'
```

The batch response includes `request_id`, aggregate `check`, a `documents` array of the specification's extraction contract plus masked text/redaction geometry, and allowlisted telemetry. Bank statements receive a per-document Not applicable status; an ID + salary pair can still be compared if a bank statement is attached.

Errors: 413 oversized request/file, 415 unsupported request content type, 422 invalid/unreadable document, 429 another request in progress. Errors never echo filenames, OCR text or parser exceptions. The frontend handles these without retaining partial results.

For predictable extraction, use labeled fields such as:

```text
IDENTITY CARD
Full name: Ravi Menon
Date of birth: 14/08/1994
ID number: ABCDE1234F
```

```text
SALARY SLIP
Employee name: Ravi Menon
Employer: Harbour Tools Pvt Ltd
Gross monthly salary: INR 82,000.00
```

The document must have at least 40 alphanumeric characters. These values are synthetic examples. English printed documents are supported; arbitrary layouts and multilingual handwriting are not validated.

## Tests and evaluation

```bash
python -m pytest backend/tests -q
python -m backend.evaluate
```

Image tests/evaluation use DejaVu Sans when present (Docker/Linux) and fall back to Arial on macOS/Windows. `python -m backend.evaluate` runs every installed engine and writes `evaluation-results.json` and `samples/20-document-test-sheet.csv`. Run `python -m samples.generate_corpus` to rebuild the 64 synthetic PDFs:

| Synthetic set | RapidOCR | Tesseract |
| --- | --- | --- |
| Clean suite: 16 text PDFs + 4 PNG scans, 12 requests | 100/100 fields · 20/20 types · 12/12 decisions | 100/100 · 20/20 · 12/12 |
| 8 degraded photo-like scans (tilt, blur, uneven light, JPEG q45) | **40/40 fields**, none below review threshold | **35/40 fields** (missed ID-card DOB/ID numbers), none flagged for review |
| Median OCR time per degraded scan (this laptop, CPU) | ~8.6 s | ~0.5 s |

Tesseract's misses were not caught by the confidence gate, which is the main reason RapidOCR is the default. Null fields are included in the field denominator. This is a synthetic smoke benchmark, not an independently annotated real-document evaluation.

Browser QA exercised uploaded photo-like scans end to end with RapidOCR, field highlighting, masking, the engine comparison, and the synthetic samples. Backend integration tests exercise the real extraction endpoint, corrupt inputs, safe audit logging and image OCR.

`requirements-tested.txt` records the tested Linux environment. It is an environment snapshot, not a portable lockfile. `backend/requirements.txt` is the supported installation manifest.

## Specification coverage and remaining work

| Area | This version | Remaining work |
| --- | --- | --- |
| Frontend | Buildless responsive JavaScript UI | React/Vite or Streamlit migration if mandatory |
| OCR and layout | PyMuPDF text first for digital PDFs; Docling layout parsing when needed; RapidOCR or Tesseract for image uploads and OCR fallback | Calibrate review thresholds on real documents; layout/table extraction for multi-page statements |
| PII | Presidio patterns + explicit labeled spans, ephemeral map | Full NER, broader multilingual/real-document recognition, adversarial recall evaluation |
| Structuring | Labeled-field parser + optional masked-text LLM (OpenAI-compatible) with token guardrails; per-field confidence and grounding | Local NER for unlabeled names; evaluation with a real model |
| Quality | Blur, contrast, brightness, EXIF orientation | DPI calibration, skew estimation/deskew |
| Poor scans | Fail closed below 40 alphanumeric chars or 60% OCR confidence | Local Qwen vision fallback with validated image redaction |
| Logs | JSON allowlist, stage timings | OpenTelemetry collector/exporter integration |
| Hosting | Private synthetic frontend; real local backend | Secure authenticated Python hosting for real documents |

**Privacy boundary:** PII detection is best effort, not a guarantee that every identifier is detected. Therefore LLM extraction is off by default and, when enabled, receives masked text only; a local endpoint is recommended. No vision egress exists. Masked-text UI may still contain unrecognized PII; exports omit OCR text and all field values. Word-box overlays are review aids, not certified irreversible image redaction. After processing, PDF page 1 is shown as a server render (returned to the operator only; never logged or exported). No images are sent to an external vision service.

Raw data exists transiently in application memory and in the operator's browser. Clearing maps/references is logical cleanup; Python cannot guarantee physical zeroization of immutable byte/string copies, OS swap or process memory dumps. Do not deploy this local-only service publicly without authentication, access controls and a reviewed operational data policy. The name result is a consistency check, not identity authentication, fraud certification or a lending decision.

## Source map

- `dist/` — complete browser UI; serves on static hosting or through FastAPI.
- `backend/app.py` — bounded in-memory intake, HTTP endpoints and response headers.
- `backend/pipeline.py` — OCR, quality checks, recognizers, token vault, field parsing and matching.
- `backend/models.py` — typed data contracts.
- `backend/tests/` — automated checks.
- `backend/evaluate.py` — reproducible synthetic benchmark.
- `Dockerfile`, `compose.yaml` — isolated local deployment.

## Supply chain

`pyproject.toml` mirrors `backend/requirements.txt` for [uv](https://docs.astral.sh/uv/) and sets `exclude-newer`, so newly published package versions are not resolved until the cutoff is moved forward deliberately. `uv.lock` was resolved under that cutoff. `uv sync && uv run pytest backend/tests -q` uses it.

## OCR engines

| | RapidOCR (default) | Tesseract |
| --- | --- | --- |
| Models | PP-OCRv6 detector (bundled in the wheel) + PP-OCRv5 English recognizer (~8 MB, fetched once, SHA-256 verified; Docker fetches it at build time) | System binary |
| Output used | Per-word text, score and box | Per-word text, confidence and box |
| Strengths | Photos, tilt, glare, small print | Speed (~15× faster on CPU) |
| Threads | `NB_OCR_THREADS` (default `min(4, CPUs)`) | Tesseract default |

Choose the OCR engine in the UI or with `POST /v1/extract?engine=rapidocr|tesseract` (`auto` = RapidOCR when installed). Digital PDFs use PyMuPDF's embedded text when the required fields are present; Docling's local layout model is reserved for PDFs where that fast path is insufficient. The review thresholds (0.85 name, 0.90 salary) still need calibration on real photographed documents.

Why the English recognizer: RapidOCR's bundled default recognizer is Chinese-first and drops spaces on degraded English scans (`name:RaviMenon`), which broke label parsing on 6 of 8 degraded scans. The first local start downloads the English model once from ModelScope; after that RapidOCR runs offline.

## Fields by document type

| Field | ID card | Salary slip | Bank statement (page 1) |
| --- | --- | --- | --- |
| `full_name` | Full name | Employee name | Account holder (joint/second holder names are masked and dropped) |
| `date_of_birth` | ISO `YYYY-MM-DD` when the printed date parses (day-first); otherwise the printed text | — | — |
| `id_number` | Masked before any model; shown only on the officer's screen | — | — |
| `employer_name` | — | Employer / company | — |
| `monthly_salary` | — | **Gross monthly salary** in rupees, number without commas (`Gross monthly salary`, `Gross pay`, `Gross earnings` labels) | **Salary credit** (net pay credited, first amount after "salary" on a credit line). Labelled "Salary credit (net)" in the UI. |

Account numbers, addresses, phone numbers and nominee names are masked and are never output fields. Bank statements are read on page 1 only.

## Troubleshooting

- **A document is called unreadable instantly.** OCR did not run. RapidOCR downloads its English model on first start; if that fails, the app now falls back to Tesseract automatically (install Tesseract, or check `/v1/health` → `ocr_engines`).
- **Changes don't show up.** Stop the running server (Ctrl+C) and start it again — Python code is loaded once at start.
- **Salary slip says the name could not be found.** The slip prints the employee name with no label (e.g. only in the header). That case is handled only when the LLM stage is enabled (`NB_LLM_PROVIDER=openrouter`).






