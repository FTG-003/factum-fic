"""Test del QueueStore SQLite: schema dual-ID, summary, migrazione legacy."""

from __future__ import annotations

from pathlib import Path

import pytest

from factum_fic.storage.queue import QueueStore


@pytest.fixture
def queue(tmp_path: Path) -> QueueStore:
    """QueueStore con DB temporaneo."""
    return QueueStore(db_path=tmp_path / "test_queue.db")


# ── Test base ─────────────────────────────────────────────────────────────────


def test_enqueue_and_exists(queue: QueueStore) -> None:
    """enqueue + exists + complete."""
    sha = "a" * 64
    path = "/tmp/test.pdf"
    queue.enqueue(sha, path)
    # Non ancora completato → exists=False
    assert queue.exists(sha) is False
    # Completa con expense_id
    queue.complete(sha, expense_id=123, path=path)
    assert queue.exists(sha) is True


def test_complete_dual_id(queue: QueueStore) -> None:
    """complete con expense_id e self_invoice_id."""
    sha = "b" * 64
    queue.complete(sha, expense_id=100, self_invoice_id=200, path="/tmp/invoice.pdf")
    assert queue.exists(sha) is True

    record = queue.get(sha)
    assert record is not None
    assert record["fic_expense_id"] == 100
    assert record["fic_self_invoice_id"] == 200
    assert record["status"] == "completed"


def test_complete_self_invoice_none(queue: QueueStore) -> None:
    """Spesa italiana senza autofattura: self_invoice_id=None."""
    sha = "c" * 64
    queue.complete(sha, expense_id=101, self_invoice_id=None, path="/tmp/italian.pdf")
    record = queue.get(sha)
    assert record is not None
    assert record["fic_expense_id"] == 101
    assert record["fic_self_invoice_id"] is None


def test_complete_idempotent(queue: QueueStore) -> None:
    """Chiamate multiple a complete con ID diversi: l'ultimo vince."""
    sha = "d" * 64
    queue.complete(sha, expense_id=1, self_invoice_id=None, path="/tmp/doc1.pdf")
    queue.complete(sha, expense_id=2, self_invoice_id=3, path="/tmp/doc2.pdf")
    record = queue.get(sha)
    assert record["fic_expense_id"] == 2
    assert record["fic_self_invoice_id"] == 3


def test_exists_only_completed(queue: QueueStore) -> None:
    """exists() restituisce True solo per status='completed'."""
    sha = "e" * 64
    queue.enqueue(sha, "/tmp/test.pdf")
    assert queue.exists(sha) is False  # queued, non completato
    queue.complete(sha, expense_id=5)
    assert queue.exists(sha) is True


def test_mark_failed(queue: QueueStore) -> None:
    """mark_failed con error_message."""
    sha = "f" * 64
    queue.enqueue(sha, "/tmp/fail.pdf")
    queue.mark_failed(sha, "Errore di test")
    record = queue.get(sha)
    assert record is not None
    assert record["status"] == "failed"
    assert record["error_message"] == "Errore di test"


def test_remove(queue: QueueStore) -> None:
    """remove elimina completamente il record."""
    sha = "g" * 64
    queue.complete(sha, expense_id=7)
    assert queue.exists(sha) is True
    queue.remove(sha)
    assert queue.exists(sha) is False
    assert queue.get(sha) is None


def test_reset(queue: QueueStore) -> None:
    """reset svuota la tabella."""
    queue.complete("h" * 64, expense_id=1)
    queue.complete("i" * 64, expense_id=2)
    queue.reset()
    assert queue.stats() == {}


# ── Test summary ──────────────────────────────────────────────────────────────


def test_summary_empty(queue: QueueStore) -> None:
    """summary su coda vuota."""
    s = queue.summary()
    assert s["processed"] == 0
    assert s["expenses"] == 0
    assert s["self_invoices"] == 0
    assert s["errors"] == 0
    assert s["queued"] == 0


def test_summary_processed_and_expenses(queue: QueueStore) -> None:
    """Spese italiane: contano come processed + expenses."""
    queue.complete("a" * 64, expense_id=1, self_invoice_id=None)
    queue.complete("b" * 64, expense_id=2, self_invoice_id=None)
    s = queue.summary()
    assert s["processed"] == 2
    assert s["expenses"] == 2  # entrambe hanno expense_id
    assert s["self_invoices"] == 0


def test_summary_with_self_invoices(queue: QueueStore) -> None:
    """Spese estere con autofattura: contano anche self_invoices."""
    queue.complete("a" * 64, expense_id=1, self_invoice_id=101)
    queue.complete("b" * 64, expense_id=2, self_invoice_id=102)
    queue.complete("c" * 64, expense_id=3, self_invoice_id=None)  # italiana
    s = queue.summary()
    assert s["processed"] == 3
    assert s["expenses"] == 3
    assert s["self_invoices"] == 2


def test_summary_with_errors_and_queued(queue: QueueStore) -> None:
    """Item falliti e in attesa contano nei rispettivi contatori."""
    queue.complete("a" * 64, expense_id=1)
    queue.mark_failed("b" * 64, "Errore")
    queue.enqueue("c" * 64, "/tmp/test.pdf")
    s = queue.summary()
    assert s["processed"] == 1
    assert s["errors"] == 1
    assert s["queued"] == 1


# ── Test migrazione legacy ────────────────────────────────────────────────────


def test_migration_from_legacy_schema(tmp_path: Path) -> None:
    """Crea un DB con schema legacy (path, fic_id) e verifica che QueueStore
    migri automaticamente al nuovo schema preservando i dati."""
    import sqlite3

    db_path = tmp_path / "legacy_queue.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS queue (
            sha256 TEXT PRIMARY KEY,
            path TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'queued',
            fic_id INTEGER,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "INSERT INTO queue (sha256, path, status, fic_id) VALUES (?, ?, ?, ?)",
        ("legacy_sha", "/tmp/legacy.pdf", "completed", 42),
    )
    conn.commit()
    conn.close()

    # Apri con QueueStore (dovrebbe migrare automaticamente)
    queue = QueueStore(db_path=db_path)
    record = queue.get("legacy_sha")
    assert record is not None
    assert record["fic_expense_id"] == 42  # migrato da fic_id
    assert record["status"] == "completed"

    # Verifica che le colonne del nuovo schema esistano
    cur = queue._conn.execute("PRAGMA table_info(queue)")
    cols = {row[1] for row in cur.fetchall()}
    for expected in ("file_path", "fic_expense_id", "fic_self_invoice_id", "processed_at", "error_message"):
        assert expected in cols, f"Colonna {expected} mancante dopo migrazione"

    queue.close()
