"""Test del filtro file temporanei e della logica di archiviazione pipeline."""

from __future__ import annotations

from pathlib import Path

from factum_fic.core.pipeline import is_temp_file, _archive_path


def test_temp_file_crdownload() -> None:
    """.crdownload → temporaneo."""
    assert is_temp_file(Path("download.pdf.crdownload")) is True


def test_temp_file_part() -> None:
    """.part → temporaneo."""
    assert is_temp_file(Path("invoice.pdf.part")) is True


def test_temp_file_tmp() -> None:
    """.tmp → temporaneo."""
    assert is_temp_file(Path("file.pdf.tmp")) is True


def test_temp_file_swp() -> None:
    """.swp → temporaneo."""
    assert is_temp_file(Path(".file.pdf.swp")) is True


def test_temp_file_bak() -> None:
    """.bak → temporaneo."""
    assert is_temp_file(Path("file.pdf.bak")) is True


def test_temp_file_tilde() -> None:
    """~ → temporaneo."""
    assert is_temp_file(Path("file.pdf~")) is True


def test_temp_file_hidden() -> None:
    """File nascosto (inizia con .) → temporaneo."""
    assert is_temp_file(Path(".hidden.pdf")) is True


def test_temp_file_hidden_with_ext() -> None:
    """File nascosto con estensione normale → temporaneo."""
    assert is_temp_file(Path(".gitignore")) is True


def test_not_temp_file_pdf() -> None:
    """PDF regolare → NON temporaneo."""
    assert is_temp_file(Path("Hetzner-luglio.pdf")) is False


def test_not_temp_file_xml() -> None:
    """XML regolare → NON temporaneo."""
    assert is_temp_file(Path("fattura.xml")) is False


def test_not_temp_file_mixed_case() -> None:
    """Case misto → NON temporaneo."""
    assert is_temp_file(Path("Invoice-2026.PDF")) is False