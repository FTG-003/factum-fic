# ⚡ Factum-FIC

> **Basta perdere 2 o 3 ore al mese con la burocrazia italiana.**
> Automatizza la registrazione delle fatture passive e le autofatture estere su **Fatture in Cloud (FIC)** in un solo comando.

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white" alt="Python 3.12+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/Licenza-AGPL--3.0-blue.svg?logo=gnu" alt="AGPL-3.0"></a>
  <a href="https://github.com/FTG-003/factum-fic/releases"><img src="https://img.shields.io/github/v/release/FTG-003/factum-fic?logo=git&logoColor=white" alt="Release"></a>
  <br>
  <a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/badge/linted%20da-ruff-7aa2f7?logo=ruff&logoColor=white" alt="Ruff"></a>
  <a href="https://img.shields.io/badge/XML%20SDI-100%25%20offline-73daca"><img src="https://img.shields.io/badge/XML%20SDI-100%25%20offline-73daca" alt="XML SDI offline"></a>
</p>

---

## 🎯 A cosa serve? (Spiegato semplice)

Se hai una **Partita IVA (in particolare in Regime Forfettario)** e acquisti strumenti digitali dall'estero (es. **GitHub, Hetzner, AWS, OpenAI, Google Workspace, Canva, Notion**), ogni mese ti tocca la solita trafila:

1. Scaricare le ricevute e fatture in PDF dai vari siti.
2. Capire quale codice contabile usare (**TD17 per Extra-UE**, **TD18 per beni Intra-UE**, **TD19 per servizi Intra-UE**).
3. Calcolare il Reverse Charge (inversione contabile) a mano.
4. Aprire **Fatture in Cloud (FIC)** e ricopiare a mano date, importi, cambi valuta e allegare i file uno per uno.

**Factum-FIC fa tutto questo al posto tuo in 3 secondi.**
Trascini i file dentro una cartella sul tuo computer, lanci un comando e trovi tutto già compilato e registrato su Fatture in Cloud.

---

## 🎁 Quanto costa? (Zero abbonamenti a sorpresa)

Trasparenza totale per chi fa impresa:

| Tipo Documento | Cosa fa | Costo |
|---|---|---|
| **Fatture XML Italiane** (Aruba, PEC, fornitori IT) | Legge i file `.xml` e registra le spese su Fatture in Cloud. | **100% GRATIS e Illimitato** (elaborazione locale sul tuo PC, zero chiamate esterne). |
| **Fatture PDF Estere** (Hetzner, AWS, OpenAI, Google, ecc.) | Converte il PDF non strutturato ed estrae il Reverse Charge corretto (TD17/TD18/TD19). | **10 PDF/MESE GRATIS per sempre** (Reset automatico il 1° di ogni mese). |
| **Ricarica Pacchetto 100 PDF** | Se superi i 10 PDF al mese, acquisti crediti senza scadenza. | **€ 9,90 una tantum** (Zero canoni mensili, zero vincoli). |

> 💡 **10 PDF al mese bastano?**
> Per oltre l'80% dei freelance e forfettari, 10 conversioni al mese coprono l'intero stack mensile (Hosting + Dominio + AI + SaaS vari). Se stai avviando la tua attività, **non spenderai mai un solo euro**.

---

## 🚀 Guida Passo-Passo per Iniziare (in 3 Minuti)

Non serve essere programmatori. Apri il terminale del tuo computer (Terminale su Mac/Linux, PowerShell su Windows) e segui questi passaggi:

### 1. Installazione Rapida

Installa lo strumento direttamente senza clonare il codice:

```bash
# Se usi pipx (consigliato per avere il comando sempre disponibile):
pipx install git+https://github.com/FTG-003/factum-fic

# Oppure se usi uv:
uv tool install git+https://github.com/FTG-003/factum-fic
```

Se sei uno sviluppatore e preferisci partire dai sorgenti:

```bash
git clone https://github.com/FTG-003/factum-fic
cd factum-fic
uv sync
```

### 2. Configurazione Iniziale (Solo la prima volta)

Lancia la procedura guidata:

```bash
factum-fic setup
```

La procedura ti guiderà in due passaggi:

1. **Token di Fatture in Cloud (FIC):**  
   Vai su *Fatture in Cloud > Impostazioni > Strumenti > API / Moduli e App > Personal Access Token* e genera un token abilitando i permessi di lettura/scrittura per Acquisti/Spese e Documenti Emessi.

2. **Attivazione Gratuita Factum Engine:**  
   Conferma con `Invio` (Sì) per collegare la tua Partita IVA e sbloccare istantaneamente i tuoi **10 PDF gratuiti al mese** (zero carte di credito richieste).

### 3. Come si usa tutti i mesi (Il Flusso Quotidiano)

Il programma crea automaticamente una cartella di lavoro sul tuo computer:

```
📂 factum-workspace/
├── 📥 da_elaborare/   ← METTI QUI I TUOI FILE (PDF o XML)
├── 📦 archiviate/     ← Qui finiscono i file registrati con successo
└── ⚠️ da_verificare/  ← Qui finiscono solo i file illeggibili o corrotti
```

**Passo 1 — Trascina i tuoi file:**  
Inserisci le fatture PDF dei tuoi fornitori esteri o gli XML dei fornitori italiani dentro `da_elaborare/`.

**Passo 2 — Lancia l'elaborazione:**

```bash
factum-fic elabora
```

**Fatto!** Apri il tuo account di Fatture in Cloud: troverai le uscite registrate, i cambi valuta applicati e le autofatture (TD17/TD18/TD19) pronte.

---

## 🛡️ Zero Rischi per il tuo Fisco (Trasparenza Totale)

- **Nessun invio "alla cieca" allo SDI:** Il tool registra le spese contabili e predispone le registrazioni su Fatture in Cloud. Sei sempre tu a mantenere la supervisione finale.

- **Privacy & GDPR:**
  - I file XML vengono elaborati **al 100% offline** sul tuo computer: nessun dato delle fatture italiane esce dal tuo PC.
  - I file PDF esteri vengono trasmessi solo come testo estratto via HTTPS per il parsing contabile e non vengono memorizzati a lungo termine né ceduti a terzi.
  - Per attivare il free tier inviamo solo **P.IVA e Company ID** per associare i crediti gratuiti: non chiediamo carte di credito né dati sensibili.

---

## 📋 Comandi Principali (Tutti in Italiano)

| Comando | Alias | Cosa fa |
|---|---|---|
| `factum-fic elabora` | `auto`, `sync` | Elabora tutti i file in `da_elaborare/` e li carica su FIC. |
| `factum-fic stato` | `status` | Mostra i crediti PDF residui, i file in coda e lo stato della connessione FIC. |
| `factum-fic ricarica` | `buy-credits` | Apre il link sicuro Lemon Squeezy per acquistare 100 crediti PDF aggiuntivi (€9,90). |
| `factum-fic watch` | `osserva` | Resta attivo in background ed elabora i file appena li trascini nella cartella. |

---

## 🤝 Supporto & Contributi

Hai trovato un formato PDF estero non riconosciuto o vuoi proporre un miglioramento?

- [Apri una Issue](https://github.com/FTG-003/factum-fic/issues) su GitHub.
- Consulta le linee guida di sviluppo in [`CONTRIBUTING.md`](CONTRIBUTING.md).

**Licenza:** [AGPL-3.0](LICENSE) — open source, copyleft.  
Sviluppato per semplificare la vita alle Partite IVA italiane.