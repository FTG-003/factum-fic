#!/usr/bin/env python3
"""Genera il PDF di test standardizzato per il ciclo E2E su API FIC reale.

Crea ``da_elaborare/test_hetzner_valido.pdf`` con dati certi:
    - Fornitore: Hetzner Online GmbH (DE), P.IVA DE812875199
    - Data: 17/08/2026
    - Imponibile: 50,00 EUR — IVA 0% (Reverse Charge UE)
    - Totale: 50,00 EUR

Uso:  uv run python scripts/make_test_pdf.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


def make_test_pdf(out_path: str | Path) -> Path:
    """Genera il PDF di test Hetzner con importi noti (50,00 EUR netto)."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    c = canvas.Canvas(str(out), pagesize=A4)
    width, height = A4
    margin = 20 * mm

    def line(y: float, text: str, size: int = 10, font: str = "Helvetica") -> None:
        c.setFont(font, size)
        c.drawString(margin, y, text)

    # ── Header fornitore ──
    line(height - margin, "Hetzner Online GmbH", 14, "Helvetica-Bold")
    line(height - margin - 5 * mm, "Industriestr. 25, 91710 Gunzenhausen, Deutschland")
    line(height - margin - 10 * mm, "USt-IdNr. / VAT ID: DE812875199")
    line(height - margin - 15 * mm, "E-Mail: invoice@hetzner.com")

    # ── Titolo fattura ──
    line(height - margin - 25 * mm, "RECHNUNG / INVOICE", 16, "Helvetica-Bold")
    line(height - margin - 30 * mm, "Rechnungsnummer / Invoice No.: 20260817-50")
    line(height - margin - 35 * mm, "Rechnungsdatum / Invoice Date: 17.08.2026")
    line(height - margin - 40 * mm, "Leistungszeitraum / Service Period: 08/2026")

    # ── Tabella riga ──
    line(height - margin - 52 * mm, "Pos.  Beschreibung                Menge  Einzelpreis  Nettobetrag", 9)
    c.setFont("Helvetica", 9)
    c.line(margin, height - margin - 54 * mm, width - margin, height - margin - 54 * mm)
    line(height - margin - 58 * mm, "1     Cloud Server SX11 (1 Monat)  1      50,00 EUR    50,00 EUR", 9)
    c.line(margin, height - margin - 60 * mm, width - margin, height - margin - 60 * mm)

    # ── Totali ──
    line(height - margin - 68 * mm, "Nettobetrag / Net Amount:                    50,00 EUR", 10, "Helvetica-Bold")
    line(height - margin - 73 * mm, "MwSt. 0% (Reverse Charge) / VAT 0%:           0,00 EUR")
    line(height - margin - 78 * mm, "Gesamtbetrag / Total Amount:                 50,00 EUR", 11, "Helvetica-Bold")

    # ── Note reverse charge ──
    c.setFont("Helvetica", 8)
    line(height - margin - 90 * mm, "Reverse Charge gem. §13b UStG — IVA assolta dal cessionario italiano")
    line(height - margin - 94 * mm, "(art. 17-ter DPR 633/72). Importo netto: EUR 50,00 — Totale: EUR 50,00.")

    c.showPage()
    c.save()
    return out


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "da_elaborare/test_hetzner_valido.pdf"
    out = make_test_pdf(target)
    print(f"✅ PDF generato: {out.resolve()} ({out.stat().st_size} byte)")
