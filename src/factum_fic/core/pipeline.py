"""Orchestratore: File → Hash → Text extraction → Factum Parse → Mapper → FIC.

Gestisce l'intero flusso:
1. Legge il file, calcola SHA-256
2. Estrae testo locale (PDF via pypdf, XML/TXT/CSV lettura diretta)
3. Invia il testo estratto a Factum Parse API per parsing
4. Mappa il risultato in request FIC
5. Registra su Fatture in Cloud (spesa o autofattura)

File lifecycle: da_elaborare → elaborate/YYYY-MM/ (successo/duplicato) | errori/YYYY-MM/ (errore)
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
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
                        import re
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
        # Duplicato → processed (non riprocessabile)
        try:
            move_to_processed(path, settings)
        except Exception as exc:
            logger.warning("Fallito spostamento duplicato: %s", exc)
        return result

    if not force:
        queue.enqueue(sha, str(path))
    else:
        # In modalità force, pulisci eventuali record pregressi
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

    # Debug: Factum raw
    logger.info("FACTUM RAW: %s", json.dumps(factum_resp.model_dump(mode='json'), indent=2, ensure_ascii=False))

    # Arricchisci FactumParseResult da payload.content (envelope v2) se i campi
    # legacy V1 sono vuoti — il server aggiornato restituisce i dati reali in
    # result.payload.content.* mentre result.total/result.supplier_name sono 0/"".
    _enrich_from_payload(factum_resp)

    # Mappatura
    result = factum_resp.result
    doc_type_detected = mapper.detect_document_type(result)
    supplier = mapper.build_supplier(result)
    expense = mapper.build_expense(result, supplier=supplier)

    # Cerca o crea fornitore su FIC
    try:
        existing = await fic.search_supplier(supplier.name, supplier.vat_number)
        if existing:
            entity_id = existing.get("id")
            expense.entity_id = entity_id
            expense.entity = None
        else:
            created = await fic.create_supplier(supplier)
            expense.entity_id = created.get("data", {}).get("id")
            expense.entity = None
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

    # Crea spesa/autofattura su FIC
    try:
        # Debug: FIC payload
        fic_payload_debug = expense.model_dump(mode='json', exclude={'entity'})
        if expense.entity:
            fic_payload_debug['entity'] = expense.entity.model_dump(exclude_none=True)
        if expense.entity_id:
            fic_payload_debug['entity'] = {'id': expense.entity_id, 'name': supplier.name}
        logger.info("FIC PAYLOAD: %s", json.dumps(fic_payload_debug, indent=2, ensure_ascii=False))

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

        queue.complete(sha, fic_resp.id)
        logger.info("Registrato su FIC: id=%d, tipo=%s", fic_resp.id, fic_resp.type)

        result = PipelineResult(
            file=file_event,
            status=DocumentStatus.RECORDED,
            factum_status="done",
            fic_status="created",
            fic_id=fic_resp.id,
            document_type=doc_type_detected,
        )

        # Successo → processed
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