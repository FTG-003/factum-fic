"""Entrypoint CLI Typer per factum-fic."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import typer

from factum_fic import __version__
from factum_fic.cli.ui import console, print_error, print_info, print_result_table
from factum_fic.config import load_settings, load_yaml_config
from factum_fic.core.pipeline import ensure_dirs

app = typer.Typer(
    name="factum-fic",
    help="Registrazione automatica fatture su Fatture in Cloud via Factum Parse API",
    no_args_is_help=True,
)


def _version_cb(value: bool) -> None:
    if value:
        console.print(f"factum-fic v{__version__}")
        raise typer.Exit


@app.callback()
def _main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Log verbose"),
    version: bool = typer.Option(False, "--version", callback=_version_cb, help="Mostra versione"),
) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s  %(message)s")
    # Assicura che le directory inbox/processed/failed esistano
    settings = load_settings()
    ensure_dirs(settings)


@app.command()
def process(
    path: str = typer.Argument(..., help="Percorso del file fattura (PDF/XML)"),
    config_file: str = typer.Option("", "--config", "-c", help="Percorso YAML categorie"),
) -> None:
    """Processa un singolo file fattura e lo registra su FIC."""
    from factum_fic.core.factum_client import FactumClient
    from factum_fic.core.fic_client import FICClient
    from factum_fic.core.mapper import Mapper
    from factum_fic.core.pipeline import process_file
    from factum_fic.storage.queue import QueueStore

    settings = load_settings()
    yaml_cfg = load_yaml_config(Path(config_file) if config_file else settings.config_file)
    mapper = Mapper(yaml_cfg)
    queue = QueueStore()

    ensure_dirs(settings)

    async def _run() -> None:
        factum = FactumClient(settings)
        fic = FICClient(settings)
        try:
            result = await process_file(
                Path(path),
                factum=factum,
                fic=fic,
                mapper=mapper,
                queue=queue,
                settings=settings,
            )
            print_result_table([result])
        finally:
            await factum.close()
            await fic.close()
            queue.close()

    asyncio.run(_run())


@app.command()
def process_inbox(
    config_file: str = typer.Option("", "--config", "-c", help="Percorso YAML categorie"),
) -> None:
    """Processa in sequenza tutti i file presenti in inbox/."""
    from factum_fic.core.factum_client import FactumClient
    from factum_fic.core.fic_client import FICClient
    from factum_fic.core.mapper import Mapper
    from factum_fic.core.pipeline import process_file
    from factum_fic.storage.queue import QueueStore

    settings = load_settings()
    yaml_cfg = load_yaml_config(Path(config_file) if config_file else settings.config_file)
    mapper = Mapper(yaml_cfg)
    queue = QueueStore()
    ensure_dirs(settings)

    inbox = Path(settings.inbox_dir).expanduser().resolve()
    files = sorted(
        p for p in inbox.iterdir()
        if p.is_file()
        and p.suffix.lower() in {".pdf", ".xml", ".txt", ".csv", ".png", ".jpg", ".jpeg"}
        and p.suffix.lower() not in {".tmp", ".crdownload"}
    )

    if not files:
        print_info(f"📂 Nessun file da processare in {inbox}")
        return

    print_info(f"📂 Trovati {len(files)} file in {inbox}")

    async def _run() -> None:
        factum = FactumClient(settings)
        fic = FICClient(settings)
        results: list[PipelineResult] = []  # type: ignore[annotation-unchecked]

        try:
            for path in files:
                print_info(f"📄 Elaborazione: {path.name}")
                try:
                    result = await process_file(
                        path,
                        factum=factum,
                        fic=fic,
                        mapper=mapper,
                        queue=queue,
                        settings=settings,
                    )
                    results.append(result)
                except Exception as e:
                    print_error(f"❌ Errore durante elaborazione {path.name}: {e}")
            if results:
                print_result_table(results)
        finally:
            await factum.close()
            await fic.close()
            queue.close()

    asyncio.run(_run())


@app.command()
def watch(
    directory: str = typer.Argument(
        default="",
        help="Directory da monitorare (default: INBOX_DIR da .env)",
    ),
    config_file: str = typer.Option("", "--config", "-c", help="Percorso YAML categorie"),
) -> None:
    """Avvia il watcher su una directory per elaborazione automatica."""
    from factum_fic.core.factum_client import FactumClient
    from factum_fic.core.fic_client import FICClient
    from factum_fic.core.mapper import Mapper
    from factum_fic.core.pipeline import process_file
    from factum_fic.storage.queue import QueueStore
    from factum_fic.watcher.daemon import WatcherDaemon

    settings = load_settings()
    # Se non è specificata una directory, usa inbox_dir
    if directory:
        settings.watch_dir = directory
    else:
        settings.watch_dir = settings.inbox_dir
    yaml_cfg = load_yaml_config(Path(config_file) if config_file else settings.config_file)
    mapper = Mapper(yaml_cfg)
    queue = QueueStore()
    factum = FactumClient(settings)
    fic = FICClient(settings)

    ensure_dirs(settings)

    async def _on_file(path: Path) -> None:
        print_info(f"📂 Elaborazione in corso: {path.name}")
        try:
            result = await process_file(
                path,
                factum=factum,
                fic=fic,
                mapper=mapper,
                queue=queue,
                settings=settings,
            )
            print_result_table([result])
        except Exception as e:
            print_error(f"❌ Errore: {e}")

    daemon = WatcherDaemon(settings, lambda p: asyncio.run(_on_file(p)))

    try:
        print_info(f"📂 Monitoraggio avviato su: {daemon.watch_dir}")
        print_info("Premi Ctrl+C per arrestare.")
        daemon.start()
        while True:
            import time  # noqa: PLC0415

            time.sleep(1)
    except KeyboardInterrupt:
        print_info("Arresto watcher...")
    finally:
        daemon.stop()
        asyncio.run(factum.close())
        asyncio.run(fic.close())
        queue.close()


@app.command()
def queue(
    action: str = typer.Argument("status", help="status | retry"),
) -> None:
    """Gestisce la coda di elaborazione."""
    from factum_fic.storage.queue import QueueStore

    q = QueueStore()
    if action == "status":
        stats = q.stats()
        console.print("[bold]Coda elaborazione:[/bold]")
        for status, count in sorted(stats.items()):
            console.print(f"  {status}: {count}")
        total = sum(stats.values())
        console.print(f"  [bold]Totale: {total}[/bold]")
    elif action == "retry":
        pending = q.pending()
        if not pending:
            console.print("[green]Nessun item da riprocessare.[/green]")
        else:
            console.print(f"[yellow]{len(pending)} item da riprocessare.[/yellow]")
            for sha, path in pending:
                console.print(f"  {path} ({sha[:12]}...)")
    else:
        print_error(f"Azione sconosciuta: {action}")
    q.close()


@app.command()
def check() -> None:
    """Verifica connettività con Factum e FIC."""
    from factum_fic.core.factum_client import FactumClient
    from factum_fic.core.fic_client import FICClient

    settings = load_settings()

    async def _check() -> None:
        factum = FactumClient(settings)
        fic = FICClient(settings)
        try:
            factum_ok = await factum.health()
            fic_ok = await fic.health()
            console.print("[bold]Verifica connettività:[/bold]")
            console.print(
                f"  Factum Parse API: {'✅' if factum_ok else '❌'} {settings.factum_api_url}"
            )
            console.print(
                f"  Fatture in Cloud:  {'✅' if fic_ok else '❌'} {settings.fic_base_url}"
            )
        finally:
            await factum.close()
            await fic.close()

    asyncio.run(_check())
