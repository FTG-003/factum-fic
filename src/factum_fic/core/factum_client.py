"""Client asincrono per Factum Parse API (/v1/parse)."""

from __future__ import annotations

import hashlib
from typing import Any

import httpx

from factum_fic.config import Settings
from factum_fic.core.models import FactumResponse
from factum_fic.core.retry_policy import selective_retry

_HEADERS = {"User-Agent": "factum-fic/0.1.0"}


class FactumClient:
    """Client per Factum Parse API con retry e rate limiting."""

    def __init__(self, settings: Settings) -> None:
        self._api_url = settings.factum_api_url.rstrip("/")
        self._api_key = settings.factum_api_key
        self._client = httpx.AsyncClient(
            base_url=self._api_url,
            headers={
                **_HEADERS,
                "X-API-Key": self._api_key,
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    @selective_retry
    async def parse_text(self, text: str, doc_type: str = "auto") -> FactumResponse:
        """Invia un testo a /v1/parse e restituisce il risultato.

        Raises:
            httpx.HTTPStatusError: 4xx fallisce subito (nessun retry).
            httpx.TimeoutException | httpx.ConnectError: retry 3x con backoff.
        """
        payload: dict[str, Any] = {"text": text, "doc_type": doc_type}
        response = await self._client.post("/v1/parse", json=payload)
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