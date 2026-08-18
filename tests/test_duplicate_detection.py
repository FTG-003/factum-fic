"""Test: controllo duplicati robusto e risoluzione fornitore.

Verifica che:
1. Due documenti diversi dello stesso fornitore nella stessa data
   NON vengano scambiati per duplicati (falso positivo).
2. Due documenti di fornitori diversi nella stessa data
   NON vengano scambiati per duplicati.
3. Il match per numero documento sia esatto (case-insensitive).
4. La ricerca fornitore per P.IVA funzioni con fallback per nome.
5. Supplier creation via POST funzioni quando VAT non trovato.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from factum_fic.config import Settings
from factum_fic.core.fic_client import FICClient
from factum_fic.core.models import (
    FICCreateSupplierRequest,
)
from factum_fic.core.pipeline import _check_fic_exists
from factum_fic.storage.queue import QueueStore


# ── Mock transport per FIC con 2 documenti nella stessa data ────────────────


def _mock_fic_duplicate_check() -> httpx.MockTransport:
    """Mock FIC che restituisce 2 documenti nella stessa data per lo stesso fornitore.

    Simula lo scenario: OVH e Scaleway, stessa data, stesso entity_id.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        # ── Search supplier (VAT) ──────────────────────────────────────────
        if request.url.path.endswith("/entities/suppliers") and request.method == "GET":
            field = request.url.params.get("field", "")
            query = request.url.params.get("query", "")

            # Cerca per P.IVA italiana → trovato (Aruba)
            if field == "vat_number" and "IT" in query:
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": 42,
                                "name": "Aruba S.p.A.",
                                "vat_number": "IT01573850516",
                                "country_iso": "IT",
                            },
                        ],
                    },
                )

            # Cerca per P.IVA estera → NON TROVATO
            if field == "vat_number" and query:
                return httpx.Response(
                    200,
                    json={"data": []},
                )

            # Cerca per nome → trovato se matcha
            if field == "name":
                if "scaleway" in query.lower():
                    # Scaleway non trovato neanche per nome (nuovo fornitore)
                    return httpx.Response(200, json={"data": []})
                if "ovh" in query.lower():
                    return httpx.Response(
                        200,
                        json={
                            "data": [
                                {
                                    "id": 101,
                                    "name": "OVH SAS",
                                    "vat_number": None,
                                    "country_iso": "FR",
                                },
                            ],
                        },
                    )
                # Default: trovato
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": 99,
                                "name": query,
                                "vat_number": None,
                            },
                        ],
                    },
                )

            return httpx.Response(200, json={"data": []})

        # ── Create supplier ────────────────────────────────────────────────
        if request.url.path.endswith("/entities/suppliers") and request.method == "POST":
            body = json.loads(request.content)
            data = body.get("data", {})
            return httpx.Response(
                201,
                json={
                    "data": {
                        "id": 777,
                        "name": data.get("name", "Nuovo Fornitore"),
                        "vat_number": data.get("vat_number"),
                    },
                },
            )

        # ── Search received_documents (duplicate check) ────────────────────
        if request.url.path.endswith("/received_documents") and request.method == "GET":
            entity_id = request.url.params.get("entity_id", "")
            date_from = request.url.params.get("date_from", "")
            date_to = request.url.params.get("date_to", "")

            # Se entity_id=101 (OVH), restituisci un documento OVH esistente
            if entity_id == "101" and date_from == "2026-08-01":
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": 430594826,
                                "description": "OVH SAS — n. INV-OVH-2026-001 — del 2026-08-01",
                                "invoice_number": "INV-OVH-2026-001",
                                "amount_net": 100.00,
                                "amount_vat": 22.00,
                                "amount_gross": 122.00,
                                "date": "2026-08-01",
                                "entity_id": 101,
                            },
                        ],
                    },
                )

            # Se entity_id=42 (Aruba), restituisci documenti Aruba
            if entity_id == "42":
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "id": 100,
                                "description": "Aruba S.p.A. — n. 2026/FP/001 — del 2026-08-15",
                                "invoice_number": "2026/FP/001",
                                "amount_net": 1000.00,
                                "amount_vat": 220.00,
                                "amount_gross": 1220.00,
                                "date": "2026-08-15",
                                "entity_id": 42,
                            },
                            {
                                "id": 101,
                                "description": "Aruba S.p.A. — n. 2026/FP/002 — del 2026-08-15",
                                "invoice_number": "2026/FP/002",
                                "amount_net": 500.00,
                                "amount_vat": 110.00,
                                "amount_gross": 610.00,
                                "date": "2026-08-15",
                                "entity_id": 42,
                            },
                        ],
                    },
                )

            # Stessa data, entity diverso → nessun documento
            return httpx.Response(200, json={"data": []})

        # ── Create expense (received_document POST) ────────────────────────
        if request.url.path.endswith("/received_documents") and request.method == "POST":
            body = json.loads(request.content)
            data = body.get("data", {})
            return httpx.Response(
                201,
                json={
                    "data": {
                        "id": 888,
                        "type": data.get("type", "expense"),
                        "status": "confirmed",
                    },
                },
            )

        # ── Health check ───────────────────────────────────────────────────
        if request.url.path == "/user/info" and request.method == "GET":
            return httpx.Response(
                200, json={"data": {"id": 1, "email": "test@test.com"}},
            )

        return httpx.Response(404)

    return httpx.MockTransport(handler)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_settings(tmp_path: Path) -> Settings:
    """Settings mock."""
    return Settings(  # type: ignore[call-arg]
        FACTUM_API_URL="https://mock.factum.test",
        FACTUM_API_KEY="mock-key",
        FIC_BASE_URL="https://mock.fic.test",
        FIC_TOKEN="mock-fic-token",
        FIC_COMPANY_ID="99999",
        WATCH_DIR="/tmp",
        BASE_STORAGE_DIR=str(tmp_path / "storage"),
    )


@pytest.fixture
def fic_client(mock_settings: Settings) -> FICClient:
    """FICClient con transport mock."""
    client = FICClient(mock_settings)
    client._client = httpx.AsyncClient(
        base_url="https://mock.fic.test",
        transport=_mock_fic_duplicate_check(),
    )
    return client


# ── Test: Falso positivo duplicati ──────────────────────────────────────────


async def test_search_document_same_supplier_same_date_different_invoices(
    fic_client: FICClient,
) -> None:
    """Due fatture Aruba stessa data, numeri diversi: NON devono matchare come duplicato.

    Se cerco la fattura 2026/FP/003 (nuova), ma in FIC ci sono già
    2026/FP/001 e 2026/FP/002, search_document deve restituire None.
    """
    result = await fic_client.search_document(
        entity_id=42,
        description="Aruba S.p.A. — n. 2026/FP/003 — del 2026-08-15",
        date="2026-08-15",
        invoice_number="2026/FP/003",
    )
    assert result is None, "Fattura con numero diverso non deve matchare"


async def test_search_document_exact_match_by_number(
    fic_client: FICClient,
) -> None:
    """Match esatto per numero documento: deve tornare il documento.

    Cerco 2026/FP/001 → deve matchare il primo documento Aruba.
    """
    result = await fic_client.search_document(
        entity_id=42,
        description="Aruba S.p.A. — n. 2026/FP/001 — del 2026-08-15",
        date="2026-08-15",
        invoice_number="2026/FP/001",
    )
    assert result is not None
    assert result.get("id") == 100
    assert result.get("invoice_number") == "2026/FP/001"


async def test_search_document_match_by_description(
    fic_client: FICClient,
) -> None:
    """Match per descrizione (case-insensitive): deve tornare il documento."""
    result = await fic_client.search_document(
        entity_id=42,
        description="aruba s.p.a. — n. 2026/FP/002 — del 2026-08-15",
        date="2026-08-15",
        invoice_number="2026/FP/002",
    )
    assert result is not None
    assert result.get("id") == 101


async def test_search_document_different_supplier_same_date(
    fic_client: FICClient,
) -> None:
    """Fornitori diversi, stessa data: nessun match.

    Entity_id=999 (nuovo fornitore) non ha documenti in FIC.
    """
    result = await fic_client.search_document(
        entity_id=999,
        description="Nuovo Fornitore — n. INV-001 — del 2026-08-15",
        date="2026-08-15",
        invoice_number="INV-001",
    )
    assert result is None


async def test_search_document_without_description_or_number(
    fic_client: FICClient,
) -> None:
    """Senza descrizione né numero: restituisce None (non può matchare)."""
    result = await fic_client.search_document(
        entity_id=42,
        date="2026-08-15",
    )
    assert result is None


# ── Test: Risoluzione fornitore ─────────────────────────────────────────────


async def test_search_supplier_vat_first_then_name(
    fic_client: FICClient,
) -> None:
    """Cerca per P.IVA prima, poi per nome come fallback.

    Scaleway (P.IVA FR estera) non trovato per VAT, e il nome non matcha
    → restituisce None (fornitore nuovo).
    """
    result = await fic_client.search_supplier(
        name="Scaleway SAS",
        vat_number="FR12345678901",
    )
    assert result is None, "Scaleway non deve essere trovato (nuovo fornitore)"


async def test_search_supplier_vat_match(
    fic_client: FICClient,
) -> None:
    """P.IVA italiana match: restituisce il fornitore Aruba."""
    result = await fic_client.search_supplier(
        name="Aruba S.p.A.",
        vat_number="IT01573850516",
    )
    assert result is not None
    assert result.get("id") == 42


async def test_search_supplier_fallback_by_name(
    fic_client: FICClient,
) -> None:
    """VAT non trovato, ma nome matcha: restituisce il fornitore OVH."""
    result = await fic_client.search_supplier(
        name="OVH SAS",
        vat_number="FR99999999999",
    )
    assert result is not None
    assert result.get("id") == 101
    assert result.get("name") == "OVH SAS"


# ── Test: Creazione fornitore quando non trovato ────────────────────────────


async def test_create_supplier_when_not_found(
    fic_client: FICClient,
) -> None:
    """Crea un fornitore nuovo su FIC quando non trovato per VAT/nome.

    Simula: Scaleway non esiste → POST /entities/suppliers → id=777.
    """
    supplier = FICCreateSupplierRequest(
        name="Scaleway SAS",
        vat_number="FR12345678901",
        country_iso="FR",
        address="10 Rue de l'Innovation, 75010 Paris, France",
        entity_type="others",
    )
    created = await fic_client.create_supplier(supplier)
    data = created.get("data", {})
    assert data.get("id") == 777
    assert data.get("vat_number") == "FR12345678901"


# ── Test: _check_fic_exists non ferma documenti diversi ─────────────────────


async def test_check_fic_exists_ovh_existing_is_skipped(
    fic_client: FICClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """OVH già presente: _check_fic_exists restituisce PipelineResult (skip)."""
    # Costruisci un expense simile a OVH
    expense = _make_expense_ovh()
    queue = QueueStore(db_path=tmp_path / "test_queue.db")
    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake content")

    result = await _check_fic_exists(
        fic=fic_client,
        expense=expense,
        sha="abc123ovh",
        queue=queue,
        path=pdf_path,
        settings=mock_settings,
    )
    assert result is not None, "OVH deve essere riconosciuto come duplicato"
    assert result.fic_id == 430594826


async def test_check_fic_exists_scaleway_new_is_not_skipped(
    fic_client: FICClient,
    mock_settings: Settings,
    tmp_path: Path,
) -> None:
    """Scaleway non presente: _check_fic_exists restituisce None (procedi)."""
    expense = _make_expense_scaleway()
    queue = QueueStore(db_path=tmp_path / "test_queue.db")
    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake content")

    result = await _check_fic_exists(
        fic=fic_client,
        expense=expense,
        sha="def456scw",
        queue=queue,
        path=pdf_path,
        settings=mock_settings,
    )
    assert result is None, "Scaleway nuovo non deve essere bloccato come duplicato"


# ── Helper: costruttori expense fittizi ─────────────────────────────────────


def _make_expense_ovh():
    """Crea un expense fittizio per OVH (esistente su FIC)."""
    from factum_fic.core.models import FICCreateExpenseRequest

    return FICCreateExpenseRequest(
        entity_id=101,
        entity=None,
        date="2026-08-01",
        description="OVH SAS — n. INV-OVH-2026-001 — del 2026-08-01",
        invoice_number="INV-OVH-2026-001",
        amount_net=100.0,
        amount_vat=22.0,
        amount_gross=122.0,
    )


def _make_expense_scaleway():
    """Crea un expense fittizio per Scaleway (non presente su FIC)."""
    from factum_fic.core.models import FICCreateExpenseRequest

    return FICCreateExpenseRequest(
        entity_id=777,
        entity=None,
        date="2026-08-01",
        description="Scaleway SAS — n. INV-SCW-2026-001 — del 2026-08-01",
        invoice_number="INV-SCW-2026-001",
        amount_net=50.0,
        amount_vat=11.0,
        amount_gross=61.0,
    )