"""Test di integrazione: pipeline completa con API mockate.

Questi test simulano Factum Parse API e Fatture in Cloud API v2
usando httpx transport mock, permettendo test offline completi
di tutti i percorsi (successo, errori, deduplicazione).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from factum_fic.config import Settings
from factum_fic.core.factum_client import FactumClient
from factum_fic.core.fic_client import FICClient
from factum_fic.core.mapper import Mapper
from factum_fic.core.models import DocumentStatus, DocumentType
from factum_fic.core.pipeline import process_file
from factum_fic.storage.queue import QueueStore

_FIXTURES = Path(__file__).parent / "fixtures"

# ── Mock transports ──────────────────────────────────────────────────────────


def _mock_factum_transport() -> httpx.MockTransport:
    """Mock Factum Parse API con risposte realistiche."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})

        if request.url.path == "/v1/parse":
            body = json.loads(request.content)
            text = body.get("text", "").lower()

            # Riconosci il fornitore dal testo
            if "digitalocean" in text or "digitalocean inc" in text:
                return httpx.Response(
                    200,
                    json={
                        "job_id": "mock-job-001",
                        "status": "done",
                        "document_type": "invoice",
                        "result": {
                            "document_type": "invoice",
                            "currency": "USD",
                            "total": 59.00,
                            "supplier_name": "DigitalOcean Inc.",
                            "supplier_vat": "",
                            "supplier_country": "US",
                            "supplier_address": "101 Avenue of the Americas, New York, NY 10013, USA",
                            "invoice_date": "2026-08-01",
                            "invoice_number": "INV-2026-08101",
                            "items": [
                                {"description": "Droplet Basic Plan", "amount": 12.00},
                                {"description": "Droplet Basic Plan", "amount": 12.00},
                                {"description": "Managed Database", "amount": 15.00},
                                {"description": "Storage 100GB", "amount": 10.00},
                                {"description": "Load Balancer", "amount": 10.00},
                            ],
                        },
                    },
                )

            if "aruba" in text or "aruba s.p.a" in text:
                return httpx.Response(
                    200,
                    json={
                        "job_id": "mock-job-002",
                        "status": "done",
                        "document_type": "invoice",
                        "result": {
                            "document_type": "invoice",
                            "currency": "EUR",
                            "total": 1464.00,
                            "supplier_name": "Aruba S.p.A.",
                            "supplier_vat": "IT01573850516",
                            "supplier_country": "IT",
                            "supplier_address": "Via San Clemente, 53, 24036 Ponte San Pietro BG",
                            "invoice_date": "2026-08-15",
                            "invoice_number": "2026/FP/001234",
                            "items": [
                                {"description": "Server dedicato Atom C2750", "amount": 960.00},
                                {"description": "Backup automatico 500GB", "amount": 240.00},
                            ],
                        },
                    },
                )

            # Fallback: unknown supplier
            return httpx.Response(
                200,
                json={
                    "job_id": "mock-job-000",
                    "status": "done",
                    "document_type": "invoice",
                    "result": {
                        "document_type": "invoice",
                        "currency": "EUR",
                        "total": 100.00,
                        "supplier_name": "Fornitore Sconosciuto",
                        "supplier_vat": "",
                        "supplier_country": "XX",
                        "supplier_address": "",
                        "invoice_date": "2026-01-01",
                        "invoice_number": "INV-000",
                        "items": [],
                    },
                },
            )

        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _mock_fic_transport() -> httpx.MockTransport:
    """Mock Fatture in Cloud v2 API."""

    def handler(request: httpx.Request) -> httpx.Response:
        # Health check → user/info
        if request.url.path == "/user/info" and request.method == "GET":
            return httpx.Response(
                200,
                json={"data": {"id": 2107961, "email": "test@test.com"}},
            )

        # Search supplier
        if request.url.path.endswith("/entities/suppliers") and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": 42,
                            "name": "DigitalOcean Inc.",
                            "vat_number": None,
                            "country_iso": "US",
                        },
                    ],
                },
            )

        # Create supplier
        if request.url.path.endswith("/entities/suppliers") and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "data": {
                        "id": 99,
                        "name": "Nuovo Fornitore",
                        "vat_number": None,
                    },
                },
            )

        # Create expense / received document
        if request.url.path.endswith("/received_documents") and request.method == "POST":
            body = json.loads(request.content)
            return httpx.Response(
                201,
                json={
                    "data": {
                        "id": 12345,
                        "type": body.get("data", {}).get("type", "expense"),
                        "status": "confirmed",
                    },
                },
            )

        # Upload attachment to received document
        if (
            "/received_documents/" in request.url.path
            and request.url.path.endswith("/attachment")
            and request.method == "POST"
        ):
            # Estrai document_id dal path
            return httpx.Response(
                201,
                json={
                    "data": {
                        "id": 999,
                        "filename": "allegato.pdf",
                        "url": "https://mock.fic.test/attachment/999",
                    },
                },
            )

        return httpx.Response(404)

    return httpx.MockTransport(handler)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_settings() -> Settings:
    """Settings con valori mock."""
    return Settings(  # type: ignore[call-arg]
        FACTUM_API_URL="https://mock.factum.test",
        FACTUM_API_KEY="mock-key",
        FIC_BASE_URL="https://mock.fic.test",
        FIC_API_KEY="mock-fic-token",
        FIC_COMPANY_ID="99999",
        WATCH_DIR="/tmp",
    )


@pytest.fixture
def mock_factum(mock_settings: Settings) -> FactumClient:
    """FactumClient con transport mock."""
    client = FactumClient(mock_settings)
    client._client = httpx.AsyncClient(
        base_url="https://mock.factum.test",
        transport=_mock_factum_transport(),
    )
    return client


@pytest.fixture
def mock_fic(mock_settings: Settings) -> FICClient:
    """FICClient con transport mock."""
    client = FICClient(mock_settings)
    client._client = httpx.AsyncClient(
        base_url="https://mock.fic.test",
        transport=_mock_fic_transport(),
    )
    return client


@pytest.fixture
def mapper() -> Mapper:
    return Mapper()


@pytest.fixture
def queue(tmp_path: Path) -> QueueStore:
    """QueueStore con DB temporaneo."""
    return QueueStore(db_path=tmp_path / "test_queue.db")


# ── Test flusso SaaS estera (DigitalOcean → US) ─────────────────────────────


async def test_pipeline_saas_estero(
    mock_factum: FactumClient,
    mock_fic: FICClient,
    mapper: Mapper,
    queue: QueueStore,
    mock_settings: Settings,
) -> None:
    """File SaaS USA → Factum OK → autofattura TD17 → FIC expense creato."""
    path = _FIXTURES / "sample_saas_invoice.txt"
    assert path.exists(), f"Fixture non trovata: {path}"

    result = await process_file(
        path,
        factum=mock_factum,
        fic=mock_fic,
        mapper=mapper,
        queue=queue,
        settings=mock_settings,
    )

    # Verifica hash e queue
    assert result.file.sha256 == "907f48e0941d05631ba384dcd5df120832b3d59ce9665e825224898e09e3d4ae"
    assert queue.exists(result.file.sha256)
    assert result.file.filename == "sample_saas_invoice.txt"

    # Verifica Factum parsing
    assert result.factum_status == "done"
    assert result.factum_error is None

    # Verifica mapping fiscale: fornitore USA senza P.IVA → SELF_INVOICE
    assert result.document_type == DocumentType.SELF_INVOICE

    # Verifica FIC
    assert result.fic_status == "created"
    assert result.fic_id == 12345
    assert result.status == DocumentStatus.RECORDED


# ── Test flusso fornitore italiano (Aruba → IT) ──────────────────────────────


async def test_pipeline_italiano(
    mock_factum: FactumClient,
    mock_fic: FICClient,
    mapper: Mapper,
    queue: QueueStore,
    mock_settings: Settings,
) -> None:
    """File fattura italiana → Factum OK → spesa diretta → FIC."""
    path = _FIXTURES / "sample_italian_invoice.txt"
    assert path.exists()

    result = await process_file(
        path,
        factum=mock_factum,
        fic=mock_fic,
        mapper=mapper,
        queue=queue,
        settings=mock_settings,
    )

    # Verifica mapping fiscale: P.IVA italiana → EXPENSE
    assert result.document_type == DocumentType.EXPENSE
    assert result.status == DocumentStatus.RECORDED
    assert result.fic_id == 12345


# ── Test deduplicazione ─────────────────────────────────────────────────────


async def test_deduplicazione(
    mock_factum: FactumClient,
    mock_fic: FICClient,
    mapper: Mapper,
    queue: QueueStore,
    mock_settings: Settings,
) -> None:
    """Stesso file due volte → secondo giro SKIPPED."""
    path = _FIXTURES / "sample_saas_invoice.txt"

    # Prima volta: processato
    result1 = await process_file(
        path,
        factum=mock_factum,
        fic=mock_fic,
        mapper=mapper,
        queue=queue,
        settings=mock_settings,
    )
    assert result1.status == DocumentStatus.RECORDED

    # Seconda volta: SKIPPED (deduplicazione)
    result2 = await process_file(
        path,
        factum=mock_factum,
        fic=mock_fic,
        mapper=mapper,
        queue=queue,
        settings=mock_settings,
    )
    assert result2.status == DocumentStatus.SKIPPED
    assert result2.fic_status == "duplicate"


# ── Test errore Factum (testo vuoto) ─────────────────────────────────────────


async def test_factum_error_empty_text(
    mock_factum: FactumClient,
    mock_fic: FICClient,
    mapper: Mapper,
    queue: QueueStore,
    mock_settings: Settings,
) -> None:
    """File vuoto → FAILED con factum_status=empty_text."""
    empty_path = _FIXTURES / "_empty_test_.txt"
    empty_path.write_text("")
    try:
        result = await process_file(
            empty_path,
            factum=mock_factum,
            fic=mock_fic,
            mapper=mapper,
            queue=queue,
            settings=mock_settings,
        )
        assert result.status == DocumentStatus.FAILED
        assert result.factum_status == "empty_text"
    finally:
        empty_path.unlink(missing_ok=True)


# ── Test FIC error (trasporto rotto) ─────────────────────────────────────────


async def test_fic_error(
    mock_factum: FactumClient,
    mapper: Mapper,
    queue: QueueStore,
    mock_settings: Settings,
) -> None:
    """FIC non raggiungibile → FAILED con fic_status=supplier_error."""
    # FIC client SENZA transport (connessione rifiutata)
    broken_fic = FICClient(mock_settings)
    # Non assegniamo transport → connessione reale fallirà

    path = _FIXTURES / "sample_saas_invoice.txt"
    result = await process_file(
        path,
        factum=mock_factum,
        fic=broken_fic,
        mapper=mapper,
        queue=queue,
        settings=mock_settings,
    )
    assert result.status == DocumentStatus.FAILED
    assert result.fic_status == "supplier_error"
    assert result.fic_error is not None


async def test_pipeline_with_attachment(
    mock_factum: FactumClient,
    mock_fic: FICClient,
    mapper: Mapper,
    queue: QueueStore,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """File PDF → Factum OK → FIC expense creato + allegato caricato."""
    # Crea un finto PDF (solo contenuto testuale)
    pdf_path = tmp_path / "test_invoice.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test invoice content digitalocean inc.")

    result = await process_file(
        pdf_path,
        factum=mock_factum,
        fic=mock_fic,
        mapper=mapper,
        queue=queue,
        settings=mock_settings,
    )

    assert result.status == DocumentStatus.RECORDED
    assert result.fic_id == 12345
    assert result.factum_status == "done"
