"""Client asincrono per Factum Parse API (/v1/parse)."""

from __future__ import annotations

import hashlib
from typing import Any

import httpx

from factum_fic.config import Settings
from factum_fic.core.models import FactumResponse
from factum_fic.core.retry_policy import selective_retry

_HEADERS = {"User-Agent": "factum-fic/0.1.0"}

# Messaggio per rate limit 429
_RATE_LIMIT_MSG = (
    "Limite mensile di 30 fatture raggiunto. "
    "La quota si resetterà il 1° del prossimo mese."
)


class FactumClient:
    """Client per Factum Parse API con retry e rate limiting."""

    def __init__(self, settings: Settings) -> None:
        self._api_url = settings.factum_api_url.rstrip("/")
        self._api_key = settings.factum_api_key
        self._fic_company_id = getattr(settings, "fic_company_id", "")
        self._fic_vat = ""
        headers = {
            **_HEADERS,
            "X-API-Key": self._api_key,
            "Content-Type": "application/json",
        }
        # Aggiunge header FIC se disponibili
        if self._fic_company_id:
            headers["X-FIC-Company-ID"] = self._fic_company_id
        self._client = httpx.AsyncClient(
            base_url=self._api_url,
            headers=headers,
            timeout=60.0,
        )

    def update_fic_vat(self, vat_number: str) -> None:
        """Aggiorna l'header X-FIC-VAT con la partita IVA dell'azienda."""
        self._fic_vat = vat_number
        if vat_number:
            self._client.headers["X-FIC-VAT"] = vat_number

    async def close(self) -> None:
        await self._client.aclose()

    @selective_retry
    async def parse_text(self, text: str, doc_type: str = "auto") -> FactumResponse:
        """Invia un testo a /v1/parse e restituisce il risultato.

        Raises:
            httpx.HTTPStatusError: 4xx fallisce subito (nessun retry).
                Per 429 (rate limit), il messaggio contiene il testo amichevole.
            httpx.TimeoutException | httpx.ConnectError: retry 3x con backoff.
        """
        payload: dict[str, Any] = {"text": text, "doc_type": doc_type}
        response = await self._client.post("/v1/parse", json=payload)
        # Gestione 429 con messaggio amichevole prima del raise
        if response.status_code == 429:
            raise httpx.HTTPStatusError(
                _RATE_LIMIT_MSG,
                request=response.request,
                response=response,
            )
        response.raise_for_status()
        data = response.json()
        return FactumResponse(**data)

    async def health(self) -> bool:
        """Verifica connettività con Factum API."""
        try:
            r = await self._client.get("/health", timeout=10.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    @staticmethod
    def compute_hash(content: bytes) -> str:
        """SHA-256 di un file per deduplicazione."""
        return hashlib.sha256(content).hexdigest()
