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
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import local_llm

# Phase 12: in BUNDLED mode (the packaged desktop app) boot the app-owned portable
# Postgres BEFORE importing anything that opens db.CONN — ensure_ready() starts the
# cluster, creates the DB + pgvector extension, migrates, and points CITEFINDER_DB
# at it. Dev defaults to Docker; set CITEFINDER_PG=bundled to use the portable one.
if os.environ.get("CITEFINDER_PG", "").lower() == "bundled":
    import pgserver
    pgserver.ensure_ready()

import chats
import crossref
from add_source import ingest_pdf
from metadata import extract_metadata
from query import answer, is_refusal, test_connection
from sources import get_source, confirm_source, cite_source, list_sources_for_chat, delete_source
from citations import format_locator
from appdata import uploads_dir
from settings import llm_public, save_llm, is_llm_configured

DEFAULT_USER = "user_1"               # single-user, frozen constant (ADR 0006)
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
# Uploads live under the per-user app-data dir (ADR 0006/0007), never inside the
# install directory. uploads_dir() resolves + creates <app-data>/uploads.
UPLOAD_DIR = str(uploads_dir())

app = FastAPI(title="CiteFinder")

# Warm the embedding model in the background at boot so the FIRST upload doesn't
# pay the one-time cold start (ONNX session init, model download on a fresh
# machine). Daemon thread: never blocks startup or shutdown, and embedder.warmup
# is self-guarded if a real embed beats it to the load.
import threading
import embedder
threading.Thread(target=embedder.warmup, name="embed-warmup", daemon=True).start()


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
    work_type: str = "book"             # book | article | website
    meta: dict | None = None            # type-specific fields (publisher, journal, …)


class Cite(BaseModel):
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


class DoiLookup(BaseModel):
    doi: str


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

    # De-dupe by filename within the chat: a chat shouldn't hold two copies of the
    # same document (a re-upload, or the same file picked twice in a folder). We
    # match the incoming basename against the names already ingested here and skip
    # a repeat instead of creating a redundant second Source. `seen` also catches a
    # duplicate that appears twice within THIS batch, before its first copy is
    # committed to the DB.
    seen = {s["filename"] for s in list_sources_for_chat(chat_id)}

    report = []
    for up in files:
        name = os.path.basename(up.filename or "")
        if not name.lower().endswith(".pdf"):
            report.append({"status": "skipped_not_pdf", "filename": name,
                           "chunks": 0, "reason": "not a PDF"})
            up.file.close()
            continue

        if name in seen:
            report.append({"status": "skipped_duplicate", "filename": name,
                           "chunks": 0,
                           "reason": "a file with this name is already in this chat"})
            up.file.close()
            continue
        seen.add(name)

        # Backstop against an on-disk clobber even after the dedupe above (e.g. a
        # name freed by a deleted Source whose file lingered): give a colliding
        # basename a distinct on-disk name so no file's bytes are overwritten.
        name = _unique_name(dest, name)
        path = os.path.join(dest, name)
        with open(path, "wb") as f:
            shutil.copyfileobj(up.file, f)
        up.file.close()

        # Auto-extract a metadata GUESS from the PDF (title/author/year) so the
        # "confirm details" form opens pre-filled instead of blank — the intent in
        # ADR 0003 ("auto-extracted as a guess; confirmation deferred"). It is
        # stored UNCONFIRMED: PDF metadata is often empty or wrong (the embedded
        # year is the file's creation date, not the publication year), so it never
        # auto-unlocks a citation — the student verifies it. A bad metadata block
        # must not abort ingest, hence the guard.
        guess = {}
        try:
            guess = extract_metadata(path)
        except Exception:
            pass

        result = ingest_pdf(path, user_id=DEFAULT_USER, chat_id=chat_id,
                            kind="work", confirmed=False,
                            title=(guess.get("title") or None),
                            author=(guess.get("author") or None),
                            year=(guess.get("year") or None))
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


@app.delete("/api/sources/{source_id}")
def api_delete_source(source_id: int):
    """Remove a single file from a chat: its Source + chunks (DB cascade) and the
    uploaded PDF on disk. Past answers keep their stored Locators (a snapshot)."""
    src = delete_source(source_id)
    if src is None:
        raise HTTPException(status_code=404, detail="No such source.")
    if src["filename"]:
        path = os.path.join(UPLOAD_DIR, str(src["chat_id"]), src["filename"])
        if os.path.isfile(path):
            os.remove(path)
    return {"deleted": source_id}


@app.post("/api/sources/{source_id}/confirm")
def api_confirm(source_id: int, body: Confirm):
    try:
        src = confirm_source(source_id, author=body.author, title=body.title,
                             year=body.year, work_type=body.work_type, meta=body.meta)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return src


@app.post("/api/lookup-doi")
def api_lookup_doi(body: DoiLookup):
    """Resolve a DOI to citation metadata via CrossRef (the "Auto-fill from DOI"
    button). Returns the normalized fields for the confirm form to pre-fill; the
    student still verifies and saves. Sync def → runs in the threadpool (network
    call). Only the DOI leaves the machine (ADR 0002)."""
    try:
        return crossref.lookup_doi(body.doi)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.post("/api/sources/{source_id}/cite")
def api_cite(source_id: int, body: Cite):
    try:
        citation = cite_source(source_id, style=body.style)
    except ValueError as e:
        # Unconfirmed (or notes) — the UI should show the confirm step.
        raise HTTPException(status_code=409, detail=str(e))
    # citation is {"in_text", "reference"}; the UI shows both.
    return {"citation": citation, "style": body.style.upper()}


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
