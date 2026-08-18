"""Entrypoint CLI Typer per factum-fic con alias italiani.

Tutti i comandi hanno un alias italiano per utenti non tecnici.
Gli alias sono risolti automaticamente via ``_AliasedGroup``.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import typer
from typer.core import TyperGroup

from factum_fic import __version__
from factum_fic.cli.ui import console, print_error, print_info, print_ok, print_result_table
from factum_fic.config import load_settings, load_yaml_config
from factum_fic.core.pipeline import ensure_dirs

# ── Alias italiano → inglese ──────────────────────────────────────────────────

_ALIASES: dict[str, str] = {
    "configura": "setup",
    "stato": "status",
    "storico": "history",
    "elabora": "sync",
    "auto": "watch",
    "riprova-autofatture": "riprova-autofatture",
    "ricarica": "ricarica",
    "buy-credits": "ricarica",
}


class _AliasedGroup(TyperGroup):
    """Click/Typer Group che risolve gli alias italiani automaticamente."""

    def get_command(self, ctx: Any, cmd_name: str) -> Any:
        cmd = super().get_command(ctx, cmd_name)
        if cmd is not None:
            return cmd
        if cmd_name in _ALIASES:
            return super().get_command(ctx, _ALIASES[cmd_name])
        return None


# ── App ───────────────────────────────────────────────────────────────────────

app = typer.Typer(
    name="factum-fic",
    cls=_AliasedGroup,
    help="Registrazione automatica fatture su Fatture in Cloud via Factum Parse API",
    no_args_is_help=True,
    epilog=(
        "Alias italiani: configura=setup, stato=status, storico=history, "
        "elabora=sync, auto=watch, riprova-autofatture=riprova-autofatture"
    ),
)


def _version_cb(value: bool) -> None:
    if value:
        console.print(f"factum-fic v{__version__}")
        raise typer.Exit


@app.command()
def riprova_autofatture(
    config_file: str = typer.Option("", "--config", "-c", help="Percorso YAML categorie"),
) -> None:
    """Riprova la generazione delle autofatture SDI per le spese in stato
    SELF_INVOICE_PENDING.

    Il comando recupera dalla coda locale tutti i file la cui spesa è stata
    creata con successo su FIC ma la cui autofattura SDI è fallita, e tenta
    nuovamente la generazione.

    Alias: riprova-autofatture
    """
    from factum_fic.cli.verify import verify_and_bind
    from factum_fic.core.factum_client import FactumClient
    from factum_fic.core.fic_client import FICClient
    from factum_fic.core.mapper import Mapper
    from factum_fic.storage.queue import QueueStore

    settings = load_settings()
    yaml_cfg = load_yaml_config(Path(config_file) if config_file else settings.config_file)
    mapper = Mapper(yaml_cfg)
    queue = QueueStore()

    pending = queue.get_pending_self_invoices()
    if not pending:
        print_ok("Nessuna autofattura in attesa di riprova.")
        return

    print_info(f"Trovate {len(pending)} autofatture da riprovare...")

    async def _run() -> None:
        factum = FactumClient(settings)
        fic = FICClient(settings)
        try:
            await verify_and_bind(fic, factum)
            riuscite = 0
            fallite = 0

            for item in pending:
                sha = item["sha256"]
                expense_id = item["fic_expense_id"]
                print_info(f"  Riprovo autofattura per spesa id={expense_id} (SHA={sha[:12]}...)")

                # Recupera la spesa da FIC per ricostruire i dati
                try:
                    expense_data = await fic.get_expense(expense_id)
                except Exception as exc:
                    print_error(f"    Impossibile recuperare spesa {expense_id}: {exc}")
                    fallite += 1
                    continue

                if not expense_data:
                    print_error(f"    Spesa {expense_id} non trovata su FIC")
                    fallite += 1
                    continue

                # Costruisce la request per l'autofattura usando i dati
                # della spesa già registrata
                try:
                    supplier_info = expense_data.get("data", {}).get("entity", {})
                    supplier_name = (supplier_info or {}).get("name", "") or ""
                    supplier_vat = (supplier_info or {}).get("vat_number", "") or None
                    supplier_country = (supplier_info or {}).get("country_iso", "XX") or "XX"

                    amount_net = float(expense_data.get("data", {}).get("amount_net", 0) or 0)
                    amount_vat = float(expense_data.get("data", {}).get("amount_vat", 0) or 0)
                    amount_gross = float(expense_data.get("data", {}).get("amount_gross", 0) or 0)
                    date = expense_data.get("data", {}).get("date", "") or ""
                    description = expense_data.get("data", {}).get("description", "") or ""
                    notes = expense_data.get("data", {}).get("notes", "") or ""
                    currency = expense_data.get("data", {}).get("currency", "EUR") or "EUR"

                    if amount_net <= 0 and amount_gross > 0:
                        amount_net = amount_gross
                        amount_vat = 0.0

                    # Determina tipologia SDI
                    self_invoice_type = mapper.classify_self_invoice_type(
                        supplier_country_iso=supplier_country,
                        supplier_vat_number=supplier_vat,
                    )

                    self_invoice_request = mapper.build_self_invoice_request(
                        supplier_name=supplier_name,
                        supplier_vat_number=supplier_vat or "",
                        supplier_country_iso=supplier_country,
                        amount_net=amount_net,
                        amount_vat=amount_vat,
                        amount_gross=amount_gross,
                        date=date,
                        description=description,
                        notes=notes,
                        currency=currency,
                        self_invoice_type=self_invoice_type,
                        original_document_id=expense_id,
                        original_document_description=description,
                    )

                    response = await fic.create_issued_document(
                        self_invoice_request,
                    )
                    fic_self_invoice_id = response.id

                    # Aggiorna la coda
                    queue.complete(
                        sha,
                        expense_id=expense_id,
                        self_invoice_id=fic_self_invoice_id,
                    )
                    print_ok(f"    Autofattura creata: id={fic_self_invoice_id}")
                    riuscite += 1

                except Exception as exc:
                    print_error(
                        f"    Autofattura ancora fallita per spesa {expense_id}: {exc}"
                    )
                    fallite += 1

            print_info(f"Riprova completata: {riuscite} riuscite, {fallite} fallite")

        finally:
            await factum.close()
            await fic.close()
            queue.close()

    asyncio.run(_run())


@app.callback()
def _main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Log verbose"),
    version: bool = typer.Option(False, "--version", callback=_version_cb, help="Mostra versione"),
) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s  %(message)s")


# ── Setup wizard ──────────────────────────────────────────────────────────────


@app.command()
def setup() -> None:
    """Configurazione guidata interattiva (credenziali, conto, .env)."""
    from factum_fic.cli.commands.setup import setup as _setup

    _setup()


# ── Process singolo file ──────────────────────────────────────────────────────


@app.command()
def process(
    path: str = typer.Argument(..., help="Percorso del file fattura (PDF/XML)"),
    config_file: str = typer.Option("", "--config", "-c", help="Percorso YAML categorie"),
) -> None:
    """Processa un singolo file fattura e lo registra su FIC."""
    from factum_fic.cli.verify import ForfettarioCheckError, verify_and_bind
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
            try:
                await verify_and_bind(fic, factum)
            except ForfettarioCheckError as e:
                print_error(str(e))
                raise typer.Exit(code=1) from None

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


# ── Sync: inbox o singolo file ────────────────────────────────────────────────


@app.command()
def sync(
    path: str = typer.Argument(None, help="Percorso del file (opzionale; se omesso elabora inbox/)"),
    config_file: str = typer.Option("", "--config", "-c", help="Percorso YAML categorie"),
    force: bool = typer.Option(False, "--force", "-f", help="Ignora deduplicazione e riprocessa"),
) -> None:
    """Elabora file fattura: singolo PDF o tutti i file in inbox/."""
    if path:
        # Delega a process()
        from factum_fic.cli.verify import ForfettarioCheckError, verify_and_bind
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

        async def _run_single() -> None:
            factum = FactumClient(settings)
            fic = FICClient(settings)
            try:
                try:
                    await verify_and_bind(fic, factum)
                except ForfettarioCheckError as e:
                    print_error(str(e))
                    raise typer.Exit(code=1) from None
                result = await process_file(
                    Path(path),
                    factum=factum,
                    fic=fic,
                    mapper=mapper,
                    queue=queue,
                    settings=settings,
                    force=force,
                )
                print_result_table([result])
            finally:
                await factum.close()
                await fic.close()
                queue.close()

        asyncio.run(_run_single())
    else:
        # Elabora inbox (delega a process_inbox)
        from factum_fic.core.factum_client import FactumClient
        from factum_fic.core.fic_client import FICClient
        from factum_fic.core.mapper import Mapper
        from factum_fic.core.models import PipelineResult
        from factum_fic.core.pipeline import is_temp_file, process_file
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

        async def _run_inbox() -> None:
            factum = FactumClient(settings)
            fic = FICClient(settings)
            results: list[PipelineResult] = []

            from factum_fic.cli.verify import ForfettarioCheckError, verify_and_bind
            try:
                await verify_and_bind(fic, factum)
            except ForfettarioCheckError as e:
                print_error(str(e))
                raise typer.Exit(code=1) from None

            try:
                for p in files:
                    print_info(f"📄 Elaborazione: {p.name}")
                    try:
                        result = await process_file(
                            p,
                            factum=factum,
                            fic=fic,
                            mapper=mapper,
                            queue=queue,
                            settings=settings,
                            force=force,
                        )
                        results.append(result)
                        # AUTH_ERROR: interrompe immediatamente tutti i PDF
                        # successivi — inutile martellare l'API con chiave
                        # non valida.
                        if result.factum_status == "auth_error":
                            print_error(
                                "🔴 Chiave API Factum non valida o revocata. "
                                "Elaborazione PDF sospesa."
                            )
                            break
                    except Exception as e:
                        print_error(f"❌ Errore durante elaborazione {p.name}: {e}")
                if results:
                    print_result_table(results)
            finally:
                await factum.close()
                await fic.close()
                queue.close()

        asyncio.run(_run_inbox())


# ── Processa inbox ────────────────────────────────────────────────────────────


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
    from factum_fic.core.pipeline import is_temp_file, process_file
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

        from factum_fic.cli.verify import ForfettarioCheckError, verify_and_bind
        try:
            await verify_and_bind(fic, factum)
        except ForfettarioCheckError as e:
            print_error(str(e))
            raise typer.Exit(code=1) from None

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
                    # AUTH_ERROR: interrompe immediatamente tutti i PDF
                    # successivi — inutile martellare l'API con chiave
                    # non valida.
                    if result.factum_status == "auth_error":
                        print_error(
                            "🔴 Chiave API Factum non valida o revocata. "
                            "Elaborazione PDF sospesa."
                        )
                        break
                except Exception as e:
                    print_error(f"❌ Errore durante elaborazione {path.name}: {e}")
            if results:
                print_result_table(results)
        finally:
            await factum.close()
            await fic.close()
            queue.close()

    asyncio.run(_run())


# ── Watch (monitoraggio automatico) ───────────────────────────────────────────


@app.command()
def watch(
    directory: str = typer.Argument(
        default="",
        help="Directory da monitorare (default: INBOX_DIR da .env)",
    ),
    config_file: str = typer.Option("", "--config", "-c", help="Percorso YAML categorie"),
) -> None:
    """Avvia il watcher su una directory per elaborazione automatica."""
    from factum_fic.cli.verify import ForfettarioCheckError, verify_and_bind
    from factum_fic.core.factum_client import FactumClient
    from factum_fic.core.fic_client import FICClient
    from factum_fic.core.mapper import Mapper
    from factum_fic.core.pipeline import process_file
    from factum_fic.storage.queue import QueueStore
    from factum_fic.watcher.daemon import WatcherDaemon

    settings = load_settings()
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

    try:
        asyncio.run(verify_and_bind(fic, factum))
    except ForfettarioCheckError as e:
        print_error(str(e))
        raise typer.Exit(code=1) from None

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


# ── Queue management ──────────────────────────────────────────────────────────


@app.command()
def queue(  # noqa: PLR0915
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


# ── Check connettività ────────────────────────────────────────────────────────


@app.command()
def check() -> None:
    """Verifica connettività con Factum e FIC e regime fiscale."""
    from factum_fic.cli.verify import ForfettarioCheckError, verify_and_bind
    from factum_fic.core.factum_client import FactumClient
    from factum_fic.core.fic_client import FICClient

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
                    raise typer.Exit(code=1) from None
        finally:
            await factum.close()
            await fic.close()

    console.print("[bold]Verifica connettività:[/bold]")
    asyncio.run(_check())


# ── Verify regime fiscale ─────────────────────────────────────────────────────


@app.command()
def verify() -> None:
    """Verifica il regime fiscale dell'azienda su Fatture in Cloud."""
    from factum_fic.cli.verify import ForfettarioCheckError, verify_and_bind
    from factum_fic.core.factum_client import FactumClient
    from factum_fic.core.fic_client import FICClient

    settings = load_settings()

    async def _verify() -> None:
        factum = FactumClient(settings)
        fic = FICClient(settings)
        try:
            info = await verify_and_bind(fic, factum)
            print_ok(f"Regime fiscale verificato: {info['tax_regime']}")
            print_ok(f"Azienda: {info['name']} (P.IVA {info['vat_number']})")
            print_ok("Binding Factum completato: header X-FIC-VAT trasmesso")
        except ForfettarioCheckError as e:
            print_error(str(e))
            raise typer.Exit(code=1) from None
        finally:
            await factum.close()
            await fic.close()

    asyncio.run(_verify())


# ── Dashboard status ──────────────────────────────────────────────────────────


def _tax_regime_label(regime: str) -> str:
    """Traduce il codice regime FIC in etichetta leggibile."""
    r = (regime or "").strip().lower()
    labels = {
        "forfettario": "Regime Forfettario",
        "rf19": "Regime Forfettario (RF19)",
        "ordinario": "Regime Ordinario",
    }
    return labels.get(r, regime or "—")


async def collect_status(factum: Any, fic: Any, queue: Any) -> dict:
    """Raccoglie i dati operativi per il dashboard ``factum-fic status``.

    Separata dal rendering per essere testabile con client mockati.

    Returns:
        Dizionario con sezioni: factum, fic, company, payment_account,
        self_invoice, queue.
    """
    try:
        factum_ok = await factum.health()
    except Exception:
        factum_ok = False

    try:
        fic_ok = await fic.health()
    except Exception:
        fic_ok = False

    company: dict[str, Any] = {}
    if fic_ok:
        try:
            company = await fic.get_company_info()
        except Exception:
            company = {}

    payment_account: dict[str, Any] | None = None
    if fic_ok:
        try:
            payment_account = await fic.resolve_payment_account()
        except Exception:
            payment_account = None

    try:
        queue_summary = queue.summary()
    except Exception:
        queue_summary = {}

    from factum_fic.config import load_settings

    settings = load_settings()

    return {
        "factum": {"ok": factum_ok, "url": getattr(factum, "_api_url", "")},
        "fic": {"ok": fic_ok, "url": getattr(fic, "_base_url", "")},
        "company": company,
        "payment_account": payment_account,
        "factum_api_key": bool(settings.factum_api_key),
        "self_invoice": {
            "enabled": settings.fic_generate_self_invoice,
            "numeration": settings.fic_self_invoice_numeration,
            "vat_value": settings.fic_self_invoice_vat_value,
        },
        "queue": queue_summary,
    }


def _factum_key_status(data: dict):
    """Tabella status FACTUM_API_KEY per il dashboard."""
    from rich.table import Table

    table = Table(box=None, show_header=False, pad_edge=False)
    table.add_column(style="bold", width=24)
    table.add_column()
    has_key = data.get("factum_api_key", False)
    if has_key:
        factum_ok = data.get("factum", {}).get("ok", False)
        if factum_ok:
            table.add_row("Stato", "✅ Attiva (Free Tier — 10 conversioni/mese)")
        else:
            table.add_row("Stato", "⚠️  Configurata ma API non raggiungibile")
    else:
        table.add_row("Stato", "❌ Non configurata (esegui `factum-fic setup`)")
    return table


def render_status(data: dict) -> None:
    """Rende il dashboard di stato con Rich."""
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table

    factum_ok = data["factum"]["ok"]
    fic_ok = data["fic"]["ok"]
    company = data["company"]
    payment_account = data["payment_account"]
    si = data["self_invoice"]
    queue_summary = data["queue"]

    conn_table = Table(box=None, show_header=False, pad_edge=False)
    conn_table.add_column(style="bold", width=24)
    conn_table.add_column()
    conn_table.add_row(
        "Factum Parse API",
        f"{'✅ OK' if factum_ok else '❌ NON RAGGIUNGIBILE'}  {data['factum']['url']}",
    )
    conn_table.add_row(
        "Fatture in Cloud",
        f"{'✅ OK' if fic_ok else '❌ NON RAGGIUNGIBILE'}  {data['fic']['url']}",
    )

    company_table = Table(box=None, show_header=False, pad_edge=False)
    company_table.add_column(style="bold", width=24)
    company_table.add_column()
    if fic_ok and company:
        company_table.add_row("Ragione Sociale", company.get("name") or "—")
        company_table.add_row("Partita IVA", company.get("vat_number") or "—")
        company_table.add_row("Codice Fiscale", company.get("fiscal_code") or "—")
        regime = _tax_regime_label(company.get("tax_regime") or "")
        is_forf = (company.get("tax_regime") or "").strip().lower() in {"forfettario", "rf19"}
        company_table.add_row("Regime Fiscale", f"{regime} {'✅' if is_forf else '⚠️ non forfettario'}")
    else:
        company_table.add_row("Azienda", "Non raggiungibile (verifica credenziali FIC)")

    pay_table = Table(box=None, show_header=False, pad_edge=False)
    pay_table.add_column(style="bold", width=24)
    pay_table.add_column()
    if payment_account:
        pay_table.add_row("Conto attivo", f"{payment_account.get('name')} (id={payment_account.get('id')})")
        pay_table.add_row("Auto-pagamento", "✅ abilitato — spese marcate come saldate")
    else:
        pay_table.add_row("Conto attivo", "Nessun conto disponibile")
        pay_table.add_row("Auto-pagamento", "⚠️ nessun conto configurato")

    si_table = Table(box=None, show_header=False, pad_edge=False)
    si_table.add_column(style="bold", width=24)
    si_table.add_column()
    si_table.add_row("Generazione SDI", f"{'✅ attiva' if si['enabled'] else '❌ disattivata'}")
    si_table.add_row("Sezionale numerazione", si["numeration"])
    si_table.add_row("Aliquota IVA (debito F24)", f"{si['vat_value']}% — art. 17 c. 2 DPR 633/72")

    q_table = Table(box=None, show_header=False, pad_edge=False)
    q_table.add_column(style="bold", width=24)
    q_table.add_column()
    q_table.add_row("Fatture elaborate", str(queue_summary.get("processed", 0)))
    q_table.add_row("Spese registrate (FIC)", str(queue_summary.get("expenses", 0)))
    q_table.add_row("Autofatture SDI generate", str(queue_summary.get("self_invoices", 0)))
    q_table.add_row("Errori in coda", str(queue_summary.get("errors", 0)))
    q_table.add_row("In attesa", str(queue_summary.get("queued", 0)))

    content = Group(
        "[bold cyan]Connessioni[/]",
        conn_table,
        "",
        "[bold cyan]Azienda FIC[/]",
        company_table,
        "",
        "[bold cyan]Factum Parse API Key[/]",
        _factum_key_status(data),
        "",
        "[bold cyan]Conto di pagamento (auto-pagamento spese)[/]",
        pay_table,
        "",
    "[bold cyan]Autofatture SDI (TD17/TD18/TD19)[/]",
    si_table,
    "",
    "[bold cyan]Coda locale SQLite[/]",
    q_table,
    )
    panel = Panel(content, title="📊 factum-fic status", border_style="cyan")
    console.print(panel)


@app.command()
def status() -> None:
    """Mostra il dashboard operativo: salute API, azienda, conto, autofatture, coda."""
    from factum_fic.core.factum_client import FactumClient
    from factum_fic.core.fic_client import FICClient
    from factum_fic.storage.queue import QueueStore

    settings = load_settings()
    factum = FactumClient(settings)
    fic = FICClient(settings)
    queue = QueueStore()

    async def _run() -> None:
        try:
            data = await collect_status(factum, fic, queue)
            render_status(data)
        finally:
            await factum.close()
            await fic.close()
            queue.close()

    asyncio.run(_run())


# ── History ───────────────────────────────────────────────────────────────────


@app.command()
def history() -> None:
    """Mostra la cronologia delle ultime elaborazioni (10 record)."""
    from factum_fic.cli.commands.history import history as _history

    _history()


@app.command()
def ricarica() -> None:
    """Genera link per ricaricare crediti PDF Factum Parse.

    Apre il browser al checkout sicuro di Factum Parse, senza
    esporre segreti nei parametri URL.

    Alias: ``factum-fic buy-credits``
    """
    import webbrowser

    from factum_fic.config import Settings
    from factum_fic.core.fic_client import FICClient

    settings = Settings()

    if not settings.fic_token or not settings.fic_company_id:
        print_error("❌ Configurazione FIC mancante. Esegui prima: factum-fic setup")
        raise typer.Exit(code=1)

    async def _ricarica() -> None:
        from rich.panel import Panel
        from rich.prompt import Confirm

        fic = FICClient(settings)
        try:
            info = await fic.get_company_info()
            piva = (info.get("vat_number") or "").strip()
            if not piva:
                print_error("❌ Impossibile recuperare la P.IVA dal profilo FIC.")
                raise typer.Exit(code=1)

            url = f"https://checkout.factum.pyragogy.org/buy/100-pdf?checkout[custom][piva]={piva}"

            # Verifica che nessun segreto sia finito nell'URL
            if "sk_live_" in url or "api_key" in url.lower():
                print_error("❌ ERRORE DI SICUREZZA: l'URL contiene credenziali!")
                raise typer.Exit(code=1)

            console.print()
            console.print(
                Panel.fit(
                    f"[bold cyan]📄 Ricarica crediti Factum Parse[/]\n\n"
                    f"  P.IVA: [green]{piva}[/]\n"
                    f"\n"
                    f"  Apri il link nel browser per acquistare "
                    f"[bold]100 conversioni PDF[/] aggiuntive:\n"
                    f"  [blue underline]{url}[/]\n",
                    border_style="cyan",
                )
            )

            if Confirm.ask("  Aprire il link nel browser?", default=True):
                webbrowser.open(url)
                print_ok("Browser aperto.")
        finally:
            await fic.close()

    asyncio.run(_ricarica())
