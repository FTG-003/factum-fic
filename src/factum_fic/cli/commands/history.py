"""Comando ``factum-fic history`` — cronologia elaborazioni locali.

Mostra gli ultimi 10 record della coda SQLite in una tabella Rich.
Ogni riga mostra: data elaborazione, file, ID spesa FIC, ID autofattura SDI,
e stato.

Alias: ``factum-fic storico``
"""

from __future__ import annotations

from rich.table import Table

from factum_fic.cli.ui import console
from factum_fic.storage.queue import QueueStore


def history(limit: int = 10) -> None:
    """Mostra la cronologia delle ultime elaborazioni."""
    queue = QueueStore()
    try:
        rows = queue.recent(limit)
    finally:
        queue.close()

    if not rows:
        console.print("[yellow]Nessuna elaborazione registrata.[/]")
        return

    table = Table(title="📋 Cronologia elaborazioni", box=None, header_style="bold cyan")
    table.add_column("Data", style="white")
    table.add_column("File", style="cyan")
    table.add_column("Spesa FIC ID", style="green", justify="right")
    table.add_column("Autofattura SDI ID", style="magenta", justify="right")
    table.add_column("Stato", style="yellow")

    for row in rows:
        processed_at = row.get("processed_at") or ""
        # Mostra solo la data (primi 10 caratteri YYYY-MM-DD)
        data = processed_at[:10] if processed_at else "—"
        file_path = row.get("file_path", "")
        filename = file_path.rsplit("/", 1)[-1] if file_path else "—"
        expense_id = row.get("fic_expense_id")
        expense_str = str(expense_id) if expense_id is not None else "—"
        si_id = row.get("fic_self_invoice_id")
        si_str = str(si_id) if si_id is not None else "—"
        status = row.get("status", "?")

        # Icona di stato
        status_icon = {"completed": "✅", "failed": "❌", "processing": "⏳", "queued": "⏳"}.get(
            status, "❓"
        )

        table.add_row(data, filename, expense_str, si_str, f"{status_icon} {status}")

    console.print(table)
