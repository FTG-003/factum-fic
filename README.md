<div align="center">
  <img src=".github/assets/logo.png" alt="Factum FIC Logo" width="140" />
  <h1>Factum FIC</h1>
  <p>Automazione contabile deterministica e sincronizzazione con Fatture in Cloud</p>
</div>

---

**factum-fic** è un tool daemon/CLI che prende le tue fatture (PDF o XML FatturaPA SDI),
le pars determiniticamente (senza AI per gli XML, con AI via Factum Parse per i PDF),
e le registra automaticamente su **Fatture in Cloud v2** — spese e autofatture
(TD17/TD18/TD19) incluse.

Nato per chi ha **Partita IVA in Regime Forfettario** e vuole automatizzare
la contabilità senza abbonamenti costosi né configurazioni complesse.

---

## 🖥️ Demo

```text
$ factum-fic elabora

📂 Elaborazione file in /home/utente/fatture/da_elaborare/

  ✅ Hetzner_2026-07.pdf       → Spesa registrata su FIC (id=430594826)
  ✅ Hetzner_2026-07.xml       → SDI XML parsed (locale, zero LLM)
                               → Spesa registrata su FIC (id=430594827)
                               → Autofattura TD17 generata (id=430594828)

📊 Riepilogo: 2 elaborate, 1 autofattura, 0 errori
```

---

## ✨ Caratteristiche

| Funzionalità | Dettaglio |
|---|---|
| 🏛️ **Parser XML SDI deterministico** | Parsing locale dei file XML FatturaPA v1.2. Zero chiamate API, zero token LLM, zero allucinazioni |
| 🧠 **Parser PDF via IA** | Estrazione testo con Factum Parse API per fatture PDF non strutturate |
| 🇪🇺 **Reverse charge intelligente** | Rilevamento corretto: N6.x + fornitore estero = RC. N2.2/N4 = NO RC (anche con IVA=0) |
| 🌍 **Conversione valuta BCE** | Tassi di cambio Frankfurter (BCE) con cache 6h. USD/GBP → EUR automatico |
| ☁️ **Fatture in Cloud v2** | Spese, autofatture TD17/18/19, allegati PDF, pagamento automatico |
| 🔒 **Zero data retention** | Factum Parse riceve solo il testo, mai i file. I tuoi dati restano tuoi |
| 🔄 **Deduplicazione SHA-256** | Stesso file → saltato. Pre-check su FIC → nessun doppione |
| 📁 **Archiviazione automatica** | Albero `archiviate/YYYY/MM/NomeFornitore_NumeroFattura.pdf` |
| 👀 **Watcher hotfolder** | Monitora una cartella (es. `~/Downloads`) ed elabora in tempo reale |
| ⚙️ **Setup guidato** | `factum-fic setup` — configurazione interattiva con validazione chiavi API |

---

## 🚀 Esempio veloce

```bash
# 1. Copia un XML FatturaPA in da_elaborare/
cp ~/Downloads/fattura_12345.xml ./da_elaborare/

# 2. Elabora
factum-fic elabora

# 3. Risultato: spesa + autofattura su FIC, XML archiviato
#    ./archiviate/2026/08/2026-08-17_Hetzner-Online-GmbH_4AUT.xml
```

---

## 📦 Installazione

```bash
# Con uv (consigliato)
uv tool install factum-fic

# Oppure con pip
pip install factum-fic

# Verifica
factum-fic --help
```

### Configurazione rapida

```bash
# Avvia il setup interattivo
factum-fic setup

# Il wizard ti guiderà a configurare:
# 1. FACTUM_API_KEY  → dal tuo account Factum Parse
# 2. FIC_API_KEY     → dal tuo account Fatture in Cloud (Personal Access Token)
# 3. FIC_COMPANY_ID  → dalla dashboard Fatture in Cloud
```

### Variabili d'ambiente

Vedi [`.env.example`](.env.example) per la lista completa delle variabili.

---

## 🧭 Guida rapida

```bash
# Elabora tutti i file in da_elaborare/
factum-fic elabora

# Elabora forzando anche già processati
factum-fic elabora --force

# Dashboard stato
factum-fic status

# Cronologia ultime 10 elaborazioni
factum-fic history

# Verifica connettività API
factum-fic verify

# Setup guidato (prima configurazione)
factum-fic setup

# Avvia watcher hotfolder (monitora ~/Downloads in tempo reale)
factum-fic watch
```

### Ciclo di vita dei file

```
da_elaborare/
├── fattura_fornitore_12345.xml   ← XML FatturaPA SDI (parsing locale, zero LLM)
├── fattura_fornitore_2026-07.pdf ← PDF fattura (parsing via Factum Parse API)
├── scontrino.pdf                 ← Altri PDF
│
├── ✅ Successo → archiviate/YYYY/MM/Fornitore_Numero.pdf
├── ⚠️ Duplicato → archiviate/YYYY/MM/Fornitore_Numero.pdf (stessa SHA-256)
└── ❌ Errore   → da_verificare/nomefile.pdf (esame manuale)
```

---

## 🏗️ Architettura

```
                    ┌──────────────┐
                    │  SDI XML     │
                    │  (FatturaPA) │
                    └──────┬───────┘
                           │ parse_sdi_xml()    ← Deterministico, 0 token
                           ▼
                    ┌──────────────┐     ┌──────────────────┐
                    │  PDF fattura │────→│  Factum Parse    │
                    │  (testo)     │     │  API (LLM)       │
                    └──────────────┘     └────────┬─────────┘
                                                  │
                    ┌──────────────┐              │
                    │  Regex       │←──fallback───┘
                    │  amounts     │
                    └──────┬───────┘
                           ▼
                    ┌──────────────┐     ┌──────────────────┐
                    │    Mapper    │────→│  Fatture in Cloud│
                    │  TD17/18/19  │     │  v2 API          │
                    └──────────────┘     └──────────────────┘
                           │                      │
                           │              ┌───────┴───────┐
                           │              │  Expense       │
                           │              │  + Self-invoice│
                           │              └───────────────┘
                           ▼
                    ┌──────────────┐
                    │  Archiver    │
                    │  YYYY/MM/    │
                    └──────────────┘
```

## 🔬 Reverse charge: logica SDI

Nello standard FatturaPA, **IVA = 0 non è sufficiente** per determinare il reverse charge.
La logica corretta è:

| Scenario | Paese | IVA | Natura SDI | RC? |
|---|---|---|---|---|
| Fornitore IT, forfettario | IT | 0,00 | **N2.2** | ❌ No |
| Fornitore IT, esente art.10 | IT | 0,00 | **N4** | ❌ No |
| Fornitore IT, inversione contabile | IT | 0,00 | **N6.x** | ✅ Sì |
| Fornitore DE, IVA applicata | DE | 3,45 | assente | ❌ No |
| Fornitore DE, IVA zero | DE | 0,00 | assente | ✅ Sì |
| Fornitore USA, IVA zero | US | 0,00 | assente | ✅ Sì |

---

## 🧪 Sviluppo

```bash
git clone https://github.com/pyragogy/factum-fic.git
cd factum-fic
uv sync --all-extras --dev
uv run pytest -q
uv run ruff check .
```

---

## 📄 Documentazione

La documentazione completa è nella cartella [`docs/`](docs/):

- [Guida introduttiva](docs/getting-started.md)
- [Configurazione](docs/configuration.md)
- [Parser XML SDI](docs/xml-parser.md)
- [Reverse charge](docs/reverse-charge.md)
- [FAQ](docs/faq.md)

---

## 🤝 Contribuire

Le contribuzioni sono benvenute! Vedi [CONTRIBUTING.md](CONTRIBUTING.md) per le linee guida.

---

## 📜 Licenza

**AGPL-3.0** — Questo software è open source e copyleft.
Chiunque lo modifichi e lo distribuisca via rete (es. SaaS) deve rilasciare
il codice sorgente delle modifiche.

Le API Factum Parse (https://factum.pyragogy.org) sono un servizio separato
e non coperte da questa licenza.

---

<div align="center">
  <sub>Fatto con ❤️ da <a href="https://pyragogy.org">Fabrizio Terzi</a> — Pyragogy Solutions</sub>
  <br>
  <sub>Italiano · <a href="#english">English</a></sub>
</div>

---

<a name="english"></a>

## English

**factum-fic** is an open-core CLI/daemon that automatically registers invoices on
[Fatture in Cloud v2](https://www.fattureincloud.it/) — Italy's leading accounting platform.

- **SDI XML invoices** are parsed locally and deterministically (zero LLM calls, zero tokens)
- **PDF invoices** are parsed via [Factum Parse API](https://factum.pyragogy.org) (LLM-powered)
- Expenses and self-invoices (TD17/TD18/TD19) are created automatically on Fatture in Cloud
- Reverse charge detection follows the official SDI standard (N6.x codes + foreign supplier check)
- Currency conversion via BCE (Frankfurter API) with 6-hour cache
- Zero data retention: Factum Parse receives text only, never stores your invoices

```bash
# Quick start
cp invoice.xml ./da_elaborare/
factum-fic elabora
# → Expense created on FIC, SDI self-invoice generated, file archived
```

---

<p align="center">
  <a href="https://pyragogy.org">Pyragogy Solutions</a> ·
  <a href="https://factum.pyragogy.org">Factum Parse</a> ·
  <a href="https://github.com/pyragogy/factum-fic">GitHub</a>
</p>