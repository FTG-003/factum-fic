"""Politica di retry condivisa per FactumClient e FICClient.

Solo errori transitori di rete e server 5xx/429 sono retriable.
Errori 4xx (client) devono fallire immediatamente.
"""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

_RETRIABLE_NETWORK = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.RemoteProtocolError,
)


def _is_retriable(exc: BaseException) -> bool:
    """True per errori di rete/transitori; False per 4xx client."""
    # Errori di rete puri
    if isinstance(exc, _RETRIABLE_NETWORK):
        return True
    # httpx.HTTPStatusError: 429 / 5xx retriable; 4xx no
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return True
        if 500 <= status < 600:
            return True
        return False
    return False


# Decoratore riutilizzabile: 3 tentativi, backoff esponenziale 1→2→4s
# Solo errori di rete + 429/5xx; 4xx fallisce subito.
selective_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception(_is_retriable),
    reraise=True,
)