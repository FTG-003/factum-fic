"""Test della politica di retry selettivo: 4xx fallisce subito, 5xx/network retry."""

from __future__ import annotations

import httpx
import pytest

from factum_fic.core.retry_policy import _is_retriable


def test_retriable_timeout() -> None:
    """httpx.TimeoutException → retriable."""
    assert _is_retriable(httpx.TimeoutException("timeout")) is True


def test_retriable_connect_error() -> None:
    """httpx.ConnectError → retriable."""
    assert _is_retriable(httpx.ConnectError("connection refused")) is True


def test_retriable_remote_protocol() -> None:
    """httpx.RemoteProtocolError → retriable."""
    assert _is_retriable(httpx.RemoteProtocolError("connection closed")) is True


def test_non_retriable_400() -> None:
    """400 Bad Request → NON retriable."""
    request = httpx.Request("POST", "https://api.test.com/endpoint")
    response = httpx.Response(400, request=request)
    assert _is_retriable(httpx.HTTPStatusError("bad request", request=request, response=response)) is False


def test_non_retriable_404() -> None:
    """404 Not Found → NON retriable."""
    request = httpx.Request("GET", "https://api.test.com/missing")
    response = httpx.Response(404, request=request)
    assert _is_retriable(httpx.HTTPStatusError("not found", request=request, response=response)) is False


def test_non_retriable_422() -> None:
    """422 Unprocessable Content → NON retriable (errore logico)."""
    request = httpx.Request("POST", "https://api.test.com/validate")
    response = httpx.Response(422, request=request)
    assert _is_retriable(httpx.HTTPStatusError("unprocessable", request=request, response=response)) is False


def test_retriable_429() -> None:
    """429 Too Many Requests → retriable (rate limit)."""
    request = httpx.Request("POST", "https://api.test.com/endpoint")
    response = httpx.Response(429, request=request)
    assert _is_retriable(httpx.HTTPStatusError("rate limit", request=request, response=response)) is True


def test_retriable_500() -> None:
    """500 Internal Server Error → retriable."""
    request = httpx.Request("POST", "https://api.test.com/endpoint")
    response = httpx.Response(500, request=request)
    assert _is_retriable(httpx.HTTPStatusError("server error", request=request, response=response)) is True


def test_retriable_502() -> None:
    """502 Bad Gateway → retriable."""
    request = httpx.Request("POST", "https://api.test.com/endpoint")
    response = httpx.Response(502, request=request)
    assert _is_retriable(httpx.HTTPStatusError("bad gateway", request=request, response=response)) is True


def test_retriable_503() -> None:
    """503 Service Unavailable → retriable."""
    request = httpx.Request("POST", "https://api.test.com/endpoint")
    response = httpx.Response(503, request=request)
    assert _is_retriable(httpx.HTTPStatusError("unavailable", request=request, response=response)) is True


def test_non_retriable_generic_exception() -> None:
    """Eccezione generica non httpx → NON retriable (cautela)."""
    assert _is_retriable(ValueError("something wrong")) is False