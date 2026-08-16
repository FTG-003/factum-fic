"""Client asincrono per Fatture in Cloud v2 API."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from factum_fic.config import Settings
from factum_fic.core.models import (
    FICCreateExpenseRequest,
    FICCreateSupplierRequest,
    FICExpenseResponse,
)

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

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
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

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
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

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def create_expense(self, expense: FICCreateExpenseRequest) -> FICExpenseResponse:
        """Crea un documento di spesa (o bozza autofattura) su FIC.

        Args:
            expense: Dati della spesa da registrare.

        Returns:
            FICExpenseResponse con id e stato del documento creato.
        """
        # Costruisce il payload nel formato atteso da FIC v2
        fic_payload: dict[str, Any] = {
            "type": "self_invoice" if expense.is_autofattura else "expense",
            "entity": {"id": expense.entity_id}
            if expense.entity_id
            else expense.entity.model_dump(exclude_none=True)
            if expense.entity
            else None,  # type: ignore[union-attr]
            "date": expense.date,
            "category": expense.category,
            "description": expense.description,
            "amount_net": expense.amount_net,
            "amount_vat": expense.amount_vat,
            "amount_gross": expense.amount_gross or expense.amount_net,
            "currency": {
                "id": expense.currency,
                "exchange_rate": 1.08 if expense.currency == "USD" else 0.86 if expense.currency == "GBP" else 1.0,
            },
            "has_iva": expense.has_iva,
            "payments_list": [
                {
                    "amount": expense.amount_gross or expense.amount_net,
                    "due_date": expense.due_date or expense.date or datetime.date.today().isoformat(),
                    "status": "not_paid",
                }
            ],
        }
        if expense.notes:
            fic_payload["notes"] = expense.notes

        payload = {"data": fic_payload}
        response = await self._client.post(
            f"/c/{self._company_id}/received_documents",
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        return FICExpenseResponse(**data.get("data", {}))

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def upload_received_document_attachment(
        self,
        document_id: int,
        file_path: str | Path,
    ) -> dict[str, Any]:
        """Carica un allegato (PDF/immagine) a un documento di spesa su FIC.

        Args:
            document_id: ID del documento ricevuto (da create_expense).
            file_path: Percorso del file da allegare (PDF, PNG, JPG, TIFF).

        Returns:
            Risposta JSON da FIC.

        Raises:
            httpx.HTTPStatusError: Se FIC rifiuta l'upload.
            FileNotFoundError: Se il file non esiste.
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File allegato non trovato: {path}")

        # FIC v2 accetta multipart/form-data con campo "attachment"
        # Il Content-Type dell'allegato è dedotto dall'estensione
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

        # Costruisce la richiesta multipart senza Content-Type globale
        # (httpx lo imposta automaticamente con il boundary)
        content = path.read_bytes()
        files = {"attachment": (path.name, content, media_type)}

        response = await self._client.post(
            f"/c/{self._company_id}/received_documents/{document_id}/attachment",
            files=files,
        )
        response.raise_for_status()
        return response.json()

    async def health(self) -> bool:
        """Verifica connettività con FIC API.

        Usa /user/info (non richiede company_id) per testare
        l'autenticazione senza necessità di permessi specifici.
        """
        try:
            r = await self._client.get("/user/info", timeout=10.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False
