"""Unit test per factum_fic.core.archiver — sanitizzazione e archiviazione zero-clutter."""

from __future__ import annotations

import datetime
from pathlib import Path

from factum_fic.core.archiver import (
    _resolve_collision,
    archive_failed_file,
    archive_processed_file,
    sanitize_filename,
)

# ── sanitize_filename ─────────────────────────────────────────────────────────

def test_sanitize_keeps_safe() -> None:
    """Stringa normale senza caratteri speciali resta invariata."""
    assert sanitize_filename("Hetzner") == "Hetzner"


def test_sanitize_unsafe_chars() -> None:
    """Sostituisce / \\ : * ? \" < > | con _ (spazio singolo preservato)."""
    assert sanitize_filename('INV/2026\\08:test*? "<>|') == "INV_2026_08_test__ ____"


def test_sanitize_unsafe_chars_collapsed_space() -> None:
    """Caratteri non sicuri contigui a spazi: spazio preservato, doppi collassati."""
    # `* ?` → `__`, poi spazio singolo, poi `"` → `_`
    assert sanitize_filename('INV/2026 *? "') == "INV_2026 __ _"


def test_sanitize_multiple_spaces() -> None:
    """Collassa spazi multipli."""
    assert sanitize_filename("Fattura   n. 5") == "Fattura n. 5"


def test_sanitize_trim() -> None:
    """Rimuove spazi iniziali e finali."""
    assert sanitize_filename("  Fattura  ") == "Fattura"


def test_sanitize_only_unsafe() -> None:
    """Solo caratteri non sicuri → underscore multipli collassati."""
    result = sanitize_filename("*** ???")
    # '*' → '_' e '?' → '_', collassati
    assert len(result) > 0
    assert "  " not in result


# ── archive_processed_file ────────────────────────────────────────────────────

def test_archive_processed_creates_tree(tmp_path: Path) -> None:
    """Crea ricorsivamente archiviate/YYYY/MM/ e vi sposta il file."""
    src = tmp_path / "fattura.pdf"
    src.write_text("contenuto fittizio")
    date_str = "2026-08-15"

    dest = archive_processed_file(src, tmp_path, date_str=date_str)

    assert dest.parent == tmp_path / "archiviate" / "2026" / "08"
    assert dest.name.startswith("2026-08-15")
    assert dest.suffix == ".pdf"
    assert dest.exists()
    assert not src.exists()  # spostato, non copiato


def test_archive_processed_no_metadata(tmp_path: Path) -> None:
    """Senza date/supplier/invoice usa data odierna e omette extra."""
    src = tmp_path / "doc.pdf"
    src.write_text("test")
    today = datetime.date.today()

    dest = archive_processed_file(src, tmp_path)

    assert dest.parent == tmp_path / "archiviate" / f"{today.year:04d}" / f"{today.month:02d}"
    assert dest.name == f"{today.isoformat()}.pdf"


def test_archive_processed_collision(tmp_path: Path) -> None:
    """File duplicato → suffisso _1, _2."""
    src1 = tmp_path / "fattura.pdf"
    src1.write_text("prima")

    src2 = tmp_path / "altra.pdf"
    src2.write_text("seconda")

    date_str = "2026-08-15"
    dest1 = archive_processed_file(src1, tmp_path, date_str=date_str)
    dest2 = archive_processed_file(src2, tmp_path, date_str=date_str, invoice_num="INV001")

    # Primo: nome normale con data
    assert dest1.name == "2026-08-15.pdf"
    # Secondo: stesso nome ma con suffisso
    assert dest2.name != dest1.name
    assert "2026-08-15" in dest2.stem

    # Terzo file: collisione su INV001
    src3 = tmp_path / "terza.pdf"
    src3.write_text("terza")
    dest3 = archive_processed_file(src3, tmp_path, date_str=date_str, invoice_num="INV001")
    assert dest3.name != dest2.name
    assert dest3.name != dest1.name


def test_archive_processed_supplier_invoice_in_name(tmp_path: Path) -> None:
    """Il nome contiene data_fornitore_numero."""
    src = tmp_path / "test_invoice.pdf"
    src.write_text("fattura test")

    dest = archive_processed_file(
        src, tmp_path, date_str="2026-08-15",
        supplier_name="Hetzner GmbH", invoice_num="INV/2026/08",
    )

    assert "Hetzner GmbH" in dest.stem
    # INV/2026/08 → sanitized a INV_2026_08
    assert "INV_2026_08" in dest.stem
    assert dest.suffix == ".pdf"


# ── archive_failed_file ───────────────────────────────────────────────────────

def test_archive_failed_creates_da_verificare(tmp_path: Path) -> None:
    """Sposta in da_verificare/."""
    src = tmp_path / "fallito.pdf"
    src.write_text("errore")

    dest = archive_failed_file(src, tmp_path)

    assert dest.parent == tmp_path / "da_verificare"
    assert dest.name == "fallito.pdf"
    assert dest.exists()
    assert not src.exists()


def test_archive_failed_collision(tmp_path: Path) -> None:
    """Due file con stesso nome → suffisso _1."""
    src1 = tmp_path / "stesso_nome.pdf"
    src1.write_text("fail1")
    first = archive_failed_file(src1, tmp_path)
    assert first.name == "stesso_nome.pdf"

    src2 = tmp_path / "stesso_nome.pdf"
    src2.write_text("fail2")
    second = archive_failed_file(src2, tmp_path)
    assert second.name == "stesso_nome_1.pdf"
    assert second.exists()
    assert second.parent == tmp_path / "da_verificare"


# ── _resolve_collision ────────────────────────────────────────────────────────

def test_resolve_collision_no_existing(tmp_path: Path) -> None:
    """Se il file non esiste, restituisce il percorso invariato."""
    dest = tmp_path / "unico.pdf"
    assert _resolve_collision(dest) == dest


def test_resolve_collision_increments(tmp_path: Path) -> None:
    """Se esiste già, aggiunge _1, _2."""
    (tmp_path / "base.pdf").write_text("originale")
    (tmp_path / "base_1.pdf").write_text("primo")

    result = _resolve_collision(tmp_path / "base.pdf")
    assert result.name == "base_2.pdf"
