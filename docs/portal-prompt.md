# Prompt: Northbridge KYC — customer portal and organization portal

You are working in the `northbridge-kyc` repository. Read `README.md`, `backend/` and `dist/` before planning. Then propose a phased plan and list the open questions at the end of this prompt. Do not write code until I have answered them.

## 1. Business context

Northbridge Finance lends money to individual customers. Before a loan application moves forward, the customer must prove who they are and what they earn. They do this by submitting KYC documents through the company portal:

- an **ID card** (Aadhaar, PAN card or another government ID; **synthetic test documents only** during development, never real ones)
- a **salary slip**
- a **bank statement** (page 1)
- optionally, other related documents

The company verifies these documents with a **local OCR pipeline** and fixed, auditable rules. The main risk being controlled is an ID card that does not belong to the person named on the salary slip.

## 2. What already exists (reuse it, do not rewrite it)

The repository is a single-operator, stateless verification tool:

- **Backend:** FastAPI (`backend/app.py`) with `POST /v1/extract` (up to 5 files, processed in memory, nothing stored) and `GET /v1/health`.
- **Pipeline** (`backend/pipeline.py`, `backend/ocr.py`):
  - byte-signature validation;
  - PyMuPDF text layer first, then local OCR (RapidOCR by default, Tesseract optional);
  - Presidio PII masking into request-scoped placeholders;
  - labeled-field parser, plus an optional LLM that sees masked text only and is guarded by placeholder checks;
  - per-field confidence and page location;
  - a deterministic name rule;
  - a human-review gate (name confidence < 0.85, salary confidence < 0.90, poor scan, LLM guardrail failure).
- **Frontend:** a buildless HTML/CSS/JS workspace in `dist/`:
  - upload slots per document type and a progress rail;
  - a page viewer with PII masking and click-to-highlight fields;
  - Fields / Decision / Masked text / Compare / Telemetry tabs;
  - light and dark mode.
- **Tests:** 47 pytest tests plus a synthetic evaluation (`python -m backend.evaluate`).

Keep the pipeline, its privacy guarantees and its tests intact. The new work is accounts, storage, roles, workflow and two portals around it.

## 3. Verification rules (the specification — implement exactly)

### Fields

| Field | Type | Rule |
| --- | --- | --- |
| `full_name` | string or null | Compared through a stable placeholder, so the ID and slip are matched without sending the raw name to any model. |
| `date_of_birth` | string or null | ISO `YYYY-MM-DD` when the date parses. Personal data. |
| `id_number` | string or null | Masked before any model. Shown only on the officer's screen. |
| `employer_name` | string or null | Organization on the salary slip. Usually null on an ID card. |
| `monthly_salary` | number or null | Monthly **gross** in rupees, no commas. Null when the document has no salary. |

### Document types

| Document | What to extract |
| --- | --- |
| ID card | Name, date of birth, ID number. Address and phone are masked and are not output fields. |
| Salary slip | Employee name, employer, monthly gross as `monthly_salary`. |
| Bank statement, page 1 | Ignore later pages. Account holder, account number, and a salary credit if present. The account number is masked and dropped. Joint-holder names are masked and dropped. |

### The check

Normalize names by NFKC, uppercasing, stripping punctuation and collapsing spaces, then require exact equality.

| Status | When |
| --- | --- |
| Verified | ID card and salary slip both present, and the normalized names are equal. |
| Mismatch | Both present, and the names differ. |
| Incomplete | Only the ID card or only the salary slip was uploaded (fields are still extracted). |
| Not applicable | The only file is a bank statement page. |

The review gate may add "human review required", but it never changes the status above.

## 4. Two portals

Use two route groups with separate pages and separate API prefixes:

| | Customer portal | Organization portal |
| --- | --- | --- |
| UI route | `/apply` | `/staff` |
| API prefix | `/api/customer/...` | `/api/staff/...` |
| Who | The loan applicant (role `customer`) | Credit officers and reviewers (role `officer`) |

### 4.1 Customer portal (`/apply`)

A customer can:

1. Sign in and see **only their own** application.
2. Upload documents into slots:
   - ID card (required)
   - Salary slip (required)
   - Bank statement page 1
   - Other related documents (up to 2)

   The same file checks as today apply: PDF/PNG/JPEG, 10 MB each, file type checked from the bytes.
3. Submit the application and follow its status:
   - one overall status;
   - a status per document;
   - plain-language reasons, e.g. "We could not read your salary slip — please upload a clearer scan."
4. **Re-upload** a document when the system or an officer has asked for it. The previous version is kept for the audit trail, not deleted. Re-uploading starts the automated check again.

A customer must **never** see:
- any other customer's application, documents, fields, or even whether they exist;
- staff-only notes;
- masked text, telemetry, confidence scores or check internals;
- another customer's data through guessed IDs or URLs.

### 4.2 Organization portal (`/staff`)

An officer can:

1. See an **exception queue** for applications that need attention. They can filter by status (Under review, Action needed, Checking, Approved, Not approved), search by application reference, and sort by age.
2. Open an application in the existing workspace UI: document viewer with masking toggle, field highlights, Fields, Decision trace, Masked text, engine Compare.
3. Act on it:
   - **Request re-upload** of a specific document, with a customer-visible reason chosen from a list or written by the officer.
   - **Correct a field.** The original extracted value is kept, and the correction records who, when, old value, new value and reason.
   - **Approve KYC** or **Reject**, with a reason.
   - Add **internal notes** that customers never see.
4. See the history: every upload version, automated result, officer action and correction, in time order.

**Safety rule:** organization staff review every customer's documents so that customers never see each other's. Staff access to raw documents and unmasked values is logged: who viewed which document, and when.

## 5. Workflow and statuses

### Application

```
Draft (documents missing)
  → Submitted
  → Automated check
    | Re-upload requested        (automatic or by an officer)
    | Verified by parser/OCR + all required documents accepted → Approved automatically
    | LLM-supplied matching name, Mismatch or Incomplete → Under review → Approved | Rejected by staff
```

- **Re-upload requested** → the customer uploads → back to **Automated check**.
- **Automatic re-upload requests** only for:
  - an unreadable scan (the pipeline already fails closed);
  - a file whose detected type does not match its slot;
  - a missing required field.
- **Mismatch never auto-rejects.** It always goes to an officer.
- Complete applications with a Verified parser/OCR name match are approved automatically. LLM-supplied matching names, Mismatch and Incomplete results stay in the officer review queue.

### Document

- Uploaded → Processing → Extracted → Accepted, or Re-upload requested
- Uploaded → Processing → Unreadable → Re-upload requested

### What the customer sees

Map internal states to customer-friendly labels:

| Internal | Customer sees |
| --- | --- |
| Automated check | "Checking your documents" |
| Under review | "Under review" |
| Re-upload requested | "Action needed" |
| Approved | "Documents approved" |
| Rejected | "Not approved" |

## 6. Data, security and privacy

The app now **stores** documents, so these become requirements:

- **Storage**
  - Metadata goes in a database (SQLite for development; design it to move to Postgres).
  - Document files are encrypted at rest with AES-256-GCM, with the key from an environment variable or KMS and never in the repo.
  - No plaintext document bytes on disk.
  - Upload temp handling stays in memory, as today.
- **Authentication**
  - Development: seeded synthetic users and a simple session login.
  - Production: keep it pluggable (OIDC), but implementing it is out of scope.
  - Sessions use secure, HttpOnly, SameSite cookies with CSRF protection on state-changing requests.
- **Authorization**
  - Every customer endpoint is scoped to the signed-in user.
  - Every object lookup checks ownership. A foreign ID returns **404**, not 403, so existence is not revealed.
  - Staff endpoints require the `officer` role.
  - No customer endpoint lists other users or applications.
- **Audit log:** append-only. It covers uploads, re-uploads, automated results, officer views of raw documents, field corrections and decisions. It holds IDs and actions only — no PII in log lines.
- **Keep the existing guarantees:**
  - no PII in telemetry or application logs;
  - the LLM (if enabled) sees masked text only;
  - errors never echo file names, OCR text or exceptions;
  - CSP and `no-store` headers stay.
- **Retention:** a configurable retention period and a staff-triggered deletion. Deleting removes the encrypted files and keeps a minimal audit record.

## 7. Out of scope

- Real Aadhaar/PAN verification against government services.
- Credit scoring or loan decisions beyond KYC document approval.
- Payments.
- Real email/SMS. Stub notifications behind an interface.
- Native mobile apps. The web portals must work on phones.

## 8. Deliverables and acceptance criteria

1. **Customer portal** at `/apply`. Mobile-first, reusing the current styling and dark mode.
2. **Organization portal** at `/staff`: the review queue, plus the existing workspace with officer actions.
3. **Database models and migrations** (or a create script) for users, applications, document versions, extraction results, officer actions, corrections, notes and audit events.
4. **Seed script** with synthetic customers, officers and documents covering all four check statuses and a re-upload cycle.
5. **Tests.** Keep all existing tests passing, and add tests for:
   - customer A cannot read, list, re-upload or infer customer B's application or documents, including by guessed IDs (expect 404);
   - customers cannot call any `/api/staff` endpoint;
   - the re-upload flow: request → upload → new version → automated check runs again → previous version kept;
   - status transitions, including automatic approval of complete Verified applications and "Mismatch never auto-rejects";
   - field corrections keep the original value and write an audit entry;
   - files on disk are encrypted (stored bytes ≠ uploaded bytes and cannot be read without the key);
   - no PII appears in logs or audit lines.
6. **README updates:** how to run both portals, the role model, statuses, storage and encryption, and the retention setting.

Work in phases (data model and auth → customer portal → staff portal → hardening and tests). After each phase, show what changed and what was tested.

## 9. Open questions — ask me before building

1. **Authentication:** are dev-only seeded logins acceptable for now? Which identity provider is the real target?
2. **Database:** is SQLite for development fine? Is Postgres the production target?
3. **Retention:** how long should documents and results be kept after approval or rejection?
4. **After approval:** can a customer still re-upload or add documents, or is the application locked?
5. **Approval rules:** does approval need a second officer (four-eyes) for a Mismatch or for corrected fields?
6. **Applications per customer:** one active application at a time, or several?
7. **Notifications:** which channel should be stubbed first (email or SMS)?
