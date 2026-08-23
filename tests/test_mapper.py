"""Test del mapper: regole fiscali, categorie, autofattura."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from factum_fic.core.mapper import Mapper, convert_currency
from factum_fic.core.models import (
    DocumentType,
    FactumParseResult,
    FICCreateExpenseRequest,
    SelfInvoiceType,
)


def test_detect_self_invoice_estero(mapper: Mapper, sample_factum_result: dict) -> None:
    """Fornitore USA senza P.IVA → autofattura TD17."""
    result = FactumParseResult(**sample_factum_result)
    doc_type = mapper.detect_document_type(result)
    assert doc_type == DocumentType.SELF_INVOICE


def test_detect_expense_italiano(mapper: Mapper, sample_italian_result: dict) -> None:
    """Fornitore con P.IVA italiana → spesa diretta."""
    result = FactumParseResult(**sample_italian_result)
    doc_type = mapper.detect_document_type(result)
    assert doc_type == DocumentType.EXPENSE


def test_categorize_aws(mapper: Mapper) -> None:
    """AWS matcha 'Servizi hosting e cloud'."""
    result = FactumParseResult(supplier_name="Amazon Web Services Inc.")
    assert mapper.categorize(result) == "Servizi hosting e cloud"


def test_categorize_github(mapper: Mapper) -> None:
    """GitHub matcha 'Abbonamenti software SaaS'."""
    result = FactumParseResult(supplier_name="GitHub, Inc.")
    assert mapper.categorize(result) == "Abbonamenti software SaaS"


def test_categorize_unknown(mapper: Mapper) -> None:
    """Fornitore sconosciuto → 'Altri costi'."""
    result = FactumParseResult(supplier_name="Unknown Corp Ltd.")
    assert mapper.categorize(result) == "Altri costi"


def test_build_supplier_estero(mapper: Mapper, sample_factum_result: dict) -> None:
    """Fornitore estero → entity_type='others', country_iso='US'."""
    result = FactumParseResult(**sample_factum_result)
    supplier = mapper.build_supplier(result)
    assert supplier.entity_type == "others"
    assert supplier.country_iso == "US"
    assert supplier.vat_number is None


def test_build_supplier_italiano(mapper: Mapper, sample_italian_result: dict) -> None:
    """Fornitore italiano → entity_type='company', vat_number='IT...'."""
    result = FactumParseResult(**sample_italian_result)
    supplier = mapper.build_supplier(result)
    assert supplier.entity_type == "company"
    assert supplier.vat_number == "IT01573850516"


def test_build_expense_autofattura(mapper: Mapper, sample_factum_result: dict) -> None:
    """Spesa estera → is_autofattura=True, note legali."""
    result = FactumParseResult(**sample_factum_result)
    expense = mapper.build_expense(result)
    assert expense.is_autofattura is True
    assert "art. 17 c. 2" in expense.notes
    assert "17-ter" not in expense.notes


def test_account_for_hosting(mapper: Mapper) -> None:
    """Categoria hosting → conto 'Costi per servizi informatici'."""
    assert mapper.account_for("Servizi hosting e cloud") == "Costi per servizi informatici"


def test_account_for_unknown(mapper: Mapper) -> None:
    """Categoria sconosciuta → fallback 'Costi vari'."""
    assert mapper.account_for("Categoria Inesistente") == "Costi vari"


def test_mapper_with_custom_yaml() -> None:
    """Mapper con YAML custom sovrascrive categorie di default."""
    yaml_cfg = {
        "categories": {"stripe": "Commissioni pagamento"},
        "accounts": {"Commissioni pagamento": "Commissioni"},
    }
    m = Mapper(yaml_cfg)
    result = FactumParseResult(supplier_name="Stripe Payments UK Ltd")
    assert m.categorize(result) == "Commissioni pagamento"
    assert m.account_for("Commissioni pagamento") == "Commissioni"


def test_build_expense_date_fallback_empty(mapper: Mapper) -> None:
    """Data fattura vuota → fallback a oggi."""
    result = FactumParseResult(
        document_type="invoice",
        currency="EUR",
        total=100.0,
        supplier_name="Test Srl",
        supplier_vat="IT01234567890",
        supplier_country="IT",
        invoice_date="",
        invoice_number="",
    )
    expense = mapper.build_expense(result)
    # Deve contenere la data odierna YYYY-MM-DD
    assert len(expense.date) == 10
    assert expense.date[4] == "-"
    assert expense.date[7] == "-"
    # due_date deve matchare issue_date
    assert expense.due_date == expense.date


def test_build_expense_date_fallback_invalid(mapper: Mapper) -> None:
    """Data fattura in formato non valido → fallback a oggi."""
    result = FactumParseResult(
        document_type="invoice",
        currency="EUR",
        total=50.0,
        supplier_name="Test Ltd",
        supplier_vat="",
        supplier_country="US",
        invoice_date="31/12/2026",  # formato non ISO
        invoice_number="INV-001",
    )
    expense = mapper.build_expense(result)
    assert len(expense.date) == 10
    assert expense.date[4] == "-"
    assert expense.date[7] == "-"
    assert expense.is_autofattura is True
    assert expense.due_date == expense.date


def test_build_expense_amount_sanitization(mapper: Mapper) -> None:
    """Importo zero con total > 0 → allineato."""
    result = FactumParseResult(
        document_type="invoice",
        currency="USD",
        total=250.00,
        supplier_name="AWS Inc.",
        supplier_vat="",
        supplier_country="US",
        invoice_date="2026-09-01",
        invoice_number="AWS-2026-091",
    )
    expense = mapper.build_expense(result)
    assert expense.amount_net == 250.00
    assert expense.amount_gross == 250.00
    assert expense.date == "2026-09-01"


# ── Currency conversion tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_convert_currency_same() -> None:
    """Stessa valuta → tasso 1.0."""
    rate = await convert_currency("EUR", "EUR")
    assert rate == 1.0


@pytest.mark.asyncio
async def test_convert_currency_usd_to_eur() -> None:
    """USD → EUR: chiamata API Frankfurter con risposta mockata."""
    from factum_fic.core.mapper import _EXCHANGE_RATES_CACHE

    # Pulisce la cache per evitare interferenze da chiamate reali precedenti
    _EXCHANGE_RATES_CACHE.clear()

    mock_response = {"amount": 1.0, "base": "USD", "date": "2026-08-16", "rates": {"EUR": 0.92}}

    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        response_mock = AsyncMock()
        response_mock.status_code = 200
        response_mock.raise_for_status = AsyncMock()
        response_mock.json = lambda: mock_response
        mock_instance.get.return_value = response_mock
        mock_client.return_value.__aenter__.return_value = mock_instance

        rate = await convert_currency("USD", "EUR")
        assert rate == 0.92


@pytest.mark.asyncio
async def test_convert_currency_api_failure() -> None:
    """API Frankfurter non disponibile → fallback a 1.0 (log warning)."""
    from factum_fic.core.mapper import _EXCHANGE_RATES_CACHE

    # Pulisce la cache per evitare interferenze dal test precedente
    _EXCHANGE_RATES_CACHE.clear()

    with patch("httpx.AsyncClient") as mock_client:
        mock_instance = AsyncMock()
        # Simula un eccezione sulla chiamata get (timeout / errore di rete)
        mock_instance.get = AsyncMock(side_effect=Exception("API down"))
        mock_client.return_value.__aenter__.return_value = mock_instance

        rate = await convert_currency("USD", "EUR")
        assert rate == 1.0


# ── Self-invoice SDI classification tests ────────────────────────────────────


def test_classify_td17_extra_ue(mapper: Mapper) -> None:
    """Fornitore Extra-UE (US) → TD17."""
    result = FactumParseResult(
        supplier_name="AWS Inc.",
        supplier_vat="",
        supplier_country="US",
    )
    assert mapper.classify_self_invoice_type(result) == SelfInvoiceType.TD17


def test_classify_td17_extra_ue_switzerland(mapper: Mapper) -> None:
    """Fornitore Svizzera (CH, non UE) → TD17."""
    result = FactumParseResult(
        supplier_name="Swiss Vendor AG",
        supplier_vat="",
        supplier_country="CH",
    )
    assert mapper.classify_self_invoice_type(result) == SelfInvoiceType.TD17


def test_classify_td18_intra_ue_with_vat(mapper: Mapper) -> None:
    """Fornitore Intra-UE con P.IVA (DE) → TD18."""
    result = FactumParseResult(
        supplier_name="German GmbH",
        supplier_vat="DE123456789",
        supplier_country="DE",
    )
    assert mapper.classify_self_invoice_type(result) == SelfInvoiceType.TD18


def test_classify_td18_intra_ue_france_with_vat(mapper: Mapper) -> None:
    """Fornitore Intra-UE con P.IVA (FR) → TD18."""
    result = FactumParseResult(
        supplier_name="French SAS",
        supplier_vat="FR12345678901",
        supplier_country="FR",
    )
    assert mapper.classify_self_invoice_type(result) == SelfInvoiceType.TD18


def test_classify_td19_intra_ue_without_vat(mapper: Mapper) -> None:
    """Fornitore Intra-UE senza P.IVA (ES) → TD19."""
    result = FactumParseResult(
        supplier_name="Spanish Vendor",
        supplier_vat="",
        supplier_country="ES",
    )
    assert mapper.classify_self_invoice_type(result) == SelfInvoiceType.TD19


def test_classify_td19_intra_ue_netherlands_without_vat(mapper: Mapper) -> None:
    """Fornitore Intra-UE senza P.IVA (NL) → TD19."""
    result = FactumParseResult(
        supplier_name="Dutch Vendor",
        supplier_vat="",
        supplier_country="NL",
    )
    assert mapper.classify_self_invoice_type(result) == SelfInvoiceType.TD19


# ── Self-invoice request builder tests ───────────────────────────────────────


def test_build_self_invoice_request_td17(mapper: Mapper, sample_factum_result: dict) -> None:
    """build_self_invoice_request con TD17: IVA 22% calcolata correttamente."""
    result = FactumParseResult(**sample_factum_result)
    expense = mapper.build_expense(result)

    si_request = mapper.build_self_invoice_request(
        expense=expense,
        expense_id=12345,
        numeration="/TD",
        vat_value=22,
        supplier_name="DigitalOcean Inc.",
        supplier_vat_number=None,
        supplier_country_iso="US",
        self_invoice_type=SelfInvoiceType.TD17,
    )

    assert si_request.self_invoice_type == SelfInvoiceType.TD17
    assert si_request.vat_value == 22
    assert si_request.amount_net == 23.40
    assert si_request.amount_vat == 5.15  # 23.40 * 22 / 100 = 5.148 → round 5.15
    assert si_request.amount_gross == 28.55  # 23.40 + 5.15
    assert si_request.original_document_id == 12345
    assert si_request.original_document_description == expense.description
    assert si_request.supplier_name == "DigitalOcean Inc."
    assert si_request.supplier_country_iso == "US"
    assert si_request.numeration == "/TD"
    assert "art. 17 c. 2 DPR 633/72" in si_request.notes
    assert "[TD17]" in si_request.description


def test_build_self_invoice_request_td18(mapper: Mapper) -> None:
    """build_self_invoice_request con TD18: fornitore tedesco con P.IVA."""
    expense = FICCreateExpenseRequest(
        entity_id=42,
        date="2026-09-15",
        description="German GmbH — n. INV-DE-2026-091 — del 2026-09-15",
        amount_net=1000.00,
        amount_vat=0.0,
        amount_gross=1000.00,
        is_autofattura=True,
    )

    si_request = mapper.build_self_invoice_request(
        expense=expense,
        expense_id=12346,
        numeration="/TD18",
        vat_value=22,
        supplier_name="German GmbH",
        supplier_vat_number="DE123456789",
        supplier_country_iso="DE",
        self_invoice_type=SelfInvoiceType.TD18,
    )

    assert si_request.self_invoice_type == SelfInvoiceType.TD18
    assert si_request.vat_value == 22
    assert si_request.amount_net == 1000.00
    assert si_request.amount_vat == 220.00  # 1000 * 0.22
    assert si_request.amount_gross == 1220.00
    assert si_request.supplier_vat_number == "DE123456789"
    assert si_request.supplier_country_iso == "DE"
    assert si_request.numeration == "/TD18"
    assert "[TD18]" in si_request.description


def test_build_self_invoice_request_td19(mapper: Mapper) -> None:
    """build_self_invoice_request con TD19: fornitore spagnolo senza P.IVA."""
    expense = FICCreateExpenseRequest(
        entity_id=43,
        date="2026-10-01",
        description="Spanish Vendor — n. ES-2026-001 — del 2026-10-01",
        amount_net=500.00,
        amount_vat=0.0,
        amount_gross=500.00,
        is_autofattura=True,
        notes="Nota di prova",
    )

    si_request = mapper.build_self_invoice_request(
        expense=expense,
        expense_id=12347,
        numeration="/TD19",
        vat_value=22,
        supplier_name="Spanish Vendor",
        supplier_vat_number=None,
        supplier_country_iso="ES",
        self_invoice_type=SelfInvoiceType.TD19,
    )

    assert si_request.self_invoice_type == SelfInvoiceType.TD19
    assert si_request.amount_vat == 110.00  # 500 * 0.22
    assert si_request.amount_gross == 610.00
    assert si_request.supplier_vat_number is None
    assert si_request.supplier_country_iso == "ES"
    assert si_request.numeration == "/TD19"
    assert "[TD19]" in si_request.description
    # Note unica e pulita: non deve contenere expense.notes (nessuna
    # concatenazione) né riferimento all'art. 17-ter, ma solo art. 17 c. 2
    assert "17-ter" not in si_request.notes
    assert "art. 17 c. 2 DPR 633/72" in si_request.notes
    assert "Nota di prova" not in si_request.notes
    assert si_request.notes.count("Autofattura") == 1


def test_build_self_invoice_request_zero_net(mapper: Mapper) -> None:
    """Importo netto zero → IVA e lordo calcolati correttamente a zero."""
    expense = FICCreateExpenseRequest(
        entity_id=0,
        date="2026-11-01",
        description="Zero amount invoice",
        amount_net=0.0,
        amount_vat=0.0,
        amount_gross=0.0,
        is_autofattura=True,
    )

    si_request = mapper.build_self_invoice_request(
        expense=expense,
        expense_id=0,
        numeration="/TD",
        vat_value=22,
        supplier_name="Test",
        self_invoice_type=SelfInvoiceType.TD17,
    )

    assert si_request.amount_net == 0.0
    assert si_request.amount_vat == 0.0
    assert si_request.amount_gross == 0.0


def test_build_self_invoice_note_conversion(mapper: Mapper) -> None:
    """Nota autofattura: include importo originale, valuta e tasso cambio (senza duplicazione)."""
    expense = FICCreateExpenseRequest(
        entity_id=43,
        date="2026-08-23",
        description="OpenRouter, Inc — n. 425IIFB00004 — del 2026-08-23",
        amount_net=42.56,
        amount_vat=0.0,
        amount_gross=42.56,
        is_autofattura=True,
        notes="Qualunque testo LLM che non deve finire nell'autofattura",
    )

    si_request = mapper.build_self_invoice_request(
        expense=expense,
        expense_id=12345,
        numeration="/TD17",
        vat_value=22,
        supplier_name="OpenRouter, Inc",
        supplier_country_iso="US",
        self_invoice_type=SelfInvoiceType.TD17,
        original_amount=49.79,
        original_currency="USD",
        exchange_rate=0.8547,
    )

    assert si_request.self_invoice_type == SelfInvoiceType.TD17
    assert "art. 17 c. 2 DPR 633/72" in si_request.notes
    assert "49.79 USD" in si_request.notes
    assert "0.8547" in si_request.notes
    assert "12345" in si_request.notes  # rif. spesa FIC
    # Nessuna duplicazione delle note LLM originali
    assert "Note originali" not in si_request.notes
    assert "Qualunque testo LLM" not in si_request.notes
    assert si_request.notes.count("Autofattura") == 1
