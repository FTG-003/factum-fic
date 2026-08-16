"""Engine di mappatura fiscale: categorie, autofattura, IVA.

Regole di business per determinare:
- Se un documento è spesa diretta o autofattura estera (TD17/18/19)
- Categoria FIC in base al vendor
- Gestione valute e IVA per fornitori extra-UE
"""

from __future__ import annotations

import datetime
from typing import Any

from factum_fic.core.models import (
    DocumentType,
    FactumParseResult,
    FICCreateExpenseRequest,
    FICCreateSupplierRequest,
)

# Vendor noti e categorie associate (default)
_DEFAULT_CATEGORIES: dict[str, str] = {
    "aws": "Servizi hosting e cloud",
    "amazon web services": "Servizi hosting e cloud",
    "digitalocean": "Servizi hosting e cloud",
    "hetzner": "Servizi hosting e cloud",
    "github": "Abbonamenti software SaaS",
    "notion": "Abbonamenti software SaaS",
    "slack": "Abbonamenti software SaaS",
    "openai": "Abbonamenti software SaaS",
    "google": "Pubblicità e marketing",
    "meta": "Pubblicità e marketing",
    "namecheap": "Domini e registrazioni",
    "godaddy": "Domini e registrazioni",
}

# Conti FIC di default
_DEFAULT_ACCOUNTS: dict[str, str] = {
    "Servizi hosting e cloud": "Costi per servizi informatici",
    "Abbonamenti software SaaS": "Spese per servizi",
    "Domini e registrazioni": "Costi per servizi informatici",
    "Pubblicità e marketing": "Spese di pubblicità",
    "Altri costi": "Costi vari",
}


class Mapper:
    """Regole di mappatura fiscale Factum → FIC."""

    def __init__(self, yaml_config: dict[str, Any] | None = None) -> None:
        self._categories = {**_DEFAULT_CATEGORIES}
        self._accounts = {**_DEFAULT_ACCOUNTS}
        if yaml_config:
            if "categories" in yaml_config:
                self._categories.update(yaml_config["categories"])
            if "accounts" in yaml_config:
                self._accounts.update(yaml_config["accounts"])

    # ── Riconoscimento tipo documento ─────────────────────────────────────

    def detect_document_type(self, result: FactumParseResult) -> DocumentType:
        """Determina se è una spesa diretta o un'autofattura estera.

        Regola: se il fornitore NON ha P.IVA italiana e il servizio è estero
        (SaaS/cloud), marca come autofattura TD17/18/19.
        """
        vat = (result.supplier_vat or "").strip()
        country = (result.supplier_country or "").strip().upper()

        # P.IVA italiana → spesa diretta
        if vat.startswith("IT") or country == "IT":
            return DocumentType.EXPENSE

        # Nessuna P.IVA italiana + fornitore estero → autofattura
        if country and country != "IT":
            return DocumentType.SELF_INVOICE

        # Fallback: se il nome del fornitore matcha vendor noti esteri
        name = (result.supplier_name or "").lower()
        if any(k in name for k in ("aws", "amazon", "github", "openai", "digitalocean", "hetzner")):
            return DocumentType.SELF_INVOICE

        return DocumentType.EXPENSE

    # ── Categorizzazione ──────────────────────────────────────────────────

    def categorize(self, result: FactumParseResult) -> str:
        """Assegna una categoria FIC in base al vendor."""
        name = (result.supplier_name or "").lower().strip()
        for pattern, category in self._categories.items():
            if pattern in name:
                return category
        return "Altri costi"

    def account_for(self, category: str) -> str:
        """Conto FIC per una data categoria."""
        return self._accounts.get(category, "Costi vari")

    # ── Costruzione entity fornitore ──────────────────────────────────────

    def build_supplier(self, result: FactumParseResult) -> FICCreateSupplierRequest:
        """Costruisce i dati del fornitore da registrare su FIC."""
        country = (result.supplier_country or "").strip().upper()
        vat = (result.supplier_vat or "").strip()

        return FICCreateSupplierRequest(
            name=result.supplier_name or "Fornitore sconosciuto",
            vat_number=vat if vat else None,
            country_iso=country if country else "XX",
            address=result.supplier_address or None,
            entity_type="others" if country and country != "IT" else "company",
        )

    # ── Costruzione request spesa/autofattura ─────────────────────────────

    def build_expense(
        self,
        result: FactumParseResult,
        entity_id: int | None = None,
        supplier: FICCreateSupplierRequest | None = None,
    ) -> FICCreateExpenseRequest:
        """Costruisce la request per creare una spesa o autofattura su FIC."""
        doc_type = self.detect_document_type(result)
        category = self.categorize(result)
        currency = (result.currency or "EUR").upper()
        gross = result.total if result.total else 0.0

        # ── Fallback date: garantisce data ISO valida ────────────────────
        raw_date = (result.invoice_date or "").strip()
        if raw_date:
            # Verifica formato ISO YYYY-MM-DD
            try:
                datetime.datetime.strptime(raw_date, "%Y-%m-%d")
                issue_date = raw_date
            except ValueError:
                issue_date = datetime.date.today().isoformat()
        else:
            issue_date = datetime.date.today().isoformat()

        # Due date: usa issue_date come fallback
        due_date = issue_date

        # ── Sanitizzazione importi ───────────────────────────────────────
        # Se amount_gross è 0 ma c'è un totale, allinea netto
        if gross == 0.0 and result.total is not None and result.total > 0:
            gross = result.total
        # Se amount_gross è 0 e amount_net pure, non impostabile
        net = gross
        vat = 0.0
        final_gross = net + vat

        # Descrizione: includi numero fattura e fornitore
        desc_parts = [result.supplier_name or "Fattura"]
        if result.invoice_number:
            desc_parts.append(f"n. {result.invoice_number}")
        if issue_date:
            desc_parts.append(f"del {issue_date}")
        description = " — ".join(desc_parts)

        # Note legali per autofattura
        notes = ""
        if doc_type == DocumentType.SELF_INVOICE:
            notes = (
                f"Autofattura ai sensi dell'art. 17-ter DPR 633/72 per acquisto "
                f"da {result.supplier_name}. Documento elaborato via Factum Parse API "
                f"(Zero Data Retention)."
            )

        return FICCreateExpenseRequest(
            entity_id=entity_id,
            entity=supplier,
            date=issue_date,
            due_date=due_date,
            category=category,
            description=description,
            amount_net=net,
            amount_vat=vat,
            amount_gross=final_gross,
            currency=currency,
            vat_percentage=0.0,
            has_iva=currency == "EUR",
            is_autofattura=doc_type == DocumentType.SELF_INVOICE,
            notes=notes,
        )
