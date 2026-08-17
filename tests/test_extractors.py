"""Test del parser deterministico XML FatturaPA SDI (bypass LLM)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from factum_fic.core.extractors import parse_sdi_xml, parse_sdi_xml_file

# ── Fixture: XML FatturaPA v1.2 (con namespace, come da SDI reale) ───────────

_FATTURAPA_NS = "http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2"


def _xml_with_ns(body: str) -> str:
    """Costruisce un XML FatturaPA con namespace standard SDI."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<ns3:FatturaElettronica versione="FPR12" '
        f'xmlns:ns3="{_FATTURAPA_NS}" '
        f'xmlns:ns2="http://www.w3.org/2000/09/xmldsig#">'
        f"{body}"
        "</ns3:FatturaElettronica>"
    )


def _minimal_xml(
    *,
    denominazione: str = "Hetzner Online GmbH",
    id_paese: str = "DE",
    id_codice: str = "812871812",
    numero: str = "4AUT",
    data: str = "2026-07-02",
    imponibile: str = "15.69",
    imposta: str = "3.45",
    totale: str = "19.14",
    natura: str | None = None,
) -> bytes:
    # Costruisci il blocco DatiRiepilogo fuori dall'f-string principale
    # per evitare problemi di f-string annidate ("{natura}" agganciato dal
    # parser esterno).
    natura_xml = f"<Natura>{natura}</Natura>" if natura else ""
    riepilogo = (
        "<DatiRiepilogo>"
        "<AliquotaIVA>22.00</AliquotaIVA>"
        f"<ImponibileImporto>{imponibile}</ImponibileImporto>"
        f"<Imposta>{imposta}</Imposta>"
        f"{natura_xml}"
        "</DatiRiepilogo>"
    )
    return _xml_with_ns(
        f"""
        <FatturaElettronicaHeader>
            <CedentePrestatore>
                <DatiAnagrafici>
                    <IdFiscaleIVA>
                        <IdPaese>{id_paese}</IdPaese>
                        <IdCodice>{id_codice}</IdCodice>
                    </IdFiscaleIVA>
                    <Anagrafica>
                        <Denominazione>{denominazione}</Denominazione>
                    </Anagrafica>
                    <RegimeFiscale>RF18</RegimeFiscale>
                </DatiAnagrafici>
                <Sede>
                    <Indirizzo>Industriestr. 25</Indirizzo>
                    <CAP>00000</CAP>
                    <Comune>Gunzenhausen - Germania (DE)</Comune>
                    <Nazione>DE</Nazione>
                </Sede>
            </CedentePrestatore>
        </FatturaElettronicaHeader>
        <FatturaElettronicaBody>
            <DatiGenerali>
                <DatiGeneraliDocumento>
                    <TipoDocumento>TD17</TipoDocumento>
                    <Divisa>EUR</Divisa>
                    <Data>{data}</Data>
                    <Numero>{numero}</Numero>
                    <ImportoTotaleDocumento>{totale}</ImportoTotaleDocumento>
                </DatiGeneraliDocumento>
            </DatiGenerali>
            <DatiBeniServizi>
                <DettaglioLinee>
                    <NumeroLinea>1</NumeroLinea>
                    <Descrizione>Servizi Server</Descrizione>
                    <Quantita>1.00</Quantita>
                    <PrezzoUnitario>15.69</PrezzoUnitario>
                    <PrezzoTotale>15.69</PrezzoTotale>
                    <AliquotaIVA>22.00</AliquotaIVA>
                </DettaglioLinee>
                {riepilogo}
            </DatiBeniServizi>
        </FatturaElettronicaBody>
        """
    ).encode("utf-8")


# ── Test campi base ──────────────────────────────────────────────────────────


def test_supplier_name() -> None:
    result = parse_sdi_xml(_minimal_xml())
    assert result.supplier_name == "Hetzner Online GmbH"


def test_supplier_vat_with_country_prefix() -> None:
    result = parse_sdi_xml(_minimal_xml())
    assert result.supplier_vat == "DE812871812"


def test_supplier_country() -> None:
    result = parse_sdi_xml(_minimal_xml())
    assert result.supplier_country == "DE"


def test_invoice_number() -> None:
    result = parse_sdi_xml(_minimal_xml())
    assert result.invoice_number == "4AUT"


def test_invoice_date_exact_from_xml() -> None:
    """La data DEVE essere quella del tag <Data>, mai la data odierna."""
    result = parse_sdi_xml(_minimal_xml())
    assert result.invoice_date == "2026-07-02"


def test_amounts_from_dati_riepilogo() -> None:
    result = parse_sdi_xml(_minimal_xml())
    raw = result.raw
    assert raw["amount_net"] == 15.69
    assert raw["amount_vat"] == 3.45
    assert raw["amount_gross"] == 19.14
    assert result.total == 19.14


def test_currency_from_divisa() -> None:
    result = parse_sdi_xml(_minimal_xml())
    assert result.currency == "EUR"


def test_document_type_fattura() -> None:
    result = parse_sdi_xml(_minimal_xml())
    assert result.document_type == "fattura"


def test_is_reverse_charge_false_with_iva() -> None:
    result = parse_sdi_xml(_minimal_xml())
    assert result.raw["is_reverse_charge"] is False


# ── Reverse charge / zero IVA ────────────────────────────────────────────────


def test_is_reverse_charge_false_with_iva() -> None:
    """DE fornitore, IVA=3.45 > 0 → RC=False (fornitore estero MA IVA applicata)."""
    result = parse_sdi_xml(_minimal_xml())
    assert result.raw["is_reverse_charge"] is False


# ── Reverse charge: logica SDI corretta ─────────────────────────────────────
# Nello standard SDI, IVA=0 da sola NON basta: serve Natura N6.x o fornitore
# estero con IVA zero. I codici N2.2 (forfettario) e N4 (esente) hanno IVA=0
# ma NON sono reverse charge.


def test_reverse_charge_foreign_supplier_zero_vat() -> None:
    """Fornitore DE, IVA 0.00 → RC True (art. 17-ter DPR 633/72)."""
    result = parse_sdi_xml(
        _minimal_xml(imponibile="15.69", imposta="0.00", totale="15.69")
    )
    assert result.raw["amount_net"] == 15.69
    assert result.raw["amount_vat"] == 0.0
    assert result.raw["amount_gross"] == 15.69
    assert result.raw["is_reverse_charge"] is True


def test_reverse_charge_n6_9_supplier_it() -> None:
    """Caso C: Fornitore IT, IVA 0%, Natura N6.9 → RC True (inversione contabile)."""
    result = parse_sdi_xml(
        _minimal_xml(
            id_paese="IT",
            id_codice="04923300166",
            denominazione="Fabrizio Terzi",
            imponibile="1000.00",
            imposta="0.00",
            totale="1000.00",
            natura="N6.9",
        )
    )
    assert result.raw["is_reverse_charge"] is True
    assert result.raw["nature"] == ["N6.9"]
    assert result.supplier_country == "IT"


def test_no_reverse_charge_n2_2_forfettario() -> None:
    """Caso A: Fornitore IT, IVA 0%, Natura N2.2 (Forfettario) → RC False."""
    result = parse_sdi_xml(
        _minimal_xml(
            id_paese="IT",
            id_codice="04923300166",
            denominazione="Mario Rossi",
            imponibile="500.00",
            imposta="0.00",
            totale="500.00",
            natura="N2.2",
        )
    )
    assert result.raw["is_reverse_charge"] is False
    assert result.raw["nature"] == ["N2.2"]


def test_no_reverse_charge_n4_esente() -> None:
    """Caso B: Fornitore IT, IVA 0%, Natura N4 (Esente art.10) → RC False."""
    result = parse_sdi_xml(
        _minimal_xml(
            id_paese="IT",
            id_codice="04923300166",
            denominazione="Studio Legale XYZ",
            imponibile="2000.00",
            imposta="0.00",
            totale="2000.00",
            natura="N4",
        )
    )
    assert result.raw["is_reverse_charge"] is False
    assert result.raw["nature"] == ["N4"]


def test_reverse_charge_usa_supplier_zero_vat() -> None:
    """Caso D: Fornitore USA (extra-UE), IVA 0% → RC True."""
    result = parse_sdi_xml(
        _minimal_xml(
            id_paese="US",
            id_codice="123456789",
            denominazione="AWS Inc.",
            imponibile="299.00",
            imposta="0.00",
            totale="299.00",
        )
    )
    assert result.raw["is_reverse_charge"] is True
    assert result.supplier_country == "US"
    assert result.supplier_vat == "US123456789"


def test_reverse_charge_nature_audit_trail() -> None:
    """nature[] deve essere popolato nel raw per audit."""
    result = parse_sdi_xml(
        _minimal_xml(
            id_paese="IT",
            id_codice="04923300166",
            imponibile="100.00",
            imposta="0.00",
            totale="100.00",
            natura="N6.1",
        )
    )
    assert "nature" in result.raw
    assert result.raw["nature"] == ["N6.1"]


def test_reverse_charge_no_nature_field_when_missing() -> None:
    """Senza tag Natura in nessun DatiRiepilogo → nature lista vuota."""
    result = parse_sdi_xml(
        _minimal_xml(imponibile="100.00", imposta="22.00", totale="122.00")
    )
    assert result.raw["nature"] == []


# ── Nome/Cognome al posto di Denominazione ──────────────────────────────────


def test_person_name_from_nome_cognome() -> None:
    xml = _minimal_xml().decode("utf-8")
    xml = xml.replace(
        "<Denominazione>Hetzner Online GmbH</Denominazione>",
        "<Nome>Mario</Nome><Cognome>Rossi</Cognome>",
    )
    result = parse_sdi_xml(xml.encode("utf-8"))
    assert result.supplier_name == "Mario Rossi"


def test_supplier_vat_fallback_codice_fiscale() -> None:
    """Senza IdFiscaleIVA/IdCodice, usa CodiceFiscale."""
    xml = _minimal_xml().decode("utf-8")
    xml = xml.replace(
        "<IdFiscaleIVA>\n                        <IdPaese>DE</IdPaese>\n                        <IdCodice>812871812</IdCodice>\n                    </IdFiscaleIVA>",
        "<CodiceFiscale>TRZFRZ80M16A794Y</CodiceFiscale>",
    )
    result = parse_sdi_xml(xml.encode("utf-8"))
    assert result.supplier_vat == "TRZFRZ80M16A794Y"


# ── Totali: multipli DatiRiepilogo e fallback ImportoTotaleDocumento ────────


def test_multiple_dati_riepilogo_summed() -> None:
    """Due aliquote (22% + 10%) → imponibile e IVA sommati."""
    xml = _minimal_xml().decode("utf-8")
    extra_riepilogo = """
            <DatiRiepilogo>
                <AliquotaIVA>10.00</AliquotaIVA>
                <ImponibileImporto>100.00</ImponibileImporto>
                <Imposta>10.00</Imposta>
            </DatiRiepilogo>
    """
    xml = xml.replace("</DatiBeniServizi>", f"{extra_riepilogo}</DatiBeniServizi>")
    result = parse_sdi_xml(xml.encode("utf-8"))
    assert result.raw["amount_net"] == 115.69  # 15.69 + 100.00
    assert result.raw["amount_vat"] == 13.45  # 3.45 + 10.00
    assert result.raw["amount_gross"] == 129.14
    assert result.total == 129.14


def test_fallback_importo_totale_documento() -> None:
    """Senza DatiRiepilogo → usa ImportoTotaleDocumento come totale."""
    xml_str = _minimal_xml().decode("utf-8")
    # Rimuove l'intero blocco <DatiRiepilogo>...</DatiRiepilogo> (single-line)
    import re
    xml_str = re.sub(r"<DatiRiepilogo>.*?</DatiRiepilogo>", "", xml_str)
    # Verifica rimozione
    assert "<DatiRiepilogo>" not in xml_str
    result = parse_sdi_xml(xml_str.encode("utf-8"))
    assert result.raw["amount_gross"] == 19.14
    assert result.raw["amount_net"] == 19.14
    assert result.raw["amount_vat"] == 0.0


# ── Robustezza formati numerici (virgola decimale) ──────────────────────────


def test_decimal_comma_italian_format() -> None:
    """Virgola decimale (15,69) accettata come alternativa al punto ISO."""
    result = parse_sdi_xml(_minimal_xml(imponibile="15,69", imposta="3,45", totale="19,14"))
    assert result.raw["amount_net"] == 15.69
    assert result.raw["amount_vat"] == 3.45
    assert result.raw["amount_gross"] == 19.14


# ── Fallback data odierna ────────────────────────────────────────────────────


def test_missing_date_falls_back_to_today() -> None:
    from datetime import date

    xml = _minimal_xml().decode("utf-8")
    xml = xml.replace("<Data>2026-07-02</Data>", "")
    result = parse_sdi_xml(xml.encode("utf-8"))
    assert result.invoice_date == date.today().isoformat()


# ── Errori ───────────────────────────────────────────────────────────────────


def test_malformed_xml_raises_parse_error() -> None:
    with pytest.raises(ET.ParseError):
        parse_sdi_xml(b"<FatturaElettronica><broken>")


def test_missing_cedente_raises_value_error() -> None:
    xml = _xml_with_ns("<FatturaElettronicaBody><DatiGenerali/></FatturaElettronicaBody>")
    with pytest.raises(ValueError, match="CedentePrestatore"):
        parse_sdi_xml(xml.encode("utf-8"))


def test_empty_xml_raises_parse_error() -> None:
    with pytest.raises(ET.ParseError):
        parse_sdi_xml(b"")


# ── parse_sdi_xml_file ───────────────────────────────────────────────────────


def test_parse_sdi_xml_file(tmp_path) -> None:
    p = tmp_path / "fattura.xml"
    p.write_bytes(_minimal_xml())
    result = parse_sdi_xml_file(p)
    assert result.supplier_name == "Hetzner Online GmbH"
    assert result.invoice_date == "2026-07-02"
    assert result.raw["amount_gross"] == 19.14


def test_parse_sdi_xml_file_missing_raises_os_error(tmp_path) -> None:
    with pytest.raises(OSError):
        parse_sdi_xml_file(tmp_path / "non-esiste.xml")
