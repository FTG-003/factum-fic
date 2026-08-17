"""Test del dashboard CLI ``factum-fic status``.

Verifica:
1. La raccolta dati ``collect_status`` con client mockati.
2. Il comando CLI ``factum-fic status`` end-to-end via Typer CliRunner,
   con risposte FIC e Factum simulate.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from factum_fic.cli.main import collect_status
from factum_fic.storage.queue import QueueStore

# ── Mock client ───────────────────────────────────────────────────────────────


class MockFactumClient:
    """Fake FactumClient con health() controllata."""

    def __init__(self, healthy: bool = True) -> None:
        self._healthy = healthy
        self._api_url = "https://mock.factum.test"

    async def health(self) -> bool:
        return self._healthy

    async def close(self) -> None:
        return None


class MockFICClient:
    """Fake FICClient con health/info/conto controllati."""

    def __init__(
        self,
        healthy: bool = True,
        company: dict[str, Any] | None = None,
        payment_account: dict[str, Any] | None = None,
    ) -> None:
        self._healthy = healthy
        self._company = company or {
            "id": "99999",
            "name": "Test Srl Forfettaria",
            "vat_number": "IT01234567890",
            "fiscal_code": "01234567890",
            "tax_regime": "RF19",
        }
        self._payment_account = payment_account
        self._base_url = "https://mock.fic.test"

    async def health(self) -> bool:
        return self._healthy

    async def get_company_info(self) -> dict[str, Any]:
        return self._company

    async def resolve_payment_account(self) -> dict[str, Any] | None:
        return self._payment_account

    async def close(self) -> None:
        return None


@pytest.fixture
def queue(tmp_path: Path) -> QueueStore:
    """QueueStore con DB temporaneo per il test."""
    return QueueStore(db_path=tmp_path / "test_queue.db")


# ── Test collect_status ───────────────────────────────────────────────────────


def test_collect_status_full(queue: QueueStore) -> None:
    """collect_status con tutto OK: azienda, conto, autofatture, coda."""
    # Popola la coda con un paio di record (di cui uno con autofattura)
    queue.complete("a" * 64, expense_id=1, self_invoice_id=101, path="/tmp/foreign.pdf")
    queue.complete("b" * 64, expense_id=2, self_invoice_id=None, path="/tmp/italian.pdf")

    factum = MockFactumClient(healthy=True)
    fic = MockFICClient(
        healthy=True,
        payment_account={"id": 55, "name": "Conto Corrente Aziendale"},
    )

    data = asyncio.run(collect_status(factum, fic, queue))

    # Connessioni
    assert data["factum"]["ok"] is True
    assert data["fic"]["ok"] is True

    # Azienda
    assert data["company"]["name"] == "Test Srl Forfettaria"
    assert data["company"]["vat_number"] == "IT01234567890"
    assert data["company"]["tax_regime"] == "RF19"

    # Conto di pagamento
    assert data["payment_account"] == {"id": 55, "name": "Conto Corrente Aziendale"}

    # Autofatture
    assert data["self_invoice"]["enabled"] is True
    assert data["self_invoice"]["numeration"] == "/TD17"
    assert data["self_invoice"]["vat_value"] == 22

    # Coda
    assert data["queue"]["processed"] == 2
    assert data["queue"]["expenses"] == 2
    assert data["queue"]["self_invoices"] == 1


def test_collect_status_api_down(queue: QueueStore) -> None:
    """API Factum e FIC non raggiungibili: nessuna eccezione, ok=False."""
    factum = MockFactumClient(healthy=False)
    fic = MockFICClient(healthy=False, payment_account=None)

    data = asyncio.run(collect_status(factum, fic, queue))

    assert data["factum"]["ok"] is False
    assert data["fic"]["ok"] is False
    assert data["company"] == {}
    assert data["payment_account"] is None
    assert data["queue"] == {
        "processed": 0,
        "expenses": 0,
        "self_invoices": 0,
        "errors": 0,
        "queued": 0,
    }


def test_collect_status_payment_account_missing(queue: QueueStore) -> None:
    """FIC OK ma nessun conto di pagamento → payment_account=None."""
    factum = MockFactumClient(healthy=True)
    fic = MockFICClient(healthy=True, payment_account=None)

    data = asyncio.run(collect_status(factum, fic, queue))
    assert data["payment_account"] is None


def test_collect_status_async_errors(queue: QueueStore) -> None:
    """Client con metodi async che sollevano eccezioni → fallback sicuri."""
    factum = MockFactumClient(healthy=True)
    fic = MockFICClient(healthy=True)

    # Simula errori sulle chiamate
    async def boom_health() -> bool:
        raise RuntimeError("boom")

    async def boom_company() -> dict[str, Any]:
        raise RuntimeError("boom")

    fic.health = boom_health
    fic.get_company_info = boom_company

    data = asyncio.run(collect_status(factum, fic, queue))
    assert data["fic"]["ok"] is False
    assert data["company"] == {}
    assert data["payment_account"] is None


# ── Test CLI end-to-end ───────────────────────────────────────────────────────


def test_cli_status_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``factum-fic status`` mostra il dashboard completo con mock FIC/Factum."""
    from typer.testing import CliRunner

    from factum_fic.cli.main import app
    from factum_fic.storage import queue as queue_module

    # Redirige il DB coda verso un path temporaneo
    test_db = tmp_path / "queue.db"
    monkeypatch.setattr(queue_module, "_QUEUE_DB", test_db)
    q = QueueStore(db_path=test_db)
    q.complete("c" * 64, expense_id=7, self_invoice_id=77, path="/tmp/aws.pdf")
    q.close()

    # Mock delle classi client usate dal comando status
    # (importate localmente dentro status() → patch sui moduli sorgente)
    monkeypatch.setattr(
        "factum_fic.core.factum_client.FactumClient",
        lambda settings: MockFactumClient(healthy=True),
    )
    monkeypatch.setattr(
        "factum_fic.core.fic_client.FICClient",
        lambda settings: MockFICClient(
            healthy=True,
            company={
                "id": "99999",
                "name": "Studio Web Srl",
                "vat_number": "IT01234567890",
                "fiscal_code": "01234567890",
                "tax_regime": "forfettario",
            },
            payment_account={"id": 55, "name": "Carta Business"},
        ),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0, result.output
    output = result.output

    # Sezioni chiave del dashboard
    assert "factum-fic status" in output
    assert "Factum Parse API" in output
    assert "Fatture in Cloud" in output
    # Azienda
    assert "Studio Web Srl" in output
    assert "IT01234567890" in output
    assert "Regime Forfettario" in output
    # Conto
    assert "Carta Business" in output
    assert "id=55" in output
    # Autofatture
    assert "/TD17" in output
    assert "22%" in output
    assert "art. 17 c. 2 DPR 633/72" in output
    # Coda
    assert "Autofatture SDI generate" in output
    assert "Spese registrate (FIC)" in output


def test_cli_status_fic_down(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``factum-fic status`` con FIC non raggiungibile → messaggio chiaro."""
    from typer.testing import CliRunner

    from factum_fic.cli.main import app
    from factum_fic.storage import queue as queue_module

    monkeypatch.setattr(queue_module, "_QUEUE_DB", tmp_path / "queue.db")

    monkeypatch.setattr(
        "factum_fic.core.factum_client.FactumClient",
        lambda settings: MockFactumClient(healthy=False),
    )
    monkeypatch.setattr(
        "factum_fic.core.fic_client.FICClient",
        lambda settings: MockFICClient(healthy=False),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["status"])

    assert result.exit_code == 0
    assert "NON RAGGIUNGIBILE" in result.output
    assert "Non raggiungibile (verifica credenziali FIC)" in result.output
