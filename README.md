# Factum → Fatture in Cloud (factum-fic)

[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

**Daemon CLI + folder watcher** per automatizzare la registrazione di fatture passive e ricevute (SaaS, cloud, domini) su **Fatture in Cloud v2** tramite **Factum Parse API** (Zero Data Retention).

Scarica il PDF → Factum lo analizza → FIC registra la spesa (o prepara la bozza di autofattura TD17/18/19). Zero click, zero trascrizione manuale.

## Funzionalità

- **Parsing automatico** di PDF/XML fatture tramite Factum Parse API
- **Registrazione spese** su Fatture in Cloud v2 (documento di acquisto)
- **Bozze autofattura** per fornitori esteri (TD17/18/19)
- **Riconoscimento valuta** (USD/GBP → EUR) e cambio automatico via FIC
- **Categorizzazione** per vendor (AWS, GitHub, Hetzner, etc.)
- **Coda offline** con deduplicazione per SHA-256
- **Watch mode** su cartella Download (hotfolder con watchdog)
- **Zero Data Retention**: i PDF vengono cancellati subito dopo il parsing

## Quickstart

```bash
# Installa
pip install factum-fic

# Configura
cp .env.example .env
# modifica FACTUM_API_KEY e FIC_* nel .env

# Avvia il watcher in background
factum-fic watch ~/Downloads
```

Oppure usa il comando one-shot:

```bash
factum-fic process path/to/fattura.pdf
```

## Documentazione

- [Connettore Fatture in Cloud — Proposta architetturale](https://docs.factum.pyragogy.org/integrations/fatture-in-cloud/)
- [Factum Parse API Reference](https://docs.factum.pyragogy.org/api-reference/endpoints/)
- [Fatture in Cloud v2 API](https://api.fattureincloud.it/v2/documentation)

## Licenza

MIT — vedi [LICENSE](LICENSE).