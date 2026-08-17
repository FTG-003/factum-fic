"""Orchestratore: File → Hash → Text extraction → Factum Parse → Mapper → FIC.

Gestisce l'intero flusso:
1. Legge il file, calcola SHA-256
2. Verifica che non sia un file temporaneo/parziale
3. Estrae testo locale (PDF via pypdf, XML/TXT/CSV lettura diretta)
4. Invia il testo estratto a Factum Parse API per parsing
5. Mappa il risultato in request FIC
6. Pre-verifica esistenza documento su FIC (evita duplicati)
7. Registra su Fatture in Cloud (spesa o autofattura)
8. Marca come completato nella coda SQLite PRIMA dello spostamento file

File lifecycle: da_elaborare → archiviate/YYYY/MM/ (successo/duplicato) | da_verificare/ (errore)
Nessun file JSON/TMP intermedio: lo stato risiede solo nel DB SQLite.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from factum_fic.config import Settings
from factum_fic.core.archiver import archive_failed_file, archive_processed_file
from factum_fic.core.extractor import extract_text
from factum_fic.core.factum_client import FactumClient
from factum_fic.core.fic_client import FICClient
from factum_fic.core.mapper import Mapper
from factum_fic.core.models import (
    DocumentStatus,
    FactumParseResult,
    FactumResponse,
    FileEvent,
    PipelineResult,
)
from factum_fic.storage.queue import QueueStore

logger = logging.getLogger(__name__)


# Estensioni supportate per l'upload allegati PDF/immagine
_ATTACHMENT_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp"}

# Pattern per file temporanei/parziali da ignorare
_TEMP_FILE_RE = re.compile(
    r"\.(part|crdownload|tmp|swp|bak)$|~$",
    re.IGNORECASE,
)


# ── File lifecycle helpers ────────────────────────────────────────────────────

def ensure_dirs(settings: Settings) -> None:
    """Crea le directory inbox e di archiviazione se non esistono.

    Nota zero-clutter: le legacy ``processed_dir``/``failed_dir`` (elaborate/,
    errori/) non vengono più create — la pipeline usa esclusivamente
    ``base_storage_dir/archiviate/YYYY/MM`` e ``base_storage_dir/da_verificare``
    (create on-demand dall'archiver).
    """
    inbox = Path(settings.inbox_dir).expanduser().resolve()
    inbox.mkdir(parents=True, exist_ok=True)
    base = Path(settings.base_storage_dir).expanduser().resolve()
    (base / "archiviate").mkdir(parents=True, exist_ok=True)
    (base / "da_verificare").mkdir(parents=True, exist_ok=True)


def is_temp_file(path: Path) -> bool:
    """True se il file è temporaneo/parziale e va ignorato."""
    name = path.name
    if name.startswith("."):
        return True
    return bool(_TEMP_FILE_RE.search(name))


# ── Helper interni ────────────────────────────────────────────────────────────

def _sha256_file(path: Path) -> str:
    """Calcola SHA-256 di un file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_amount(value: Any) -> float:
    """Converte un importo in float gestendo stringhe "50,00 EUR" o "EUR 50.0,00".

    Returns:
        float dell'importo, 0.0 se non parsabile.
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.replace("\u20ac", "").replace("\u20ac", "").strip()
        m = re.search(r"\d[\d.,]*", s)
        if not m:
            return 0.0
        num = m.group(0)
        if "," in num and "." in num:
            num = (
                num.replace(".", "").replace(",", ".")
                if num.rfind(",") > num.rfind(".")
                else num.replace(",", "")
            )
        else:
            num = num.replace(",", ".")
        try:
            return float(num)
        except ValueError:
            return 0.0
    return 0.0


def _fallback_amount_from_text(text: str) -> float:
    """Cerca importi nel testo estratto dal PDF quando Factum non li estrae.

    Usa pattern regex multi-lingua (EN/DE/IT) per trovare:
    - "Total (excl. VAT) € 15.69"
    - "Gesamtbetrag € 50,40"
    - "Imponibile € 15,69"
    - "Subtotal € 15.69"
    - "€ 15.69" come ultima risorsa
    """
    amount_patterns = [
        # Pattern prioritari con etichetta (total/subtotal/netto/imponibile/zwischensumme)
        r'(?:total|subtotal|gesamtbetrag|zwischensumme|netto|imponibile|amount\s*due)'
        r'\s*(?:\(excl\.?\s*VAT\))?'
        r'[\s:]*[€€]?\s*([\d]{1,4}(?:[.,][\d]{3})*[.,][\d]{2})',
        # Pattern "Total € 15.69" / "€ 15.69" generico
        r'[€€]\s*([\d]{1,4}(?:[.,][\d]{3})*[.,][\d]{2})',
        # Pattern "15.69 EUR" / "15,69 EUR"
        r'([\d]{1,4}(?:[.,][\d]{3})*[.,][\d]{2})\s*(?:EUR|€|euro|Euro)',
    ]
    best = 0.0
    for pattern in amount_patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            raw = m.group(1)
            # Normalizza la virgola decimale
            if "," in raw and "." in raw:
                # 1.200,00 → punti come separatori migliaia
                if raw.rfind(",") > raw.rfind("."):
                    raw = raw.replace(".", "").replace(",", ".")
                # 1,200.00 → virgola come separatore migliaia
                else:
                    raw = raw.replace(",", "")
            elif "," in raw:
                raw = raw.replace(",", ".")
            # Se rimangono più punti, formattazione errata
            if raw.count(".") > 1:
                parts = raw.split(".")
                # Prendi il numero finché non ci sono 2 decimali finali
                # Esempio: 1.200.00 → parts = ["1", "200", "00"]
                raw = "".join(parts[:-1]) + "." + parts[-1]
            try:
                val = float(raw)
                if val > best:
                    best = val
            except ValueError:
                continue
    return round(best, 2)


def _fallback_enrich_from_file(result: FactumParseResult, text: str) -> None:
    """Arricchisce FactumParseResult con importi estratti dal testo PDF
    quando Factum restituisce importi nulli o zero.

    Cerca importi nel testo, data fattura e numero fattura con regex.
    """
    if result.total and result.total > 0:
        return  # già arricchito da Factum

    amount = _fallback_amount_from_text(text)
    if amount:
        result.total = amount
        raw_norm = dict(result.raw or {})
        raw_norm.setdefault("amount_net", amount)
        raw_norm.setdefault("amount_gross", amount)
        raw_norm.setdefault("amount_vat", 0.0)
        result.raw = raw_norm
        logger.info("Fallback importo da testo PDF: %.2f", amount)

    # Data fattura da regex
    if not result.invoice_date:
        date_m = re.search(r'(?:Invoice\s*date|Data\s*fattura)[^\d]*(\d{2}/\d{2}/\d{4})', text, re.IGNORECASE)
        if date_m:
            result.invoice_date = date_m.group(1)
            logger.info("Fallback data da testo PDF: %s", result.invoice_date)

    # Numero fattura da regex
    if not result.invoice_number:
        inv_m = re.search(r'(?:Invoice\s*no\.?|Fattura\s*n\.?|N\.?\s*fattura)[:\s]+([\w/-]+)', text, re.IGNORECASE)
        if inv_m:
            result.invoice_number = inv_m.group(1).strip()
            logger.info("Fallback numero fattura da testo PDF: %s", result.invoice_number)


def _amount_from_dict(data: dict[str, Any], exact_keys: tuple[str, ...], substrings: tuple[str, ...]) -> float:
    """Cerca un importo in un dict: prima chiavi esatte, poi per sottostringa.

    Le risposte LLM di Factum usano chiavi variabili (en: net_amount/total_amount,
    it: netto_totale/iva_importo/totale, miste). Il matching per sottostringa
    rende la normalizzazione robusta alle variazioni.
    """
    for key in exact_keys:
        val = data.get(key)
        if val is not None and val != "":
            parsed = _parse_amount(val)
            if parsed:
                return parsed
    for key, val in data.items():
        key_lower = key.lower()
        if any(sub in key_lower for sub in substrings) and val is not None and val != "":
            parsed = _parse_amount(val)
            if parsed:
                return parsed
    return 0.0


def _enrich_from_payload(factum_resp: FactumResponse) -> None:
    """Arricchisce FactumParseResult da payload.content (envelope v2).

    Le risposte di Factum sono non deterministiche: a volte gli importi sono
    in ``payload.content.importi`` (imponibile_totale/iva_totale/
    totale_documento), altre volte solo in ``raw_extracted`` con chiavi diverse
    (en: net_amount/vat_amount/total_amount, it: netto_totale/iva_importo/
    totale). La funzione normalizza entrambe le fonti in ``result.total`` +
    ``result.raw`` (amount_net/vat/gross) così il mapper ``build_expense``
    può costruire il payload FIC con importi esatti.
    """
    result = factum_resp.result
    if result is None:
        return
    extra = getattr(result, "model_extra", {}) or {}
    payload = extra.get("payload")
    if not isinstance(payload, dict):
        return
    content: dict[str, Any] = payload.get("content", {}) or {}
    if not content:
        return

    # ── Importi: normalizza da importi E raw_extracted ────────────────
    importi: dict[str, Any] = content.get("importi", {}) or {}
    raw_extracted: dict[str, Any] = content.get("raw_extracted", {}) or {}
    if not isinstance(raw_extracted, dict):
        raw_extracted = {}

    net = _parse_amount(importi.get("imponibile_totale"))
    vat = _parse_amount(importi.get("iva_totale"))
    gross = _parse_amount(importi.get("totale_documento"))

    # Fallback: raw_extracted (chiavi LLM variabili, fuzzy match)
    if not net:
        net = _amount_from_dict(raw_extracted, ("amount_net",), ("netto", "net_amount"))
    if not vat:
        vat = _amount_from_dict(raw_extracted, ("amount_vat",), ("iva", "vat_amount"))
    if not gross:
        gross = _amount_from_dict(raw_extracted, ("amount_gross",), ("totale", "total_amount", "importo"))

    # Fallback: somma items estratti (raw_extracted.items[].net_amount)
    if not gross:
        items_raw = raw_extracted.get("items") or []
        if isinstance(items_raw, list):
            total_items = 0.0
            for item in items_raw:
                if isinstance(item, dict):
                    total_items += _parse_amount(item.get("net_amount"))
            if total_items:
                gross = total_items
                if not net:
                    net = total_items

    if gross:
        result.total = round(gross, 2)
    if not net:
        net = gross
    if not vat:
        vat = 0.0

    # Espone net/vat/gross normalizzati in result.raw per build_expense
    raw_norm = dict(result.raw or {})
    raw_norm.setdefault("amount_net", round(net, 2))
    raw_norm.setdefault("amount_vat", round(vat, 2))
    raw_norm.setdefault("amount_gross", round(gross, 2))
    result.raw = raw_norm

    # ── Emittente ─────────────────────────────────────────────────────
    if not result.supplier_name:
        emittente: dict[str, Any] = content.get("emittente", {}) or {}
        result.supplier_name = emittente.get("ragione_sociale") or ""
        result.supplier_vat = emittente.get("partita_iva") or ""
        result.supplier_address = emittente.get("indirizzo") or ""
    # Fallback nome fornitore da raw_extracted (chiavi LLM variabili)
    if not result.supplier_name:
        for key in ("fornitore", "ragione_sociale", "supplier_name", "vendor", "company", "nome"):
            candidate = raw_extracted.get(key)
            if isinstance(candidate, str) and candidate.strip():
                result.supplier_name = candidate.strip()
                break
    if not result.supplier_vat:
        for key in ("partita_iva", "vat_number", "p_iva", "iva"):
            candidate = raw_extracted.get(key)
            if isinstance(candidate, str) and candidate.strip():
                result.supplier_vat = candidate.strip()
                break
    if not result.supplier_country:
        emittente = content.get("emittente", {}) or {}
        country_candidate = (
            emittente.get("paese")
            or emittente.get("country")
            or emittente.get("country_iso")
            or ""
        )
        country_hint = str(country_candidate or result.supplier_address or result.supplier_name)
        country_map = {
            "germany": "DE", "deutschland": "DE", "de": "DE", "tedesco": "DE",
            "francia": "FR", "france": "FR", "frankreich": "FR",
            "spagna": "ES", "spain": "ES", "spanien": "ES",
            "austria": "AT", "\u00f6sterreich": "AT",
            "paesi bassi": "NL", "netherlands": "NL", "niederlande": "NL", "oland": "NL",
            "irlanda": "IE", "ireland": "IE", "irland": "IE",
            "usa": "US", "united states": "US", "stati uniti": "US",
            "regno unito": "GB", "united kingdom": "GB", "great britain": "GB",
            "svizzera": "CH", "switzerland": "CH", "schweiz": "CH",
        }
        hint_lower = country_hint.lower()
        for keyword, iso in country_map.items():
            if keyword in hint_lower:
                result.supplier_country = iso
                break

    # ── Date / numero ──────────────────────────────────────────────────
    if not result.invoice_date:
        dati_doc: dict[str, Any] = content.get("dati_documento", {}) or {}
        result.invoice_date = dati_doc.get("data_emissione") or ""
        result.invoice_number = dati_doc.get("numero") or ""
    # raw_extracted → raw (senza sovrascrivere net/vat/gross normalizzati)
    raw_extra = content.get("raw_extracted") or {}
    if isinstance(raw_extra, dict):
        for k, v in raw_extra.items():
            result.raw.setdefault(k, v)
    # Document type reale (non wrapper legacy "generic")
    inner_dt = content.get("document_type")
    if inner_dt:
        factum_resp.document_type = inner_dt


async def _ensure_fic_supplier(
    fic: FICClient,
    supplier: Any,
    expense: Any,
) -> int | None:
    """Cerca o crea un fornitore su FIC. Restituisce l'entity_id.

    Aggiorna ``supplier.name`` con il nome reale su FIC (utile per
    autofattura SDI che richiede entity.name nel payload).
    """
    existing = await fic.search_supplier(supplier.name, supplier.vat_number)
    if existing:
        entity_id = existing.get("id")
        expense.entity_id = entity_id
        expense.entity = None
        # Aggiorna supplier.name con il nome reale su FIC
        fic_name = existing.get("name") or ""
        if fic_name:
            supplier.name = fic_name
        return entity_id
    created = await fic.create_supplier(supplier)
    entity_id = created.get("data", {}).get("id")
    expense.entity_id = entity_id
    expense.entity = None
    return entity_id


async def _check_fic_exists(
    fic: FICClient,
    expense: Any,
    sha: str,
    queue: QueueStore,
    path: Path,
    settings: Settings,
) -> PipelineResult | None:
    """Pre-verifica: se il documento esiste già su FIC, salta la creazione.

    Restituisce un PipelineResult già completo se il documento è stato
    trovato, None se non esiste e si deve procedere con la creazione.
    """
    if not expense.description or not expense.date:
        return None
    try:
        existing = await fic.search_document(
            entity_id=expense.entity_id,
            description=expense.description,
            date=expense.date,
        )
    except Exception:
        return None  # fallback silenzioso: procedi con creazione

    if existing is not None:
        doc_id = existing.get("id")
        logger.info(
            "Documento già esistente su FIC (id=%d, desc=%s): salto creazione",
            doc_id,
            expense.description,
        )
        queue.complete(sha, doc_id)
        base_dir = Path(settings.base_storage_dir)
        # Cattura stat PRIMA dello spostamento
        file_size = path.stat().st_size
        try:
            archive_processed_file(path, base_dir)
        except Exception as exc:
            logger.warning("Fallito spostamento duplicato FIC: %s", exc)
        return PipelineResult(
            file=FileEvent(
                path=str(path),
                sha256=sha,
                filename=path.name,
                size_bytes=file_size,
            ),
            status=DocumentStatus.SKIPPED,
            factum_status="done",
            fic_status="already_exists",
            fic_id=doc_id,
        )
    return None


# ── Pipeline principale ───────────────────────────────────────────────────────

async def process_file(
    path: Path,
    *,
    factum: FactumClient,
    fic: FICClient,
    mapper: Mapper,
    queue: QueueStore,
    settings: Settings,
    force: bool = False,
) -> PipelineResult:
    """Processa un singolo file: Factum → Mapper → FIC.

    Al termine sposta il file in processed/ (successo o duplicato)
    o in failed/ (errore irreversibile).

    Args:
        path: Percorso del file da processare.
        factum: Client Factum già inizializzato.
        fic: Client FIC già inizializzato.
        mapper: Engine regole fiscali.
        queue: Coda SQLite per deduplicazione.
        settings: Configurazione globale.

    Returns:
        PipelineResult con esito delle varie fasi.
    """
    sha = _sha256_file(path)
    file_event = FileEvent(
        path=str(path),
        sha256=sha,
        filename=path.name,
        size_bytes=path.stat().st_size,
    )

    # Deduplicazione — solo i completati bloccano
    if not force and queue.exists(sha):
        logger.info("File già processato (SHA-256 match): %s", path.name)
        result = PipelineResult(
            file=file_event,
            status=DocumentStatus.SKIPPED,
            fic_status="duplicate",
        )
        base_dir = Path(settings.base_storage_dir)
        try:
            archive_processed_file(path, base_dir)
        except Exception as exc:
            logger.warning("Fallito spostamento duplicato: %s", exc)
        return result

    base_dir = Path(settings.base_storage_dir)

    if not force:
        queue.enqueue(sha, str(path))
    else:
        queue.remove(sha)

    # Estrai testo tramite estrattore locale
    try:
        text = extract_text(path)
    except ValueError as exc:
        logger.warning("Testo non estraibile per %s: %s", path.name, exc)
        result = PipelineResult(
            file=file_event,
            status=DocumentStatus.FAILED,
            factum_status="empty_text",
        )
        try:
            archive_failed_file(path, base_dir)
        except Exception as exc:
            logger.warning("Fallito spostamento errore: %s", exc)
        return result

    # Chiamata Factum
    try:
        logger.info("TESTO INVIATO A FACTUM (%d caratteri):\n%s", len(text), text[:2000])
        factum_resp = await factum.parse_text(text)
    except Exception as exc:
        logger.exception("Factum parsing fallito per %s", path.name)
        result = PipelineResult(
            file=file_event,
            status=DocumentStatus.FAILED,
            factum_status="error",
            factum_error=str(exc),
        )
        try:
            archive_failed_file(path, base_dir)
        except Exception as exc2:
            logger.warning("Fallito spostamento errore: %s", exc2)
        return result

    if factum_resp.status != "done" or factum_resp.result is None:
        result = PipelineResult(
            file=file_event,
            status=DocumentStatus.FAILED,
            factum_status=factum_resp.status,
            factum_error=factum_resp.error,
        )
        try:
            archive_failed_file(path, base_dir)
        except Exception as exc:
            logger.warning("Fallito spostamento errore: %s", exc)
        return result

    logger.info("FACTUM RAW: %s", json.dumps(factum_resp.model_dump(mode='json'), indent=2, ensure_ascii=False))

    # Arricchisci FactumParseResult da payload.content (envelope v2)
    _enrich_from_payload(factum_resp)

    # Fallback: se Factum non ha estratto importi, prova regex su testo PDF
    _fallback_enrich_from_file(factum_resp.result, text)

    # Mappatura
    result = factum_resp.result
    doc_type_detected = mapper.detect_document_type(result)
    supplier = mapper.build_supplier(result)
    expense = mapper.build_expense(result, supplier=supplier)

    # Applica conversione valuta se diversa da EUR
    if expense.currency and expense.currency != "EUR":
        try:
            from factum_fic.core.mapper import convert_currency
            rate = await convert_currency(expense.currency, "EUR")
            if rate != 1.0:
                original_net = expense.amount_net
                expense.amount_net = round(expense.amount_net * rate, 2)
                if expense.amount_gross is not None:
                    expense.amount_gross = round(expense.amount_gross * rate, 2)
                note_orig = (
                    f"Importo originale: {original_net:.2f} {expense.currency} — "
                    f"Tasso cambio {rate:.4f} applicato"
                )
                if expense.notes:
                    expense.notes += "\n" + note_orig
                else:
                    expense.notes = note_orig
                expense.currency = "EUR"
                logger.info(
                    "Conversione valuta: %.2f %s → %.2f EUR (tasso=%.4f)",
                    original_net, expense.currency, expense.amount_net, rate,
                )
        except Exception as exc:
            logger.warning(
                "Conversione valuta fallita per %s: %s — procedo con valuta originale",
                path.name, exc,
            )

    # Cerca o crea fornitore su FIC
    try:
        await _ensure_fic_supplier(fic, supplier, expense)
    except Exception as exc:
        logger.exception("FIC supplier fallito per %s", path.name)
        result = PipelineResult(
            file=file_event,
            status=DocumentStatus.FAILED,
            factum_status="parsed",
            fic_status="supplier_error",
            fic_error=str(exc),
            document_type=doc_type_detected,
        )
        try:
            archive_failed_file(path, base_dir)
        except Exception as exc2:
            logger.warning("Fallito spostamento errore: %s", exc2)
        return result

    try:
        # Debug: FIC payload
        fic_payload_debug = expense.model_dump(mode='json', exclude={'entity'})
        fic_payload_debug['entity'] = (
            {'id': expense.entity_id, 'name': supplier.name}
            if expense.entity_id
            else {'name': supplier.name}
        )
        logger.info("FIC PAYLOAD: %s", json.dumps(fic_payload_debug, indent=2, ensure_ascii=False))

        # Pre-verifica esistenza su FIC (anti-duplicazione su timeout/retry)
        pre_check = await _check_fic_exists(
            fic, expense, sha, queue, path, settings,
        )
        if pre_check is not None:
            return pre_check

        # Ottieni attachment_token PRIMA di creare la spesa (FIC v2)
        attachment_token: str | None = None
        if path.suffix.lower() in _ATTACHMENT_EXTENSIONS:
            try:
                attachment_token = await fic.get_attachment_token(path)
                logger.debug("Ottenuto attachment_token per %s", path.name)
            except Exception as exc:
                logger.warning(
                    "Upload preview fallito per %s: %s — la spesa verrà creata senza allegato",
                    path.name,
                    exc,
                )

        # Crea spesa/autofattura su FIC (con allegato se token disponibile)
        # ── Circuit breaker anti-zero: blocca se importo non determinabile ──
        if not expense.amount_net or expense.amount_net <= 0:
            raise ValueError(
                f"Impossibile determinare l'importo per '{path.name}'. "
                "L'operazione è stata interrotta per evitare di creare "
                "documenti a 0,00 € su FIC."
            )
        fic_resp = await fic.create_expense(expense, attachment_token=attachment_token)

        # ✅ Genera autofattura SDI (TD17/TD18/TD19) per spese estere
        fic_self_invoice_id: int | None = None
        if (
            expense.is_autofattura
            and settings.fic_generate_self_invoice
            and fic_resp.id
        ):
            try:
                # Determina il tipo SDI dal FactumParseResult originale
                si_type = mapper.classify_self_invoice_type(result)
                si_request = mapper.build_self_invoice_request(
                    expense=expense,
                    expense_id=fic_resp.id,
                    numeration=settings.fic_self_invoice_numeration,
                    vat_value=settings.fic_self_invoice_vat_value,
                    supplier_name=supplier.name,
                    supplier_vat_number=supplier.vat_number,
                    supplier_country_iso=supplier.country_iso,
                    self_invoice_type=si_type,
                )
                si_resp = await fic.create_issued_document(si_request)
                fic_self_invoice_id = si_resp.id
                logger.info(
                    "✅ Autofattura SDI %s generata per spesa %d: id=%d",
                    si_type.value, fic_resp.id, si_resp.id,
                )
            except Exception as exc:
                logger.warning(
                    "⚠️ Generazione autofattura SDI fallita (spesa %d): %s",
                    fic_resp.id, exc,
                )

        # ✅ Marca come completato SUBITO dopo la risposta FIC, PRIMA dello spostamento
        queue.complete(sha, fic_resp.id, fic_self_invoice_id, path=str(path))
        logger.info(
            "Registrato su FIC: spesa id=%d, autofattura SDI id=%s, coda aggiornata",
            fic_resp.id, fic_self_invoice_id,
        )

        # Cattura i dati estratti da Factum PRIMA che `result` venga riassegnato
        archive_date = result.invoice_date or None
        archive_supplier = result.supplier_name or None
        archive_number = result.invoice_number or None

        result = PipelineResult(
            file=file_event,
            status=DocumentStatus.RECORDED,
            factum_status="done",
            fic_status="created",
            fic_id=fic_resp.id,
            fic_self_invoice_id=fic_self_invoice_id,
            document_type=doc_type_detected,
        )

        try:
            archive_processed_file(
                path,
                base_dir,
                date_str=archive_date,
                supplier_name=archive_supplier,
                invoice_num=archive_number,
            )
        except Exception as exc:
            logger.warning("Fallito spostamento successo: %s", exc)
        return result

    except Exception as exc:
        logger.exception("FIC expense fallito per %s", path.name)
        result = PipelineResult(
            file=file_event,
            status=DocumentStatus.FAILED,
            factum_status="parsed",
            fic_status="expense_error",
            fic_error=str(exc),
            document_type=doc_type_detected,
        )
        try:
            archive_failed_file(path, base_dir)
        except Exception as exc2:
            logger.warning("Fallito spostamento errore: %s", exc2)
        return result
