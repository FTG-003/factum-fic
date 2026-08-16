"""Client asincrono per Fatture in Cloud v2 API."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

import httpx

from factum_fic.config import Settings
from factum_fic.core.models import (
    FICCreateExpenseRequest,
    FICCreateSupplierRequest,
    FICExpenseResponse,
)
from factum_fic.core.retry_policy import selective_retry

_HEADERS = {"User-Agent": "factum-fic/0.1.0"}


class FICClient:
    """Client per Fatture in Cloud v2 API con retry e rate limiting."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.fic_base_url.rstrip("/")
        self._api_key = settings.fic_api_key
        self._company_id = settings.fic_company_id
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
        """Cerca un fornitore esistente per nome o partita IVA.

        Returns:
            Il primo fornitore matchato, o None.
        """
        params: dict[str, str] = {"field": "name", "query": name}
        if vat_number:
            params["field"] = "vat_number"
            params["query"] = vat_number
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
    ) -> dict[str, Any] | None:
        """Cerca un documento di spesa esistente su FIC per descrizione e fornitore.

        Usato per anti-duplicazione: se un documento con la stessa descrizione
        e data esiste già (es. da un timeout/retry precedente), non lo ricrea.

        Args:
            entity_id: ID fornitore su FIC.
            description: Descrizione del documento da cercare.
            date: Data documento (YYYY-MM-DD).

        Returns:
            Il primo documento matchato, o None.
        """
        params: dict[str, str] = {}
        if entity_id:
            params["entity_id"] = str(entity_id)
        if description:
            params["description"] = description
        if date:
            params["date_from"] = date
            params["date_to"] = date
        params["per_page"] = "5"
        response = await self._client.get(
            f"/c/{self._company_id}/received_documents",
            params=params,
        )
        response.raise_for_status()
        data = response.json()
        docs = data.get("data", [])
        if not docs:
            return None
        # Match esatto per descrizione (o primo risultato se descrizione non data)
        if description and len(docs) > 1:
            for doc in docs:
                if doc.get("description") == description:
                    return doc
        return docs[0]

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
    async def create_expense(
        self,
        expense: FICCreateExpenseRequest,
        *,
        attachment_token: str | None = None,
    ) -> FICExpenseResponse:
        """Crea un documento di spesa (o bozza autofattura) su FIC.

        Args:
            expense: Dati della spesa da registrare.
            attachment_token: Token ottenuto da get_attachment_token() per
                              allegare un PDF contestualmente alla creazione.

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
            fic_payload["payments_list"] = [
                {
                    "amount": gross,
                    "due_date": expense.due_date or expense.date or datetime.date.today().isoformat(),
                }
            ]

        # items_list: detraibilità esplicita per Regime Forfettario
        # - tax_deductibility: 100  → costo deducibile al 100%
        # - vat_deductibility: 0    → IVA indetraibile (Forfettario)
        fic_payload["items_list"] = [
            {
                "name": expense.description or "Acquisto servizi",
                "net_price": expense.amount_net,
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

    async def health(self) -> bool:
        """Verifica connettività con FIC API."""
        try:
            r = await self._client.get("/user/info", timeout=10.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False