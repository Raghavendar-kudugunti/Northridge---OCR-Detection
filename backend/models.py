from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

DocumentType = Literal['ID card', 'Salary slip', 'Bank statement, page 1', 'Unknown']
Status = Literal['Verified', 'Mismatch', 'Incomplete', 'Not applicable']
FieldSource = Literal['labeled_parser', 'llm', 'none']
FIELD_NAMES = ('full_name', 'date_of_birth', 'id_number', 'employer_name', 'monthly_salary')

class NorthbridgeFields(BaseModel):
    full_name: str | None = None
    date_of_birth: str | None = None
    id_number: str | None = None
    employer_name: str | None = None
    monthly_salary: float | None = Field(default=None, ge=0)

class Box(BaseModel):
    """Normalized (0-1) page rectangle, top-left origin."""
    x: float
    y: float
    w: float
    h: float

class FieldEvidence(BaseModel):
    """Per-field confidence and visual grounding (where on the page the value came from)."""
    confidence: float = Field(ge=0, le=1)
    source: FieldSource
    grounded: bool
    needs_review: bool = False
    boxes: list[Box] = []

class VerificationCheck(BaseModel):
    status: Status
    reason: str
    # Review gates can only ADD a review requirement; they never change the deterministic status.
    review_required: bool = False
    review_reasons: list[str] = []  # Non-PII strings only.

class PIIMetadata(BaseModel):
    masked_before_llm: bool
    entity_counts: dict[str, int]

class PipelineTimings(BaseModel):
    ocr: int = 0
    pii: int = 0
    llm: int = 0
    rule: int = 0

class PipelineMetadata(BaseModel):
    ocr_engine: str
    used_vision_fallback: bool = False
    timings_ms: PipelineTimings
    warnings: list[str] = []
    quality: dict[str, float | None] = {}
    confidence: float | None = None
    llm_used: bool = False
    llm_provider: str = 'none'

class ExtractionResponse(BaseModel):
    request_id: str
    document_type: DocumentType
    fields: NorthbridgeFields
    field_evidence: dict[str, FieldEvidence] = {}
    check: VerificationCheck
    pii: PIIMetadata
    pipeline: PipelineMetadata
    masked_text: str = ''
    redactions: list[dict] = []
    # PDF page 1 rendered for on-screen highlighting (operator display only; never logged/exported).
    page_image: str | None = None
    # Set when the file could not be read (tolerant mode); the document is left out of the check.
    error: str | None = None

class BatchResponse(BaseModel):
    request_id: str
    check: VerificationCheck
    documents: list[ExtractionResponse]
    telemetry: list[dict]
    mode: str = 'local'
    # Five pipeline stages (pre-scan, OCR, PII masking, LLM evaluation, verification); no document values.
    stages: list[dict] = []
    metrics: dict = {}

class LLMExtraction(BaseModel):
    """Untrusted LLM output. Validated, then filtered by token-passthrough guardrails."""
    model_config = ConfigDict(extra='ignore')
    document_type: DocumentType | None = None
    full_name: str | None = None
    date_of_birth: str | None = None
    id_number: str | None = None
    employer_name: str | None = None
    monthly_salary: float | None = Field(default=None, ge=0)
