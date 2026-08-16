"""Pydantic v2 schemas per Factum e Fatture in Cloud."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ── Enums ────────────────────────────────────────────────────────────────────


class Tier(StrEnum):
    """Tier di servizio Factum (per rate limiting)."""

    STARTER = "starter"
    PRO = "pro"
    CUSTOM = "custom"
    LEGACY = "legacy"


class Currency(StrEnum):
    """Codici ISO 4217 supportati."""

    EUR = "EUR"
    USD = "USD"
    GBP = "GBP"
    CHF = "CHF"
    JPY = "JPY"


class DocumentType(StrEnum):
    """Tipologia documento FIC."""

    EXPENSE = "expense_document"
    SELF_INVOICE = "self_invoice"
    UNKNOWN = "unknown"


class DocumentStatus(StrEnum):
    """Stato documento dopo elaborazione."""

    PENDING = "pending"
    PARSED = "parsed"
    RECORDED = "recorded"
    FAILED = "failed"
    SKIPPED = "skipped"


# ── Factum schemas ───────────────────────────────────────────────────────────


class FactumParseRequest(BaseModel):
    """Request per Factum Parse API /v1/parse."""

    text: str
    doc_type: str = "auto"


class FactumSupplier(BaseModel):
    """Fornitore estratto da Factum."""

    name: str = ""
    vat_number: str = ""
    tax_code: str = ""
    country_iso: str = ""
    address: str = ""
    email: str = ""


class FactumTotals(BaseModel):
    """Dati economici estratti da Factum."""

    amount_net: float = 0.0
    amount_vat: float = 0.0
    amount_gross: float = 0.0
    currency: str = "EUR"
    vat_percentage: float = 0.0
    has_iva: bool = True
    date: str = ""
    invoice_number: str = ""


class FactumParseResult(BaseModel):
    """Risultato parsing da Factum (envelope v2).

    I campi legacy V1 (total, supplier_name) possono essere vuoti;
    i dati reali sono in payload.content (envelope v2).
    """

    model_config = ConfigDict(extra="allow")  # cattura payload.* e altri extra

    document_type: str = ""
    currency: str = "EUR"
    total: float = 0.0
    supplier_name: str = ""
    supplier_vat: str = ""
    supplier_country: str = ""
    supplier_address: str = ""
    invoice_date: str = ""
    invoice_number: str = ""
    items: list[dict[str, Any]] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class FactumResponse(BaseModel):
    """Risposta completa da Factum /v1/parse.

    I campi legacy V1 (result.total, result.supplier_name) possono essere
    vuoti se il server restituisce envelope v2 con i dati in payload.content.
    """

    model_config = ConfigDict(extra="allow")  # cattura payload.* e altri extra

    job_id: str = ""
    status: str = ""
    document_type: str = ""
    result: FactumParseResult | None = None
    error: str | None = None


# ── Fatture in Cloud schemas ─────────────────────────────────────────────────


class FICEntity(BaseModel):
    """Entity (fornitore/cliente) su Fatture in Cloud."""

    id: int | None = None
    name: str = ""
    vat_number: str | None = None
    tax_code: str | None = None
    country_iso: str = "IT"
    address: str | None = None
    entity_type: str = "company"


class FICCreateSupplierRequest(BaseModel):
    """Creazione fornitore su FIC."""

    name: str
    vat_number: str | None = None
    tax_code: str | None = None
    country_iso: str = "IT"
    address: str | None = None
    entity_type: str = "company"


class FICCreateExpenseRequest(BaseModel):
    """Request per creare un documento di spesa su FIC v2."""

    entity_id: int | None = None
    entity: FICCreateSupplierRequest | None = None
    date: str = ""
    due_date: str = ""
    category: str = "Altri costi"
    description: str = ""
    amount_net: float = 0.0
    amount_vat: float = 0.0
    amount_gross: float | None = None
    currency: str = "EUR"
    vat_percentage: float = 0.0
    has_iva: bool = True
    is_autofattura: bool = False
    notes: str = ""


class FICExpenseResponse(BaseModel):
    """Risposta da FIC dopo creazione spesa."""

    id: int = 0
    type: str = ""
    status: str = ""


# ── Pipeline schemas ─────────────────────────────────────────────────────────


class FileEvent(BaseModel):
    """Evento file processato dalla pipeline."""

    path: str = ""
    sha256: str = ""
    filename: str = ""
    size_bytes: int = 0


class PipelineResult(BaseModel):
    """Risultato finale della pipeline file → Factum → FIC."""

    file: FileEvent
    factum_status: str = ""
    factum_error: str | None = None
    fic_status: str = ""
    fic_id: int | None = None
    fic_error: str | None = None
    document_type: DocumentType = DocumentType.UNKNOWN
    status: DocumentStatus = DocumentStatus.PENDING
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
