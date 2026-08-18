"""Wizard interattivo ``factum-fic setup`` per configurazione guidata.

Chiede all'utente le credenziali FIC, attiva la chiave Factum gratuita
via claim server-side (verifica token FIC + P.IVA), chiede il conto
per il saldo automatico, e scrive il file ``.env`` in modo atomico.

Alias: ``factum-fic configura``
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import httpx
import typer
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt

from factum_fic.cli.ui import console, print_error, print_info, print_ok, print_warning
from factum_fic.config import Settings
from factum_fic.core.factum_client import FactumClient
from factum_fic.core.fic_client import FICClient

# ── Permessi minimi richiesti per il token FIC ───────────────────────────────

_FIC_TOKEN_PERMISSIONS = Panel(
    "[bold yellow]Permessi necessari per il Token FIC (Personal Access Token)[/]\n"
    "\n"
    "  1. [cyan]Acquisti / Spese[/] → [green]Lettura e Scrittura[/]  (obbligatorio)\n"
    "  2. [cyan]Documenti Emessi[/] → [green]Lettura e Scrittura[/]  (obbligatorio per autofatture TD17/18/19)\n"
    "  3. [cyan]Impostazioni / Azienda[/] → [yellow]Sola Lettura[/]  (per rilevare P.IVA e regime fiscale)\n"
    "\n"
    "[dim]Vai su: fattureincloud.it › Impostazioni › Sicurezza › API Key[/]",
    title="🔑 Token FIC (Personal Access Token)",
    border_style="yellow",
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _sanitize(value: str) -> str:
    """Rimuove spazi, newline e prefissi tipo 'FIC_COMPANY_ID=' o 'FIC_TOKEN='."""
    value = value.strip()
    # Rimuovi prefissi tipo CHIAVE=valore
    value = re.sub(
        r'^(?:FIC_TOKEN|FIC_API_KEY|FIC_COMPANY_ID|FACTUM_API_KEY|FIC_ACCESS_TOKEN)\s*=\s*',
        '', value, flags=re.IGNORECASE,
    )
    return value.strip()


def _load_env(path: Path) -> dict[str, str]:
    """Legge un file .env e restituisce un dict chiave → valore."""
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, val = stripped.partition("=")
        env[key.strip()] = val.strip()
    return env


def _write_env(path: Path, updates: dict[str, str]) -> None:
    """Aggiorna il file .env in modo conservativo: modifica i valori esistenti,
    aggiunge i nuovi, preserva commenti e blank line."""
    lines: list[str] = []
    if path.exists():
        lines = path.read_text().splitlines()

    seen: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
        else:
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                if updates[key]:
                    new_lines.append(f"{key}={updates[key]}")
                seen.add(key)
            else:
                new_lines.append(line)

    for key, val in updates.items():
        if val and key not in seen:
            new_lines.append(f"{key}={val}")

    path.write_text("\n".join(new_lines) + "\n")


def _make_dirs(workspace: Path) -> None:
    """Crea le sottocartelle di lavoro."""
    for sub in ("da_elaborare", "archiviate", "da_verificare"):
        (workspace / sub).mkdir(parents=True, exist_ok=True)


# ── Wizard ────────────────────────────────────────────────────────────────────


async def _run_setup(settings: Settings) -> None:
    """Esegue il wizard interattivo di configurazione."""
    env_current = _load_env(Path(".env"))

    console.print()
    console.print(Panel("[bold cyan]⚙️  Configurazione guidata factum-fic[/]", border_style="cyan"))
    console.print()

    # ── Step 0 — Workspace ─────────────────────────────────────────────
    console.print("[bold]Passo 0/4  —  Cartella di lavoro[/]")
    console.print("  [dim]Dove vuoi salvare le fatture da elaborare e quelle archiviate?[/]")

    default_workspace = str(Path.home() / "factum-workspace")
    workspace_raw = Prompt.ask(
        "  Cartella di lavoro",
        default=env_current.get("FACTUM_WORKSPACE_DIR", default_workspace),
    )
    workspace = Path(workspace_raw).expanduser().resolve()
    _make_dirs(workspace)
    print_ok(f"Cartella di lavoro: [bold]{workspace}[/]")
    print_ok("  ├── 📥 da_elaborare/   (trascina qui i tuoi file PDF / XML)")
    print_ok("  ├── 📦 archiviate/     (file registrati con successo)")
    print_ok("  └── ⚠️  da_verificare/  (file illeggibili o con anomalie)")
    console.print()

    inbox_dir = str(workspace / "da_elaborare")
    base_storage_dir = str(workspace)
    workspace_dir = str(workspace)

    # ── Step 1 — FIC credentials ────────────────────────────────────────
    console.print("[bold]Passo 1/4  —  Credenziali Fatture in Cloud[/]")
    console.print(_FIC_TOKEN_PERMISSIONS)
    console.print()

    fic_api_key_raw = Prompt.ask(
        "  Token FIC (Personal Access Token)",
        default=env_current.get("FIC_TOKEN", env_current.get("FIC_API_KEY", "")),
    )
    fic_token = _sanitize(fic_api_key_raw)

    # Auto-discovery aziende
    temp_fic = FICClient(Settings(fic_token=fic_token, FIC_COMPANY_ID="0"))
    companies: list[dict] = []
    try:
        companies = await temp_fic.get_user_companies()
    except Exception:
        pass
    finally:
        await temp_fic.close()

    fic_company_id = ""
    if len(companies) == 1:
        fic_company_id = str(companies[0].get("id", ""))
        print_ok(f"Azienda rilevata: [bold]{companies[0].get('name', '?')}[/] (ID {fic_company_id})")
    elif len(companies) > 1:
        console.print("  Aziende trovate sul tuo account FIC:")
        for i, c in enumerate(companies, 1):
            console.print(f"    {i}. {c.get('name', '?')}  (ID {c.get('id', '?')})")
        choice = IntPrompt.ask("  Scegli l'azienda", default=1)
        if 1 <= choice <= len(companies):
            fic_company_id = str(companies[choice - 1].get("id", ""))
    else:
        fic_company_id_raw = Prompt.ask(
            "  Codice cliente / ID Azienda",
            default=env_current.get("FIC_COMPANY_ID", ""),
        )
        fic_company_id = _sanitize(fic_company_id_raw)

    if not fic_company_id:
        print_error("ID Azienda non valido.")
        raise typer.Exit(1)

    console.print()

    # Test FIC — info azienda
    temp_fic2 = FICClient(Settings(fic_token=fic_token, FIC_COMPANY_ID=fic_company_id))
    try:
        info = await temp_fic2.get_company_info()
        regime = (info.get("tax_regime") or "").strip().lower()
        is_forf = regime in {"forfettario", "rf19"}
        regime_label = _tax_regime_label(regime)
        print_ok(f"Connesso ad azienda: [bold]{info.get('name', '?')}[/]")

        # Mostra P.IVA o CF
        vat = (info.get("vat_number") or "").strip()
        cf = (info.get("fiscal_code") or info.get("tax_code") or "").strip()
        if vat:
            print_ok(f"  P.IVA: {vat}")
        if cf:
            print_ok(f"  Codice Fiscale: {cf}")

        print_ok(f"  Regime: {regime_label} {'✅ Forfettario' if is_forf else '⚠️'}")
        if not is_forf:
            print_warning("factum-fic è ottimizzato per il Regime Forfettario (RF19).")
            if Prompt.ask("  Continuare lo stesso?", choices=["s", "n"], default="n") != "s":
                raise typer.Exit(0)
    except Exception as e:
        print_error(f"Connessione FIC fallita: {e}")
        raise typer.Exit(1) from None
    finally:
        await temp_fic2.close()

    # ── Step 2 — Auto-claim chiave Factum (automatico) ──────────────
    console.print()
    console.print("[bold]Passo 2/4  —  Attivazione chiave Factum Parse (gratuita)[/]")
    console.print(
        "  [dim]10 conversioni PDF/mese gratis, collegate alla tua P.IVA.[/]"
    )

    factum_api_key = ""
    factum_key_is_existing = False

    piva = (info.get("vat_number") or "").strip()
    cf = (info.get("fiscal_code") or "").strip()

    print_info(f"  Verifica server-side in corso per {piva or cf}…")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            claim_payload: dict[str, str | None] = {
                "fic_token": fic_token,
                "fic_company_id": fic_company_id,
                "company_id": fic_company_id,
            }
            if piva:
                claim_payload["vat_number"] = piva
            if cf:
                claim_payload["fiscal_code"] = cf
            resp = await client.post(
                f"{settings.factum_api_url}/api/v1/auth/claim",
                json=claim_payload,
            )
        if resp.status_code == 200:
            data = resp.json()
            factum_api_key = (data.get("api_key") or "").strip()
            is_existing = data.get("is_existing", False)
            factum_key_is_existing = is_existing
            credits = data.get("free_remaining", 10)

            if factum_api_key:
                if is_existing:
                    print_ok(
                        f"Chiave Factum Parse recuperata dal profilo: "
                        f"[bold]{factum_api_key[:12]}…[/] "
                        f"(Crediti: {credits}/10)"
                    )
                else:
                    print_ok(
                        f"Nuova chiave Factum Parse Free Tier attivata: "
                        f"[bold]{factum_api_key[:12]}…[/] "
                        f"({credits} conversioni PDF/mese)"
                    )
            else:
                print_warning(
                    "Claim completato ma chiave vuota. "
                    "Inseriscila manualmente."
                )
        elif resp.status_code == 401:
            print_error(
                "Token FIC non valido o permessi insufficienti. "
                "Verifica che il token FIC abbia permessi di lettura "
                "su 'Impostazioni / Azienda'."
            )
            raise typer.Exit(1)
        else:
            print_warning(
                f"Claim API ha risposto HTTP {resp.status_code}. "
                "Inserisci la chiave Factum manualmente."
            )
    except httpx.HTTPError as e:
        print_warning(f"Impossibile contattare il server Factum: {e}")
        print_info("Inserisci la chiave Factum manualmente.")

    # Override manuale per piani a pagamento
    if not factum_api_key:
        # Claim fallito → prompt manuale sempre
        console.print("[bold]Passo facoltativo  —  API Key Factum Parse[/]")
        console.print(
            "  [dim]La trovi su: factum.pyragogy.org › Profilo › API Keys[/]"
        )
        factum_api_key_raw = Prompt.ask(
            "  API Key Factum",
            default=env_current.get("FACTUM_API_KEY", ""),
        )
        factum_api_key = _sanitize(factum_api_key_raw)
        console.print()
    elif Confirm.ask(
        "  Vuoi usare una chiave a pagamento personalizzata "
        "invece del Free Tier?",
        default=False,
    ):
        console.print("[bold]Passo facoltativo  —  API Key Factum Parse[/]")
        console.print(
            "  [dim]La trovi su: factum.pyragogy.org › Profilo › API Keys[/]"
        )
        factum_api_key_raw = Prompt.ask(
            "  API Key Factum",
            default=env_current.get("FACTUM_API_KEY", factum_api_key or ""),
        )
        factum_api_key = _sanitize(factum_api_key_raw)
        console.print()

    # ── Step 3 — Conto di pagamento ────────────────────────────────────
    console.print("[bold]Passo 3/4  —  Conto per saldo automatico spese[/]")
    console.print("  [dim]Ogni spesa verrà marcata come saldata su questo conto.[/]")

    acc_name = env_current.get("FIC_PAYMENT_ACCOUNT_NAME", "")
    acc_id = env_current.get("FIC_PAYMENT_ACCOUNT_ID", "")

    temp_fic3 = FICClient(Settings(fic_token=fic_token, FIC_COMPANY_ID=fic_company_id))
    try:
        accounts = await temp_fic3.get_payment_accounts()
        if accounts:
            console.print("  Conti disponibili sul tuo profilo FIC:")
            for i, acc in enumerate(accounts, 1):
                console.print(f"    {i}. {acc.get('name', '?')}  ({acc.get('type', '?')})")
            choice = IntPrompt.ask("  Scegli il conto (0 = salta)", default=1)
            if 1 <= choice <= len(accounts):
                sel = accounts[choice - 1]
                acc_name = sel.get("name", "")
                acc_id = str(sel.get("id", ""))
                print_ok(f"Conto selezionato: {acc_name} (id={acc_id})")
            else:
                print_info("Configurazione conto saltata — spese non saranno auto-saldate.")
                acc_name = ""
                acc_id = ""
        else:
            print_warning("Nessun conto di pagamento disponibile su FIC.")
            if acc_name:
                print_info(f"Verrà usato il conto già configurato: {acc_name}")
    except Exception as e:
        print_warning(f"Impossibile recuperare conti FIC: {e}")
        if acc_name:
            print_info(f"Verrà usato il conto già configurato: {acc_name}")
    finally:
        await temp_fic3.close()

    # ── Verifica finale Factum ────────────────────────────────────────
    temp_factum = FactumClient(
        Settings(FACTUM_API_KEY=factum_api_key, fic_token=fic_token, FIC_COMPANY_ID=fic_company_id)
    )
    try:
        factum_ok = await temp_factum.health()
        if factum_ok:
            print_ok("Connessione Factum Parse API riuscita")
        else:
            print_error("API Factum non raggiungibile (health check fallito)")
            raise typer.Exit(1)
    except Exception as e:
        print_error(f"Connessione Factum fallita: {e}")
        raise typer.Exit(1) from None
    finally:
        await temp_factum.close()

    # ── Riepilogo e scrittura ──────────────────────────────────────────
    console.print()
    console.print("[bold]Riepilogo configurazione[/]")
    console.print(f"  FIC_TOKEN                  {'✅ impostata' if fic_token else '❌'}")
    console.print(f"  FIC_COMPANY_ID             {fic_company_id}")
    console.print(f"  FACTUM_WORKSPACE_DIR       {workspace_dir}")
    if acc_name:
        console.print(f"  Conto saldo automatico      {acc_name} (id={acc_id})")
    if factum_api_key:
        status_icon = "🔁" if factum_key_is_existing else "✅"
        label = "recuperata" if factum_key_is_existing else "attiva"
        console.print(f"  FACTUM_API_KEY             {status_icon} {label.capitalize()} (Free Tier — 10 conv./mese)")
    else:
        console.print(f"  FACTUM_API_KEY             {'❌ non impostata'}")
    console.print()

    if Prompt.ask("  Scrivere il file .env con questi valori?", choices=["s", "n"], default="s") != "s":
        print_info("Configurazione annullata.")
        raise typer.Exit(0)

    _write_env(Path(".env"), {
        "FIC_TOKEN": fic_token,
        "FIC_COMPANY_ID": fic_company_id,
        "FACTUM_API_KEY": factum_api_key,
        "FACTUM_WORKSPACE_DIR": workspace_dir,
        "INBOX_DIR": inbox_dir,
        "BASE_STORAGE_DIR": base_storage_dir,
        "FIC_PAYMENT_ACCOUNT_NAME": acc_name,
        "FIC_PAYMENT_ACCOUNT_ID": acc_id,
    })
    print_ok("File .env aggiornato")
    console.print()

    # ── Schermata finale ────────────────────────────────────────────────
    console.print(Panel.fit(
        "[bold green]🎉 Configurazione completata con successo![/]\n\n"
        f"📂 [bold]Cartella di lavoro attiva:[/]\n"
        f"   {workspace_dir}\n"
        "\n"
        f"   ├── 📥 [cyan]da_elaborare/[/]   ← TRASCINA QUI I TUOI FILE (PDF / XML)\n"
        f"   ├── 📦 [green]archiviate/[/]     ← File registrati con successo\n"
        f"   └── ⚠️  [yellow]da_verificare/[/]  ← File illeggibili o con anomalie\n"
        "\n"
        "🚀 [bold]Per iniziare:[/]\n"
        "   Inserisci una fattura in 'da_elaborare/' e lancia:\n"
        "   [cyan]factum-fic elabora[/]\n"
        "\n"
        "ℹ️  [bold]Nota & Supporto:[/]\n"
        "   Questo strumento è in fase di sviluppo attivo. Verifica sempre le\n"
        "   registrazioni generate su Fatture in Cloud prima dell'invio allo SDI.\n"
        "\n"
        "   • Guida FIC: [link=https://www.fattureincloud.it/guida-fatturazione-elettronica/]"
        "fattureincloud.it/guida-fatturazione-elettronica[/link]\n"
        "   • Canale YouTube: [link=https://www.youtube.com/@Fatture-in-Cloud]"
        "youtube.com/@Fatture-in-Cloud[/link]",
        border_style="green",
    ))


# ── Comando CLI ──────────────────────────────────────────────────────────────


def setup() -> None:
    """Configurazione guidata interattiva (credenziali, workspace, conto, .env)."""
    settings = Settings()
    try:
        asyncio.run(_run_setup(settings))
    except typer.Exit:
        raise
    except Exception as e:
        print_error(f"Errore durante la configurazione: {e}")
        raise typer.Exit(1) from e


# ── Helper riutilizzato dal dashboard ────────────────────────────────────────


def _tax_regime_label(regime: str) -> str:
    """Traduce il codice regime FIC in etichetta leggibile."""
    r = (regime or "").strip().lower()
    labels = {
        "forfettario": "Regime Forfettario",
        "rf19": "Regime Forfettario (RF19)",
        "ordinario": "Regime Ordinario",
    }
    return labels.get(r, regime or "—")
