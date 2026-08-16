"""Test del mapper: regole fiscali, categorie, autofattura."""

from __future__ import annotations

from factum_fic.core.mapper import Mapper
from factum_fic.core.models import DocumentType, FactumParseResult


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
    assert "art. 17-ter" in expense.notes


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
