"""Orchestratore: File → Hash → Factum Parse → Mapper → FIC.

Gestisce l'intero flusso:
1. Legge il file, calcola SHA-256
2. Estrae testo (PDF/XML) o usa contenuto testuale
3. Invia a Factum Parse API per parsing
4. Mappa il risultato in request FIC
5. Registra su Fatture in Cloud (spesa o autofattura)
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from factum_fic.config import Settings
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


def _extract_text(path: Path) -> tuple[str, str]:
    """Estrae il contenuto testuale da un file.

    Per ora supporta solo file di testo semplice.

    Returns:
        (testo_estratto, tipo_documento)
    """
    ext = path.suffix.lower()
    if ext in {".txt", ".csv", ".json"}:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text, "auto"

    # PDF e XML verranno gestiti in fasi successive
    # Per ora leggiamo come testo (fallback)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return text, "auto"
    except (UnicodeDecodeError, Exception):
        # File binario (PDF): estrazione da implementare
        return "", "auto"


def _sha256_file(path: Path) -> str:
    """Calcola SHA-256 di un file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


async def process_file(
    path: Path,
    *,
    factum: FactumClient,
    fic: FICClient,
    mapper: Mapper,
    queue: QueueStore,
    settings: Settings,
) -> PipelineResult:
    """Processa un singolo file: Factum → Mapper → FIC.

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

    # Deduplicazione
    if queue.exists(sha):
        logger.info("File già processato (SHA-256 match): %s", path.name)
        return PipelineResult(
            file=file_event,
            status=DocumentStatus.SKIPPED,
            fic_status="duplicate",
        )

    queue.enqueue(sha, str(path))

    # Estrai testo
    text, doc_type = _extract_text(path)
    if not text:
        logger.warning("Testo vuoto per %s", path.name)
        return PipelineResult(
            file=file_event,
            status=DocumentStatus.FAILED,
            factum_status="empty_text",
        )

    # Chiamata Factum
    try:
        factum_resp = await factum.parse_text(text, doc_type=doc_type)
    except Exception as exc:
        logger.exception("Factum parsing fallito per %s", path.name)
        return PipelineResult(
            file=file_event,
            status=DocumentStatus.FAILED,
            factum_status="error",
            factum_error=str(exc),
        )

    if factum_resp.status != "done" or factum_resp.result is None:
        return PipelineResult(
            file=file_event,
            status=DocumentStatus.FAILED,
            factum_status=factum_resp.status,
            factum_error=factum_resp.error,
        )

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
        return PipelineResult(
            file=file_event,
            status=DocumentStatus.FAILED,
            factum_status="parsed",
            fic_status="supplier_error",
            fic_error=str(exc),
            document_type=doc_type_detected,
        )

    # Crea spesa/autofattura su FIC
    try:
        fic_resp = await fic.create_expense(expense)
        queue.complete(sha, fic_resp.id)
        logger.info("Registrato su FIC: id=%d, tipo=%s", fic_resp.id, fic_resp.type)
        return PipelineResult(
            file=file_event,
            status=DocumentStatus.RECORDED,
            factum_status="done",
            fic_status="created",
            fic_id=fic_resp.id,
            document_type=doc_type_detected,
        )
    except Exception as exc:
        logger.exception("FIC expense fallito per %s", path.name)
        return PipelineResult(
            file=file_event,
            status=DocumentStatus.FAILED,
            factum_status="parsed",
            fic_status="expense_error",
            fic_error=str(exc),
            document_type=doc_type_detected,
        )
