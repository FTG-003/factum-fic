# Contribuire a Factum FIC

Grazie per aver deciso di contribuire! 🎉

Factum FIC è un progetto **Open Core**. Il motore principale è gratuito e open
source (AGPL-3.0). Ogni contributo è benvenuto — codice, documentazione,
segnalazioni di bug, idee o traduzioni.

---

## 📋 Codice di Condotta

Questo progetto adotta un [Codice di Condotta](CODE_OF_CONDUCT.md).
Partecipando, ti impegni a rispettarlo. Segnala comportamenti inaccettabili
a **info@pyragogy.org**.

---

## 🚀 Primi passi

```bash
# Clona
git clone https://github.com/FTG-003/factum-fic.git
cd factum-fic

# Installa con uv (consigliato)
uv sync --all-extras --dev

# Avvia i test
uv run pytest -q

# Lint
uv run ruff check .

# Prova
uv run factum-fic --help
```

---

## 🧪 Prima di inviare una PR

1. **Una funzionalità per PR** — piccole modifiche mirate vengono revisionate prima.
2. **Test richiesti** — nuove funzionalità richiedono nuovi test; `pytest -q` deve passare.
3. **Ruff pulito** — esegui `ruff check .` prima di pushare; la CI lo impone.
4. **Niente segreti** — `.env` è gitignorato. Mai committare API key, token o password.
5. **Messaggi di commit** seguono [Conventional Commits](https://www.conventionalcommits.org/):
   - `feat:` — nuova funzionalità
   - `fix:` — correzione bug
   - `docs:` — solo documentazione
   - `refactor:` — modifica senza cambiamento funzionale
   - `test:` — nuovi test o aggiornamento
   - `chore:` — tooling, CI, dipendenze

---

## 📁 Struttura

```
factum-fic/
├── src/factum_fic/         # Codice sorgente
│   ├── cli/                # Comandi CLI (elabora, status, history, setup, verify)
│   ├── core/               # Logica principale (parser, pipeline, mapper, client)
│   ├── storage/            # Coda SQLite
│   └── watcher/            # Demone watchdog
├── tests/                  # Suite pytest (185+ test)
├── scripts/                # Script di utilità
├── .github/                # CI workflow, asset grafici
└── docs/                   # Documentazione
```

---

## 🔐 Sicurezza

Se scopri una vulnerabilità di sicurezza, **non aprire una issue pubblica**.
Scrivi a **info@pyragogy.org**. Vedi [SECURITY.md](SECURITY.md) per dettagli.

---

## 📖 Riferimenti

- [README.md](README.md) — panoramica del progetto
- [ROADMAP.md](ROADMAP.md) — funzionalità pianificate
- [LICENSE](LICENSE) — AGPL-3.0

---

*Fatto con ❤️ da Fabrizio Terzi e collaboratori.*