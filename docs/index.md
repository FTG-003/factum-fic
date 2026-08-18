# Factum FIC — Documentazione

> Automazione contabile deterministica e sincronizzazione con Fatture in Cloud.

---

## Architettura del progetto

Factum FIC è un tool CLI/daemon scritto in Python che automatizza il ciclo
di vita di una fattura digitale: dal file (PDF o XML FatturaPA SDI) alla
registrazione contabile su **Fatture in Cloud v2**.

```
┌──────────────────────────────────────────────────────────┐
│                     factum-fic CLI                        │
│                                                          │
│  inbox/                                                   │
│   ├── fattura_12345.xml   ───→  parse_sdi_xml()          │
│   │                            (locale, deterministico)  │
│   │                                                      │
│   ├── fattura_2026-07.pdf ───→  Factum Parse API (LLM)   │
│   │                            solo per PDF non XML      │
│   │                                                      │
│   └── scontrino.pdf      ───→  Regex fallback amounts    │
│                                                          │
│              │                                           │
│              ▼                                           │
│   ┌──────────────────┐    ┌─────────────────────────┐   │
│   │  Mapper          │───→│  Fatture in Cloud v2    │   │
│   │  TD17/18/19      │    │  Expense + Self-Invoice  │   │
│   │  Reverse charge  │    │  + PDF allegato         │   │
│   └──────────────────┘    └─────────────────────────┘   │
│              │                                           │
│              ▼                                           │
│   ┌──────────────────┐                                   │
│   │  Archiver        │                                   │
│   │  archiviate/     │                                   │
│   │  YYYY/MM/        │                                   │
│   └──────────────────┘                                   │
└──────────────────────────────────────────────────────────┘
```

### Componenti principali

| Componente | Ruolo | Linguaggio |
|---|---|---|
| **CLI** (`src/factum_fic/cli/`) | Entrypoint Typer con alias italiani | Python |
| **Parser XML SDI** (`extractors.py`) | Parsing deterministico XML FatturaPA v1.2 | Python `xml.etree` |
| **Parser PDF** (`factum_client.py`) | Chiamata API Factum Parse per estrazione testo | HTTP Async |
| **Mapper** (`mapper.py`) | Logica di classificazione e costruzione payload FIC | Python |
| **Archiver** (`archiver.py`) | Spostamento file in albero `archiviate/YYYY/MM/` | Python |
| **FIC Client** (`fic_client.py`) | Wrapper asincrono per API Fatture in Cloud v2 | HTTPX |
| **Queue** (`queue.py`) | Coda SQLite per tracciamento elaborazioni | SQLite |
| **Watcher** (`daemon.py`) | Monitoraggio hotfolder in tempo reale | watchdog |

### Flusso di elaborazione

1. **Rilevamento tipo file**: l'estensione `.xml` attiva il parser locale SDI;
   `.pdf` e altri formati usano l'API Factum Parse (LLM).
2. **Estrazione dati**: campi strutturati (fornitore, importi, IVA, data).
3. **Classificazione**: `detect_document_type()` decide se è una spesa (fornitore
   italiano) o un'autofattura (fornitore estero).
4. **Reverse charge**: logica N6.x + fornitore estero per SDI; `iva_totale == 0.0`
   per PDF.
5. **Registrazione FIC**: crea `Expense` + eventuale `Self-Invoice` (TD17/18/19).
6. **Archiviazione**: sposta il file in `archiviate/YYYY/MM/`.
7. **Tracciamento**: registra l'esito su coda SQLite per deduplicazione.

---

## Guida di avvio rapido

### Prerequisiti

- Python 3.12+
- `uv` (consigliato) o `pip`
- Un account [Fatture in Cloud](https://www.fattureincloud.it/) con API Key
- Una API Key per [Factum Parse](https://factum.pyragogy.org) (solo per PDF)

### Installazione

```bash
# Con uv
uv tool install factum-fic

# Oppure da sorgente
git clone https://github.com/FTG-003/factum-fic.git
cd factum-fic
uv sync --dev
```

### Configurazione

```bash
# Setup interattivo — guida passo-passo
factum-fic configura

# Oppure configurazione manuale: copia .env.example in .env
# e modifica le credenziali
cp .env.example .env
```

### Permessi minimi del token FIC

Per generare il Personal Access Token su Fatture in Cloud, assicurati di
abilitare almeno questi permessi:

| Area | Permesso | Necessario per |
|---|---|---|
| **Acquisti / Spese** | Lettura e Scrittura | Creare e allegare spese |
| **Documenti Emessi** | Lettura e Scrittura | Generare autofatture TD17/18/19 |
| **Impostazioni / Azienda** | Sola Lettura | Rilevare P.IVA e regime fiscale |

> ⚠️ **Senza i permessi di scrittura su "Documenti Emessi", le autofatture
> per fornitori esteri (reverse charge) non possono essere generate.**

### Utilizzo base

```bash
# Crea la cartella inbox e copia le fatture
mkdir -p da_elaborare
cp ~/Downloads/fattura_12345.xml ./da_elaborare/

# Elabora tutti i file
factum-fic sync

# Elabora un singolo file
factum-fic sync ~/Downloads/fattura_2026-07.pdf

# Avvia il monitoraggio automatico (hotfolder)
factum-fic watch ~/Downloads
```

### Struttura delle directory

```
.
├── da_elaborare/          ← Metti qui i file da processare
├── archiviate/            ← Elaborati con successo (YYYY/MM/)
│   └── 2026/
│       └── 08/
│           ├── Hetzner_4AUT.xml
│           └── Hetzner_087001028658.pdf
├── da_verificare/         ← File con errori (esame manuale)
├── .env                   ← Credenziali (NON committare)
├── config.yaml            ← Mappatura categorie (opzionale)
└── factum-fic.db          ← Coda SQLite (NON committare)
```

---

## Reverse charge e natura SDI

La corretta gestione del reverse charge è fondamentale per la contabilità IVA.

### Logica di rilevamento

| Scenario | Paese | IVA | Natura SDI | Reverse charge | Tipo SDI |
|---|---|---|---|---|---|
| Fornitore IT, forfettario | IT | 0,00 | **N2.2** | ❌ No | — |
| Fornitore IT, esente art.10 | IT | 0,00 | **N4** | ❌ No | — |
| Fornitore IT, inversione contabile | IT | 0,00 | **N6.x** | ✅ Sì | TD16 |
| Fornitore DE con P.IVA, IVA≠0 | DE | 3,45 | assente | ❌ No | Spesa diretta |
| Fornitore DE con P.IVA, IVA=0 | DE | 0,00 | assente | ✅ Sì | **TD18** |
| Fornitore DE senza P.IVA, IVA=0 | DE | 0,00 | assente | ✅ Sì | **TD19** |
| Fornitore USA, IVA zero | US | 0,00 | assente | ✅ Sì | TD17 |

### Tipi di autofattura SDI

| Codice | Descrizione | Quando si usa |
|---|---|---|
| **TD17** | Autofattura per acquisto servizi extra-UE | Fornitore extra-UE (es. USA, UK, Svizzera) |
| **TD18** | Autofattura per acquisto intra-UE con P.IVA | Fornitore UE con partita IVA valida |
| **TD19** | Autofattura per acquisto intra-UE senza P.IVA | Fornitore UE senza partita IVA valida |

### Codici Natura (FatturaPA)

I codici Natura nel campo `<Natura>` dei `<DatiRiepilogo>` determinano la
classificazione IVA:

| Codice | Significato | RC? |
|---|---|---|
| N1 | Escluse ex art. 15 | ❌ |
| N2.1 | Non soggette — UE | ❌ |
| **N2.2** | **Non soggette — Regime Forfettario** | ❌ |
| N3 | Non imponibili | ❌ |
| N4 | Esenti | ❌ |
| N5 | Margine | ❌ |
| **N6.x** | **Inversione contabile (reverse charge)** | ✅ |
| N7 | IVA assolta in altro stato UE | ❌ |

---

## Licenza

Factum FIC è rilasciato sotto **GNU AGPL-3.0**. Vedi il file [LICENSE](../LICENSE)
per il testo completo.

Le API Factum Parse (https://factum.pyragogy.org) sono un servizio SaaS separato
e non coperte da questa licenza.

---

## Riferimenti

- [Fatture in Cloud API v2](https://developers.fatturincloud.it)
- [Fattura PA — Specifica tecnica v1.2](https://www.finanze.gov.it/fattura-elettronica/)
- [Normativa reverse charge — DPR 633/72, art. 17 c. 2 e c. 5](https://www.normativa.it/DPR-633-1972-art17)