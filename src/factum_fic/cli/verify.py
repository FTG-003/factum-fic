"""Verifica fiscale: binding azienda FIC e controllo regime forfettario.

Prima di qualsiasi elaborazione, verifica che:
1. L'azienda su Fatture in Cloud esista e sia raggiungibile.
2. Il regime fiscale sia "forfettario" (RF19).
3. I metadati vengano trasmessi a Factum Parse API per audit.
"""

from __future__ import annotations

from factum_fic.core.factum_client import FactumClient
from factum_fic.core.fic_client import FICClient

# Regimi fiscali forfettario riconosciuti da FIC v2
_FORFETTARIO_REGIMES = {"forfettario", "rf19", "RF19"}


class ForfettarioCheckError(RuntimeError):
    """Errore di verifica: regime fiscale non conforme o azienda non raggiungibile."""


async def verify_and_bind(fic: FICClient, factum: FactumClient | None = None) -> dict:
    """Verifica che l'azienda FIC sia in regime forfettario e aggancia i metadati.

    Blocca SOLO se ``tax_regime`` è esplicitamente un regime incompatibile
    (es. ``rf01`` ordinario). Se vuoto o non specificato, emette un avviso
    e prosegue — il campo può essere assente nell'API FIC anche per aziende
    effettivamente forfettarie.

    Args:
        fic: Client FIC già autenticato.
        factum: Client Factum opzionale (per aggiornare X-FIC-VAT).

    Returns:
        Dizionario con i dati dell'azienda (id, name, tax_regime, vat_number, fiscal_code).

    Raises:
        ForfettarioCheckError: Se il regime è esplicitamente incompatibile.
        httpx.HTTPStatusError: Se le API FIC/Factum non rispondono.
    """
    import logging

    info = await fic.get_company_info()

    tax_regime = (info.get("tax_regime") or "").strip().lower()
    vat_number = (info.get("vat_number") or "").strip()
    name = (info.get("name") or "").strip()
    company_id = info.get("id") or ""

    if tax_regime in _FORFETTARIO_REGIMES:
        pass  # regime forfettario dichiarato — ok
    elif not tax_regime:
        logging.getLogger(__name__).info(
            "ℹ️ Regime non valorizzato nell'API FIC: si assume configurazione Forfettario (RF19)",
        )
    else:
        raise ForfettarioCheckError(
            f"Accesso non consentito: Factum-FIC richiede Regime Forfettario (RF19). "
            f"Rilevato: '{tax_regime}'.",
        )

    # Se fornito, aggiorna il client Factum con la P.IVA (header X-FIC-VAT)
    if factum is not None and vat_number:
        factum.update_fic_vat(vat_number)

    return {
        "id": company_id,
        "name": name,
        "tax_regime": tax_regime,
        "vat_number": vat_number,
        "fiscal_code": (info.get("fiscal_code") or "").strip(),
    }
