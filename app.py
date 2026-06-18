"""
CiteFinder web API (Phase 8).

A thin FastAPI layer over the existing pipeline — it adds no logic of its own,
it just exposes what already exists as JSON the browser SPA (in web/) can call:

    chats        -> chats.py            (create / list / rename / messages)
    add a folder -> add_source.ingest_pdf (non-blocking, ADR 0003)
    ask          -> query.answer        (chat-scoped, ADR 0005)
    cite         -> sources.py          (confirm_source / cite_source)

Single-user by design: CiteFinder ships as a personal desktop app, not a hosted
service (see ADR 0006). user_id is a frozen constant (DEFAULT_USER); scoping is
done per-chat via chat_id (ADR 0005), so user_id no longer partitions anything —
it is kept only because the evaluation harness still uses it.

Run:  python app.py   (then open http://localhost:8000)
The ingest/ask endpoints are sync `def` on purpose: they do slow, blocking work
(local embeddings, the Ollama call), so FastAPI runs them in a threadpool and
the event loop stays responsive.
"""
import json
import os
import shutil

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import local_llm

import chats
from add_source import ingest_pdf
from query import answer, is_refusal, test_connection
from sources import get_source, confirm_source, cite_source, list_sources_for_chat
from citations import format_locator
from appdata import uploads_dir
from settings import llm_public, save_llm, is_llm_configured

DEFAULT_USER = "user_1"               # single-user, frozen constant (ADR 0006)
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
# Uploads live under the per-user app-data dir (ADR 0006/0007), never inside the
# install directory. uploads_dir() resolves + creates <app-data>/uploads.
UPLOAD_DIR = str(uploads_dir())

app = FastAPI(title="CiteFinder")


# --- request bodies ----------------------------------------------------------
class NewChat(BaseModel):
    title: str | None = None


class RenameChat(BaseModel):
    title: str


class Ask(BaseModel):
    question: str


class Confirm(BaseModel):
    author: str
    title: str | None = None
    year: str | None = None


class Cite(BaseModel):
    page: int
    style: str = "APA"


class LlmSettings(BaseModel):
    mode: str                       # 'local' or 'cloud'
    base_url: str
    model: str
    api_key: str | None = None      # only sent for cloud; omitted keeps the stored one
    provider: str | None = None     # preset id ('groq', 'openai', 'ollama', 'custom')


class TestConn(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None


class PullModel(BaseModel):
    model: str


# --- upload helper -----------------------------------------------------------
def _unique_name(dest, name):
    """A filename that does not collide with an existing upload in dest, so two
    same-named PDFs never overwrite each other on disk. 'intro.pdf' -> 'intro.pdf',
    then 'intro_1.pdf', 'intro_2.pdf', ..."""
    base, ext = os.path.splitext(name)
    candidate, i = name, 1
    while os.path.exists(os.path.join(dest, candidate)):
        candidate = f"{base}_{i}{ext}"
        i += 1
    return candidate


# --- attribution helper ------------------------------------------------------
def _build_attribution(chunks):
    """
    Turn the chunks an answer used into the attribution list the UI renders:
    one entry per unique (file, page), each a Locator (always) plus the bits the
    "cite this source" button needs (source_id, confirmed, kind). This is what
    gets stored on the assistant message as JSONB so a replay shows exactly what
    the student saw — no re-running retrieval. See ADR 0003.
    """
    seen, items = set(), []
    for c in chunks:
        key = (c["filename"], c["page"])
        if key in seen:
            continue
        seen.add(key)
        loc = format_locator(c["filename"], c["page"], c["text"], title=c["source"])
        items.append({
            "source_id": c.get("source_id"),
            "title": c["source"],
            "filename": c["filename"],
            "page": c["page"],
            "summary": loc["summary"],
            "context": loc["context"],
            "kind": c["kind"],
            "confirmed": bool(c["confirmed"]),
        })
    return items


# --- chat endpoints ----------------------------------------------------------
@app.get("/api/chats")
def api_list_chats():
    out = []
    for ch in chats.list_chats(DEFAULT_USER):
        out.append({
            "id": ch["id"],
            "title": ch["title"],
            "created_at": ch["created_at"].isoformat() if ch["created_at"] else None,
        })
    return out


@app.post("/api/chats")
def api_create_chat(body: NewChat):
    chat_id = chats.create_chat(DEFAULT_USER, title=body.title)
    return {"id": chat_id, "title": body.title}


@app.patch("/api/chats/{chat_id}")
def api_rename_chat(chat_id: int, body: RenameChat):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title can't be empty.")
    chats.rename_chat(chat_id, title)
    return {"id": chat_id, "title": title}


@app.delete("/api/chats/{chat_id}")
def api_delete_chat(chat_id: int):
    """Delete the chat, its corpus, and its uploaded files on disk."""
    chats.delete_chat(chat_id)
    folder = os.path.join(UPLOAD_DIR, str(chat_id))
    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)
    return {"deleted": chat_id}


@app.get("/api/chats/{chat_id}/sources")
def api_chat_sources(chat_id: int):
    """The files present in this chat (the 'files in this chat' list)."""
    return list_sources_for_chat(chat_id)


@app.get("/api/chats/{chat_id}/messages")
def api_get_messages(chat_id: int):
    out = []
    for m in chats.get_messages(chat_id):
        out.append({
            "role": m["role"],
            "content": m["content"],
            "attribution": m["attribution"],
            "created_at": m["created_at"].isoformat() if m["created_at"] else None,
        })
    return out


# --- ingest ------------------------------------------------------------------
@app.post("/api/chats/{chat_id}/upload")
def api_upload(chat_id: int, files: list[UploadFile] = File(...)):
    """
    Non-blocking ingest of one or more PDFs into this chat (ADR 0003). The
    browser sends a single file or a whole folder's files (webkitdirectory);
    we save each PDF under <app-data>/uploads/<chat_id>/ and ingest it as an
    unconfirmed Work — a Locator now, citable later. Non-PDF files and files
    that yield no text are reported as skipped, never fatal to the batch.
    """
    dest = os.path.join(UPLOAD_DIR, str(chat_id))
    os.makedirs(dest, exist_ok=True)

    report = []
    for up in files:
        name = os.path.basename(up.filename or "")
        if not name.lower().endswith(".pdf"):
            report.append({"status": "skipped_not_pdf", "filename": name,
                           "chunks": 0, "reason": "not a PDF"})
            up.file.close()
            continue

        # Never overwrite an existing upload: two PDFs that share a basename
        # (different subfolders of a folder upload, or a re-upload) each get a
        # distinct on-disk name, so no file's bytes are clobbered and each
        # becomes its own Source with an accurate Locator.
        name = _unique_name(dest, name)
        path = os.path.join(dest, name)
        with open(path, "wb") as f:
            shutil.copyfileobj(up.file, f)
        up.file.close()

        result = ingest_pdf(path, user_id=DEFAULT_USER, chat_id=chat_id,
                            kind="work", confirmed=False)
        report.append({
            "status": result["status"],
            "filename": result["filename"],
            "title": result["title"],
            "source_id": result["source_id"],
            "chunks": result["chunks"],
            "reason": result["reason"],
        })

    stored = [r for r in report if r["status"] == "stored"]
    return {"stored": len(stored),
            "total": len(report),
            "chunks": sum(r["chunks"] for r in stored),
            "files": report}


# --- ask ---------------------------------------------------------------------
@app.post("/api/chats/{chat_id}/ask")
def api_ask(chat_id: int, body: Ask):
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Empty question.")

    # Gate only the ASK action on LLM config (ADR 0007): ingest needs no LLM, so
    # the user reaches this point having added material but maybe not yet chosen
    # an LLM. Signal the UI to open the "choose how to answer" step instead of
    # failing against an unconfigured default. Nothing is persisted.
    if not is_llm_configured():
        return {"needs_setup": True, "answer": None, "refused": False, "attribution": []}

    # Run the pipeline BEFORE persisting anything: if the local model is down
    # (e.g. Ollama out of memory) we surface a clean error and leave no orphaned
    # user turn in the history for the student to retry against.
    try:
        ans, used = answer(question, user_id=DEFAULT_USER, chat_id=chat_id)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"The local model is unavailable right now ({e}). "
                   "Check that Ollama is running and has enough memory, then retry.",
        )

    # Attribution only when the system actually answered from material.
    refused = is_refusal(ans)
    attribution = _build_attribution(used) if (used and not refused) else []

    chats.add_message(chat_id, "user", question)
    chats.add_message(chat_id, "assistant", ans, attribution=attribution or None)

    # The first question titles the chat — but only if it actually got an
    # answer, so a refused or off-topic opener doesn't permanently label the chat.
    if not refused:
        chat = next((c for c in chats.list_chats(DEFAULT_USER) if c["id"] == chat_id), None)
        if chat and not chat["title"]:
            chats.rename_chat(chat_id, question[:60])

    return {"answer": ans, "refused": refused, "attribution": attribution,
            "needs_setup": False}


# --- cite --------------------------------------------------------------------
@app.get("/api/sources/{source_id}")
def api_get_source(source_id: int):
    src = get_source(source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="No such source.")
    return src


@app.post("/api/sources/{source_id}/confirm")
def api_confirm(source_id: int, body: Confirm):
    try:
        src = confirm_source(source_id, author=body.author,
                             title=body.title, year=body.year)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return src


@app.post("/api/sources/{source_id}/cite")
def api_cite(source_id: int, body: Cite):
    try:
        citation = cite_source(source_id, page=body.page, style=body.style)
    except ValueError as e:
        # Unconfirmed (or notes) — the UI should show the confirm step.
        raise HTTPException(status_code=409, detail=str(e))
    return {"citation": citation, "style": body.style.upper(), "page": body.page}


# --- settings (LLM choice) ---------------------------------------------------
@app.get("/api/settings")
def api_get_settings():
    """The current LLM settings for the Settings panel (never the raw key)."""
    return llm_public()


@app.put("/api/settings")
def api_put_settings(body: LlmSettings):
    """Persist the user's LLM choice to config.json. Takes effect on the next
    question with no restart (query._llm() reads live settings per call)."""
    if body.mode not in ("local", "cloud"):
        raise HTTPException(status_code=400, detail="mode must be 'local' or 'cloud'.")
    save_llm(mode=body.mode, base_url=body.base_url, model=body.model,
             api_key=body.api_key, provider=body.provider)
    return llm_public()


@app.post("/api/settings/test")
def api_test_settings(body: TestConn):
    """One cheap LLM call to verify an endpoint (Test-connection button). Tests
    the supplied values, or the saved settings when fields are omitted."""
    ok, detail = test_connection(base_url=body.base_url, api_key=body.api_key,
                                 model=body.model)
    return {"ok": ok, "detail": detail}


# --- local LLM provisioning (Ollama) -----------------------------------------
@app.get("/api/ollama/status")
def api_ollama_status():
    """Detect Ollama (installed/running), list pulled models + the catalog."""
    return local_llm.status()


@app.post("/api/ollama/pull")
def api_ollama_pull(body: PullModel):
    """Pull a model, streaming Ollama's progress as newline-delimited JSON so the
    Settings UI can show a live download bar."""
    def gen():
        for evt in local_llm.pull(body.model):
            yield json.dumps(evt) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson")


# --- static SPA --------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(os.path.join(WEB_DIR, "index.html"))


app.mount("/", StaticFiles(directory=WEB_DIR), name="web")


if __name__ == "__main__":
    import uvicorn
    print("CiteFinder running at http://localhost:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000)
