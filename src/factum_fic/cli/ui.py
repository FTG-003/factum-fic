"""Render CLI con Rich — tabelle, log colorati, progress."""

from __future__ import annotations

from rich.console import Console
from rich.table import Table

from factum_fic.core.models import DocumentStatus, PipelineResult

console = Console(stderr=True)


def print_info(message: str) -> None:
    """Stampa un messaggio informativo."""
    console.print(f"[blue]ℹ[/blue] {message}")


def print_warning(message: str) -> None:
    """Stampa un avviso."""
    console.print(f"[yellow]⚠[/yellow] {message}")


def print_ok(message: str) -> None:
    """Stampa un messaggio di successo."""
    console.print(f"[green]✓[/green] {message}")


def print_error(message: str) -> None:
    """Stampa un messaggio di errore."""
    console.print(f"[red]✗[/red] {message}")


def print_result_table(results: list[PipelineResult]) -> None:
    """Stampa una tabella con i risultati di elaborazione."""
    table = Table(title="Risultati elaborazione", box=None)
    table.add_column("File", style="cyan")
    table.add_column("Factum", style="magenta")
    table.add_column("FIC", style="green")
    table.add_column("Stato", style="yellow")
    table.add_column("Dettaglio", style="white")

    for r in results:
        fic_status = f"id={r.fic_id}" if r.fic_id else r.fic_status
        detail = r.fic_error or r.factum_error or r.document_type.value

        status_icon = {
            DocumentStatus.RECORDED: "✅",
            DocumentStatus.SKIPPED: "⏭",
            DocumentStatus.FAILED: "❌",
            DocumentStatus.PARSED: "📄",
            DocumentStatus.PENDING: "⏳",
        }.get(r.status, "❓")

        table.add_row(
            r.file.filename,
            r.factum_status,
            fic_status,
            f"{status_icon} {r.status.value}",
            detail,
        )

    console.print(table)
