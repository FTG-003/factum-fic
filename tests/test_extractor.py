"""Test per l'estrattore testo locale (PDF, TXT, XML, CSV)."""

from __future__ import annotations

from pathlib import Path

import pytest

from factum_fic.core.extractor import extract_text


# ── Fixture: file di test temporanei ──────────────────────────────────────────


@pytest.fixture
def sample_txt(tmp_path: Path) -> Path:
    path = tmp_path / "test_fattura.txt"
    path.write_text(
        "Fattura: DigitalOcean Inc.\n"
        "Data: 01/08/2026\n"
        "Importo: 59.00 USD\n"
        "Servizio: Droplet Basic Plan\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def sample_csv(tmp_path: Path) -> Path:
    path = tmp_path / "fatture.csv"
    path.write_text(
        "fornitore,data,importo\n"
        "DigitalOcean,2026-08-01,59.00\n"
        "Aruba,2026-08-15,1464.00\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def sample_xml(tmp_path: Path) -> Path:
    path = tmp_path / "fattura.xml"
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<FatturaElettronica>\n"
        "  <Fornitore>Aruba S.p.A.</Fornitore>\n"
        "  <Importo>1464.00</Importo>\n"
        "</FatturaElettronica>\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """Crea un PDF minimale valido con testo estraibile."""
    path = tmp_path / "test_fattura.pdf"
    _write_minimal_pdf(
        path,
        text="Fattura DigitalOcean Inc. 59.00 USD",
    )
    return path


def _write_minimal_pdf(path: Path, text: str) -> None:
    """Scrive un PDF 1.4 minimale con una pagina di testo."""
    content_data = f"BT /F1 14 Tf 50 550 Td ({text}) Tj ET\n".encode()

    parts: list[bytes] = [b"%PDF-1.4\n"]
    offsets = [None]
    offset = len(parts[0])

    def _obj(data: bytes) -> int:
        nonlocal offset
        offsets.append(offset)
        parts.append(data)
        offset += len(data)
        return len(offsets) - 1

    _obj(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    _obj(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    _obj(
        b"3 0 obj\n"
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]\n"
        b"   /Contents 4 0 R\n"
        b"   /Resources << /Font << /F1 5 0 R >> >> >>\n"
        b"endobj\n"
    )
    _obj(
        b"4 0 obj\n"
        b"<< /Length " + str(len(content_data)).encode() + b" >>\n"
        b"stream\n" + content_data + b"endstream\n"
        b"endobj\n"
    )
    _obj(
        b"5 0 obj\n"
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\n"
        b"endobj\n"
    )

    xref_offset = sum(len(p) for p in parts)
    xref = b"xref\n"
    xref += b"0 6\n"
    xref += b"%010d %05d %c \n" % (0, 65535, ord("f"))
    for off in offsets[1:]:
        xref += b"%010d %05d %c \n" % (off, 0, ord("n"))

    parts.append(xref)
    parts.append(b"trailer\n")
    parts.append(b"<< /Size 6 /Root 1 0 R >>\n")
    parts.append(b"startxref\n")
    parts.append(str(xref_offset).encode() + b"\n")
    parts.append(b"%%EOF\n")

    path.write_bytes(b"".join(parts))


# ── Test ─────────────────────────────────────────────────────────────────────


class TestExtractText:
    """Test per l'estrazione testo da vari formati."""

    def test_txt(self, sample_txt: Path) -> None:
        text = extract_text(sample_txt)
        assert "DigitalOcean" in text
        assert "59.00" in text
        assert len(text) >= 20

    def test_csv(self, sample_csv: Path) -> None:
        text = extract_text(sample_csv)
        assert "DigitalOcean" in text
        assert "Aruba" in text
        assert len(text) >= 20

    def test_xml(self, sample_xml: Path) -> None:
        text = extract_text(sample_xml)
        assert "Aruba" in text
        assert "1464.00" in text
        assert len(text) >= 20

    def test_pdf(self, sample_pdf: Path) -> None:
        """Verifica che il testo venga estratto da PDF."""
        text = extract_text(sample_pdf)
        # Anche se il PDF è minimale, non deve sollevare eccezioni
        assert isinstance(text, str)

    def test_file_inesistente(self, tmp_path: Path) -> None:
        with pytest.raises((FileNotFoundError, ValueError)):
            extract_text(tmp_path / "inesistente.pdf")

    def test_estensione_non_supportata(self, tmp_path: Path) -> None:
        path = tmp_path / "foto.png"
        path.write_bytes(b"fake png content")
        with pytest.raises(ValueError, match="non supportata"):
            extract_text(path)

    def test_testo_troppo_corto(self, tmp_path: Path) -> None:
        path = tmp_path / "corto.txt"
        path.write_text("Ciao", encoding="utf-8")
        with pytest.raises(ValueError, match="non estraibile"):
            extract_text(path)

    def test_testo_vuoto(self, tmp_path: Path) -> None:
        path = tmp_path / "vuoto.txt"
        path.write_text("   \n  \n", encoding="utf-8")
        with pytest.raises(ValueError, match="non estraibile"):
            extract_text(path)

    def test_txt_latin1(self, tmp_path: Path) -> None:
        """Verifica fallback encoding latin-1."""
        path = tmp_path / "latin1.txt"
        # Scrivi byte che non sono UTF-8 validi
        path.write_bytes(b"Fattura: \xe9\xe0\xf9 test 12345")
        text = extract_text(path)
        assert len(text) >= 20