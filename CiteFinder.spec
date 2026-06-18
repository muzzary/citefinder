# -*- mode: python ; coding: utf-8 -*-
# PyInstaller onedir bundle for CiteFinder (Phase 16).
#
# Bundles EVERYTHING except the LLM: the app + web UI, the portable PostgreSQL +
# MSVC-built pgvector (from vendor/), and the e5 ONNX model — so a fresh install
# ingests, embeds, and retrieves fully offline. The answer LLM is configured at
# runtime (cloud key or local Ollama); no key is shipped (ADR 0007).
#
# Build:  venv\Scripts\pyinstaller CiteFinder.spec
# Output: dist/CiteFinder/CiteFinder.exe  (a self-contained onedir)
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(os.getcwd())

# Resolve the e5 model files to bundle (flat model.onnx + tokenizer.json), straight
# from the dev HF cache on D: (no module import — the spec runs without the repo on
# sys.path). Keep REPO in sync with embedder.REPO.
os.environ.setdefault("CITEFINDER_HOME", r"D:\CiteFinderData")
from huggingface_hub import hf_hub_download
_REPO = "Xenova/e5-small-v2"
_cache = str(Path(os.environ["CITEFINDER_HOME"]) / "models")
_onnx = hf_hub_download(_REPO, filename="onnx/model.onnx", cache_dir=_cache)
_tok = hf_hub_download(_REPO, filename="tokenizer.json", cache_dir=_cache)

datas = [
    ("web", "web"),                                             # the SPA
    (_onnx, "model"),                                           # -> model/model.onnx
    (_tok, "model"),                                            # -> model/tokenizer.json
    (str(ROOT / "vendor" / "pgextract" / "pgsql"), "pgsql"),    # portable PG + pgvector
]
binaries = []
# Local modules pulled in conditionally/lazily (so static analysis can miss them).
hiddenimports = [
    "clr", "app", "pgserver", "setup_db", "db", "embedder", "embed_store",
    "chunk", "ingest", "metadata", "sources", "citations", "chats",
    "add_source", "query", "settings", "appdata", "local_llm",
]

# Native / data-heavy packages that need their binaries + data files collected.
for pkg in ("onnxruntime", "tokenizers", "huggingface_hub", "webview",
            "pythonnet", "clr_loader", "psycopg", "psycopg_binary",
            "pgvector", "uvicorn", "fastapi", "pydantic", "openai",
            "pypdf", "dotenv"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass
hiddenimports += collect_submodules("uvicorn")

a = Analysis(
    ["desktop.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["torch", "transformers", "tkinter", "matplotlib", "scipy"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="CiteFinder",
    console=False,            # windowed desktop app (no console)
)
coll = COLLECT(exe, a.binaries, a.datas, name="CiteFinder")
