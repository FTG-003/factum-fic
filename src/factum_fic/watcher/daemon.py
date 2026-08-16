"""Watchdog observer per monitoraggio hotfolder.

Osserva una directory (es. ~/Downloads) per nuovi file PDF/XML
e li invia automaticamente alla pipeline di elaborazione.
"""

from __future__ import annotations

import logging
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

import re

from factum_fic.config import Settings

logger = logging.getLogger(__name__)

# Estensioni supportate
_SUPPORTED_EXTENSIONS = {".pdf", ".xml", ".txt", ".csv"}

# Pattern per file temporanei/parziali da ignorare
_TEMP_FILE_RE = re.compile(r"\.(part|crdownload|tmp|swp|bak)$|~$", re.IGNORECASE)


class FactumFICHandler(FileSystemEventHandler):
    """Handler watchdog: processa nuovi file nella cartella monitorata."""

    def __init__(self, callback) -> None:  # noqa: ANN001
        self._callback = callback

    def on_created(self, event) -> None:  # noqa: ANN001
        if event.is_directory:
            return
        path = Path(event.src_path)
        # Ignora file nascosti e temporanei
        if path.name.startswith("."):
            return
        if _TEMP_FILE_RE.search(path.name):
            logger.debug("Ignorato file temporaneo: %s", path.name)
            return
        if path.suffix.lower() not in _SUPPORTED_EXTENSIONS:
            return
        # Aspetta che il file sia completamente scritto (copiatura in corso)
        if not path.exists() or path.stat().st_size == 0:
            return
        logger.info("Nuovo file rilevato: %s", path.name)
        self._callback(path)


class WatcherDaemon:
    """Folder watcher basato su watchdog.

    Usage:
        daemon = WatcherDaemon(settings, callback)
        daemon.start()
        # ...
        daemon.stop()
    """

    def __init__(
        self,
        settings: Settings,
        callback,  # noqa: ANN001
    ) -> None:
        self._watch_dir = Path(settings.watch_dir).expanduser().resolve()
        self._watch_dir.mkdir(parents=True, exist_ok=True)
        self._handler = FactumFICHandler(callback)
        self._observer: Observer | None = None

    def start(self) -> None:
        """Avvia l'osservazione della cartella."""
        self._observer = Observer()
        self._observer.schedule(self._handler, str(self._watch_dir), recursive=False)
        self._observer.start()
        logger.info("Watcher avviato su: %s", self._watch_dir)

    def stop(self) -> None:
        """Arresta l'osservazione."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None
        logger.info("Watcher arrestato.")

    @property
    def watch_dir(self) -> Path:
        return self._watch_dir
