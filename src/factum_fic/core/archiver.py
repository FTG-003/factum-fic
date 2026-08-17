"""Zero-clutter file archiver: sanitizzazione nomi e albero YYYY/MM.

Nessun file JSON/TXT intermedio: lo stato risiede solo nel DB SQLite.
Le funzioni sono pure (nessun accoppiamento a Settings): il chiamante passa
i parametri esplicitamente.
"""

from __future__ import annotations

import datetime
import logging
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Caratteri non sicuri per filesystem (Windows/Linux/macOS)
_UNSAFE_CHARS_RE = re.compile(r'[/\\:*?"<>|]')
# Spazi multipli
_MULTI_SPACE_RE = re.compile(r"\s+")


def sanitize_filename(name: str) -> str:
    """Rimuove caratteri non sicuri, collassa spazi multipli e fa trim.

    I caratteri ``/ \\ : * ? \" < > |`` vengono sostituiti con ``_``.
    Esempi::

        sanitize_filename("INV/2026/08")   → "INV_2026_08"
        sanitize_filename("Fattura  n. 5") → "Fattura_n._5"
    """
    cleaned = _UNSAFE_CHARS_RE.sub("_", name)
    cleaned = _MULTI_SPACE_RE.sub(" ", cleaned)
    return cleaned.strip()


def _resolve_collision(dest: Path) -> Path:
    """Se *dest* esiste già, aggiunge un suffisso numerico ``_N.ext``."""
    if not dest.exists():
        return dest
    counter = 1
    while True:
        candidate = dest.parent / f"{dest.stem}_{counter}{dest.suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def archive_processed_file(
    src_path: Path,
    base_dir: Path,
    date_str: str | None = None,
    supplier_name: str | None = None,
    invoice_num: str | None = None,
) -> Path:
    """Sposta un file processato in ``base_dir / archiviate / YYYY / MM /``.

    Il nome del file di destinazione è costruito come::

        {YYYY-MM-DD}_{SanitizedSupplier}_{SanitizedInvoiceNum}.{ext}

    Se *date_str* è mancante o non valido, viene usata la data odierna.
    Se *supplier_name* e/o *invoice_num* sono vuoti, vengono omessi
    (con eventuali separatori).

    Gestisce collisioni con suffisso ``_1``, ``_2`` etc.
    Non scrive alcun file JSON/TMP intermedio.

    Returns:
        Percorso assoluto del file dopo lo spostamento.
    """
    date: datetime.date
    if date_str:
        try:
            date = datetime.date.fromisoformat(date_str)
        except (ValueError, TypeError):
            date = datetime.date.today()
    else:
        date = datetime.date.today()

    # Costruzione nome file
    parts = [date.isoformat()]
    supplier_sanitized = sanitize_filename(supplier_name or "").strip()
    if supplier_sanitized:
        parts.append(supplier_sanitized)
    invoice_sanitized = sanitize_filename(invoice_num or "").strip()
    if invoice_sanitized:
        parts.append(invoice_sanitized)

    stem = "_".join(parts)
    suffix = src_path.suffix if src_path.suffix else ".pdf"
    filename = f"{stem}{suffix}"

    # Albero archiviate/YYYY/MM/
    dest_dir = base_dir.resolve() / "archiviate" / f"{date.year:04d}" / f"{date.month:02d}"
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest = _resolve_collision(dest_dir / filename)
    shutil.move(str(src_path), str(dest))
    logger.info("Archiviato: %s → %s", src_path.name, dest)
    return dest


def archive_failed_file(src_path: Path, base_dir: Path) -> Path:
    """Sposta un file fallito in ``base_dir / da_verificare /``.

    Mantiene il nome originale (già univoco per la coda SQLite).
    Non crea struttura anno/mese: i falliti sono pochi e vanno esaminati.
    Non scrive file JSON/TMP intermedi.

    Returns:
        Percorso assoluto del file dopo lo spostamento.
    """
    dest_dir = base_dir.resolve() / "da_verificare"
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest = _resolve_collision(dest_dir / src_path.name)
    shutil.move(str(src_path), str(dest))
    logger.warning("Fallito spostato in: %s", dest)
    return dest
