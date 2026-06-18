"""
Local LLM provisioning via Ollama (Phase 14) — the detect-and-guide path
(ADR 0007).

The "Local" option in Settings needs Ollama to actually serve a model. This
module checks whether Ollama is installed and running, lists what's already
pulled, offers a small curated catalog (sized for typical consumer RAM), and
pulls a chosen model with streamed progress. It speaks Ollama's native HTTP API
on :11434 using only the standard library (no extra dependency). The answer path
still reaches the model through the OpenAI-compatible :11434/v1 endpoint via
query._llm(); this module only handles getting a model onto the machine.
"""
import json
import shutil
import urllib.error
import urllib.request

OLLAMA_HOST = "http://localhost:11434"
DOWNLOAD_URL = "https://ollama.com/download"

# Curated local models small enough for typical consumer hardware. The local
# default (phi4-mini) matches the config default. RAM hints are the rough FREE
# memory needed to run the Q4 quant; sizes are approximate download sizes. The
# picker shows these so a user doesn't pick something that won't fit (DEVLOG D8:
# a too-big model OOMs on a low-memory machine).
CATALOG = [
    {"id": "gemma2:2b",   "label": "Gemma 2 · 2B",    "size": "~1.6 GB", "ram": "~4 GB free"},
    {"id": "llama3.2:3b", "label": "Llama 3.2 · 3B",  "size": "~2.0 GB", "ram": "~6 GB free"},
    {"id": "qwen2.5:3b",  "label": "Qwen 2.5 · 3B",   "size": "~2.0 GB", "ram": "~6 GB free"},
    {"id": "phi4-mini",   "label": "Phi-4 Mini · 3.8B", "size": "~2.5 GB", "ram": "~6 GB free"},
]


def status(timeout=4):
    """
    Detect Ollama: installed (binary on PATH) and/or running (API responds),
    plus the models already pulled. 'installed' is true if the API responds even
    when the binary isn't on PATH (a running server is proof enough).
    """
    on_path = shutil.which("ollama") is not None
    running, models = False, []
    try:
        req = urllib.request.Request(OLLAMA_HOST + "/api/tags")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        running = True
        models = [m["name"] for m in data.get("models", [])]
    except Exception:
        running = False
    return {
        "installed": on_path or running,
        "running": running,
        "models": models,
        "catalog": CATALOG,
        "download_url": DOWNLOAD_URL,
    }


def pull(model):
    """
    Stream a model pull from Ollama. Yields progress dicts as they arrive:
    {status, completed?, total?, percent?}. Terminates with {status:'success'}
    on completion or {status:'error', error:...} if the pull can't start/finish.
    Re-pulling an already-present model is cheap (Ollama just verifies layers),
    so this doubles as a "make sure it's there" call.
    """
    body = json.dumps({"name": model, "stream": True}).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_HOST + "/api/pull", data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=1800) as r:
            for raw in r:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    evt = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                total, completed = evt.get("total"), evt.get("completed")
                if total and completed is not None:
                    evt["percent"] = round(completed / total * 100, 1)
                yield evt
    except urllib.error.URLError as e:
        # Server down, or model name rejected (HTTPError is a URLError subclass).
        detail = getattr(e, "reason", None) or str(e)
        yield {"status": "error", "error": str(detail)}
