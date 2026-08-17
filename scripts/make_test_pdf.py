#!/usr/bin/env python3
"""Genera un PDF realistico a 2 pagine simile a una fattura Hetzner autentica.

Crea ``da_elaborare/Hetzner-luglio.pdf`` con dati certi:
    - Fornitore: Hetzner Online GmbH (DE), P.IVA DE812875199
    - Data: 17/08/2026
    - Fattura n.: 20260817-50
    - 1x Cloud Server SX11: EUR 49,90 netto
    - 2x Additional IPv4:   EUR  0,50 netto
    - Imponibile: EUR 50,40 — IVA 0% (Reverse Charge) — Totale: EUR 50,40
    - 2 pagine (fronte/retro) con layout professionale

Uso:  uv run python scripts/make_test_pdf.py [output_path]
"""

from __future__ import annotations

import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


def _line(c: canvas.Canvas, y: float, text: str, size: int = 10, font: str = "Helvetica") -> None:
    c.setFont(font, size)
    c.drawString(20 * mm, y, text)


def _rline(c: canvas.Canvas, y: float, text: str, size: int = 10, font: str = "Helvetica") -> None:
    """Right-aligned text at right margin."""
    c.setFont(font, size)
    c.drawRightString(A4[0] - 20 * mm, y, text)


def make_test_pdf(out_path: str | Path) -> Path:
    """Genera il PDF di test Hetzner realistico a 2 pagine."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    c = canvas.Canvas(str(out), pagesize=A4)
    w, h = A4
    m = 20 * mm
    left = m
    right = w - m

    def line(y: float, text: str, size: int = 10, font: str = "Helvetica") -> None:
        _line(c, y, text, size, font)

    def rline(y: float, text: str, size: int = 10, font: str = "Helvetica") -> None:
        _rline(c, y, text, size, font)

    y = h - m

    # ═══════════════════════════════════════════════════════════
    # PAGE 1
    # ═══════════════════════════════════════════════════════════

    # -- Intestazione azienda --
    c.setFont("Helvetica-Bold", 16)
    c.drawString(left, y, "Hetzner Online GmbH")
    y -= 6 * mm

    c.setFont("Helvetica", 9)
    c.drawString(left, y, "Industriestr. 25, 91710 Gunzenhausen, Deutschland")
    y -= 4 * mm
    c.drawString(left, y, "Tel.: +49 (0) 9831 505-0  |  Fax: +49 (0) 9831 505-3")
    y -= 4 * mm
    c.drawString(left, y, "E-Mail: invoice@hetzner.com  |  Web: www.hetzner.com")
    y -= 4 * mm
    c.drawString(left, y, "USt-IdNr. / VAT ID: DE812875199  |  HRB 6089 Amtsgericht Gunzenhausen")
    y -= 8 * mm

    # -- Linea separatrice --
    c.setStrokeColorRGB(0.8, 0.2, 0.2)  # rosso Hetzner
    c.setLineWidth(0.5)
    c.line(left, y, right, y)
    y -= 6 * mm

    # -- Dati fattura --
    c.setStrokeColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(left, y, "RECHNUNG / INVOICE")
    y -= 8 * mm

    c.setFont("Helvetica", 10)
    invoice_lines = [
        ("Rechnungsnr. / Invoice No.:", "20260817-50"),
        ("Rechnungsdatum / Invoice Date:", "17.08.2026"),
        ("Leistungszeitraum / Service Period:", "01.08.2026 - 31.08.2026"),
        ("Zahlungsziel / Payment Terms:", "14 Tage netto / 14 days net"),
    ]
    for label, val in invoice_lines:
        c.drawString(left, y, f"{label}  {val}")
        y -= 5 * mm

    y -= 4 * mm

    # -- Kundendaten (cliente) --
    c.setFont("Helvetica-Bold", 10)
    c.drawString(left, y, "Kunde / Customer:")
    y -= 5 * mm
    c.setFont("Helvetica", 10)
    c.drawString(left, y, "Fatture in Cloud S.r.l.")
    y -= 4 * mm
    c.drawString(left, y, "Via del Commercio 10")
    y -= 4 * mm
    c.drawString(left, y, "37135 Verona (VR) — Italia")
    y -= 4 * mm
    c.drawString(left, y, "VAT IT12345678901  |  Cod. SDI: 0123456789")
    y -= 8 * mm

    # -- Linea separatrice --
    c.line(left, y, right, y)
    y -= 6 * mm

    # -- Intestazione tabella servizi --
    c.setFont("Helvetica-Bold", 9)
    cols_x = [left, left + 8 * mm, left + 110 * mm, left + 140 * mm, left + 165 * mm]
    c.drawString(cols_x[0], y, "Pos.")
    c.drawString(cols_x[1], y, "Beschreibung / Description")
    c.drawString(cols_x[2], y, "Menge")
    c.drawString(cols_x[3], y, "Einzelpreis")
    c.drawRightString(right, y, "Nettobetrag")
    y -= 3 * mm
    c.line(left, y, right, y)
    y -= 5 * mm

    # -- Righe servizi --
    c.setFont("Helvetica", 9)
    items = [
        ("1", "Cloud Server SX11 (1 Monat)", "1", "EUR 49,90", "EUR 49,90"),
        ("2", "Zusätzliche IPv4-Adresse (x2)", "2", "EUR  0,25", "EUR  0,50"),
    ]
    for pos, desc, qty, price, net in items:
        c.drawString(cols_x[0], y, pos)
        c.drawString(cols_x[1], y, desc)
        c.drawString(cols_x[2], y, qty)
        c.drawString(cols_x[3], y, price)
        c.drawRightString(right, y, net)
        y -= 5 * mm

    y -= 3 * mm
    c.line(left, y, right, y)
    y -= 6 * mm

    # -- Totali --
    totals = [
        ("Nettobetrag / Net Amount:", "EUR 50,40"),
        ("MwSt. 0% (Reverse Charge / §13b UStG):", "EUR  0,00"),
        ("Gesamtbetrag / Total Amount:", "EUR 50,40"),
    ]
    for label, val in totals:
        c.setFont("Helvetica-Bold", 11 if "Gesamt" in label else 10)
        c.drawString(left, y, label)
        c.drawRightString(right, y, val)
        y -= 6 * mm

    y -= 4 * mm

    # -- Reverse Charge Notice --
    c.setFont("Helvetica", 8)
    rc_lines = [
        "Hinweis zum Reverse Charge / Reverse Charge Notice:",
        "Die Mehrwertsteuer wird gemäß §13b UStG auf den Erwerber übertragen.",
        "Der Erwerber (Fatture in Cloud S.r.l., Italien) schuldet die Umsatzsteuer.",
        "IVA assolta dal cessionario italiano ai sensi dell'art. 17-ter DPR 633/72.",
        "Netto EUR 50,40 — Iva 0% (Reverse Charge) — Totale EUR 50,40.",
    ]
    for rc_line in rc_lines:
        c.drawString(left, y, rc_line)
        y -= 3.5 * mm

    y -= 6 * mm

    # -- Footer pagina 1 --
    c.setFont("Helvetica", 7)
    c.drawString(left, 10 * mm, "Seite 1/2  |  Hetzner Online GmbH  |  Rechnung 20260817-50")

    c.showPage()
    y = h - m

    # ═══════════════════════════════════════════════════════════
    # PAGE 2 — Terms, Payment Info, Legal Notes
    # ═══════════════════════════════════════════════════════════

    c.setFont("Helvetica-Bold", 12)
    c.drawString(left, y, "Allgemeine Geschäftsbedingungen / Terms & Conditions")
    y -= 8 * mm

    c.setFont("Helvetica", 9)
    terms = [
        "1. Zahlungsbedingungen / Payment Terms",
        "   Die Zahlung erfolgt innerhalb von 14 Tagen netto ab Rechnungsdatum.",
        "   Zahlungen per Überweisung auf das unten angegebene Konto.",
        "",
        "2. Reverse Charge / Steuerschuldnerschaft des Leistungsempfängers",
        "   Gemäß §13b UStG wird die Umsatzsteuer auf den Leistungsempfänger",
        "   übertragen. Der Leistungsempfänger hat die Steuer in Italien",
        "   im Rahmen des Reverse-Charge-Verfahrens zu erklären und abzuführen.",
        "   Der Nettorechnungsbetrag ist ohne Abzug von Umsatzsteuer zur Zahlung fällig.",
        "",
        "3. Leistungsbeschreibung / Service Description",
        "   Bereitstellung und Betrieb von dedizierten Cloud-Servern",
        "   im Rechenzentrum Nürnberg (NBG1-DC1).",
        "   Inklusive 24/7 Support, Monitoring und DDoS-Schutz.",
        "",
        "4. Lieferzeitraum / Delivery Period",
        "   01.08.2026 — 31.08.2026 (1 Monat)",
        "",
        "5. Bankverbindung / Bank Details",
        "   Kontoinhaber:  Hetzner Online GmbH",
        "   Bank:          Deutsche Bank, Gunzenhausen",
        "   IBAN:          DE227607001234567890",
        "   BIC/SWIFT:     DEUTDEMM760",
        "   Verwendungszweck: 20260817-50",
        "",
        "6. Umsatzsteuer-Identifikationsnummer / VAT ID",
        "   DE812875199",
        "",
        "7. Steuernummer / Tax Number",
        "   201/123/45678",
        "",
        "8. Sitz der Gesellschaft / Registered Office",
        "   Hetzner Online GmbH, Industriestr. 25, 91710 Gunzenhausen",
        "   Registergericht: Amtsgericht Ansbach, HRB 6089",
        "   Geschäftsführer: Martin Hetzner",
        "",
        "Diese Rechnung wurde maschinell erstellt und ist ohne Unterschrift gültig.",
    ]
    for t in terms:
        c.drawString(left, y, t)
        y -= 3.8 * mm

    # -- Footer pagina 2 --
    y = 10 * mm
    c.setFont("Helvetica", 7)
    c.drawString(left, y, "Seite 2/2  |  Hetzner Online GmbH  |  Rechnung 20260817-50")

    c.save()
    return out


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "da_elaborare/Hetzner-luglio.pdf"
    out = make_test_pdf(target)
    print(f"✅ PDF generato: {out.resolve()} ({out.stat().st_size} byte, {Path(str(out)).stat().st_size // 1000} KB)")
