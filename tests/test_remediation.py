"""Test per le remediation audit: SELF_INVOICE_PENDING, lock atomico, strict currency.

Verifica:
1. ``SELF_INVOICE_PENDING`` — spesa creata, autofattura fallita
2. ``acquire()`` — lock atomico previene doppia elaborazione
3. ``strict_currency`` — CurrencyConversionError bloccante
"""

from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pytest

from factum_fic.config import Settings
from factum_fic.core.factum_client import FactumClient
from factum_fic.core.fic_client import FICClient
from factum_fic.core.mapper import Mapper
from factum_fic.core.models import (
    CurrencyConversionError,
    DocumentStatus,
)
from factum_fic.core.pipeline import process_file
from factum_fic.storage.queue import QueueStore

# ── Fixtures condivise ───────────────────────────────────────────────────────

_FIXTURES = Path(__file__).parent / "fixtures"


def _mock_factum_transport() -> httpx.MockTransport:
    """Factum OK — fornitore USA → reverse charge."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/parse" and request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "job_id": "mock-job-001",
                    "status": "done",
                    "document_type": "invoice",
                    "result": {
                        "document_type": "invoice",
                        "currency": "USD",
                        "total": 59.0,
                        "supplier_name": "DigitalOcean Inc.",
                        "supplier_vat": "",
                        "supplier_country": "US",
                        "supplier_address": "101 Avenue of the Americas",
                        "invoice_date": "2026-08-01",
                        "invoice_number": "INV-2026-08101",
                    },
                },
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _mock_fic_transport_si_fail() -> httpx.MockTransport:
    """FIC: expense OK, issued_documents fallisce 422."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/info" and request.method == "GET":
            return httpx.Response(200, json={"data": {"id": 1}})
        if request.url.path.endswith("/entities/suppliers") and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": [{
                        "id": 42,
                        "name": "DigitalOcean Inc.",
                        "vat_number": None,
                        "country_iso": "US",
                    }],
                },
            )
        if request.url.path.endswith("/entities/suppliers") and request.method == "POST":
            return httpx.Response(201, json={"data": {"id": 99, "name": "Fornitore"}})
        if request.url.path.endswith("/received_documents") and request.method == "POST":
            return httpx.Response(
                201,
                json={"data": {"id": 12345, "type": "expense", "status": "confirmed"}},
            )
        if request.url.path.endswith("/issued_documents") and request.method == "POST":
            return httpx.Response(422, json={"error": "ei_data required"})
        if request.url.path.endswith("/payment_accounts") and request.method == "GET":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _mock_fic_transport_ok() -> httpx.MockTransport:
    """FIC: tutto OK — expense + issued_document."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/info" and request.method == "GET":
            return httpx.Response(200, json={"data": {"id": 1}})
        if request.url.path.endswith("/entities/suppliers") and request.method == "GET":
            return httpx.Response(
                200, json={"data": [{"id": 42, "name": "DigitalOcean Inc."}]},
            )
        if request.url.path.endswith("/received_documents") and request.method == "POST":
            return httpx.Response(
                201, json={"data": {"id": 12345, "type": "expense"}},
            )
        if request.url.path.endswith("/issued_documents") and request.method == "POST":
            return httpx.Response(
                201, json={"data": {"id": 67890, "type": "self_supplier_invoice"}},
            )
        if request.url.path.endswith("/payment_accounts") and request.method == "GET":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


# ── Fixtures pytest ──────────────────────────────────────────────────────────


@pytest.fixture
def mock_settings(tmp_path: Path) -> Settings:
    storage_dir = tmp_path / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        FACTUM_API_URL="https://mock.factum.test",
        FACTUM_API_KEY="mock-key",
        FIC_BASE_URL="https://mock.fic.test",
        FIC_API_KEY="mock-fic-token",
        FIC_COMPANY_ID="99999",
        WATCH_DIR=str(tmp_path),
        BASE_STORAGE_DIR=str(storage_dir),
        FIC_GENERATE_SELF_INVOICE="true",
        FIC_SELF_INVOICE_NUMERATION="/TD",
        FIC_SELF_INVOICE_VAT_VALUE="22",
    )


@pytest.fixture
def mock_factum(mock_settings: Settings) -> FactumClient:
    client = FactumClient(mock_settings)
    client._client = httpx.AsyncClient(
        base_url="https://mock.factum.test",
        transport=_mock_factum_transport(),
    )
    return client


@pytest.fixture
def mock_fic_si_fail(mock_settings: Settings) -> FICClient:
    client = FICClient(mock_settings)
    client._client = httpx.AsyncClient(
        base_url="https://mock.fic.test",
        transport=_mock_fic_transport_si_fail(),
    )
    return client


@pytest.fixture
def mock_fic_ok(mock_settings: Settings) -> FICClient:
    client = FICClient(mock_settings)
    client._client = httpx.AsyncClient(
        base_url="https://mock.fic.test",
        transport=_mock_fic_transport_ok(),
    )
    return client


@pytest.fixture
def queue(tmp_path: Path) -> QueueStore:
    return QueueStore(db_path=tmp_path / "test_queue.db")


@pytest.fixture
def mapper() -> Mapper:
    return Mapper()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. SELF_INVOICE_PENDING — spesa OK, autofattura KO
# ═══════════════════════════════════════════════════════════════════════════════


class TestSelfInvoicePending:
    """Test del recupero parziale SELF_INVOICE_PENDING."""

    @pytest.mark.asyncio
    async def test_pending_state_on_failure(
        self,
        mock_factum: FactumClient,
        mock_fic_si_fail: FICClient,
        mapper: Mapper,
        queue: QueueStore,
        mock_settings: Settings,
        tmp_path: Path,
    ) -> None:
        """Spesa creata, autofattura KO → PARTIAL + SELF_INVOICE_PENDING."""
        src = _FIXTURES / "sample_saas_invoice.txt"
        path = tmp_path / "test_si_fail.txt"
        shutil.copy2(src, path)

        result = await process_file(
            path,
            factum=mock_factum,
            fic=mock_fic_si_fail,
            mapper=mapper,
            queue=queue,
            settings=mock_settings,
        )

        assert result.status == DocumentStatus.PARTIAL
        assert result.fic_id == 12345
        assert result.fic_status == "self_invoice_pending"

        record = queue.get(result.file.sha256)
        assert record is not None
        assert record["status"] == "SELF_INVOICE_PENDING"
        assert record["fic_expense_id"] == 12345

    @pytest.mark.asyncio
    async def test_get_pending_self_invoices(
        self,
        mock_factum: FactumClient,
        mock_fic_si_fail: FICClient,
        mapper: Mapper,
        queue: QueueStore,
        mock_settings: Settings,
        tmp_path: Path,
    ) -> None:
        """get_pending_self_invoices torna i record pendenti."""
        src = _FIXTURES / "sample_saas_invoice.txt"
        path = tmp_path / "test_get_pending.txt"
        shutil.copy2(src, path)

        await process_file(
            path,
            factum=mock_factum,
            fic=mock_fic_si_fail,
            mapper=mapper,
            queue=queue,
            settings=mock_settings,
        )

        pending = queue.get_pending_self_invoices()
        assert len(pending) == 1
        assert pending[0]["fic_expense_id"] == 12345

    @pytest.mark.asyncio
    async def test_complete_recovery(
        self,
        mock_factum: FactumClient,
        mock_fic_si_fail: FICClient,
        mapper: Mapper,
        queue: QueueStore,
        mock_settings: Settings,
        tmp_path: Path,
    ) -> None:
        """Completare un pending con self_invoice_id."""
        src = _FIXTURES / "sample_saas_invoice.txt"
        path = tmp_path / "test_recovery.txt"
        shutil.copy2(src, path)

        result = await process_file(
            path,
            factum=mock_factum,
            fic=mock_fic_si_fail,
            mapper=mapper,
            queue=queue,
            settings=mock_settings,
        )
        sha = result.file.sha256

        # Simula recupero riuscito
        queue.complete(sha, self_invoice_id=99999)

        record = queue.get(sha)
        assert record["status"] == "completed"
        assert record["fic_self_invoice_id"] == 99999
        assert record["fic_expense_id"] == 12345

    @pytest.mark.asyncio
    async def test_full_success_no_pending(
        self,
        mock_factum: FactumClient,
        mock_fic_ok: FICClient,
        mapper: Mapper,
        queue: QueueStore,
        mock_settings: Settings,
        tmp_path: Path,
    ) -> None:
        """Flusso completo OK → nessun pending."""
        src = _FIXTURES / "sample_saas_invoice.txt"
        path = tmp_path / "test_full_ok.txt"
        shutil.copy2(src, path)

        result = await process_file(
            path,
            factum=mock_factum,
            fic=mock_fic_ok,
            mapper=mapper,
            queue=queue,
            settings=mock_settings,
        )

        assert result.status == DocumentStatus.RECORDED
        assert result.fic_self_invoice_id == 67890

        pending = queue.get_pending_self_invoices()
        assert len(pending) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Lock atomico acquire()
# ═══════════════════════════════════════════════════════════════════════════════


class TestAcquireLock:
    """Test del lock atomico SQLite."""

    @pytest.mark.asyncio
    async def test_first_acquire_succeeds(self, queue: QueueStore) -> None:
        queue.enqueue("sha-a", "/tmp/a.pdf")
        assert queue.acquire("sha-a") is True

    @pytest.mark.asyncio
    async def test_already_processing_returns_false(self, queue: QueueStore) -> None:
        queue.enqueue("sha-b", "/tmp/b.pdf")
        queue.acquire("sha-b")
        assert queue.acquire("sha-b") is False

    @pytest.mark.asyncio
    async def test_already_completed_returns_false(self, queue: QueueStore) -> None:
        queue.complete("sha-c", expense_id=1)
        assert queue.acquire("sha-c") is False

    @pytest.mark.asyncio
    async def test_failed_is_reacquirable(self, queue: QueueStore) -> None:
        queue.enqueue("sha-d", "/tmp/d.pdf")
        queue.acquire("sha-d")
        queue.mark_failed("sha-d", "errore")
        assert queue.acquire("sha-d") is True

    @pytest.mark.asyncio
    async def test_acquire_nonexistent_needs_enqueue(self, queue: QueueStore) -> None:
        """Record inesistente: enqueue prima, poi acquire."""
        queue.enqueue("sha-e", "/tmp/e.pdf")
        assert queue.acquire("sha-e") is True


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Strict currency fail-fast
# ═══════════════════════════════════════════════════════════════════════════════


class TestStrictCurrency:
    """Test del comportamento strict_currency."""

    def test_strict_currency_default_true(self) -> None:
        """Valore di default = True."""
        s = Settings(
            FACTUM_API_URL="http://test",
            FACTUM_API_KEY="k",
            FIC_BASE_URL="http://test",
            FIC_API_KEY="k",
            FIC_COMPANY_ID="0",
        )
        assert s.strict_currency is True

    def test_strict_currency_disabled(self) -> None:
        """STRICT_CURRENCY=False funziona."""
        s = Settings(
            FACTUM_API_URL="http://test",
            FACTUM_API_KEY="k",
            FIC_BASE_URL="http://test",
            FIC_API_KEY="k",
            FIC_COMPANY_ID="0",
            STRICT_CURRENCY="false",
        )
        assert s.strict_currency is False

    def test_currency_conversion_error_is_exception(self) -> None:
        """CurrencyConversionError è una eccezione."""
        exc = CurrencyConversionError("Test fallimento conversione")
        assert str(exc) == "Test fallimento conversione"
        assert isinstance(exc, Exception)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Queue helper: mark_self_invoice_pending with/without path
# ═══════════════════════════════════════════════════════════════════════════════


class TestSelfInvoicePendingQueue:
    """Test diretti di mark_self_invoice_pending e get_pending_self_invoices."""

    @pytest.mark.asyncio
    async def test_mark_with_path(self, queue: QueueStore) -> None:
        queue.mark_self_invoice_pending("s1", 100, path="/tmp/fattura.pdf")
        pending = queue.get_pending_self_invoices()
        assert pending[0]["file_path"] == "/tmp/fattura.pdf"

    @pytest.mark.asyncio
    async def test_mark_without_path(self, queue: QueueStore) -> None:
        queue.mark_self_invoice_pending("s2", 200)
        pending = queue.get_pending_self_invoices()
        assert pending[0]["file_path"] == ""

    @pytest.mark.asyncio
    async def test_mark_multiple_returns_all(self, queue: QueueStore) -> None:
        queue.mark_self_invoice_pending("s3", 300)
        queue.mark_self_invoice_pending("s4", 400)
        assert len(queue.get_pending_self_invoices()) == 2

    @pytest.mark.asyncio
    async def test_get_pending_empty(self, queue: QueueStore) -> None:
        assert queue.get_pending_self_invoices() == []

    @pytest.mark.asyncio
    async def test_upsert_overwrites(self, queue: QueueStore) -> None:
        queue.mark_self_invoice_pending("u1", 500, error_message="primo")
        queue.mark_self_invoice_pending("u1", 600, error_message="aggiornato")
        pending = queue.get_pending_self_invoices()
        assert len(pending) == 1
        assert pending[0]["fic_expense_id"] == 600
