# SECURITY.md

## Segnalare una vulnerabilità

Se scopri una vulnerabilità di sicurezza in **factum-fic**, **non aprire una issue pubblica**.

Invece, invia una segnalazione privata a **info@pyragogy.org**.

Faremo:
1. Conferma di ricezione entro **48 ore**.
2. Indagine e rilascio della correzione entro **14 giorni** (o comunicazione della tempistica).
3. Pubblicazione di un advisory di sicurezza su GitHub dopo il rilascio.

## Perimetro

**In perimetro:**
- Esecuzione di codice remoto tramite file XML/PDF manipolati
- Perdita di credenziali tramite log o messaggi di errore
- SQL injection nel database della coda
- Accesso non autorizzato all'API di Fatture in Cloud tramite gestione impropria del token

**Fuori perimetro:**
- Attacchi di ingegneria sociale al manutentore
- Accesso fisico alla macchina che esegue factum-fic
- Denial of Service tramite file di grandi dimensioni

## Safe Harbor

Non intraprenderemo azioni legali contro ricercatori che:
- Seguono questa politica di divulgazione
- Operano in buona fede per evitare violazioni della privacy e distruzione di dati
- Segnalano il problema privatamente prima della divulgazione pubblica

---

## Trattamento dei dati fiscali e GDPR

Factum FIC elabora **dati fiscali e fatture** che possono contenere dati personali
ai sensi del Regolamento Generale sulla Protezione dei Dati (GDPR UE 2016/679).
La seguente sezione descrive come vengono trattati i tuoi dati.

### XML FatturaPA (SDI) — elaborazione 100% locale

- **Nessun dato lascia la tua macchina.**
- Il parsing dei file XML FatturaPA è eseguito interamente in locale tramite
  `xml.etree.ElementTree` della libreria standard Python.
- Nessuna chiamata API, nessuna trasmissione in rete, nessun token LLM consumato.
- I file XML rimangono sulla tua macchina e vengono spostati nella directory
  `archiviate/` locale al termine dell'elaborazione.

### PDF fattura — testo cifrato, mai il file originale

- Solo il **testo estratto** (non il file PDF originale) viene trasmesso a
  Factum Parse API per il parsing tramite LLM.
- La trasmissione avviene su canale **HTTPS cifrato** (TLS 1.3).
- Il file PDF originale **non viene mai trasmesso né conservato** da Factum Parse.
- Factum Parse non conserva il testo inviato oltre il tempo necessario
  alla risposta (politica zero-retention).

### Fatture in Cloud — destinazione finale autorizzata

- I dati contabili estratti (fornitore, importi, IVA, data) vengono inviati
  a **Fatture in Cloud v2 API** esclusivamente per la registrazione contabile.
- L'utente autorizza esplicitamente questo trasferimento tramite **token
  personale** (API Key) generato dalla dashboard FIC.
- Factum FIC non ha accesso ai dati di Fatture in Cloud al di fuori
  delle operazioni richieste dall'utente.

### Responsabilità del titolare del trattamento

- Sei tu il **titolare del trattamento** dei tuoi dati fiscali.
- Factum FIC è uno **strumento** che opera esclusivamente sotto le tue istruzioni.
- I manutentori del progetto non hanno accesso ai tuoi dati, file o credenziali.
- Tutte le credenziali sono memorizzate in locale nel file `.env` (gitignorato).

### Raccomandazioni

1. **Non condividere il file `.env`** — contiene le tue API key.
2. **Verifica i log** — i log di factum-fic non contengono dati fiscali,
   solo metadati di elaborazione.
3. **Revoca il token FIC** dalla dashboard FIC se smetti di usare factum-fic.

### Attivazione della Factum API Key

Alla prima configurazione, Factum FIC invia al backend Factum Parse
unicamente i seguenti dati:

- **Partita IVA** dell'azienda configurata
- **Company ID** dell'azienda su Fatture in Cloud

Questi dati vengono trasmessi esclusivamente per associare in modo univoco
il pool gratuito di **10 crediti mensili per Partita IVA** e prevenire abusi
(es. email usa-e-getta per rigenerare chiavi).

**Nessun altro dato aziendale** (fatture, nominativi clienti, importi, conti
correnti) viene trasmesso in questa fase.

Per domande sulla privacy, scrivi a **info@pyragogy.org**.