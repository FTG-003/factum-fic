"""Coda SQLite per deduplicazione SHA-256 e retry.

Mantiene traccia dei file già processati per evitare duplicati,
anche dopo riavvii del daemon.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_QUEUE_DB = Path.home() / ".factum-fic" / "queue.db"


class QueueStore:
    """Coda persistente basata su SQLite.

    Schema:
        - sha256 (PRIMARY KEY): hash del file
        - path: percorso originale del file
        - status: queued | processing | completed | failed
        - fic_id: ID del documento su FIC (se completato)
        - created_at: timestamp inserimento
        - updated_at: timestamp ultimo aggiornamento
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or _QUEUE_DB
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS queue (
                sha256 TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                fic_id INTEGER,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self._conn.commit()

    def exists(self, sha256: str) -> bool:
        """Verifica se un file (per hash) è già stato processato con successo.

        Solo gli item con status='completed' contano: i tentativi falliti
        (queued/failed) NON bloccano un nuovo tentativo.
        """
        cur = self._conn.execute(
            "SELECT 1 FROM queue WHERE sha256 = ? AND status = 'completed'",
            (sha256,),
        )
        return cur.fetchone() is not None

    def enqueue(self, sha256: str, path: str) -> None:
        """Inserisce un file in coda (idempotente).

        Deprecato: la coda ora registra solo i file completati.
        Mantenuto per compatibilità con test esistenti.
        """
        self._conn.execute(
            "INSERT OR IGNORE INTO queue (sha256, path, status) VALUES (?, ?, 'queued')",
            (sha256, path),
        )
        self._conn.commit()

    def complete(self, sha256: str, fic_id: int | None = None) -> None:
        """Registra un file come COMPLETATO (solo dopo successo FIC).

        Inserisce o aggiorna la riga con status='completed'.
        """
        self._conn.execute(
            "INSERT INTO queue (sha256, path, status, fic_id, created_at, updated_at) "
            "VALUES (?, ?, 'completed', ?, datetime('now'), datetime('now')) "
            "ON CONFLICT(sha256) DO UPDATE SET "
            "status='completed', fic_id=excluded.fic_id, updated_at=datetime('now')",
            (sha256, "", fic_id),
        )
        self._conn.commit()

    def mark_failed(self, sha256: str) -> None:
        """Marca un item come fallito (e lo rende riprocessabile)."""
        self._conn.execute(
            "UPDATE queue SET status = 'failed', updated_at = datetime('now') "
            "WHERE sha256 = ?",
            (sha256,),
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
            "SELECT sha256, path FROM queue WHERE status IN ('queued', 'failed')",
        )
        return cur.fetchall()

    def stats(self) -> dict[str, int]:
        """Restituisce statistiche coda."""
        cur = self._conn.execute(
            "SELECT status, COUNT(*) FROM queue GROUP BY status",
        )
        return dict(cur.fetchall())

    def close(self) -> None:
        self._conn.close()
