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
        """Verifica se un file (per hash) è già stato processato."""
        cur = self._conn.execute(
            "SELECT 1 FROM queue WHERE sha256 = ?",
            (sha256,),
        )
        return cur.fetchone() is not None

    def enqueue(self, sha256: str, path: str) -> None:
        """Inserisce un file in coda (idempotente)."""
        self._conn.execute(
            "INSERT OR IGNORE INTO queue (sha256, path, status) VALUES (?, ?, 'queued')",
            (sha256, path),
        )
        self._conn.commit()

    def complete(self, sha256: str, fic_id: int | None = None) -> None:
        """Marca un item come completato."""
        self._conn.execute(
            "UPDATE queue SET status = 'completed', fic_id = ?, updated_at = datetime('now') "
            "WHERE sha256 = ?",
            (fic_id, sha256),
        )
        self._conn.commit()

    def mark_failed(self, sha256: str) -> None:
        """Marca un item come fallito."""
        self._conn.execute(
            "UPDATE queue SET status = 'failed', updated_at = datetime('now') "
            "WHERE sha256 = ?",
            (sha256,),
        )
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
