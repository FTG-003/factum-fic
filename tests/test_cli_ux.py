"""Test UX: wizard setup, cronologia history, alias italiani.

Verifica:
1. ``factum-fic setup`` con input simulato → .env scritto correttamente
2. ``factum-fic history`` con record mock in SQLite → tabella Rich
3. Risoluzione alias italiani (configura→setup, stato→status, etc.)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from factum_fic.cli.main import app
from factum_fic.storage.queue import QueueStore

# ── Mock helpers ──────────────────────────────────────────────────────────────


class MockFICClientSetup:
    """Mock FICClient per wizard setup (get_company_info, get_payment_accounts)."""

    def __init__(
        self,
        settings: Any = None,
        company: dict[str, Any] | None = None,
        payment_accounts: list[dict[str, Any]] | None = None,
    ) -> None:
        self._company = company or {
            "id": "99999",
            "name": "Test Srl Forfettaria",
            "vat_number": "IT01234567890",
            "fiscal_code": "01234567890",
            "tax_regime": "RF19",
        }
        self._payment_accounts = payment_accounts or [
            {"id": 42, "name": "Conto Corrente", "type": "standard"},
            {"id": 43, "name": "Carta di Credito", "type": "credit_card"},
        ]

    async def get_company_info(self) -> dict[str, Any]:
        return self._company

    async def get_payment_accounts(self) -> list[dict[str, Any]]:
        return self._payment_accounts

    async def close(self) -> None:
        return None


class MockFactumClientSetup:
    """Mock FactumClient per wizard setup."""

    def __init__(self, settings: Any = None) -> None:
        self._healthy = True

    async def health(self) -> bool:
        return self._healthy

    async def close(self) -> None:
        return None


# ── Test alias italiani ───────────────────────────────────────────────────────


class TestAliases:
    """Verifica che gli alias italiani risolvano ai comandi inglesi."""

    def test_alias_configura(self) -> None:
        """``factum-fic configura`` risolve a ``setup``."""
        runner = CliRunner()
        result = runner.invoke(app, ["configura", "--help"])
        assert result.exit_code == 0
        assert "Configurazione guidata" in result.output

    def test_alias_stato(self) -> None:
        """``factum-fic stato`` risolve a ``status`` (con mock per non fallire)."""
        runner = CliRunner()
        result = runner.invoke(app, ["stato", "--help"])
        assert result.exit_code == 0
        assert "Mostra il dashboard operativo" in result.output

    def test_alias_storico(self) -> None:
        """``factum-fic storico`` risolve a ``history``."""
        runner = CliRunner()
        result = runner.invoke(app, ["storico", "--help"])
        assert result.exit_code == 0
        assert "Mostra la cronologia" in result.output

    def test_alias_elabora(self) -> None:
        """``factum-fic elabora`` risolve a ``sync``."""
        runner = CliRunner()
        result = runner.invoke(app, ["elabora", "--help"])
        assert result.exit_code == 0
        assert "Elabora file fattura" in result.output

    def test_alias_auto(self) -> None:
        """``factum-fic auto`` risolve a ``watch``."""
        runner = CliRunner()
        result = runner.invoke(app, ["auto", "--help"])
        assert result.exit_code == 0
        assert "Avvia il watcher" in result.output

    def test_epilog_mentions_aliases(self) -> None:
        """L'epilog di ``--help`` elenca gli alias italiani."""
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "configura=setup" in result.output
        assert "stato=status" in result.output
        assert "storico=history" in result.output
        assert "elabora=sync" in result.output
        assert "auto=watch" in result.output


# ── Test history ──────────────────────────────────────────────────────────────


class TestHistory:
    """Verifica ``factum-fic history`` con dati mock in SQLite."""

    def test_history_empty(self, tmp_path: Path) -> None:
        """Coda vuota → messaggio 'Nessuna elaborazione'."""
        from factum_fic.storage import queue as queue_module

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(queue_module, "_QUEUE_DB", tmp_path / "queue.db")
        QueueStore(db_path=tmp_path / "queue.db").close()

        runner = CliRunner()
        result = runner.invoke(app, ["history"])

        assert result.exit_code == 0
        assert "Nessuna elaborazione" in result.output

        monkeypatch.undo()

    def test_history_with_records(self, tmp_path: Path) -> None:
        """Coda con record → tabella con colonne Data, File, Spesa, Autofattura, Stato."""
        from factum_fic.storage import queue as queue_module

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(queue_module, "_QUEUE_DB", tmp_path / "queue.db")
        q = QueueStore(db_path=tmp_path / "queue.db")
        # Record con autofattura (estera)
        q.complete(
            "a" * 64, expense_id=100, self_invoice_id=777,
            path="/tmp/aws-invoice.pdf",
        )
        # Record senza autofattura (italiana)
        q.complete(
            "b" * 64, expense_id=101, self_invoice_id=None,
            path="/tmp/aruba-fattura.pdf",
        )
        # Record fallito
        q.mark_failed("c" * 64, "Errore di connessione")
        q.close()

        runner = CliRunner()
        result = runner.invoke(app, ["history"])

        assert result.exit_code == 0
        output = result.output

        # Intestazione tabella
        assert "Cronologia elaborazioni" in output
        assert "Spesa FIC ID" in output
        assert "Autofattura SDI ID" in output
        assert "Stato" in output

        # Dati record
        assert "aws-invoice.pdf" in output
        assert "100" in output
        assert "777" in output
        assert "aruba-fattura.pdf" in output
        assert "101" in output
        assert "✅ completed" in output
        assert "❌ failed" in output

        monkeypatch.undo()


# ── Test setup wizard ─────────────────────────────────────────────────────────


class TestSetupWizard:
    """Verifica ``factum-fic setup`` con input simulato."""

    def _setup_patches(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Applica monkeypatch per isolare il wizard da API reali e filesystem."""
        # Mock FICClient
        monkeypatch.setattr(
            "factum_fic.cli.commands.setup.FICClient",
            MockFICClientSetup,
        )
        # Mock FactumClient
        monkeypatch.setattr(
            "factum_fic.cli.commands.setup.FactumClient",
            MockFactumClientSetup,
        )
        # Lavora in tmp_path invece di cwd per non toccare .env reale
        monkeypatch.chdir(tmp_path)

    def test_setup_writes_dotenv(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """setup con risposte valide → .env creato con i valori inseriti."""
        self._setup_patches(monkeypatch, tmp_path)

        # Input simulato:
        #   0. Workspace (default)                  → enter
        #   1. FIC_TOKEN (password)                 → "sk_fic_123"
        #   2. FIC_COMPANY_ID                       → "12345"
        #   3. (RF19 → nessuna domanda "continuare")
        #   4. Attivare chiave Factum?              → "n"
        #   5. FACTUM_API_KEY (password)            → "fk_factum_abc"
        #   6. Scegli conto (1-2, default=1)        → 2
        #   7. Scrivere .env?                        → "s"
        input_data = "\nsk_fic_123\n12345\nn\nfk_factum_abc\n2\ns\n"

        runner = CliRunner()
        result = runner.invoke(app, ["setup"], input=input_data)

        assert result.exit_code == 0, f"Setup fallito: {result.output}"

        # Verifica .env
        env_path = tmp_path / ".env"
        assert env_path.exists()
        content = env_path.read_text()

        assert "FIC_TOKEN=sk_fic_123" in content
        assert "FIC_COMPANY_ID=12345" in content
        assert "FACTUM_API_KEY=fk_factum_abc" in content
        assert "FIC_PAYMENT_ACCOUNT_NAME=Carta di Credito" in content
        assert "FIC_PAYMENT_ACCOUNT_ID=43" in content

    def test_setup_cancels_on_user_request(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Se l'utente rifiuta di scrivere → .env non creato."""
        self._setup_patches(monkeypatch, tmp_path)

        # Input: scelte valide fino alla domanda "Scrivere .env?" → "n"
        input_data = "\nsk_fic_123\n12345\nn\nfk_factum_abc\n1\nn\n"

        runner = CliRunner()
        result = runner.invoke(app, ["setup"], input=input_data)

        assert result.exit_code == 0
        assert "Configurazione annullata" in result.output
        assert not (tmp_path / ".env").exists()

    def test_setup_updates_existing_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """.env esistente con valori vecchi viene aggiornato (merge)."""
        self._setup_patches(monkeypatch, tmp_path)

        # Crea .env esistente con INBOX_DIR relativo (non assoluto, per evitare
        # che ensure_dirs nel callback provi a creare /custom/path)
        (tmp_path / ".env").write_text(
            "FIC_TOKEN=sk_old\nINBOX_DIR=./custom_path\n"
        )

        input_data = "\nsk_fic_new\n99999\nn\nfk_factum_new\n1\ns\n"

        runner = CliRunner()
        runner.invoke(app, ["setup"], input=input_data)

        content = (tmp_path / ".env").read_text()

        # Valore aggiornato
        assert "FIC_TOKEN=sk_fic_new" in content
        # INBOX_DIR sovrascritto dal wizard (calcolato dal workspace)
        assert "INBOX_DIR=" in content
        # Nuova chiave aggiunta
        assert "FACTUM_API_KEY=fk_factum_new" in content

    def test_setup_skip_payment_account(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Scegliendo 0 per conto → PAYMENT_ACCOUNT non scritto."""
        self._setup_patches(monkeypatch, tmp_path)

        input_data = "\nsk_fic_123\n12345\nn\nfk_factum_abc\n0\ns\n"

        runner = CliRunner()
        runner.invoke(app, ["setup"], input=input_data)

        content = (tmp_path / ".env").read_text()
        # PAYMENT_ACCOUNT_NAME deve essere stringa vuota (non scritto)
        assert "FIC_PAYMENT_ACCOUNT_NAME=" not in content
        assert "FIC_PAYMENT_ACCOUNT_ID=" not in content


# ── Test ricarica command ─────────────────────────────────────────────────────


class TestRicarica:
    """Verifica ``factum-fic ricarica`` — URL sicuro senza segreti."""

    def test_ricarica_needs_config(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Senza .env configurato → errore e uscita."""
        from factum_fic.cli.main import app

        runner = CliRunner()
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["ricarica"])
        assert result.exit_code != 0

    def test_ricarica_url_no_secret_in_params(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """URL generato non contiene sk_live_ o segreti.

        Simula configurazione FIC valida e verifica che l'URL
        contenga solo piva, non credenziali.
        """
        from factum_fic.cli.main import app

        # Mock FICClient.get_company_info a livello di modulo
        class _MockFIC:
            def __init__(self, settings=None) -> None:
                pass

            async def get_company_info(self) -> dict:
                return {
                    "vat_number": "IT01234567890",
                    "name": "Test Srl",
                }

            async def close(self) -> None:
                return None

        monkeypatch.setattr(
            "factum_fic.core.fic_client.FICClient",
            _MockFIC,
        )

        # Imposta FIC_TOKEN e FIC_COMPANY_ID nell'ambiente
        monkeypatch.setenv("FIC_TOKEN", "sk_fic_test_123")
        monkeypatch.setenv("FIC_COMPANY_ID", "99999")

        runner = CliRunner()
        # Usa input="n" per non aprire il browser
        result = runner.invoke(app, ["ricarica"], input="n\n")

        assert result.exit_code == 0, f"Uscito con errore: {result.output}"
        output = result.output

        # Deve mostrare l'URL con piva
        assert "checkout.factum.pyragogy.org" in output
        assert "IT01234567890" in output

        # Non deve contenere segreti
        assert "sk_live_" not in output
        assert "api_key" not in output.lower()


# ── Test get_user_companies response parsing ────────────────────────────────


class TestGetUserCompanies:
    """Verifica che get_user_companies estragga correttamente la lista
    dal formato annidato di FIC v2: ``{"data": {"companies": [...]}}``."""

    @pytest.mark.asyncio
    async def test_parses_nested_data_companies(self, monkeypatch) -> None:
        """Payload FIC v2 reale → lista estratta correttamente."""
        from factum_fic.config import Settings
        from factum_fic.core.fic_client import FICClient

        client = FICClient(Settings(fic_token="test", FIC_COMPANY_ID="0"))

        async def _fake_get(url: str, **kw):
            class _Resp:
                status_code = 200

                def json(self):
                    return {
                        "data": {
                            "companies": [
                                {"id": 123, "name": "Azienda 1", "vat_number": "IT001"},
                                {"id": 456, "name": "Azienda 2", "vat_number": "IT002"},
                            ]
                        }
                    }

                def raise_for_status(self):
                    pass

            return _Resp()

        monkeypatch.setattr(client._client, "get", _fake_get)
        companies = await client.get_user_companies()
        await client.close()

        assert len(companies) == 2
        assert companies[0]["id"] == 123
        assert companies[0]["name"] == "Azienda 1"
        assert companies[1]["id"] == 456

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_missing_data(self, monkeypatch) -> None:
        """Payload senza data → lista vuota, nessun crash."""
        from factum_fic.config import Settings
        from factum_fic.core.fic_client import FICClient

        client = FICClient(Settings(fic_token="test", FIC_COMPANY_ID="0"))

        async def _fake_get(url: str, **kw):
            class _Resp:
                status_code = 200

                def json(self):
                    return {}

                def raise_for_status(self):
                    pass

            return _Resp()

        monkeypatch.setattr(client._client, "get", _fake_get)
        companies = await client.get_user_companies()
        await client.close()

        assert companies == []
