"""Test del watcher daemon con directory temporanea."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from factum_fic.config import Settings
from factum_fic.watcher.daemon import WatcherDaemon

_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def watch_dir(tmp_path: Path) -> Path:
    """Directory temporanea per il watcher."""
    d = tmp_path / "watch"
    d.mkdir()
    return d


@pytest.fixture
def mock_settings_for_watcher(watch_dir: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        FACTUM_API_URL="https://mock.factum.test",
        FACTUM_API_KEY="mock-key",
        FIC_BASE_URL="https://mock.fic.test",
        FIC_API_KEY="mock-fic-token",
        FIC_COMPANY_ID="99999",
        WATCH_DIR=str(watch_dir),
    )


async def test_watcher_rileva_file(
    mock_settings_for_watcher: Settings,
) -> None:
    """Il watcher rileva un nuovo file nella cartella monitorata.

    Verifica che il callback venga chiamato con il path corretto.
    """
    detected_paths: list[Path] = []

    async def _callback(path: Path) -> None:
        detected_paths.append(path)

    # Usa un wrapper sincrono per watchdog
    def sync_callback(path: Path) -> None:
        asyncio.run(_callback(path))

    daemon = WatcherDaemon(mock_settings_for_watcher, sync_callback)
    daemon.start()

    # Simula un nuovo file nella cartella
    src = _FIXTURES / "sample_saas_invoice.txt"
    dst = Path(mock_settings_for_watcher.watch_dir) / "test_invoice.txt"
    shutil.copy2(src, dst)

    # Aspetta che watchdog rilevi
    await asyncio.sleep(1.5)

    daemon.stop()

    # Verifica che il callback sia stato chiamato
    assert len(detected_paths) >= 1
    assert detected_paths[0].name == "test_invoice.txt"


async def test_watcher_ignora_non_pdf(
    mock_settings_for_watcher: Settings,
) -> None:
    """Il watcher ignora file con estensione non supportata."""
    detected_paths: list[Path] = []

    def sync_callback(path: Path) -> None:
        detected_paths.append(path)

    daemon = WatcherDaemon(mock_settings_for_watcher, sync_callback)
    daemon.start()

    # File .tmp ignorato
    src = Path(mock_settings_for_watcher.watch_dir) / "temp.crdownload"
    src.write_text("temp")

    await asyncio.sleep(1.5)
    daemon.stop()

    assert len(detected_paths) == 0


async def test_watcher_start_stop(
    mock_settings_for_watcher: Settings,
) -> None:
    """Il watcher si avvia e si arresta senza errori."""
    daemon = WatcherDaemon(mock_settings_for_watcher, lambda p: None)
    daemon.start()
    assert daemon.watch_dir == Path(mock_settings_for_watcher.watch_dir).resolve()
    daemon.stop()
    # Dopo stop, dovrebbe essere possibile riavviare
    daemon.start()
    daemon.stop()
    assert True  # nessuna eccezione = test passato
