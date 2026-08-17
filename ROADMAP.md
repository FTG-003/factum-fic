# ROADMAP

> Visione e priorità di sviluppo per Factum FIC.
> *Le date sono indicative e soggette a cambiamento.*

---

## ✅ Completato (v0.2.0)

- [x] Parser XML/SDI deterministico (locale, zero LLM, zero costi)
- [x] Reverse charge intelligente (N6.x + fornitore estero)
- [x] Classificazione autofatture TD17/TD18/TD19
- [x] Pipeline di registrazione su Fatture in Cloud v2
- [x] CLI con alias italiani (elabora, stato, storico, configura, auto)
- [x] Recupero parziale SELF_INVOICE_PENDING
- [x] Lock atomico SQLite (`acquire()`)
- [x] Conversione valuta BCE con cache 6h
- [x] Hotfolder watcher (watchdog)
- [x] Setup guidato interattivo
- [x] 185+ test, CI con mock isolation

---

## 🔜 In arrivo (v0.3.0)

- [ ] **Parser PDF offline** — OCR deterministico via marker-pdf/tesseract per PDF senza Factum Parse API
- [ ] **Multi-azienda** — supporto per più compagnie FIC in configurazione
- [ ] **Report mensile** — esportazione CSV/PDF delle spese elaborate
- [ ] **Notifiche desktop** — toast notification al termine dell'elaborazione
- [ ] **Drag & drop GUI** — interfaccia web minima (TUI + webview)
- [ ] **GitHub Actions self-hosted** — supporto per GHA runner locale

---

## 🔭 Visione (v1.0.0)

- [ ] **Plugin system** — hooks pre/post elaborazione personalizzabili
- [ ] **Web dashboard** — stato, storico, retry da interfaccia browser
- [ ] **API REST** — esporre endpoint per integrazione con altri tool
- [ ] **Multi-utente** — supporto per studi commercialisti
- [ ] **Supporto FatturaPA in uscita** — generazione XML FatturaPA per autofatture

---

## 💡 Proposte (da valutare)

- [ ] Integrazione con Stripe / PayPal per riconciliazione pagamenti
- [ ] Supporto PEC per invio diretto al SDI
- [ ] Template personalizzabili per categorie FIC
- [ ] Integrazione con Exchange Online / Gmail per estrazione allegati
- [ ] App mobile per scansione fatture

---

*Hai un'idea? Apri una [discussion](https://github.com/FTG-003/factum-fic/discussions).*