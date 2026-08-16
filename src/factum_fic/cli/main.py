"""Entrypoint CLI Typer per factum-fic."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import typer

from factum_fic import __version__
from factum_fic.cli.ui import console, print_error, print_info, print_result_table, print_warning
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
    # Assicura che le directory da_elaborare/elaborate/errori esistano
    settings = load_settings()
    ensure_dirs(settings)


@app.command()
def process(
    path: str = typer.Argument(..., help="Percorso del file fattura (PDF/XML)"),
    config_file: str = typer.Option("", "--config", "-c", help="Percorso YAML categorie"),
) -> None:
    """Processa un singolo file fattura e lo registra su FIC."""
    from factum_fic.cli.verify import verify_and_bind, ForfettarioCheckError
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
            # Verifica regime fiscale e aggancia metadati Factum
            try:
                await verify_and_bind(fic, factum)
            except ForfettarioCheckError as e:
                print_error(str(e))
                raise typer.Exit(code=1)

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
    force: bool = typer.Option(False, "--force", "-f", help="Ignora deduplicazione e riprocessa"),
) -> None:
    """Processa in sequenza tutti i file presenti in inbox/."""
    from factum_fic.core.factum_client import FactumClient
    from factum_fic.core.fic_client import FICClient
    from factum_fic.core.mapper import Mapper
    from factum_fic.core.models import PipelineResult
    from factum_fic.core.pipeline import process_file, is_temp_file
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
        and not is_temp_file(p)
        and p.suffix.lower() in {'.pdf', '.xml', '.txt', '.csv', '.png', '.jpg', '.jpeg'}
    )

    if not files:
        print_info(f"📂 Nessun file da processare in {inbox}")
        return

    if force:
        print_info(f"⚡ Modalità force: deduplicazione disabilitata per {len(files)} file")
    else:
        print_info(f"📂 Trovati {len(files)} file in {inbox}")

    async def _run() -> None:
        factum = FactumClient(settings)
        fic = FICClient(settings)
        results: list[PipelineResult] = []

        # Verifica regime fiscale una tantum per tutti i file
        from factum_fic.cli.verify import verify_and_bind, ForfettarioCheckError
        try:
            await verify_and_bind(fic, factum)
        except ForfettarioCheckError as e:
            print_error(str(e))
            raise typer.Exit(code=1)

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
                        force=force,
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
    from factum_fic.cli.verify import verify_and_bind, ForfettarioCheckError
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

    # Verifica regime fiscale prima di avviare il watcher
    try:
        asyncio.run(verify_and_bind(fic, factum))
    except ForfettarioCheckError as e:
        print_error(str(e))
        raise typer.Exit(code=1)

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
    """Verifica connettività con Factum e FIC e regime fiscale."""
    from factum_fic.core.factum_client import FactumClient
    from factum_fic.core.fic_client import FICClient
    from factum_fic.cli.verify import verify_and_bind, ForfettarioCheckError

    settings = load_settings()

    async def _check() -> None:
        factum = FactumClient(settings)
        fic = FICClient(settings)
        try:
            factum_ok = await factum.health()
            console.print(f"  Factum Parse API: {'✅' if factum_ok else '❌'} {settings.factum_api_url}")
            fic_ok = await fic.health()
            console.print(f"  Fatture in Cloud:  {'✅' if fic_ok else '❌'} {settings.fic_base_url}")
            if fic_ok:
                try:
                    info = await verify_and_bind(fic, factum)
                    console.print(f"  Azienda:          {info['name']}")
                    console.print(f"  P.IVA:            {info['vat_number']}")
                    console.print(f"  Regime fiscale:   {info['tax_regime']} ✅")
                except ForfettarioCheckError as e:
                    print_error(str(e))
                    raise typer.Exit(code=1)
        finally:
            await factum.close()
            await fic.close()

    console.print("[bold]Verifica connettività:[/bold]")
    asyncio.run(_check())


@app.command()
def verify() -> None:
    """Verifica il regime fiscale dell'azienda su Fatture in Cloud."""
    from factum_fic.core.factum_client import FactumClient
    from factum_fic.core.fic_client import FICClient
    from factum_fic.cli.verify import verify_and_bind, ForfettarioCheckError

    settings = load_settings()

    async def _verify() -> None:
        factum = FactumClient(settings)
        fic = FICClient(settings)
        try:
            info = await verify_and_bind(fic, factum)
            print_ok(f"Regime fiscale verificato: {info['tax_regime']}")
            print_ok(f"Azienda: {info['name']} (P.IVA {info['vat_number']})")
            print_ok(f"Binding Factum completato: header X-FIC-VAT trasmesso")
        except ForfettarioCheckError as e:
            print_error(str(e))
            raise typer.Exit(code=1)
        finally:
            await factum.close()
            await fic.close()

    asyncio.run(_verify())
