"""Client asincrono per Fatture in Cloud v2 API."""

from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Any

import httpx

from factum_fic.config import Settings
from factum_fic.core.models import (
    FICCreateExpenseRequest,
    FICCreateIssuedDocumentRequest,
    FICCreateSupplierRequest,
    FICExpenseResponse,
    FICIssuedDocumentResponse,
)
from factum_fic.core.retry_policy import selective_retry

_HEADERS = {"User-Agent": "factum-fic/0.1.0"}
logger = logging.getLogger(__name__)


class FICClient:
    """Client per Fatture in Cloud v2 API con retry e rate limiting."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.fic_base_url.rstrip("/")
        self._api_key = settings.fic_token
        self._company_id = settings.fic_company_id
        self._auto_paid = settings.fic_auto_paid
        self._payment_account_name = settings.fic_payment_account_name
        self._payment_account_id: int | None = settings.fic_payment_account_id
        self._payment_account_resolved: int | None = None
        self._payment_account_name_resolved: str | None = None
        self._generate_self_invoice = settings.fic_generate_self_invoice
        self._self_invoice_numeration = settings.fic_self_invoice_numeration
        self._self_invoice_vat_value = settings.fic_self_invoice_vat_value
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                **_HEADERS,
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    @selective_retry
    async def get_payment_accounts(self) -> list[dict[str, Any]]:
        """Recupera l'elenco dei conti di pagamento disponibili su FIC v2.

        Returns:
            Lista di dizionari con id, name, type, iban, sia, virtual.

        Raises:
            httpx.HTTPStatusError: Se l'API rifiuta la richiesta.
        """
        response = await self._client.get(
            f"/c/{self._company_id}/info/payment_accounts",
        )
        response.raise_for_status()
        data = response.json()
        return data.get("data", [])

    async def _resolve_payment_account(self) -> int | None:
        """Risolve il conto di pagamento da usare per auto-pagamento.

        Ordine di priorità:
          1. FIC_PAYMENT_ACCOUNT_ID (override esplicito via settings)
          2. Cache interna (evita chiamate API ripetute)
          3. FIC_PAYMENT_ACCOUNT_NAME → cerca per nome su FIC
          4. Fallback: primo conto disponibile

        Returns:
            ID del conto di pagamento, o None se non disponibile.
        """
        # Override esplicito via ID
        if self._payment_account_id is not None:
            self._payment_account_name_resolved = self._payment_account_name or "Override (ID)"
            return self._payment_account_id

        # Cache già risolta
        if self._payment_account_resolved is not None:
            return self._payment_account_resolved

        # Query API
        try:
            accounts = await self.get_payment_accounts()
        except Exception:
            logger.warning("Impossibile recuperare conti di pagamento da FIC")
            return None

        if not accounts:
            logger.warning("Nessun conto di pagamento disponibile su FIC")
            return None

        # Match per nome (case-insensitive)
        if self._payment_account_name:
            for acc in accounts:
                if acc.get("name", "").lower() == self._payment_account_name.lower():
                    self._payment_account_resolved = acc.get("id")
                    self._payment_account_name_resolved = acc.get("name")
                    return self._payment_account_resolved

        # Fallback: primo conto disponibile
        self._payment_account_resolved = accounts[0].get("id")
        self._payment_account_name_resolved = accounts[0].get("name", "?")
        logger.info(
            "Usato conto di pagamento '%s' (id=%s) come fallback",
            self._payment_account_name_resolved,
            self._payment_account_resolved,
        )
        return self._payment_account_resolved

    async def resolve_payment_account(self) -> dict[str, Any] | None:
        """Risolve il conto di pagamento attivo per l'auto-pagamento spese.

        Espone al dashboard ``factum-fic status`` il conto che verrà usato
        per marcare le spese come saldate (FIC_AUTO_PAID).

        Returns:
            Dizionario con ``id`` e ``name`` del conto risolto,
            o None se nessun conto è disponibile/configurato.
        """
        account_id = await self._resolve_payment_account()
        if account_id is None:
            return None
        return {
            "id": account_id,
            "name": self._payment_account_name_resolved or "—",
        }

    @selective_retry
    async def create_supplier(self, supplier: FICCreateSupplierRequest) -> dict[str, Any]:
        """Crea un fornitore su Fatture in Cloud.

        Args:
            supplier: Dati del fornitore da creare.

        Returns:
            Risposta JSON da FIC con i dati dell'entity creata.
        """
        payload = {"data": supplier.model_dump(exclude_none=True)}
        response = await self._client.post(
            f"/c/{self._company_id}/entities/suppliers",
            json=payload,
        )
        response.raise_for_status()
        return response.json()

    @selective_retry
    async def search_supplier(self, name: str, vat_number: str | None = None) -> dict[str, Any] | None:
        """Cerca un fornitore esistente per partita IVA o nome.

        Ordine di ricerca:
          1. Per partita IVA (se fornita)
          2. Per nome (fallback)

        Returns:
            Il primo fornitore matchato, o None se non trovato.
        """
        # 1. Cerca per partita IVA
        if vat_number:
            params: dict[str, str] = {"field": "vat_number", "query": vat_number}
            response = await self._client.get(
                f"/c/{self._company_id}/entities/suppliers",
                params=params,
            )
            response.raise_for_status()
            data = response.json()
            entities = data.get("data", [])
            if entities:
                return entities[0]

        # 2. Fallback: cerca per nome
        params = {"field": "name", "query": name}
        response = await self._client.get(
            f"/c/{self._company_id}/entities/suppliers",
            params=params,
        )
        response.raise_for_status()
        data = response.json()
        entities = data.get("data", [])
        return entities[0] if entities else None

    # ── Ricerca documento esistente ────────────────────────────────────────────

    @selective_retry
    async def search_document(
        self,
        entity_id: int | None = None,
        description: str | None = None,
        date: str | None = None,
        invoice_number: str | None = None,
    ) -> dict[str, Any] | None:
        """Cerca un documento di spesa esistente su FIC con match client-side.

        FIC NON supporta il filtro per ``description`` via query params.
        Per evitare falsi positivi (due documenti diversi dello stesso
        fornitore nella stessa data), la funzione:

        1. Interroga FIC per ``entity_id`` + ``date_from``/``date_to``
        2. Filtra **in memoria** su descrizione E numero documento
        3. Restituisce il match solo se descrizione O numero documento coincidono

        Args:
            entity_id: ID fornitore su FIC.
            description: Descrizione del documento da cercare (match fuzzy).
            date: Data documento (YYYY-MM-DD).
            invoice_number: Numero documento per match esatto.

        Returns:
            Il documento matchato, o None se nessun match.
        """
        params: dict[str, str] = {"per_page": "50"}
        if entity_id:
            params["entity_id"] = str(entity_id)
        if date:
            params["date_from"] = date
            params["date_to"] = date
        response = await self._client.get(
            f"/c/{self._company_id}/received_documents",
            params=params,
        )
        response.raise_for_status()
        data = response.json()
        docs = data.get("data", [])
        if not docs:
            return None

        target_desc = (description or "").strip().lower()
        target_num = (invoice_number or "").strip().lower()

        # Se non abbiamo descrizione né numero, non possiamo matchare
        if not target_desc and not target_num:
            return None

        for doc in docs:
            doc_desc = (doc.get("description") or "").strip().lower()
            doc_num = (doc.get("invoice_number") or doc.get("numero") or "").strip().lower()

            # Match per descrizione (case-insensitive, containment)
            if target_desc and target_desc in doc_desc:
                return doc

            # Match per numero documento
            if target_num and (target_num == doc_num or target_num in doc_num):
                return doc

        return None

    # ── Recupero spesa per ID ────────────────────────────────────────────────

    @selective_retry
    async def get_expense(self, expense_id: int) -> dict[str, Any] | None:
        """Recupera un documento di spesa da FIC per ID.

        Usato da ``riprova-autofatture`` per ricostruire i dati necessari
        alla generazione dell'autofattura SDI dopo un fallimento parziale.

        Args:
            expense_id: ID del documento di spesa su FIC.

        Returns:
            Dizionario con i dati della spesa, o None se non trovato.
        """
        response = await self._client.get(
            f"/c/{self._company_id}/received_documents/{expense_id}",
        )
        response.raise_for_status()
        data = response.json()
        return data

    # ── Upload attachment ─────────────────────────────────────────────────────

    @selective_retry
    async def get_attachment_token(self, file_path: str | Path) -> str:
        """Carica un file PDF su FIC v2 e restituisce un attachment_token.

        Flusso FIC v2:
        1. POST /received_documents/attachment (multipart: campo 'attachment')
        2. FIC restituisce {"data": {"attachment_token": "..."}}
        3. Il token va incluso nel payload di creazione documento.

        Args:
            file_path: Percorso del file PDF da allegare.

        Returns:
            attachment_token (stringa) da usare in create_expense.

        Raises:
            httpx.HTTPStatusError: Se FIC rifiuta l'upload.
            FileNotFoundError: Se il file non esiste.
            ValueError: Se il token non è presente nella risposta.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File allegato non trovato: {path}")

        suffix = path.suffix.lower()
        media_type_map = {
            ".pdf": "application/pdf",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".tiff": "image/tiff",
            ".tif": "image/tiff",
        }
        media_type = media_type_map.get(suffix, "application/octet-stream")

        content = path.read_bytes()
        files = {"attachment": (path.name, content, media_type)}
        data = {"filename": path.name}

        # Client separato senza Content-Type per multipart
        async with httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "User-Agent": "factum-fic/0.1.0",
                "Authorization": f"Bearer {self._api_key}",
            },
            timeout=60.0,
        ) as upload_client:
            response = await upload_client.post(
                f"/c/{self._company_id}/received_documents/attachment",
                files=files,
                data=data,
            )
        response.raise_for_status()
        data = response.json()
        token = (data.get("data") or {}).get("attachment_token")
        if not token:
            raise ValueError(
                f"FIC non ha restituito attachment_token: {response.text[:300]}"
            )
        return token

    # ── Creazione documento ───────────────────────────────────────────────────

    @selective_retry
    async def create_issued_document(
        self,
        doc: FICCreateIssuedDocumentRequest,
    ) -> FICIssuedDocumentResponse:
        """Crea un documento emesso (autofattura SDI) su FIC.

        Genera una bozza di ``issued_documents`` di tipo ``self_supplier_invoice``
        per acquisti da fornitori esteri (art. 17 c. 2 DPR 633/72), con IVA
        calcolata sull'imponibile e i nodi SDI ``e_invoice`` (ei_raw)
        per la trasmissione telematica al Sistema di Interscambio.

        La classificazione SDI (TD17/18/19) è determinata dal mapper
        ``classify_self_invoice_type()`` e passata via ``doc.self_invoice_type``.

        Args:
            doc: Dati dell'autofattura SDI da creare.

        Returns:
            FICIssuedDocumentResponse con id e stato del documento creato.
        """
        gross = doc.amount_gross or (doc.amount_net + doc.amount_vat)
        # FIC v2 con e_invoice richiede entity.name (non solo id) altrimenti 422
        entity_payload: dict[str, Any] = {"id": doc.entity_id}
        if doc.supplier_name:
            entity_payload["name"] = doc.supplier_name
        fic_payload: dict[str, Any] = {
            "type": "self_supplier_invoice",
            "entity": entity_payload,
            "date": doc.date,
            "numeration": doc.numeration,
            "description": doc.description,
            "amount_net": float(doc.amount_net),
            "amount_vat": float(doc.amount_vat),
            "amount_gross": round(float(gross), 2),
            "notes": doc.notes,
            "items_list": [
                {
                    "name": doc.description or "Servizi software / hosting estero",
                    "net_price": float(doc.amount_net),
                    "qty": 1,
                    "vat": {"id": 0, "value": doc.vat_value},
                }
            ],
            "payments_list": [
                {
                    "amount": round(float(gross), 2),
                    "due_date": doc.date,
                }
            ],
        }

        # Attiva trasmissione SDI (booleano) per autofattura esteri.
        # Occorre fornire anche ei_data (payment_method, ecc.) altrimenti
        # FIC restituisce 422. Se il campo venisse rifiutato (es. 403
        # NO_PERMISSION o 422 per mancanza di ei_data), la pipeline gestisce
        # il fallimento gracefulmente (log warning + prosegue).
        fic_payload["e_invoice"] = True
        fic_payload["ei_data"] = {
            "payment_method": "MP08",
            "vat_kind": "I",
            "reverse_charge": "N6.3",  # acquisto servizi esteri art. 17 c. 2
        }

        payload = {"data": fic_payload}
        response = await self._client.post(
            f"/c/{self._company_id}/issued_documents",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return FICIssuedDocumentResponse(**data.get("data", {}))

    @selective_retry
    async def create_expense(
        self,
        expense: FICCreateExpenseRequest,
        *,
        attachment_token: str | None = None,
        paid: bool | None = None,
    ) -> FICExpenseResponse:
        """Crea un documento di spesa (o bozza autofattura) su FIC.

        Se ``paid=True``, il documento viene marcato come saldato alla data
        della fattura, associandolo al conto di pagamento configurato (vedi
        FIC_AUTO_PAID, FIC_PAYMENT_ACCOUNT_NAME, FIC_PAYMENT_ACCOUNT_ID).

        Args:
            expense: Dati della spesa da registrare.
            attachment_token: Token ottenuto da get_attachment_token() per
                              allegare un PDF contestualmente alla creazione.
            paid: Se True, marca la spesa come pagata. Se None, usa il valore
                  di ``fic_auto_paid`` dalle settings.

        Returns:
            FICExpenseResponse con id e stato del documento creato.
        """
        # Costruisce il payload nel formato atteso da FIC v2
        fic_payload: dict[str, Any] = {
            "type": "self_invoice" if expense.is_autofattura else "expense",
            "description": expense.description or "Acquisto servizi / SaaS",
            "entity": {"id": expense.entity_id}
            if expense.entity_id
            else expense.entity.model_dump(exclude_none=True)
            if expense.entity
            else {"name": "Fornitore sconosciuto"},
            "date": expense.date or datetime.date.today().isoformat(),
            "due_date": expense.due_date or expense.date or datetime.date.today().isoformat(),
            "category": expense.category or "Altri costi",
            "amount_net": expense.amount_net,
            "amount_vat": expense.amount_vat,
            "amount_gross": expense.amount_gross if expense.amount_gross is not None else expense.amount_net,
            "rc_center": "",
        }
        if expense.notes:
            fic_payload["notes"] = expense.notes

        # FIC v2 richiede payments_list per salvare importi non-zero
        gross = expense.amount_gross if expense.amount_gross is not None else expense.amount_net
        if gross > 0:
            payment_entry: dict[str, Any] = {
                "amount": gross,
                "due_date": expense.due_date or expense.date or datetime.date.today().isoformat(),
            }

            # Auto-pagamento: marca come saldato se abilitato
            do_paid = self._auto_paid if paid is None else paid
            if do_paid:
                payment_account_id = await self._resolve_payment_account()
                if payment_account_id is not None:
                    # FIC v2: status "paid" richiede paid_date + payment_account
                    # (oggetto, non payment_account_id flat) altrimenti viene ignorato
                    payment_entry["status"] = "paid"
                    payment_entry["paid_date"] = expense.date or datetime.date.today().isoformat()
                    payment_entry["payment_account"] = {"id": payment_account_id}
                else:
                    logger.warning(
                        "Auto-pagamento abilitato ma nessun conto disponibile: "
                        "spesa registrata senza stato pagato"
                    )

            fic_payload["payments_list"] = [payment_entry]

        # items_list: detraibilità esplicita per Regime Forfettario
        # - tax_deductibility: 100  → costo deducibile al 100%
        # - vat_deductibility: 0    → IVA indetraibile (Forfettario)
        fic_payload["items_list"] = [
            {
                "name": expense.description or "Acquisto servizi",
                "net_price": float(expense.amount_net),
                "qty": 1,
                "category": expense.category or "Servizi",
                "tax_deductibility": 100,
                "vat_deductibility": 0,
                "vat": {"id": 0, "value": 0},
            }
        ]

        # Attachment token: PDF allegato in unico colpo
        if attachment_token:
            fic_payload["attachment_token"] = attachment_token

        payload = {"data": fic_payload}
        response = await self._client.post(
            f"/c/{self._company_id}/received_documents",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return FICExpenseResponse(**data.get("data", {}))

    @selective_retry
    async def get_user_companies(self) -> list[dict[str, Any]]:
        """Recupera l'elenco delle aziende associate all'utente FIC.

        Returns:
            Lista di dizionari con id, name, vat_number, fiscal_code.
        """
        response = await self._client.get("/user/companies")
        response.raise_for_status()
        raw = response.json()
        nested = raw.get("data", {})
        if isinstance(nested, dict):
            return nested.get("companies", [])
        return nested if isinstance(nested, list) else []

    async def health(self) -> bool:
        """Verifica connettività con FIC API."""
        try:
            r = await self._client.get("/user/info", timeout=10.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    # ── Info azienda ──────────────────────────────────────────────────────────

    @selective_retry
    async def get_company_info(self) -> dict[str, Any]:
        """Recupera le informazioni fiscali dell'azienda da FIC v2.

        Returns:
            Dizionario con id, name, tax_regime, vat_number, fiscal_code.

        Raises:
            httpx.HTTPStatusError: Se l'API rifiuta la richiesta.
        """
        response = await self._client.get(
            f"/c/{self._company_id}/company/info",
        )
        response.raise_for_status()
        data = response.json()
        return data.get("data", {})
