"""Wizard interattivo ``factum-fic setup`` per configurazione guidata.

Chiede all'utente le credenziali FIC, attiva la chiave Factum gratuita
via claim server-side (verifica token FIC + P.IVA), chiede il conto
per il saldo automatico, e scrive il file ``.env`` in modo atomico.

Alias: ``factum-fic configura``
"""

from __future__ import annotations

import asyncio
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
    "[bold yellow]Permessi necessari per il Personal Access Token[/]\n"
    "\n"
    "  1. [cyan]Acquisti / Spese[/] → [green]Lettura e Scrittura[/]  (obbligatorio)\n"
    "  2. [cyan]Documenti Emessi[/] → [green]Lettura e Scrittura[/]  (obbligatorio per autofatture TD17/18/19)\n"
    "  3. [cyan]Impostazioni / Azienda[/] → [yellow]Sola Lettura[/]  (per rilevare P.IVA e regime fiscale)\n"
    "\n"
    "[dim]Vai su: fattureincloud.it › Impostazioni › Sicurezza › API Key[/]",
    title="🔑 Token FIC",
    border_style="yellow",
)

# ── Helpers ───────────────────────────────────────────────────────────────────


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
                # Aggiungi commento se il file non l'ha ancora fatta
                if updates[key]:
                    new_lines.append(f"{key}={updates[key]}")
                seen.add(key)
            else:
                new_lines.append(line)

    # Aggiunge le chiavi nuove (con valore non vuoto) in fondo
    for key, val in updates.items():
        if val and key not in seen:
            new_lines.append(f"{key}={val}")

    path.write_text("\n".join(new_lines) + "\n")


# ── Wizard ────────────────────────────────────────────────────────────────────


async def _run_setup(settings: Settings) -> None:
    """Esegue il wizard interattivo di configurazione."""
    env_current = _load_env(Path(".env"))

    console.print()
    console.print(Panel("[bold cyan]⚙️  Configurazione guidata factum-fic[/]", border_style="cyan"))
    console.print()

    # ── Step 1 — FIC credentials ────────────────────────────────────────
    console.print("[bold]Passo 1/3  —  Credenziali Fatture in Cloud[/]")
    console.print(_FIC_TOKEN_PERMISSIONS)
    console.print()
    fic_api_key = Prompt.ask(
        "  API Key FIC",
        default=env_current.get("FIC_API_KEY", ""),
    )
    fic_company_id = Prompt.ask(
        "  Company ID FIC",
        default=env_current.get("FIC_COMPANY_ID", ""),
    )
    console.print()

    # Test FIC
    temp_fic = FICClient(Settings(FIC_API_KEY=fic_api_key, FIC_COMPANY_ID=fic_company_id))
    try:
        info = await temp_fic.get_company_info()
        regime = (info.get("tax_regime") or "").strip().lower()
        is_forf = regime in {"forfettario", "rf19"}
        regime_label = _tax_regime_label(regime)
        print_ok(f"Connesso ad azienda: [bold]{info.get('name', '?')}[/]")
        print_ok(f"  P.IVA: {info.get('vat_number', '—')}")
        print_ok(f"  Regime: {regime_label} {'✅ Forfettario' if is_forf else '⚠️'}")
        if not is_forf:
            print_warning("factum-fic è ottimizzato per il Regime Forfettario (RF19).")
            if Prompt.ask("  Continuare lo stesso?", choices=["s", "n"], default="n") != "s":
                raise typer.Exit(0)
    except Exception as e:
        print_error(f"Connessione FIC fallita: {e}")
        raise typer.Exit(1) from None
    finally:
        await temp_fic.close()

    # ── Step 2 (opzionale) — Auto-claim chiave Factum ──────────────────
    console.print()
    console.print("[bold]Passo 2/3  —  Attivazione chiave Factum Parse (gratuita)[/]")
    console.print(
        "  [dim]10 conversioni PDF/mese gratis, collegate alla tua P.IVA.[/]"
    )

    do_claim = Confirm.ask(
        "  Vuoi attivare la chiave Factum Parse?",
        default=True,
    )
    factum_api_key = ""

    if do_claim:
        piva = (info.get("vat_number") or "").strip()
        print_info(
            f"  Verifica server-side in corso per P.IVA {piva}…"
        )

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{settings.factum_api_url}/api/v1/auth/claim",
                    json={
                        "fic_token": fic_api_key,
                        "fic_company_id": fic_company_id,
                    },
                )
            if resp.status_code == 200:
                data = resp.json()
                factum_api_key = (data.get("api_key") or "").strip()
                if factum_api_key:
                    print_ok(
                        f"Chiave Factum Parse attivata con successo "
                        f"(10 PDF/mese gratuiti collegati a P.IVA {piva})"
                    )
                else:
                    print_warning(
                        "Claim completato ma chiave vuota. "
                        "Inseriscila manualmente."
                    )
            elif resp.status_code == 409:
                data = resp.json()
                factum_api_key = (data.get("api_key") or "").strip()
                if factum_api_key:
                    print_ok("Chiave già attiva per questa P.IVA — recuperata.")
                else:
                    piva_reuse = (data.get("piva") or "").strip()
                    if piva_reuse and piva_reuse != piva:
                        print_warning(
                            f"Il token FIC è già associato a P.IVA {piva_reuse}. "
                            "Serve una chiave separata. Contatta info@pyragogy.org."
                        )
                    else:
                        print_warning(
                            "Chiave già reclamata. Se l'hai smarrita, "
                            "contatta info@pyragogy.org per un reset."
                        )
            elif resp.status_code == 403:
                print_error(
                    "Token FIC non valido o permessi insufficienti. "
                    "Verifica che il token FIC abbia permessi di lettura "
                    "su 'Impostazioni / Azienda'."
                )
            else:
                print_warning(
                    f"Claim API ha risposto HTTP {resp.status_code}. "
                    "Inserisci la chiave Factum manualmente."
                )
        except httpx.HTTPError as e:
            print_warning(f"Impossibile contattare il server Factum: {e}")
            print_info("Inserisci la chiave Factum manualmente.")
    else:
        print_info("Auto-claim saltato.")

    if not factum_api_key:
        # ── Step legacy — Inserimento manuale chiave Factum ───────────
        console.print("[bold]Passo facoltativo  —  API Key Factum Parse[/]")
        console.print(
            "  [dim]La trovi su: factum.pyragogy.org › Profilo › API Keys[/]"
        )
        factum_api_key = Prompt.ask(
            "  API Key Factum",
            default=env_current.get("FACTUM_API_KEY", ""),
        )
        console.print()

    # ── Step — Conto di pagamento ─────────────────────────────────────
    console.print("[bold]Passo  —  Conto per saldo automatico spese[/]")
    console.print("  [dim]Ogni spesa verrà marcata come saldata su questo conto.[/]")

    acc_name = env_current.get("FIC_PAYMENT_ACCOUNT_NAME", "")
    acc_id = env_current.get("FIC_PAYMENT_ACCOUNT_ID", "")

    temp_fic2 = FICClient(Settings(FIC_API_KEY=fic_api_key, FIC_COMPANY_ID=fic_company_id))
    try:
        accounts = await temp_fic2.get_payment_accounts()
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
        await temp_fic2.close()

    # ── Verifica finale Factum ────────────────────────────────────────
    temp_factum = FactumClient(
        Settings(FACTUM_API_KEY=factum_api_key, FIC_API_KEY=fic_api_key, FIC_COMPANY_ID=fic_company_id)
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
    console.print(f"  FIC_API_KEY                {'✅ impostata' if fic_api_key else '❌'}")
    console.print(f"  FIC_COMPANY_ID             {fic_company_id}")
    if acc_name:
        console.print(f"  Conto saldo automatico      {acc_name} (id={acc_id})")
    console.print(f"  FACTUM_API_KEY             {'✅ impostata' if factum_api_key else '❌'}")
    console.print()

    if Prompt.ask("  Scrivere il file .env con questi valori?", choices=["s", "n"], default="s") != "s":
        print_info("Configurazione annullata.")
        raise typer.Exit(0)

    _write_env(Path(".env"), {
        "FIC_API_KEY": fic_api_key,
        "FIC_COMPANY_ID": fic_company_id,
        "FACTUM_API_KEY": factum_api_key,
        "FIC_PAYMENT_ACCOUNT_NAME": acc_name,
        "FIC_PAYMENT_ACCOUNT_ID": acc_id,
    })
    print_ok("File .env aggiornato")
    console.print()

    console.print(Panel.fit(
        "[bold green]✅  Configurazione completata![/]\n\n"
        "Ora puoi iniziare subito:\n"
        "  [cyan]factum-fic sync[/]            Elabora tutti i file in inbox/\n"
        "  [cyan]factum-fic sync fattura.pdf[/]  Elabora un singolo file\n"
        "  [cyan]factum-fic watch[/]            Avvia il monitoraggio automatico\n"
        "  [cyan]factum-fic status[/]           Dashboard operativo",
        border_style="green",
    ))


# ── Comando CLI ──────────────────────────────────────────────────────────────


def setup() -> None:
    """Configurazione guidata interattiva (credenziali, conto, .env)."""
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
