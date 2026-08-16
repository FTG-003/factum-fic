"""Configurazione pytest per factum-fic."""

from __future__ import annotations

from typing import Any

import pytest

from factum_fic.core.mapper import Mapper


@pytest.fixture
def mapper() -> Mapper:
    """Mapper con configurazione di default."""
    return Mapper()


@pytest.fixture
def sample_factum_result() -> dict[str, Any]:
    """Risultato Factum simulato per una fattura SaaS estera."""
    return {
        "document_type": "invoice",
        "currency": "USD",
        "total": 23.40,
        "supplier_name": "DigitalOcean Inc.",
        "supplier_vat": "",
        "supplier_country": "US",
        "supplier_address": "101 Avenue of the Americas, New York, NY",
        "invoice_date": "2026-08-01",
        "invoice_number": "INV-2026-08101",
        "items": [{"description": "Droplet Basic Plan", "amount": 23.40}],
    }


@pytest.fixture
def sample_italian_result() -> dict[str, Any]:
    """Risultato Factum simulato per una fattura italiana."""
    return {
        "document_type": "invoice",
        "currency": "EUR",
        "total": 1200.00,
        "supplier_name": "Aruba S.p.A.",
        "supplier_vat": "IT01573850516",
        "supplier_country": "IT",
        "supplier_address": "Via San Clemente, 53, 24036 Ponte San Pietro BG",
        "invoice_date": "2026-08-15",
        "invoice_number": "2026/FP/001234",
        "items": [{"description": "Server dedicato", "amount": 1200.00}],
    }
