<div align="center">
  <img src=".github/assets/factum-FIC-github.png" alt="Factum FIC Logo" width="180" />
  <h1>Factum FIC</h1>
  <p><strong>Da fattura digitale a Fatture in Cloud — automazione contabile deterministica</strong></p>
  <p>🇮🇹 Parser XML/SDI locale + pipeline di registrazione su Fatture in Cloud v2</p>
</div>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.13%2B-blue" alt="Python 3.13+"></a>
  <a href="https://github.com/FTG-003/factum-fic/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/FTG-003/factum-fic/ci.yml?branch=main&label=CI" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="License"></a>
  <a href="https://github.com/FTG-003/factum-fic/stargazers"><img src="https://img.shields.io/github/stars/FTG-003/factum-fic?style=social" alt="Stars"></a>
</p>

---

## 📋 Sommario

- [Cos'è Factum FIC](#-cosè-factum-fic)
- [At a glance](#-at-a-glance)
- [Installazione](#-installazione)
- [Guida rapida](#-guida-rapida)
- [Ciclo di vita dei file](#-ciclo-di-vita-dei-file)
- [Parser XML/SDI deterministico](#-parser-xmlsdi-deterministico)
- [Reverse charge SDI](#-reverse-charge-sdi)
- [Autofatture (TD17/TD18/TD19)](#-autofatture-td17td18td19)
- [CLI completa](#-cli-completa)
- [Architettura](#-architettura)
- [Sviluppo](#-sviluppo)
- [FAQ](#-faq)
- [Licenza](#-licenza)
- [English](#english)

---

## 🎯 Cos'è Factum FIC

**Factum FIC** è un tool daemon/CLI che automatizza la registrazione contabile delle fatture
su **Fatture in Cloud v2**, con un approccio **deterministico e trasparente**:

| Ingresso | Elaborazione | Output |
|---|---|---|
| 🏛️ **XML FatturaPA SDI** (`.xml`) | Parser locale — **zero API, zero AI, zero costi** | Spesa + autofattura su FIC |
| 🧾 **PDF fattura** (`.pdf`) | Factum Parse API (LLM) — solo il testo estratto, mai il file | Spesa su FIC |
| 🧾 **Altri PDF/scontrini** | Factum Parse API — estrazione dati flessibile | Spesa su FIC |

Nato per chi ha **Partita IVA in Regime Forfettario** e vuole automatizzare
la contabilità senza abbonamenti costosi (niente Zucchetti, TeamSystem, etc.).

### Perché Factum FIC?

- ✅ **XML SDI gratis**: zero chiamate API, zero token LLM, zero allucinazioni
- ✅ **Reverse charge corretto**: segue lo standard FatturaPA (N6.x, non IVA=0)
- ✅ **Autofatture automatiche**: TD17/TD18/TD19 generate al volo
- ✅ **Recupero guasti**: se l'autofattura fallisce, la spesa è salvata — riprovi con `riprova-autofatture`
- ✅ **Niente cloud forzato**: giri in locale, i tuoi dati restano tuoi

---

## 🚀 At a glance

```text
$ factum-fic elabora

📂 Elaborazione file in ./da_elaborare/

  ✅ Hetzner_2026-07.xml       → SDI XML parsed (locale, zero LLM)
                               → Spesa registrata su FIC (id=430594826)
                               → ✅ Autofattura TD17 generata (id=430594827)

  ✅ Hetzner_2026-07.pdf       → Spesa registrata su FIC (id=430594828)

  ✅ scontrino.pdf             → Spesa registrata su FIC (id=430594829)

📊 Riepilogo: 3 elaborate, 1 autofattura, 0 errori

────────────────────────────────────────────────────────────

$ factum-fic status

  Factum API:        ✅ ok
  Fatture in Cloud:  ✅ ok (azienda: La Mia SRL)
  Conto pagamento:   ✅ Banca Intesa (IT12X1234567890)

  📦 Coda elaborazione:
    Processati:  47
    Spese FIC:  47
    Autofatture: 12
    In coda:     3
    Errori:      1
    Pendenti SI: 0

────────────────────────────────────────────────────────────

$ factum-fic history --last 5

  🕐 2026-08-17 14:32  ✅  Hetzner Online GmbH          DE812871812     15,69€    xml
  🕐 2026-08-17 14:30  ✅  DigitalOcean Inc.             US              59,00$    pdf
  🕐 2026-08-16 09:15  ✅  Aruba S.p.A.                  IT02128540513   45,00€    xml
  🕐 2026-08-15 18:42  ⚠️  OFFICEPLUS SRL                IT03123450987   2755,80€  pdf
  🕐 2026-08-14 11:00  ❌  fattura_illeggibile.pdf        —               —        pdf
```

---

## 📦 Installazione

### Con uv (consigliato — 2 secondi)

```bash
uv tool install factum-fic
factum-fic --help
```

### Con pip

```bash
pip install factum-fic
factum-fic --help
```

### Da sorgente (sviluppo)

```bash
git clone https://github.com/FTG-003/factum-fic.git
cd factum-fic
uv sync --all-extras --dev
uv run factum-fic --help
```

### Prerequisiti

- **Python 3.13+**
- **Fatture in Cloud** account con API abilitata (Personal Access Token)
- **Factum Parse API Key** (opzionale, solo per PDF — [richiedi su pyragogy.org](https://pyragogy.org))

### Configurazione

```bash
# Setup guidato interattivo
factum-fic setup

# Il wizard configura:
#   1. FACTUM_API_KEY   → per parsing PDF via AI
#   2. FIC_API_KEY      → Personal Access Token Fatture in Cloud
#   3. FIC_COMPANY_ID   → dalla dashboard FIC
#   4. reverse charge   → abilita/disabilita autofatture SDI
#   5. pagamento auto   → account di pagamento predefinito
```

Oppure crea un file `.env`:

```ini
# Obbligatoria — Fatture in Cloud
FIC_API_KEY="iltuotokenpersonale"
FIC_COMPANY_ID="12345"

# Per parsing PDF (opzionale — senza, solo XML funzionano)
FACTUM_API_KEY="latuachiavefactum"

# Opzionali
STRICT_CURRENCY=true           # Fallisce su USD/GBP se non convertibile
FIC_GENERATE_SELF_INVOICE=true # Genera autofatture per fornitore estero
FIC_SELF_INVOICE_NUMERATION="/TD"  # Numerazione autofatture
WATCH_DIR=~/Downloads          # Cartella da monitorare (watcher)
```

---

## 🧭 Guida rapida

### 1. Metti le fatture in `da_elaborare/`

```bash
mkdir -p da_elaborare
cp ~/Downloads/fattura_*.xml da_elaborare/
cp ~/Downloads/fattura_*.pdf da_elaborare/
```

### 2. Elabora

```bash
factum-fic elabora
```

### 3. Verifica

```bash
factum-fic status        # Dashboard completo
factum-fic history       # Cronologia elaborazioni
```

### 4. (Opzionale) Watcher hotfolder

```bash
factum-fic watch         # Monitora WATCH_DIR in tempo reale
```

---

## 📁 Ciclo di vita dei file

```
da_elaborare/                        ← Qui metti le fatture
├── fattura_12345.xml               ← XML FatturaPA SDI
├── fattura_2026-07.pdf             ← PDF fattura
├── scontrino.pdf                   ← Altro PDF
│
└── (elaborazione automatica)
      │
      ├── ✅ Successo → archiviate/YYYY/MM/Fornitore_Numero.pdf
      ├── ⚠️ Duplicato → archiviate/YYYY/MM/... (stessa SHA-256)
      ├── ⏳ Già in coda → skip (stato 'processing')
      └── ❌ Errore   → da_verificare/nomefile.pdf (esame manuale)

Struttura archivio:

archiviate/
├── 2026/
│   ├── 08/
│   │   ├── 2026-08-17_Hetzner-Online-GmbH_4AUT.xml
│   │   ├── 2026-08-17_Hetzner-Online-GmbH_087001028658.pdf
│   │   ├── 2026-08-01_DigitalOcean-Inc_INV-2026-08101.pdf
│   │   └── .../
│   └── 09/
└── 2027/
```

### Deduplicazione

- **SHA-256 del file**: stesso contenuto → saltato automaticamente
- **Pre-check FIC**: evita doppioni anche tra rinomine
- **Lock atomico**: elaborazione concorrente impossibile per lo stesso file

---

## 🏛️ Parser XML/SDI deterministico

Il **cuore** di Factum FIC. Quando incontra un file `.xml`, lo parserizza in locale
— **zero chiamate API, zero token LLM, zero costi, zero allucinazioni**.

### Cosa estrae dal XML FatturaPA

```xml
<?xml version="1.0" encoding="UTF-8"?>
<FatturaElettronica>
  <FatturaElettronicaHeader>
    <CedentePrestatore>
      <DatiAnagrafici>
        <IdFiscaleIVA>
          <IdPaese>DE</IdPaese>
          <IdCodice>812871812</IdCodice>
        </IdFiscaleIVA>
        <Anagrafica>
          <Denominazione>Hetzner Online GmbH</Denominazione>
        </Anagrafica>
      </DatiAnagrafici>
    </CedentePrestatore>
    ...
  </FatturaElettronicaHeader>
  <FatturaElettronicaBody>
    <DatiGenerali>
      <DatiGeneraliDocumento>
        <Numero>4AUT</Numero>
        <Data>2026-07-02</Data>
        <Divisa>EUR</Divisa>
        <ImportoTotaleDocumento>15.69</ImportoTotaleDocumento>
      </DatiGeneraliDocumento>
    </DatiGenerali>
    <DatiBeniServizi>
      <DatiRiepilogo>
        <ImponibileImporto>15.69</ImponibileImporto>
        <Imposta>0.00</Imposta>
        <Natura>N6.9</Natura>   <!-- ← Reverse charge! -->
      </DatiRiepilogo>
    </DatiBeniServizi>
  </FatturaElettronicaBody>
</FatturaElettronica>
```

↓ **Parsing locale deterministico**:

```json
{
  "ragione_sociale": "Hetzner Online GmbH",
  "partita_iva": "DE812871812",
  "paese": "DE",
  "numero": "4AUT",
  "data_emissione": "2026-07-02",
  "valuta": "EUR",
  "imponibile_totale": 15.69,
  "iva_totale": 0.0,
  "totale_documento": 15.69,
  "is_reverse_charge": true,
  "nature": ["N6.9"]
}
```

### Fallback

| Campo | Prima scelta | Fallback |
|---|---|---|
| Nome fornitore | `<Denominazione>` | `<Nome>` + `<Cognome>` |
| Partita IVA | `<IdFiscaleIVA>` (Paese+Codice) | `<CodiceFiscale>` |
| Importi | Somma di tutti i `<DatiRiepilogo>` | `<ImportoTotaleDocumento>` |
| Reverse charge | `<Natura>` = N6.x | Paese ≠ IT + IVA = 0 |

---

## 🔬 Reverse charge SDI

Nello standard FatturaPA, **IVA = 0 non significa reverse charge**.
Un forfettario italiano (N2.2) ha IVA=0 ma NON è reverse charge.
La logica corretta è implementata nel parser:

### Tabella decisionale

| Scenario | Paese | IVA | Natura SDI | Reverse charge? |
|---|---|---|---|---|
| Fornitore IT, **forfettario** | IT | 0,00 | **N2.2** | ❌ No |
| Fornitore IT, **esente art.10** | IT | 0,00 | **N4** | ❌ No |
| Fornitore IT, **inversione contabile** | IT | 0,00 | **N6.1–N6.9** | ✅ **Sì** |
| Fornitore DE, **IVA applicata** | DE | 3,45 | *(nessuna)* | ❌ No |
| Fornitore DE, **IVA zero** | DE | 0,00 | *(nessuna)* | ✅ **Sì** |
| Fornitore US, **IVA zero** | US | 0,00 | *(nessuna)* | ✅ **Sì** |

### Codice Natura SDI

| Codice | Significato | Reverse charge? |
|---|---|---|
| N1 | Escluse ex art. 15 | ❌ |
| N2.1 | Non soggette UE | ❌ |
| **N2.2** | **Non soggette — forfettario** | ❌ |
| N3 | Non imponibili | ❌ |
| **N4** | **Esenti** | ❌ |
| N5 | Margine | ❌ |
| N6.1 | Inversione contabile — beni | ✅ |
| N6.2 | Inversione contabile — servizi | ✅ |
| N6.3 | Inversione contabile — subappalto | ✅ |
| N6.4 | Inversione contabile — edilizia | ✅ |
| N6.5 | Inversione contabile — energia | ✅ |
| N6.6 | Inversione contabile — rottami | ✅ |
| N6.7 | Inversione contabile — telefonia | ✅ |
| N6.8 | Inversione contabile — elettronica | ✅ |
| **N6.9** | **Inversione contabile — altro** | ✅ |
| N7 | IVA assolta in altro Stato UE | ❌ |

---

## 📄 Autofatture (TD17/TD18/TD19)

Factum FIC genera automaticamente le autofatture SDI quando rileva un **reverse charge**:

| Tipo SDI | Descrizione | Quando |
|---|---|---|
| **TD17** | Integrazione/autofattura acquisto servizi esteri | Fornitore extra-UE con IVA=0 |
| **TD18** | Integrazione acquisto beni UE | Fornitore UE con reverse charge |
| **TD19** | Integrazione/autofattura acquisto beni extra-UE | Fornitore extra-UE con reverse charge |

### Recupero automatico

Se l'autofattura SDI fallisce (es. errore FIC), **la spesa è comunque salvata**
con stato `SELF_INVOICE_PENDING`. Puoi riprovare in seguito:

```bash
# Riprova tutte le autofatture pendenti
factum-fic riprova-autofatture

# Oppure verifica lo stato
factum-fic status
# → Pendenti SI: 1  ← una autofattura da completare
```

---

## 🛠️ CLI completa

```text
Uso: factum-fic <comando> [opzioni]

Comandi principali:
  elabora              Elabora tutti i file in da_elaborare/
  status               Dashboard stato e coda
  history              Cronologia ultime elaborazioni
  verify               Verifica connettività API (Factum + FIC)
  setup                Configurazione guidata interattiva
  watch                Avvia watcher hotfolder (background)

Comandi di recupero:
  riprova-autofatture  Riprova generazione autofatture pendenti

Opzioni globali:
  --help               Mostra questo aiuto
  --version            Mostra versione

Opzioni elabora:
  --force              Rielabora anche file già processati
  --dry-run            Simula senza scrivere su FIC
  --verbose, -v        Output dettagliato

Opzioni watch:
  --daemon, -d         Avvia come demone in background
  --dir PATH           Cartella da monitorare (default: WATCH_DIR)
  --interval SEC       Intervallo polling (default: 30)
```

---

## 🏗️ Architettura

```
┌─────────────────────────────────────────────────────────────────────┐
│                     da_elaborare/ (.xml / .pdf)                     │
└────────────────────────────────────┬────────────────────────────────┘
                                     │
                          (path.suffix)
                         /              \
                   .xml                   .pdf / altro
                     │                       │
                     ▼                       ▼
          ┌──────────────────┐    ┌──────────────────────┐
          │  parse_sdi_xml() │    │  Factum Parse API    │
          │  (xml.etree)     │    │  (DeepSeek V3 via    │
          │  deterministico  │    │   OpenRouter)        │
          │  0 token, 0 €    │    │   solo testo, mai    │
          └────────┬─────────┘    │   il file originale  │
                   │              └──────────┬───────────┘
                   │                         │
                   └──────┬──────────────────┘
                          │
                          ▼
               ┌────────────────────┐
               │  FactumParseResult │
               │  (struct tipata)   │
               └────────┬───────────┘
                        │
                        ▼
               ┌────────────────────┐
               │      Mapper        │
               │  result → expense  │
               │  RC → self-invoice │
               └────────┬───────────┘
                        │
                        ▼
          ┌──────────────────────────────┐
          │      FICClient (v2 API)      │
          │                               │
          │  1. get_attachment_token()    │
          │  2. create_expense()          │
          │  3. (se RC) create_issued_    │
          │     document() → autofattura  │
          └────────┬──────────────────────┘
                   │
                   ▼
          ┌──────────────────────────────┐
          │          Archiver            │
          │  archiviate/YYYY/MM/Nome.pdf │
          └──────────────────────────────┘

Componenti chiave:

📦 src/factum_fic/
├── cli/         ← Interfaccia CLI (Typer)
│   ├── main.py       ← Comandi: elabora, status, history, verify, setup, watch
│   └── verify.py     ← Verifica connettività API
├── core/        ← Logica di dominio
│   ├── pipeline.py   ← Orchestratore: lock → parse → mappa → FIC → archivia
│   ├── extractors.py ← parse_sdi_xml() — parser XML deterministico
│   ├── mapper.py     ← FactumParseResult → Expense + SelfInvoice requests
│   ├── factum_client.py ← Client HTTP Factum Parse API
│   ├── fic_client.py    ← Client HTTP Fatture in Cloud v2
│   ├── models.py     ← Tutte le strutture dati (Pydantic v2)
│   └── archiver.py   ← Spostamento file in archiviate/YYYY/MM/
│       retry_policy.py ← Retry con backoff esponenziale
├── storage/      ← Persistenza
│   └── queue.py      ← Coda SQLite (enqueue, acquire, complete, pending SI)
├── config.py     ← Config (env + YAML)
└── watcher/      ← Hotfolder watchdog
    └── daemon.py     ← Monitoraggio cartella in tempo reale
```

---

## 🧪 Sviluppo

### Prerequisiti

- Python 3.13+
- [uv](https://github.com/astral-sh/uv)

### Setup

```bash
git clone https://github.com/FTG-003/factum-fic.git
cd factum-fic
uv sync --all-extras --dev

# Crea file .env per i test (con chiavi finte)
cp .env.example .env
```

### Test

```bash
# Suite completa
uv run pytest -q

# Solo test specifici
uv run pytest tests/test_extractors.py -q
uv run pytest tests/test_remediation.py -q

# Con copertura
uv run pytest --cov=factum_fic --cov-report=term-missing
```

### Lint

```bash
uv run ruff check .
uv run ruff format --check .
```

### CI/CD

La CI (GitHub Actions) esegue automaticamente:

| Evento | Cosa fa | Secrets richiesti |
|---|---|---|
| `push` / `pull_request` su `main` | Ruff lint + Pytest (con chiavi mock) | ❌ Nessuno |
| `workflow_dispatch` (manuale) | Smoke test su produzione | ✅ FACTUM_API_KEY, FIC_API_KEY, FIC_COMPANY_ID |

---

## ❓ FAQ

**D: Devo per forza avere Factum Parse API Key?**
No. Gli XML FatturaPA funzionano senza nessuna chiave API (parser 100% locale).
La chiave serve solo per i PDF.

**D: L'autofattura è davvero una email al SDI?**
No. Factum FIC genera autofatture **interne a Fatture in Cloud** (TD17/TD18/TD19).
Non invia al Sistema di Interscambio (SDI). Per l'AdE, l'autofattura è già assolta
dalla controparte — la registrazione contabile su FIC è sufficiente ai fini
della contabilità aziendale.

**D: I miei dati finiscono su server esterni?**
- **XML**: mai. Il parsing è 100% locale.
- **PDF**: solo il testo estratto va a Factum Parse API. Il file PDF originale
  non viene mai trasmesso. Factum non conserva il testo dopo la risposta.
- **Fatture in Cloud**: sì, è il destinatario finale — lì finiscono i dati
  contabili estratti (esattamente come se li inserissi a mano).

**D: Cosa succede se un PDF è illeggibile?**
Factum FIC rileva automaticamente testo corrotto (>10% caratteri
non validi) e sposta il file in `da_verificare/` per revisione manuale.

**D: Supporta fatture in valuta estera?**
Sì. USD/GBP/CHF vengono convertiti in EUR usando i tassi BCE
(Frankfurter API) con cache di 6 ore. Con `STRICT_CURRENCY=true`
(default), la conversione fallisce con errore se il tasso non è
disponibile (fail-fast anziché procedere con dati errati).

**D: Come gestisce la concorrenza?**
Lock atomico SQLite + `asyncio.Lock` in pipeline. Due processi
concorrenti non possono elaborare lo stesso file.

---

## 📜 Licenza

**GNU Affero General Public License v3.0** (AGPL-3.0)

Questo software è **open source e copyleft**. Chiunque lo modifichi e lo
distribuisca via rete (es. come servizio SaaS) deve rilasciare il codice
sorgente delle modifiche agli utenti della rete.

Le API [Factum Parse](https://factum.pyragogy.org) sono un servizio
separato e non coperte da questa licenza.

[Testo completo della licenza →](LICENSE)

---

<div align="center">
  <sub>Fatto con ❤️ da <a href="https://github.com/FTG-003">FTG-003</a></sub>
  <br>
  <sub>🇮🇹 Documentazione in Italiano · <a href="#english">🇬🇧 English</a></sub>
</div>

---

<a name="english"></a>
<br>

<h1 align="center">Factum FIC <span style="font-weight:normal;font-size:0.6em">— English</span></h1>

**Factum FIC** is an open-core CLI/daemon that automatically registers invoices on
[Fatture in Cloud v2](https://www.fattureincloud.it/), Italy's leading accounting platform.

### Key features

| Feature | Description |
|---|---|
| **SDI/XML parser** | Deterministic local parsing of FatturaPA XML — **zero API calls, zero cost, zero hallucinations** |
| **PDF parser** | LLM-powered via [Factum Parse API](https://factum.pyragogy.org) — text-only, file never leaves your machine |
| **Smart reverse charge** | Follows official SDI standard (N6.x codes + foreign supplier), not simplistic IVA=0 |
| **Auto self-invoices** | TD17/TD18/TD19 generated automatically on Fatture in Cloud |
| **Currency conversion** | ECB rates (Frankfurter API) with 6-hour cache |
| **Partial failure recovery** | Expense saved even if self-invoice fails — retry with `riprova-autofatture` |
| **Deduplication** | SHA-256 + FIC pre-check + atomic SQLite lock |
| **Hotfolder watcher** | Real-time processing of `~/Downloads` (or any folder) |

### Quick start

```bash
# Install
pip install factum-fic

# Configure
factum-fic setup

# Drop an XML invoice
cp ~/Downloads/fattura.xml ./da_elaborare/

# Process
factum-fic elabora
# → Expense created on FIC
# → SDI self-invoice generated (TD17/18/19 if reverse charge)
# → File archived
```

### CLI commands

| Command | Description |
|---|---|
| `elabora` | Process all files in `da_elaborare/` |
| `status` | Dashboard — API health, queue stats, pending self-invoices |
| `history` | Processing history (last N records) |
| `verify` | Test API connectivity (Factum + FIC) |
| `setup` | Interactive configuration wizard |
| `watch` | Launch hotfolder watcher daemon |
| `riprova-autofatture` | Retry failed self-invoices |

### License

AGPL-3.0 — Open core. The Factum Parse API is a separate closed-source SaaS service.

---

<p align="center">
  <a href="https://github.com/FTG-003/factum-fic">GitHub</a> ·
  <a href="https://pyragogy.org">Pyragogy Solutions</a> ·
  <a href="docs/">Documentazione</a>
</p>