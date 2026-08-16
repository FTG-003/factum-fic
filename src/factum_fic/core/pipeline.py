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

File lifecycle: da_elaborare → elaborate/YYYY-MM/ (successo/duplicato) | errori/YYYY-MM/ (errore)
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
import shutil
from typing import Any
from pathlib import Path

from factum_fic.config import Settings
from factum_fic.core.extractor import extract_text
from factum_fic.core.factum_client import FactumClient
from factum_fic.core.fic_client import FICClient
from factum_fic.core.mapper import Mapper
from factum_fic.core.models import (
    DocumentStatus,
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
    """Crea le directory inbox/processed/failed se non esistono."""
    for name in ("inbox_dir", "processed_dir", "failed_dir"):
        path = Path(getattr(settings, name)).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)


def _archive_path(settings: Settings, subdir: str, original: Path) -> Path:
    """Calcola il percorso di destinazione con struttura anno/mese.

    Esempio: processed/2026-08/nome_file.pdf
    Evita sovrascritture aggiungendo un suffisso numerico se il file esiste già.
    """
    base = Path(getattr(settings, subdir)).expanduser().resolve()
    today = datetime.date.today()
    dated_dir = base / f"{today.year}-{today.month:02d}"
    dated_dir.mkdir(parents=True, exist_ok=True)

    dest = dated_dir / original.name
    counter = 1
    while dest.exists():
        stem = original.stem
        suffix = original.suffix
        dest = dated_dir / f"{stem}_{counter}{suffix}"
        counter += 1
    return dest


def move_to_processed(path: Path, settings: Settings) -> Path:
    """Sposta il file in processed/ (con struttura anno/mese).

    Returns:
        Percorso di destinazione.
    """
    dest = _archive_path(settings, "processed_dir", path)
    shutil.move(str(path), str(dest))
    logger.info("✅ File elaborato e archiviato in: %s", dest)
    return dest


def move_to_failed(path: Path, settings: Settings) -> Path:
    """Sposta il file in failed/.

    Returns:
        Percorso di destinazione.
    """
    dest = _archive_path(settings, "failed_dir", path)
    shutil.move(str(path), str(dest))
    logger.warning("❌ Errore elaborazione, spostato in: %s", dest)
    return dest


def is_temp_file(path: Path) -> bool:
    """True se il file è temporaneo/parziale e va ignorato."""
    name = path.name
    if name.startswith("."):
        return True
    if _TEMP_FILE_RE.search(name):
        return True
    return False


# ── Helper interni ────────────────────────────────────────────────────────────

def _sha256_file(path: Path) -> str:
    """Calcola SHA-256 di un file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _enrich_from_payload(factum_resp: "FactumResponse") -> None:
    """Arricchisce FactumParseResult da payload.content (envelope v2)."""
    result = factum_resp.result
    if result is None:
        return
    # payload.content è dentro model_extra di result (FactumParseResult)
    # perché l'API restituisce result.payload.content.*
    extra = getattr(result, "model_extra", {}) or {}
    payload = extra.get("payload")
    if not isinstance(payload, dict):
        return
    content: dict[str, Any] = payload.get("content", {}) or {}
    if not content:
        return

    # Importi — tenta parsing da raw_extracted se importi è null
    importi: dict[str, Any] = content.get("importi", {}) or {}
    if result.total == 0.0 and importi.get("totale_documento"):
        result.total = float(importi["totale_documento"])
    elif result.total == 0.0:
        # Fallback: cerca amount in raw_extracted (es. "€ 15.69")
        raw_extracted = content.get("raw_extracted") or {}
        if isinstance(raw_extracted, dict):
            for key in ("total_excl_vat", "total", "totale", "totale_documento", "importo_totale"):
                val = raw_extracted.get(key) or ""
                if isinstance(val, str) and val:
                    try:
                        nums = re.findall(r"[\d]+[.,][\d]+", val.replace("€", "").replace("\u20ac", ""))
                        if nums:
                            result.total = float(nums[0].replace(",", "."))
                            break
                    except (ValueError, IndexError):
                        continue
                elif isinstance(val, (int, float)) and val:
                    result.total = float(val)
                    break
    # Emittente
    if not result.supplier_name:
        emittente: dict[str, Any] = content.get("emittente", {}) or {}
        result.supplier_name = emittente.get("ragione_sociale") or ""
        result.supplier_vat = emittente.get("partita_iva") or ""
        result.supplier_address = emittente.get("indirizzo") or ""
    # Date / numero
    if not result.invoice_date:
        dati_doc: dict[str, Any] = content.get("dati_documento", {}) or {}
        result.invoice_date = dati_doc.get("data_emissione") or ""
        result.invoice_number = dati_doc.get("numero") or ""
    # raw_extracted → raw
    if not result.raw:
        raw_extracted = content.get("raw_extracted") or {}
        if isinstance(raw_extracted, dict):
            result.raw = raw_extracted
    # Document type reale (non wrapper legacy "generic")
    inner_dt = content.get("document_type")
    if inner_dt:
        factum_resp.document_type = inner_dt


async def _ensure_fic_supplier(
    fic: FICClient,
    supplier: Any,
    expense: Any,
) -> int | None:
    """Cerca o crea un fornitore su FIC. Restituisce l'entity_id."""
    existing = await fic.search_supplier(supplier.name, supplier.vat_number)
    if existing:
        entity_id = existing.get("id")
        expense.entity_id = entity_id
        expense.entity = None
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
        try:
            move_to_processed(path, settings)
        except Exception as exc:
            logger.warning("Fallito spostamento duplicato FIC: %s", exc)
        return PipelineResult(
            file=FileEvent(
                path=str(path),
                sha256=sha,
                filename=path.name,
                size_bytes=path.stat().st_size,
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
        try:
            move_to_processed(path, settings)
        except Exception as exc:
            logger.warning("Fallito spostamento duplicato: %s", exc)
        return result

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
            move_to_failed(path, settings)
        except Exception as exc:
            logger.warning("Fallito spostamento errore: %s", exc)
        return result

    # Chiamata Factum
    try:
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
            move_to_failed(path, settings)
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
            move_to_failed(path, settings)
        except Exception as exc:
            logger.warning("Fallito spostamento errore: %s", exc)
        return result

    logger.info("FACTUM RAW: %s", json.dumps(factum_resp.model_dump(mode='json'), indent=2, ensure_ascii=False))

    # Arricchisci FactumParseResult da payload.content (envelope v2)
    _enrich_from_payload(factum_resp)

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
                original_gross = expense.amount_gross or expense.amount_net
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
            move_to_failed(path, settings)
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
        fic_resp = await fic.create_expense(expense, attachment_token=attachment_token)

        # ✅ Marca come completato SUBITO dopo la risposta FIC, PRIMA dello spostamento
        queue.complete(sha, fic_resp.id)
        logger.info(
            "Registrato su FIC: id=%d, tipo=%s, coda aggiornata",
            fic_resp.id, fic_resp.type,
        )

        result = PipelineResult(
            file=file_event,
            status=DocumentStatus.RECORDED,
            factum_status="done",
            fic_status="created",
            fic_id=fic_resp.id,
            document_type=doc_type_detected,
        )

        try:
            move_to_processed(path, settings)
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
            move_to_failed(path, settings)
        except Exception as exc2:
            logger.warning("Fallito spostamento errore: %s", exc2)
        return result