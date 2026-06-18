"""
Runtime configuration for CiteFinder — the single place that resolves the
database connection string and the LLM endpoint.

Precedence (highest wins):
  1. Environment variables  — dev override; also what a local .env populates.
  2. config.json in app-data — what the Settings UI will write (Phase 13).
  3. Built-in defaults       — local Docker DB + local Ollama.

db.py and query.py read from here instead of touching os.environ directly, so
there is one config story for both the dev setup (env / .env) and the packaged
desktop app (config.json). Today the values are read when the caller asks;
Phase 13 makes the LLM client rebuild per-call from llm_config() so the Settings
UI can switch Local<->Cloud without a restart. See ADR 0007.
"""
import json
import os

from appdata import config_path

# .env support lives here now (moved from db.py) so all config resolution is in
# one module. Load before anything reads os.environ. A missing python-dotenv or
# .env is a no-op: real shell env vars still work, so nothing hard-depends on it.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    pass

_DEFAULT_DB = "host=localhost dbname=citefinder user=postgres password=devpass"
_DEFAULT_LLM = {
    "base_url": "http://localhost:11434/v1",
    "api_key": "ollama",
    "model": "phi4-mini",
}


def _config_file() -> dict:
    """The parsed config.json, or {} if absent/unreadable (never raises)."""
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def db_conn_string() -> str:
    """The Postgres connection string (env > config.json > local Docker default)."""
    env = os.environ.get("CITEFINDER_DB")
    if env:
        return env
    return _config_file().get("db") or _DEFAULT_DB


def llm_config() -> dict:
    """The LLM endpoint config: {base_url, api_key, model} (env > config.json > default)."""
    cfg = _config_file().get("llm", {})
    return {
        "base_url": os.environ.get("CITEFINDER_LLM_BASE_URL") or cfg.get("base_url") or _DEFAULT_LLM["base_url"],
        "api_key": os.environ.get("CITEFINDER_LLM_KEY") or cfg.get("api_key") or _DEFAULT_LLM["api_key"],
        "model": os.environ.get("CITEFINDER_LLM_MODEL") or cfg.get("model") or _DEFAULT_LLM["model"],
    }


def load_config() -> dict:
    """The full config.json as a dict (for the Settings UI to read back)."""
    return _config_file()


def save_config(data: dict) -> None:
    """Persist the full config dict to config.json (Phase 13 Settings UI writes here)."""
    with open(config_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _env_llm_override() -> bool:
    """True if env vars (e.g. a dev .env) are overriding the LLM config, so the
    Settings UI can say 'managed by environment' and explain that saving won't
    take effect until those are unset."""
    return bool(os.environ.get("CITEFINDER_LLM_BASE_URL")
                or os.environ.get("CITEFINDER_LLM_KEY"))


def is_llm_configured() -> bool:
    """
    Has the user (or the environment) actually chosen an LLM? Used to gate the
    'ask' action (ADR 0007): a fresh desktop install with only the built-in
    default should be prompted to choose Local or Cloud, NOT silently fail
    against a localhost Ollama that isn't running. An env override (dev) or an
    explicit `llm.mode` in config.json both count as configured.
    """
    if _env_llm_override():
        return True
    return bool(_config_file().get("llm", {}).get("mode"))


def llm_public() -> dict:
    """
    The LLM settings shape the Settings UI reads — never includes the raw
    api_key (only whether one is set). `env_locked` tells the UI the values come
    from the environment and config.json edits are currently inert.
    """
    cfg = _config_file().get("llm", {})
    resolved = llm_config()
    return {
        "configured": is_llm_configured(),
        "mode": cfg.get("mode"),
        "provider": cfg.get("provider"),
        "base_url": resolved["base_url"],
        "model": resolved["model"],
        "has_key": bool(resolved["api_key"]) and resolved["api_key"] != "ollama",
        "env_locked": _env_llm_override(),
    }


def save_llm(mode, base_url, model, api_key=None, provider=None) -> None:
    """
    Persist the user's LLM choice to config.json. The api_key is only written
    when one is supplied, so re-saving other fields (e.g. switching model) never
    wipes a previously stored key. `mode` is 'local' or 'cloud'.
    """
    data = _config_file()
    llm = data.get("llm", {})
    llm.update({"mode": mode, "base_url": base_url, "model": model})
    if provider is not None:
        llm["provider"] = provider
    if api_key:
        llm["api_key"] = api_key
    data["llm"] = llm
    save_config(data)
