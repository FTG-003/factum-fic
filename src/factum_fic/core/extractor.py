"""Estrazione testo locale da PDF e file di testo.

Prima di inviare il contenuto alla Factum Parse API, estraiamo il testo
dal documento: PDF (via pypdf), XML, TXT, CSV (lettura diretta UTF-8).

Se il testo estratto è troppo corto o vuoto (es. PDF scansionato/immagine),
viene sollevato ValueError così la pipeline può gestire il fallimento.
"""

from __future__ import annotations

from pathlib import Path

# Estensioni lette direttamente come testo (UTF-8 con fallback latin-1)
_TEXT_EXTENSIONS = {".xml", ".txt", ".csv"}
# Soglia minima: sotto questo numero di caratteri il testo è inutilizzabile
_MIN_TEXT_LENGTH = 20


def extract_text(file_path: Path) -> str:
    """Estrae il contenuto testuale di un documento.

    Args:
        file_path: Percorso del file (PDF/XML/TXT/CSV).

    Returns:
        Testo estratto.

    Raises:
        ValueError: Se il testo è vuoto, troppo corto (< 20 caratteri) o il
            file non è estraibile (es. PDF scansionato/immagine).
    """
    ext = file_path.suffix.lower()

    if ext == ".pdf":
        text = _extract_pdf(file_path)
    elif ext in _TEXT_EXTENSIONS:
        text = _read_text_file(file_path)
    else:
        raise ValueError(f"Estensione non supportata: {ext}")

    text = text.strip()
    if len(text) < _MIN_TEXT_LENGTH:
        raise ValueError(
            "Testo non estraibile o PDF scansionato/immagine "
            f"(estratti {len(text)} caratteri)"
        )
    return text


def _extract_pdf(file_path: Path) -> str:
    """Estrae il testo da un PDF iterando le pagine con pypdf."""
    from pypdf import PdfReader

    reader = PdfReader(str(file_path))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages)


def _read_text_file(file_path: Path) -> str:
    """Legge un file di testo in UTF-8, con fallback su latin-1."""
    try:
        return file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return file_path.read_text(encoding="latin-1")
