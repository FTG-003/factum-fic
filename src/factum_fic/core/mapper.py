"""Engine di mappatura fiscale: categorie, autofattura, IVA.

Regole di business per determinare:
- Se un documento è spesa diretta o autofattura estera (TD17/18/19)
- Categoria FIC in base al vendor
- Gestione valute e IVA per fornitori extra-UE
"""

from __future__ import annotations

import datetime
import logging
import re
from typing import Any

from factum_fic.core.models import (
    DocumentType,
    FactumParseResult,
    FICCreateExpenseRequest,
    FICCreateIssuedDocumentRequest,
    FICCreateSupplierRequest,
    SelfInvoiceType,
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

# Paesi UE (ISO alpha-2) per classificazione SDI
# Vedi: https://www.agenziaentrate.gov.it/portale/elenco-codici-iso
_EU_COUNTRIES: frozenset[str] = frozenset({
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI",
    "FR", "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT",
    "NL", "PL", "PT", "RO", "SE", "SI", "SK",
    # Codici estesi usati da SDI
    "EL", "GB",
})


def _to_iso_date(raw_date: str) -> str | None:
    """Converte date nei formati DD/MM/YYYY, DD.MM.YYYY, YYYY-MM-DD in ISO.

    Restituisce la stringa ISO YYYY-MM-DD, o None se la data non è valida.
    """
    raw_date = raw_date.strip()[:10]
    # Già ISO
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw_date):
        return raw_date
    # DD/MM/YYYY o DD.MM.YYYY
    m = re.match(r"^(\d{2})[/\.](\d{2})[/\.](\d{4})$", raw_date)
    if m:
        day, month, year = m.group(1), m.group(2), m.group(3)
        try:
            datetime.datetime.strptime(f"{year}-{month}-{day}", "%Y-%m-%d")
            return f"{year}-{month}-{day}"
        except ValueError:
            return None
    # Prova parsing generico
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(raw_date, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ── Exchange rate cache ─────────────────────────────────────────────────────────

_EXCHANGE_RATES_CACHE: dict[str, tuple[float, datetime.datetime]] = {}
_CACHE_TTL = datetime.timedelta(hours=6)

logger = logging.getLogger(__name__)


async def convert_currency(from_currency: str, to_currency: str = "EUR") -> float:
    """Interroga l'API Frankfurter (BCE) per il tasso di cambio corrente.

    I risultati sono cached in memoria per 6 ore.

    Returns:
        Tasso di cambio (moltiplicatore: amount * rate = amount_in_EUR).
        1.0 se from_currency == to_currency o in caso di errore.
    """
    if from_currency == to_currency:
        return 1.0
    now = datetime.datetime.now()
    cache_key = f"{from_currency}_{to_currency}"
    cached = _EXCHANGE_RATES_CACHE.get(cache_key)
    if cached and (now - cached[1]) < _CACHE_TTL:
        return cached[0]

    import httpx

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                "https://api.frankfurter.dev/v1/latest",
                params={"from": from_currency, "to": to_currency},
            )
        resp.raise_for_status()
        data = resp.json()
        rate = float(data["rates"][to_currency])
        _EXCHANGE_RATES_CACHE[cache_key] = (rate, now)
        return rate
    except Exception:
        logger.warning(
            "Tasso di cambio non disponibile per %s→%s, restituisco 1.0",
            from_currency,
            to_currency,
        )
        return 1.0


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

        # VAT non italiana presente (es. EIN USA): fornitore estero certo
        if vat and not vat.startswith("IT"):
            return DocumentType.SELF_INVOICE

        # Fallback: se il nome del fornitore matcha vendor noti esteri
        name = (result.supplier_name or "").lower()
        if any(k in name for k in ("aws", "amazon", "github", "openai", "digitalocean", "hetzner", "openrouter")):
            return DocumentType.SELF_INVOICE

        return DocumentType.EXPENSE

    # ── Classificazione SDI (TD17 / TD18 / TD19) ─────────────────────

    def classify_self_invoice_type(
        self,
        result: FactumParseResult,
    ) -> SelfInvoiceType:
        """Classifica il tipo di autofattura SDI in base alla nazionalità del fornitore.

        Regole:
        - Extra-UE (paese non presente in _EU_COUNTRIES) → TD17
        - Intra-UE con partita IVA → TD18
        - Intra-UE senza partita IVA → TD19

        Args:
            result: Risultato del parsing Factum.

        Returns:
            SelfInvoiceType.TD17, .TD18 o .TD19.
        """
        country = (result.supplier_country or "").strip().upper()
        vat = (result.supplier_vat or "").strip()

        # Extra-UE → TD17
        if country not in _EU_COUNTRIES:
            return SelfInvoiceType.TD17

        # Intra-UE con P.IVA → TD18
        if vat:
            return SelfInvoiceType.TD18

        # Intra-UE senza P.IVA → TD19
        return SelfInvoiceType.TD19

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

        # ── Importi: cerca in result.raw se total è 0 ────────────────────
        gross = result.total or 0.0
        net = 0.0
        vat = 0.0

        # Se Factum non ha popolato total, prova dal raw nested
        if gross == 0.0 and result.raw:
            raw = result.raw
            # Alcune API nidificano in "amount" o "totals"
            for key in ("amount_gross", "gross_amount", "total_amount", "total", "importo"):
                val = raw.get(key) or 0.0
                if val:
                    gross = float(val)
                    break
            # Cerca anche amount_net / amount_vat
            for key in ("amount_net", "net_amount", "imponibile"):
                val = raw.get(key) or 0.0
                if val:
                    net = float(val)
                    break
            for key in ("amount_vat", "vat_amount", "iva"):
                val = raw.get(key) or 0.0
                if val:
                    vat = float(val)
                    break

        # Se ancora gross=0, usa net+vat o somma items
        if gross == 0.0 and not net and not vat:
            for item in result.items:
                gross += float(item.get("amount", 0.0))

        # Fallback finale: net = gross, vat = 0
        if not net:
            net = gross
        if not vat:
            vat = 0.0
        final_gross = gross or (net + vat)
        raw_date = (result.invoice_date or "").strip()
        if not raw_date and result.raw:
            for key in ("date", "invoice_date", "document_date", "data"):
                val = result.raw.get(key, "")
                if val:
                    raw_date = str(val).strip()[:10]
                    break
        if raw_date:
            # Verifica formato ISO YYYY-MM-DD o converti da DD/MM/YYYY, DD.MM.YYYY
            iso_date = _to_iso_date(raw_date)
            issue_date = iso_date or datetime.date.today().isoformat()
        else:
            issue_date = datetime.date.today().isoformat()

        # Due date: usa issue_date come fallback
        due_date = issue_date

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
                f"Autofattura ai sensi dell'art. 17 c. 2 DPR 633/72 per acquisto "
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
            invoice_number=result.invoice_number or "",
            amount_net=net,
            amount_vat=vat,
            amount_gross=final_gross,
            currency=currency,
            vat_percentage=0.0,
            has_iva=currency == "EUR",
            is_autofattura=doc_type == DocumentType.SELF_INVOICE,
            notes=notes,
        )

    # ── Costruzione request autofattura SDI (TD17/TD18/TD19) ─────────────

    def build_self_invoice_request(
        self,
        expense: FICCreateExpenseRequest,
        expense_id: int,
        *,
        numeration: str = "/TD",
        vat_value: int = 22,
        supplier_name: str = "",
        supplier_vat_number: str | None = None,
        supplier_country_iso: str = "XX",
        self_invoice_type: SelfInvoiceType = SelfInvoiceType.TD17,
        original_amount: float | None = None,
        original_currency: str = "",
        exchange_rate: float | None = None,
        original_invoice_number: str = "",
        original_invoice_date: str = "",
    ) -> FICCreateIssuedDocumentRequest:
        """Costruisce la request per creare un'autofattura SDI su FIC.

        Partendo dalla spesa estera già registrata (``expense``), calcola
        l'IVA al ``vat_value``% sull'imponibile e costruisce il payload
        per ``issued_documents`` di tipo ``self_supplier_invoice`` con
        i nodi SDI ``e_invoice`` (ei_raw) per la trasmissione telematica.

        La classificazione (TD17/18/19) viene passata esternamente in
        ``self_invoice_type``; il mapper espone ``classify_self_invoice_type()``
        per ottenerla dal ``FactumParseResult`` originale.

        Args:
            expense: La spesa estera già creata su FIC.
            expense_id: ID della spesa originale su FIC (per riferimento).
            numeration: Numerazione da usare (default "/TD").
            vat_value: Aliquota IVA percentuale (default 22).
            supplier_name: Nome fornitore estero per SDI.
            supplier_vat_number: P.IVA fornitore estero (se presente).
            supplier_country_iso: Codice ISO del paese fornitore.
            self_invoice_type: Tipologia SDI (TD17/TD18/TD19).
            original_amount: Importo originale in valuta estera prima della conversione
                             (es. 49.79). Se None, non viene inclusa la nota cambio.
            original_currency: Valuta originale (es. "USD"). Necessario solo se
                               original_amount è fornito.
            exchange_rate: Tasso di cambio applicato (es. 0.85477). Necessario solo
                           se original_amount è fornito.
            original_invoice_number: Numero fattura originale del fornitore estero
                                     (per DatiFattureCollegate in SDI).
            original_invoice_date: Data fattura originale del fornitore estero
                                   (per DatiFattureCollegate in SDI, AAAA-MM-GG).

        Returns:
            FICCreateIssuedDocumentRequest pronto per ``create_issued_document()``.
        """
        net = expense.amount_net
        vat = round(net * vat_value / 100, 2)
        gross = round(net + vat, 2)

        # Nota unica e pulita: nessuna concatenazione di expense.notes
        # (che conterrebbe il testo LLM generato in build_expense e la
        # nota di conversione da _convert_currency_strict, creando duplicazione).
        notes = (
            f"Autofattura ai sensi dell'art. 17 c. 2 DPR 633/72 per acquisto "
            f"da fornitore estero — rif. spesa FIC n. {expense_id}."
        )
        if original_amount is not None and original_currency and exchange_rate is not None:
            notes += (
                f" Importo originale: {original_amount:.2f} {original_currency} — "
                f"Tasso cambio {exchange_rate:.4f} applicato."
            )
        notes += (
            f" Documento elaborato via Factum Parse API "
            f"(Zero Data Retention)."
        )

        description = expense.description
        if description and self_invoice_type.value not in description:
            description = f"[{self_invoice_type.value}] {description}"

        return FICCreateIssuedDocumentRequest(
            entity_id=expense.entity_id if expense.entity_id is not None else 0,
            date=expense.date,
            numeration=numeration,
            description=description,
            amount_net=net,
            amount_vat=vat,
            amount_gross=gross,
            vat_value=vat_value,
            self_invoice_type=self_invoice_type,
            notes=notes,
            original_document_id=expense_id,
            original_document_description=expense.description,
            supplier_name=supplier_name,
            supplier_vat_number=supplier_vat_number,
            supplier_country_iso=supplier_country_iso,
            original_invoice_number=original_invoice_number,
            original_invoice_date=original_invoice_date,
        )
