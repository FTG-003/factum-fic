"""Coda SQLite per deduplicazione SHA-256, retry e tracciamento dual-ID.

Mantiene traccia dei file già processati per evitare duplicati, anche dopo
riavvii del daemon. Oltre al SHA-256 registra i due ID FIC collegati a ogni
fattura elaborata:

- ``fic_expense_id``: ID del documento di spesa creato su FIC
- ``fic_self_invoice_id``: ID dell'autofattura SDI (TD17/TD18/TD19) generata
  per acquisti esteri (art. 17 c. 2 DPR 633/72), se applicabile

Schema (v2):

    - sha256 TEXT PRIMARY KEY: hash del file
    - file_path TEXT: percorso originale del file
    - status TEXT: queued | processing | completed | failed | SELF_INVOICE_PENDING
    - fic_expense_id INTEGER: ID spesa su FIC (se completato)
    - fic_self_invoice_id INTEGER NULL: ID autofattura SDI su FIC
    - processed_at TEXT: timestamp completamento
    - error_message TEXT NULL: ultimo errore (per status=failed o SELF_INVOICE_PENDING)
    - created_at / updated_at: timestamp di audit

La migrazione dallo schema legacy (colonne ``path`` e ``fic_id``) avviene
automaticamente all'apertura, preservando la cronologia esistente.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# Stati non-terminali: un file in questi stati PUÒ essere rielaborato
# al ciclo successivo (bypassa la deduplicazione SHA-256).
_NON_TERMINAL_STATES = {"queued", "processing", "QUOTA_EXCEEDED", "NETWORK_DELAY"}

# Stati terminali: un file in questi stati NON viene rielaborato.
_TERMINAL_STATES = {"completed", "failed", "SELF_INVOICE_PENDING", "AUTH_ERROR"}

_QUEUE_DB = Path.home() / ".factum-fic" / "queue.db"


class QueueStore:
    """Coda persistente basata su SQLite con tracciamento dual-ID."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or _QUEUE_DB
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()

    # ── Schema & migrazioni ──────────────────────────────────────────────

    def _ensure_schema(self) -> None:
        """Crea la tabella v2 e migra eventuali DB legacy (colonne path/fic_id)."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS queue (
                sha256 TEXT PRIMARY KEY,
                file_path TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'queued',
                fic_expense_id INTEGER,
                fic_self_invoice_id INTEGER,
                processed_at TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)

        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(queue)")}

        # Colonne mancanti su DB legacy → ALTER TABLE ADD COLUMN
        if "file_path" not in existing:
            self._conn.execute(
                "ALTER TABLE queue ADD COLUMN file_path TEXT NOT NULL DEFAULT ''"
            )
        if "fic_expense_id" not in existing:
            self._conn.execute("ALTER TABLE queue ADD COLUMN fic_expense_id INTEGER")
        if "fic_self_invoice_id" not in existing:
            self._conn.execute("ALTER TABLE queue ADD COLUMN fic_self_invoice_id INTEGER")
        if "processed_at" not in existing:
            self._conn.execute("ALTER TABLE queue ADD COLUMN processed_at TEXT")
        if "error_message" not in existing:
            self._conn.execute("ALTER TABLE queue ADD COLUMN error_message TEXT")

        # Migrazione v1 → v2: copia dati legacy (path → file_path, fic_id → fic_expense_id)
        if "path" in existing:
            self._conn.execute(
                "UPDATE queue SET file_path = path "
                "WHERE file_path = '' AND path != ''"
            )
            self._conn.execute("ALTER TABLE queue DROP COLUMN path")
        if "fic_id" in existing:
            self._conn.execute(
                "UPDATE queue SET fic_expense_id = fic_id "
                "WHERE fic_expense_id IS NULL AND fic_id IS NOT NULL"
            )

        self._conn.commit()

    # ── Lock atomico (concorrenza) ────────────────────────────────────────

    def acquire(self, sha256: str) -> bool:
        """Transizione atomica allo stato 'processing'.

        Imposta ``status = 'processing'`` SOLO se l'item non è già
        in stato 'processing' o 'completed'. Previene doppie elaborazioni
        concorrenti dello stesso file.

        Args:
            sha256: Hash del file da acquisire.

        Returns:
            True se l'acquisizione è riuscita, False se già in corso o
            già completato.
        """
        cur = self._conn.execute(
            "UPDATE queue SET status = 'processing', updated_at = datetime('now') "
            "WHERE sha256 = ? AND status NOT IN ('processing', 'completed')",
            (sha256,),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # ── Gestione SELF_INVOICE_PENDING ─────────────────────────────────────

    def mark_self_invoice_pending(
        self,
        sha256: str,
        expense_id: int,
        error_message: str | None = None,
        *,
        path: str | None = None,
    ) -> None:
        """Marca un item come SELF_INVOICE_PENDING.

        Usato quando la spesa FIC è stata creata con successo ma la
        generazione dell'autofattura SDI (TD17/18/19) è fallita.
        Il record conserva l'``expense_id`` per consentire il retry
        tramite ``riprova-autofatture``.

        Args:
            sha256: Hash del file.
            expense_id: ID della spesa già creata su FIC.
            error_message: Messaggio di errore dell'autofattura fallita.
            path: Percorso originale del file.
        """
        self._conn.execute(
            "INSERT INTO queue (sha256, file_path, status, fic_expense_id, "
            "error_message, created_at, updated_at) "
            "VALUES (?, ?, 'SELF_INVOICE_PENDING', ?, ?, "
            "datetime('now'), datetime('now')) "
            "ON CONFLICT(sha256) DO UPDATE SET "
            "status='SELF_INVOICE_PENDING', "
            "fic_expense_id=excluded.fic_expense_id, "
            "error_message=excluded.error_message, "
            "updated_at=datetime('now')",
            (sha256, path or "", expense_id, error_message),
        )
        self._conn.commit()

    def get_pending_self_invoices(self) -> list[dict]:
        """Restituisce tutti i record in stato SELF_INVOICE_PENDING.

        Returns:
            Lista di dizionari con chiavi: sha256, file_path,
            fic_expense_id, error_message.
        """
        cur = self._conn.execute(
            "SELECT sha256, file_path, fic_expense_id, error_message "
            "FROM queue WHERE status = 'SELF_INVOICE_PENDING'",
        )
        return [
            {
                "sha256": row[0],
                "file_path": row[1],
                "fic_expense_id": row[2],
                "error_message": row[3],
            }
            for row in cur.fetchall()
        ]

    # ── Gestione stati non-terminali (retry automatico) ──────────────────

    def mark_auth_error(
        self,
        sha256: str,
        error_message: str | None = None,
        *,
        path: str | None = None,
    ) -> None:
        """Marca un item come AUTH_ERROR (credenziali Factum non valide).

        Stato terminale: il file NON viene rielaborato automaticamente.
        L'utente deve aggiornare la API key e rimuovere manualmente
        il file da ``da_verificare/``.
        """
        self._conn.execute(
            "INSERT INTO queue (sha256, file_path, status, error_message, "
            "created_at, updated_at) "
            "VALUES (?, ?, 'AUTH_ERROR', ?, "
            "datetime('now'), datetime('now')) "
            "ON CONFLICT(sha256) DO UPDATE SET "
            "status='AUTH_ERROR', "
            "error_message=excluded.error_message, "
            "updated_at=datetime('now')",
            (sha256, path or "", error_message),
        )
        self._conn.commit()

    def mark_quota_exceeded(
        self,
        sha256: str,
        error_message: str | None = None,
        *,
        path: str | None = None,
    ) -> None:
        """Marca un item come QUOTA_EXCEEDED (crediti Factum esauriti).

        Stato non-terminale: il file PUÒ essere rielaborato al ciclo
        successivo (bypassa la deduplicazione SHA-256). Il file resta
        in ``da_elaborare/``.
        """
        self._conn.execute(
            "INSERT INTO queue (sha256, file_path, status, error_message, "
            "created_at, updated_at) "
            "VALUES (?, ?, 'QUOTA_EXCEEDED', ?, "
            "datetime('now'), datetime('now')) "
            "ON CONFLICT(sha256) DO UPDATE SET "
            "status='QUOTA_EXCEEDED', "
            "error_message=excluded.error_message, "
            "updated_at=datetime('now')",
            (sha256, path or "", error_message),
        )
        self._conn.commit()

    def mark_network_delay(
        self,
        sha256: str,
        error_message: str | None = None,
        *,
        path: str | None = None,
    ) -> None:
        """Marca un item come NETWORK_DELAY (errore transitorio di rete).

        Stato non-terminale: il file PUÒ essere rielaborato al ciclo
        successivo (bypassa la deduplicazione SHA-256). Il file resta
        in ``da_elaborare/``.
        """
        self._conn.execute(
            "INSERT INTO queue (sha256, file_path, status, error_message, "
            "created_at, updated_at) "
            "VALUES (?, ?, 'NETWORK_DELAY', ?, "
            "datetime('now'), datetime('now')) "
            "ON CONFLICT(sha256) DO UPDATE SET "
            "status='NETWORK_DELAY', "
            "error_message=excluded.error_message, "
            "updated_at=datetime('now')",
            (sha256, path or "", error_message),
        )
        self._conn.commit()

    def should_retry(self, sha256: str) -> bool:
        """Verifica se un file può essere rielaborato.

        Restituisce True se il file NON è in uno stato terminale
        (completed, failed, SELF_INVOICE_PENDING, AUTH_ERROR) oppure
        se non esiste ancora nel database.

        Questo metodo sostituisce ``exists()`` per la logica di
        deduplicazione SHA-256: i file in stato non-terminale
        (QUOTA_EXCEEDED, NETWORK_DELAY, queued, processing) NON
        bloccano la rielaborazione.

        Args:
            sha256: Hash SHA-256 del file.

        Returns:
            True se il file può essere rielaborato, False se è in
            uno stato terminale e non va toccato.
        """
        cur = self._conn.execute(
            "SELECT status FROM queue WHERE sha256 = ?",
            (sha256,),
        )
        row = cur.fetchone()
        if row is None:
            return True  # non esiste → può essere elaborato
        status = row[0]
        return status in _NON_TERMINAL_STATES

    # ── Query base ──────────────────────────────────────────────────────

    def exists(self, sha256: str) -> bool:
        """Verifica se un file (per hash) è già stato processato con successo.

        Solo gli item con status='completed' contano: i tentativi falliti
        e gli stati non-terminali NON bloccano un nuovo tentativo.

        Nota: per la logica di deduplicazione SHA-256 che considera anche
        gli stati non-terminali, usare ``should_retry()``.
        """
        cur = self._conn.execute(
            "SELECT 1 FROM queue WHERE sha256 = ? AND status = 'completed'",
            (sha256,),
        )
        return cur.fetchone() is not None

    def get(self, sha256: str) -> dict | None:
        """Restituisce il record completo per un hash, o None."""
        cur = self._conn.execute(
            "SELECT sha256, file_path, status, fic_expense_id, "
            "fic_self_invoice_id, processed_at, error_message "
            "FROM queue WHERE sha256 = ?",
            (sha256,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "sha256": row[0],
            "file_path": row[1],
            "status": row[2],
            "fic_expense_id": row[3],
            "fic_self_invoice_id": row[4],
            "processed_at": row[5],
            "error_message": row[6],
        }

    def enqueue(self, sha256: str, path: str) -> None:
        """Inserisce un file in coda (idempotente).

        Deprecato: la coda ora registra solo i file completati.
        Mantenuto per compatibilità con test esistenti.
        """
        self._conn.execute(
            "INSERT OR IGNORE INTO queue (sha256, file_path, status) "
            "VALUES (?, ?, 'queued')",
            (sha256, path),
        )
        self._conn.commit()

    def complete(
        self,
        sha256: str,
        expense_id: int | None = None,
        self_invoice_id: int | None = None,
        *,
        path: str | None = None,
        status: str = "completed",
    ) -> None:
        """Registra un file come completato o aggiorna self_invoice_id.

        Se il record esiste già con status='SELF_INVOICE_PENDING' e viene
        chiamato con un ``self_invoice_id``, aggiorna solo il campo
        ``fic_self_invoice_id`` e passa a 'completed' preservando
        l'``expense_id`` già registrato.

        Args:
            sha256: Hash SHA-256 del file elaborato.
            expense_id: ID del documento di spesa creato su FIC.
            self_invoice_id: ID dell'autofattura SDI (TD17/18/19) generata.
            path: Percorso originale del file.
            status: Stato da impostare (default 'completed').
        """
        # Caso speciale: aggiornamento parziale dopo SELF_INVOICE_PENDING
        existing = self.get(sha256)
        if existing and existing["status"] == "SELF_INVOICE_PENDING":
            self._conn.execute(
                "UPDATE queue SET "
                "status = ?, "
                "fic_self_invoice_id = ?, "
                "processed_at = datetime('now'), "
                "error_message = NULL, "
                "updated_at = datetime('now') "
                "WHERE sha256 = ?",
                (status, self_invoice_id, sha256),
            )
            self._conn.commit()
            return

        self._conn.execute(
            "INSERT INTO queue (sha256, file_path, status, fic_expense_id, "
            "fic_self_invoice_id, processed_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'), "
            "datetime('now'), datetime('now')) "
            "ON CONFLICT(sha256) DO UPDATE SET "
            "status=excluded.status, "
            "fic_expense_id=excluded.fic_expense_id, "
            "fic_self_invoice_id=COALESCE(excluded.fic_self_invoice_id, "
            "fic_self_invoice_id), "
            "processed_at=datetime('now'), "
            "error_message=NULL, "
            "updated_at=datetime('now')",
            (sha256, path or "", status, expense_id, self_invoice_id),
        )
        self._conn.commit()

    def mark_failed(self, sha256: str, error_message: str | None = None) -> None:
        """Marca un item come fallito (e lo rende riprocessabile).

        Inserisce la riga se non esiste ancora (es. fallimento prima della
        registrazione in coda), altrimenti ne aggiorna lo stato e il
        messaggio di errore.

        Args:
            sha256: Hash del file fallito.
            error_message: Messaggio di errore da conservare per il dashboard.
        """
        self._conn.execute(
            "INSERT INTO queue (sha256, status, error_message, created_at, updated_at) "
            "VALUES (?, 'failed', ?, datetime('now'), datetime('now')) "
            "ON CONFLICT(sha256) DO UPDATE SET "
            "status='failed', error_message=excluded.error_message, "
            "updated_at=datetime('now')",
            (sha256, error_message),
        )
        self._conn.commit()

    def remove(self, sha256: str) -> None:
        """Rimuove completamente un item dalla coda (per retry pulito)."""
        self._conn.execute(
            "DELETE FROM queue WHERE sha256 = ?",
            (sha256,),
        )
        self._conn.commit()

    def reset(self) -> None:
        """Svuota completamente la tabella coda."""
        self._conn.execute("DELETE FROM queue")
        self._conn.commit()

    def pending(self) -> list[tuple[str, str]]:
        """Restituisce gli item in attesa di processare."""
        cur = self._conn.execute(
            "SELECT sha256, file_path FROM queue WHERE status IN ('queued', 'failed')",
        )
        return cur.fetchall()

    def stats(self) -> dict[str, int]:
        """Restituisce statistiche coda raggruppate per stato."""
        cur = self._conn.execute(
            "SELECT status, COUNT(*) FROM queue GROUP BY status",
        )
        return dict(cur.fetchall())

    # ── Statistiche operative (dashboard status) ─────────────────────────

    def summary(self) -> dict[str, int]:
        """Riepilogo operativo per il comando ``factum-fic status``.

        Returns:
            Dizionario con:
            - processed: totale fatture elaborate con successo
            - expenses: spese registrate su FIC (fic_expense_id non null)
            - self_invoices: autofatture SDI generate (fic_self_invoice_id non null)
            - errors: item falliti in coda
            - queued: item in attesa
            - pending_si: item in attesa retry autofattura
            - auth_errors: item bloccati per credenziali non valide
            - quota_exceeded: item in attesa ripristino crediti
            - network_delays: item in attesa di rete/server
        """
        def _count(sql: str) -> int:
            row = self._conn.execute(sql).fetchone()
            return int(row[0]) if row and row[0] is not None else 0

        return {
            "processed": _count("SELECT COUNT(*) FROM queue WHERE status = 'completed'"),
            "expenses": _count(
                "SELECT COUNT(*) FROM queue WHERE fic_expense_id IS NOT NULL"
            ),
            "self_invoices": _count(
                "SELECT COUNT(*) FROM queue WHERE fic_self_invoice_id IS NOT NULL"
            ),
            "errors": _count("SELECT COUNT(*) FROM queue WHERE status = 'failed'"),
            "queued": _count("SELECT COUNT(*) FROM queue WHERE status = 'queued'"),
            "pending_si": _count(
                "SELECT COUNT(*) FROM queue WHERE status = 'SELF_INVOICE_PENDING'"
            ),
            "auth_errors": _count(
                "SELECT COUNT(*) FROM queue WHERE status = 'AUTH_ERROR'"
            ),
            "quota_exceeded": _count(
                "SELECT COUNT(*) FROM queue WHERE status = 'QUOTA_EXCEEDED'"
            ),
            "network_delays": _count(
                "SELECT COUNT(*) FROM queue WHERE status = 'NETWORK_DELAY'"
            ),
        }

    # ── Cronologia (history) ─────────────────────────────────────────────

    def recent(self, limit: int = 10) -> list[dict]:
        """Restituisce gli ultimi N record elaborati ordinati per data decrescente.

        Args:
            limit: Numero massimo di record da restituire (default 10).

        Returns:
            Lista di dizionari con chiavi: sha256, file_path, status,
            fic_expense_id, fic_self_invoice_id, processed_at, error_message.
        """
        cur = self._conn.execute(
            "SELECT sha256, file_path, status, fic_expense_id, "
            "fic_self_invoice_id, processed_at, error_message "
            "FROM queue "
            "ORDER BY COALESCE(processed_at, updated_at, created_at) DESC "
            "LIMIT ?",
            (limit,),
        )
        return [
            {
                "sha256": row[0],
                "file_path": row[1],
                "status": row[2],
                "fic_expense_id": row[3],
                "fic_self_invoice_id": row[4],
                "processed_at": row[5],
                "error_message": row[6],
            }
            for row in cur.fetchall()
        ]

    def close(self) -> None:
        self._conn.close()
