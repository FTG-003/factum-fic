"""Parser deterministico XML FatturaPA SDI — bypass totale dell'LLM.

I file ``.xml`` (Fattura Elettronica SDI, standard FatturaPA v1.2) vengono
parsati in locale e in modo istantaneo via ``xml.etree.ElementTree``:
zero chiamate di rete verso Factum Parse, zero token LLM spesi, zero rischio
di allucinazioni su data/importi.

Mappatura campi (standard FatturaPA v1.2):
    - ``CedentePrestatore`` → fornitore (ragione sociale, P.IVA con prefisso paese)
    - ``DatiGeneraliDocumento`` → numero, data, divisa, totale documento
    - ``DatiBeniServizi/DatiRiepilogo`` → imponibile e IVA (sommati per aliquota)

Riferimento: https://www.fatturapa.gov.it/it/norme-e-regole/documentazione-fattura-elettronica/
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from factum_fic.core.models import FactumParseResult

logger = logging.getLogger(__name__)

# Formato data standard FatturaPA: YYYY-MM-DD (tag <Data> obbligatorio)
_ISO_DATE_FMT = "%Y-%m-%d"


def _strip_namespace(root: ET.Element) -> ET.Element:
    """Rimuove i prefissi namespace dai tag (in-place) per query semplici.

    I file FatturaPA hanno namespace di default (``ns3:FatturaElettronica``):
    sbianchettando i tag, le find ``.//CedentePrestatore`` diventano pulite
    e indipendenti dal prefisso usato dal mittente.
    """
    for elem in root.iter():
        if "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]
    return root


def _to_float(value: str | None) -> float:
    """Converte un valore XML in float.

    Lo standard FatturaPA usa il punto decimale (``15.69``); accettiamo
    anche la virgola (``15,69``) per esportazioni non conformi.
    """
    if value is None:
        return 0.0
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return 0.0


def parse_sdi_xml(xml_bytes: bytes) -> FactumParseResult:
    """Parsa deterministicamente un XML FatturaPA SDI (locale, zero LLM).

    Args:
        xml_bytes: Contenuto byte del file XML FatturaPA.

    Returns:
        ``FactumParseResult`` con fornitore, P.IVA, numero, data e importi
        (imponibile, IVA, totale) valorizzati al 100%.

    Raises:
        ET.ParseError: XML malformato o non parsabile.
        ValueError: nodi obbligatori mancanti (CedentePrestatore).
    """
    root = ET.fromstring(xml_bytes)
    _strip_namespace(root)

    # ── 1. Fornitore (CedentePrestatore) ────────────────────────────────
    cedente = root.find(".//CedentePrestatore")
    if cedente is None:
        raise ValueError("XML SDI non valido: nodo <CedentePrestatore> mancante")

    supplier_name = (
        cedente.findtext(".//Anagrafica/Denominazione")
        or " ".join(
            filter(
                None,
                (
                    cedente.findtext(".//Anagrafica/Nome", ""),
                    cedente.findtext(".//Anagrafica/Cognome", ""),
                ),
            ),
        )
        or "Fornitore Sconosciuto"
    )

    country_code = cedente.findtext(".//IdFiscaleIVA/IdPaese", "")
    vat_code = cedente.findtext(".//IdFiscaleIVA/IdCodice", "")
    supplier_vat = (
        f"{country_code}{vat_code}"
        if vat_code
        else cedente.findtext(".//CodiceFiscale", "") or ""
    )

    # Sede: Indirizzo + civico + Comune (campo informativo, best-effort)
    indirizzo = cedente.findtext(".//Sede/Indirizzo", "") or ""
    civico = cedente.findtext(".//Sede/Civico", "") or ""
    comune = cedente.findtext(".//Sede/Comune", "") or ""
    supplier_address = ", ".join(p for p in (indirizzo, civico, comune) if p)

    # ── 2. Dati documento (DatiGeneraliDocumento) ──────────────────────
    dati_gen = root.find(".//DatiGeneraliDocumento")
    invoice_number = (
        dati_gen.findtext("Numero") if dati_gen is not None else None
    ) or "SENZA_NUMERO"
    raw_date = dati_gen.findtext("Data") if dati_gen is not None else None
    # La data è un campo OBBLIGATORIO dello standard; il fallback odierno è
    # solo difensivo per XML anomali (mai usato sui file SDI reali).
    invoice_date = raw_date or datetime.now().strftime(_ISO_DATE_FMT)
    currency = (
        (dati_gen.findtext("Divisa") if dati_gen is not None else None) or "EUR"
    ).upper()

    # ── 3. Importi e IVA (DatiRiepilogo o ImportoTotaleDocumento) ─────
    riepiloghi = root.findall(".//DatiRiepilogo")
    subtotal = 0.0
    vat_amount = 0.0
    if riepiloghi:
        for r in riepiloghi:
            subtotal += _to_float(r.findtext("ImponibileImporto"))
            vat_amount += _to_float(r.findtext("Imposta"))
        total_gross = round(subtotal + vat_amount, 2)
    else:
        total_str = (
            dati_gen.findtext("ImportoTotaleDocumento", "0.0")
            if dati_gen is not None
            else "0.0"
        )
        total_gross = _to_float(total_str)
        subtotal = total_gross

    subtotal = round(subtotal, 2)
    vat_amount = round(vat_amount, 2)
    total_gross = round(total_gross, 2)

    logger.info(
        "SDI XML parsed: %s — P.IVA %s — n. %s del %s — "
        "netto %.2f / IVA %.2f / totale %.2f %s",
        supplier_name,
        supplier_vat,
        invoice_number,
        invoice_date,
        subtotal,
        vat_amount,
        total_gross,
        currency,
    )

    return FactumParseResult(
        document_type="fattura",
        currency=currency,
        total=total_gross,
        supplier_name=supplier_name,
        supplier_vat=supplier_vat,
        supplier_country=country_code,
        supplier_address=supplier_address,
        invoice_date=invoice_date,
        invoice_number=invoice_number,
        raw={
            "amount_net": subtotal,
            "amount_vat": vat_amount,
            "amount_gross": total_gross,
            "currency": currency,
            "invoice_number": invoice_number,
            "invoice_date": invoice_date,
            "is_reverse_charge": vat_amount == 0.0,
        },
    )


def parse_sdi_xml_file(path: Path) -> FactumParseResult:
    """Legge un file XML SDI e lo parsaa in modo deterministico.

    Args:
        path: Percorso del file ``.xml`` FatturaPA.

    Returns:
        ``FactumParseResult`` con i campi SDI estratti.

    Raises:
        OSError: lettura file fallita.
        ET.ParseError: XML malformato.
        ValueError: nodi obbligatori mancanti.
    """
    return parse_sdi_xml(path.read_bytes())
