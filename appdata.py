"""
Where CiteFinder keeps its mutable data on the user's machine.

As a single-user desktop app (ADR 0006) CiteFinder must never write into its
install directory; everything mutable (the database cluster, uploaded PDFs, the
embedding model, config.json) lives in a per-user app-data folder:

    Windows:  %APPDATA%\\CiteFinder
    macOS:    ~/Library/Application Support/CiteFinder
    Linux:    $XDG_DATA_HOME/CiteFinder  (or ~/.local/share/CiteFinder)

Set CITEFINDER_HOME to override the base directory. Used in dev so we don't
touch the real user profile, and by tests for an isolated scratch dir. Paths are
resolved cross-platform here (Windows-first per ADR 0007, but macOS/Linux are a
later port, not a rewrite — keep this the only place that branches on OS).
"""
import os
import sys
from pathlib import Path

APP_NAME = "CiteFinder"


def app_data_dir() -> Path:
    """The base app-data directory, created if missing."""
    override = os.environ.get("CITEFINDER_HOME")
    if override:
        base = Path(override)
    elif os.name == "nt":
        root = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
        base = Path(root) / APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        root = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
        base = Path(root) / APP_NAME
    base.mkdir(parents=True, exist_ok=True)
    return base


def uploads_dir() -> Path:
    """Where uploaded PDFs are stored (under <app-data>/uploads/<chat_id>/)."""
    d = app_data_dir() / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    """The config.json file holding LLM choice / DB override (Phase 13 writes it)."""
    return app_data_dir() / "config.json"
