"""Client asincrono per Factum Parse API (/v1/parse)."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

import httpx

from factum_fic.config import Settings
from factum_fic.core.models import (
    FactumAuthError,
    FactumInsufficientCreditsError,
    FactumNetworkError,
    FactumParsingError,
    FactumQuotaExceededError,
    FactumResponse,
)
from factum_fic.core.retry_policy import selective_retry

_HEADERS = {"User-Agent": "factum-fic/0.1.0"}

logger = logging.getLogger(__name__)

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

        Gestisce gli errori HTTP in modo granulare:
        - 401/403 → FactumAuthError (credenziali non valide)
        - 402     → FactumInsufficientCreditsError (crediti insufficienti)
        - 422     → FactumParsingError (errore permanente di parsing)
        - 429     → FactumQuotaExceededError (crediti esauriti)
        - 5xx     → FactumNetworkError (errore transitorio server)
        - Timeout/Connessione → FactumNetworkError (rete assente)
        """
        payload: dict[str, Any] = {"text": text, "doc_type": doc_type}
        try:
            response = await self._client.post("/v1/parse", json=payload)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            raise FactumNetworkError(
                f"Errore di rete durante la chiamata Factum: {exc}"
            ) from exc

        # 401/403 → credenziali non valide
        if response.status_code in (401, 403):
            raise FactumAuthError(
                "Chiave API Factum non valida o revocata. "
                "Elaborazione PDF sospesa."
            )

        # 402 → crediti insufficienti
        if response.status_code == 402:
            body = response.text[:500]
            raise FactumInsufficientCreditsError(
                f"Crediti insufficienti per completare il parsing (HTTP 402): {body}"
            )

        # 422 → errore permanente di parsing (testo non elaborabile)
        if response.status_code == 422:
            body = response.text[:500]
            raise FactumParsingError(
                f"Factum non ha potuto elaborare il testo (HTTP 422): {body}"
            )

        # 429 → crediti esauriti
        if response.status_code == 429:
            raise FactumQuotaExceededError(_RATE_LIMIT_MSG)

        # 5xx → errore transitorio del server
        if 500 <= response.status_code < 600:
            raise FactumNetworkError(
                f"Errore server Factum (HTTP {response.status_code}): "
                f"{response.text[:300]}"
            )

        response.raise_for_status()
        data = response.json()
        logger.info("--- RAW FACTUM RESPONSE ---\n%s\n---------------------------", response.text)
        return FactumResponse(**data)

    async def health(self) -> bool:
        """Verifica connettività con Factum API."""
        try:
            r = await self._client.get("/health", timeout=10.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def get_checkout_link(self) -> dict:
        """Chiama GET /api/v1/auth/checkout-link e restituisce il link di ricarica.

        Returns:
            Dizionario con checkout_url, piva, variant_id.

        Raises:
            FactumAuthError: Se la chiave API non è valida (401).
            FactumNetworkError: Per errori di rete o 5xx.
        """
        try:
            r = await self._client.get("/api/v1/auth/checkout-link", timeout=15.0)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
            raise FactumNetworkError(
                f"Errore di rete durante la richiesta checkout link: {exc}"
            ) from exc

        if r.status_code == 401:
            raise FactumAuthError(
                "Chiave API Factum non valida. "
                "Impossibile generare il link di ricarica."
            )

        if r.status_code == 404:
            # Nessun account associato alla chiave
            return {
                "checkout_url": "",
                "piva": "",
                "variant_id": "",
            }

        if 500 <= r.status_code < 600:
            raise FactumNetworkError(
                f"Errore server Factum nel checkout-link (HTTP {r.status_code}): "
                f"{r.text[:300]}"
            )

        r.raise_for_status()
        return r.json()

    @staticmethod
    def compute_hash(content: bytes) -> str:
        """SHA-256 di un file per deduplicazione."""
        return hashlib.sha256(content).hexdigest()
