"""Test per il modulo verify: controllo regime forfettario e binding metadati.

Usa httpx MockTransport per simulare FIC API e Factum API.
"""

from __future__ import annotations

import pytest
import httpx

from factum_fic.cli.verify import verify_and_bind, ForfettarioCheckError
from factum_fic.config import Settings
from factum_fic.core.factum_client import FactumClient
from factum_fic.core.fic_client import FICClient


# ── helpers ──────────────────────────────────────────────────────────────

_VALID_COMPANY = {
    "id": 12345,
    "name": "Test S.r.l.",
    "tax_regime": "forfettario",
    "vat_number": "IT01234567890",
    "fiscal_code": "TSTSRM00A01H501A",
}

_NON_FORFETTARIO = {
    "id": 67890,
    "name": "Ordinaria S.p.A.",
    "tax_regime": "ordinario",
    "vat_number": "IT09876543210",
    "fiscal_code": "ORDSMA99B02H601B",
}


def _make_settings() -> Settings:
    return Settings(
        factum_api_url="http://factum.test",
        factum_api_key="test-key",
        fic_base_url="http://fic.test",
        fic_api_key="test-fic-key",
        fic_company_id="12345",
    )  # type: ignore[call-arg]


def _make_fic_client(*, transport: httpx.MockTransport) -> httpx.AsyncClient:
    """Crea un AsyncClient mock per FIC con base_url e trasporto."""
    return httpx.AsyncClient(base_url="http://fic.test", transport=transport)


def _make_factum_client(
    *, transport: httpx.MockTransport, extra_headers: dict | None = None
) -> httpx.AsyncClient:
    """Crea un AsyncClient mock per Factum con base_url, header standard e trasporto."""
    base_headers = {
        "User-Agent": "factum-fic/0.1.0",
        "X-API-Key": "test-key",
        "Content-Type": "application/json",
        "X-FIC-Company-ID": "12345",
    }
    if extra_headers:
        base_headers.update(extra_headers)
    return httpx.AsyncClient(
        base_url="http://factum.test",
        transport=transport,
        headers=base_headers,
    )


def _mock_fic_company(company_data: dict) -> httpx.MockTransport:
    """Crea un trasporto mock che risponde a /company/info."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/company/info") and request.method == "GET":
            return httpx.Response(200, json={"data": company_data})
        # Health check
        if request.url.path == "/user/info" and request.method == "GET":
            return httpx.Response(200, json={"data": {"id": 1, "email": "test@test.com"}})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _mock_factum_transport() -> httpx.MockTransport:
    """Crea un trasporto mock per Factum (sempre ok)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health" and request.method == "GET":
            return httpx.Response(200, json={"status": "healthy"})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


# ── Test per ForfettarioCheckError ─────────────────────────────────────


class TestForfettarioCheckError:
    def test_is_runtime_error(self) -> None:
        err = ForfettarioCheckError("test")
        assert isinstance(err, RuntimeError)

    def test_message_preserved(self) -> None:
        msg = "Regime non forfettario"
        err = ForfettarioCheckError(msg)
        assert str(err) == msg


# ── Test per verify_and_bind ───────────────────────────────────────────


class TestVerifyAndBind:
    """Testa la funzione verify_and_bind con diversi scenari di regime."""

    @pytest.mark.asyncio
    async def test_forfettario_success(self) -> None:
        """Il regime forfettario deve passare e restituire i dati azienda."""
        settings = _make_settings()
        fic = FICClient(settings)
        factum = FactumClient(settings)

        fic._client = _make_fic_client(transport=_mock_fic_company(_VALID_COMPANY))
        factum._client = _make_factum_client(transport=_mock_factum_transport())

        try:
            info = await verify_and_bind(fic, factum)
            assert info["id"] == 12345
            assert info["name"] == "Test S.r.l."
            assert info["tax_regime"] == "forfettario"
            assert info["vat_number"] == "IT01234567890"
            assert info["fiscal_code"] == "TSTSRM00A01H501A"
            # Verifica che FactumClient abbia ricevuto la P.IVA
            assert factum._client.headers.get("X-FIC-VAT") == "IT01234567890"
            assert factum._client.headers.get("X-FIC-Company-ID") == "12345"
        finally:
            await fic.close()
            await factum.close()

    @pytest.mark.asyncio
    async def test_forfettario_rf19(self) -> None:
        """Il regime 'rf19' deve essere riconosciuto come forfettario."""
        company = dict(_VALID_COMPANY, tax_regime="rf19")
        settings = _make_settings()
        fic = FICClient(settings)
        factum = FactumClient(settings)

        fic._client = _make_fic_client(transport=_mock_fic_company(company))
        factum._client = _make_factum_client(transport=_mock_factum_transport())

        try:
            info = await verify_and_bind(fic, factum)
            assert info["tax_regime"] == "rf19"
        finally:
            await fic.close()
            await factum.close()

    @pytest.mark.asyncio
    async def test_ordinario_raises(self) -> None:
        """Il regime ordinario deve sollevare ForfettarioCheckError."""
        settings = _make_settings()
        fic = FICClient(settings)
        factum = FactumClient(settings)

        fic._client = _make_fic_client(transport=_mock_fic_company(_NON_FORFETTARIO))
        factum._client = _make_factum_client(transport=_mock_factum_transport())

        try:
            with pytest.raises(ForfettarioCheckError) as exc:
                await verify_and_bind(fic, factum)
            assert "riservato esclusivamente" in str(exc.value).lower() and "forfettario" in str(exc.value).lower()
        finally:
            await fic.close()
            await factum.close()

    @pytest.mark.asyncio
    async def test_empty_tax_regime_raises(self) -> None:
        """Regime vuoto deve sollevare ForfettarioCheckError."""
        company = dict(_VALID_COMPANY, tax_regime="")
        settings = _make_settings()
        fic = FICClient(settings)

        fic._client = _make_fic_client(transport=_mock_fic_company(company))

        try:
            with pytest.raises(ForfettarioCheckError):
                await verify_and_bind(fic)
        finally:
            await fic.close()

    @pytest.mark.asyncio
    async def test_missing_vat_number_success(self) -> None:
        """La mancanza di P.IVA non blocca la verifica del regime."""
        company = dict(_VALID_COMPANY, vat_number="")
        settings = _make_settings()
        fic = FICClient(settings)

        fic._client = _make_fic_client(transport=_mock_fic_company(company))

        try:
            # Se factum è None, non deve crashare
            info = await verify_and_bind(fic, factum=None)
            assert info["tax_regime"] == "forfettario"
            assert info["vat_number"] == ""
        finally:
            await fic.close()

    @pytest.mark.asyncio
    async def test_factum_optional(self) -> None:
        """verify_and_bind deve funzionare anche senza FactumClient."""
        settings = _make_settings()
        fic = FICClient(settings)

        fic._client = _make_fic_client(transport=_mock_fic_company(_VALID_COMPANY))

        try:
            info = await verify_and_bind(fic, factum=None)
            assert info["id"] == 12345
            assert info["tax_regime"] == "forfettario"
            assert info["vat_number"] == "IT01234567890"
        finally:
            await fic.close()

    @pytest.mark.asyncio
    async def test_fic_api_error_propagates(self) -> None:
        """Un errore HTTP da FIC deve propagarsi (non ForfettarioCheckError)."""

        def _error_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": "Unauthorized"})

        settings = _make_settings()
        fic = FICClient(settings)
        fic._client = _make_fic_client(transport=httpx.MockTransport(_error_handler))

        try:
            with pytest.raises(httpx.HTTPStatusError):
                await verify_and_bind(fic)
        finally:
            await fic.close()


# ── Test per FICClient.get_company_info ─────────────────────────────────


class TestGetCompanyInfo:
    """Testa il metodo get_company_info di FICClient."""

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        """get_company_info restituisce i dati azienda."""
        settings = _make_settings()
        fic = FICClient(settings)
        fic._client = _make_fic_client(transport=_mock_fic_company(_VALID_COMPANY))

        try:
            info = await fic.get_company_info()
            assert info["id"] == 12345
            assert info["name"] == "Test S.r.l."
            assert info["tax_regime"] == "forfettario"
            assert info["vat_number"] == "IT01234567890"
        finally:
            await fic.close()

    @pytest.mark.asyncio
    async def test_error_propagates(self) -> None:
        """Un errore HTTP deve propagarsi."""

        def _fail_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, json={"error": "Forbidden"})

        settings = _make_settings()
        fic = FICClient(settings)
        fic._client = _make_fic_client(transport=httpx.MockTransport(_fail_handler))

        try:
            with pytest.raises(httpx.HTTPStatusError) as exc:
                await fic.get_company_info()
            assert exc.value.response.status_code == 403
        finally:
            await fic.close()

    @pytest.mark.asyncio
    async def test_no_data_key(self) -> None:
        """Se la risposta manca della chiave 'data', restituisce dict vuoto."""

        def _empty_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={})

        settings = _make_settings()
        fic = FICClient(settings)
        fic._client = _make_fic_client(transport=httpx.MockTransport(_empty_handler))

        try:
            info = await fic.get_company_info()
            assert info == {}
        finally:
            await fic.close()

    @pytest.mark.asyncio
    async def test_extra_fields_preserved(self) -> None:
        """Campi extra nella risposta devono essere preservati."""
        company = dict(_VALID_COMPANY, extra_field="foo", another=42)
        settings = _make_settings()
        fic = FICClient(settings)
        fic._client = _make_fic_client(transport=_mock_fic_company(company))

        try:
            info = await fic.get_company_info()
            assert info["extra_field"] == "foo"
            assert info["another"] == 42
        finally:
            await fic.close()


# ── Test per FactumClient.update_fic_vat ────────────────────────────────


class TestUpdateFicVat:
    """Testa la propagazione degli header FIC su FactumClient."""

    @pytest.mark.asyncio
    async def test_sets_vat_header(self) -> None:
        """update_fic_vat imposta l'header X-FIC-VAT."""
        settings = _make_settings()
        factum = FactumClient(settings)
        factum._client = _make_factum_client(transport=_mock_factum_transport())

        try:
            assert "X-FIC-VAT" not in factum._client.headers
            factum.update_fic_vat("IT01234567890")
            assert factum._client.headers.get("X-FIC-VAT") == "IT01234567890"
        finally:
            await factum.close()

    @pytest.mark.asyncio
    async def test_sets_company_id_header(self) -> None:
        """Il company ID deve essere impostato al bootstrap."""
        settings = _make_settings()
        factum = FactumClient(settings)
        factum._client = _make_factum_client(transport=_mock_factum_transport())

        try:
            # Il company ID è impostato in __init__ da settings
            assert factum._client.headers.get("X-FIC-Company-ID") == "12345"
        finally:
            await factum.close()

    @pytest.mark.asyncio
    async def test_update_vat_twice(self) -> None:
        """Aggiornamenti multipli della P.IVA funzionano."""
        settings = _make_settings()
        factum = FactumClient(settings)
        factum._client = _make_factum_client(transport=_mock_factum_transport())

        try:
            factum.update_fic_vat("IT01234567890")
            assert factum._client.headers.get("X-FIC-VAT") == "IT01234567890"
            factum.update_fic_vat("IT09988776655")
            assert factum._client.headers.get("X-FIC-VAT") == "IT09988776655"
        finally:
            await factum.close()

    @pytest.mark.asyncio
    async def test_empty_vat_does_not_set_header(self) -> None:
        """P.IVA vuota non deve modificare l'header."""
        settings = _make_settings()
        factum = FactumClient(settings)
        factum._client = _make_factum_client(transport=_mock_factum_transport())

        try:
            factum.update_fic_vat("")
            assert "X-FIC-VAT" not in factum._client.headers
        finally:
            await factum.close()

    @pytest.mark.asyncio
    async def test_vat_header_sent_on_requests(self) -> None:
        """L'header X-FIC-VAT deve essere inviato nelle richieste a Factum."""

        received_headers: list[dict] = []

        def _capture_handler(request: httpx.Request) -> httpx.Response:
            received_headers.append(dict(request.headers))
            return httpx.Response(200, json={"status": "healthy"})

        settings = _make_settings()
        factum = FactumClient(settings)
        factum._client = _make_factum_client(
            transport=httpx.MockTransport(_capture_handler),
            extra_headers={"X-FIC-Company-ID": "12345"},
        )

        try:
            factum.update_fic_vat("IT01234567890")
            await factum.health()
            assert len(received_headers) == 1
            assert received_headers[0].get("x-fic-vat") == "IT01234567890"
        finally:
            await factum.close()