# CiteFinder

A local-first RAG tool that answers questions **only from your own PDFs**, points to **where** each answer came from (file + page), and turns a source into a **formatted citation** only once you confirm its details. Nothing is invented, nothing leaves your machine by default.

Drop a folder of readings into a chat, ask in plain English, and get a grounded, tutor-style answer with a **Locator** for every claim — and an APA / Harvard / IEEE citation on demand.

---

## Why

Students and researchers accumulate dozens of PDFs and need to find *where* a topic is discussed in their own material, understand it, and cite it correctly. A general chatbot hallucinates and cites things you never read. CiteFinder is the opposite: it is a grounded tutor over *your* corpus, honest about where every statement comes from.

Two integrity rules drive the whole design:

1. **Answers come only from your uploaded material** — never the model's own knowledge, never the open web. If nothing is covered, it refuses.
2. **Attribution is a Locator by default** (file + page, always honest). A **formatted citation** is an opt-in extra, built in code from metadata *you confirm* — never auto-generated from a guess.

## Features

- **Grounded answers** — a strict tutor prompt plus a dense-distance coverage gate; off-topic or empty queries are refused *before* any LLM call.
- **Hybrid retrieval** — dense vector search (pgvector) fused with Postgres full-text search via Reciprocal Rank Fusion, with adaptive multi-query expansion only when a result looks weak.
- **Locate-by-default, cite-on-confirmation** — every answer carries Locators; a citation appears only for sources whose author/title/year you have confirmed.
- **Chat-owned corpora** — each chat owns the files added to it, so a question searches only that chat's material.
- **Local-first, hosted opt-in** — embeddings always run locally; the LLM defaults to a local Ollama model and switches to any OpenAI-compatible provider (e.g. Groq) via environment variables, with no code change.
- **Evaluated, not guessed** — retrieval thresholds are tuned from a labeled gold set (recall@k / hit@k / MRR).

## How it works

```
INGEST   PDF ─▶ extract text per page ─▶ chunk + tag (page/source/chat)
            ─▶ embed locally (all-MiniLM-L6-v2) ─▶ store vectors in pgvector

QUERY    question ─▶ embed ─▶ hybrid retrieve (dense + keyword, RRF)
            ─▶ refuse if nothing is covered
            ─▶ local/hosted LLM answers using ONLY those chunks
            ─▶ answer + a Locator per part (+ a Citation for confirmed sources)
```

## Tech stack

| Layer | Choice |
|------|--------|
| Backend | Python, FastAPI |
| Vector store | PostgreSQL + pgvector (Docker) |
| Embeddings | `sentence-transformers` `all-MiniLM-L6-v2` (local, CPU) |
| LLM | Ollama (Phi-4 Mini) by default; any OpenAI-compatible endpoint (e.g. Groq) |
| PDF parsing | pypdf |
| Frontend | Vanilla SPA (no build step) served by FastAPI |

## Quickstart

**Prerequisites:** Docker, Python 3.11+, and either a local [Ollama](https://ollama.com) install or an API key for a hosted OpenAI-compatible provider.

```bash
# 1. Start Postgres + pgvector
docker run -d --name citefinder-db -p 5432:5432 \
  -e POSTGRES_PASSWORD=devpass -e POSTGRES_DB=citefinder pgvector/pgvector:pg16

# 2. Create a virtual environment and install dependencies
python -m venv venv
source venv/Scripts/activate        # Windows (Git Bash);  use venv/bin/activate on macOS/Linux
pip install -r requirements.txt

# 3. Create the database schema (idempotent)
python setup_db.py

# 4. Run the web app, then open http://localhost:8000
python app.py
```

## Configuration

CiteFinder reads configuration from a `.env` file (auto-loaded on startup). Copy the template and fill it in:

```bash
cp .env.example .env
```

To run on a local Ollama model, leave the LLM settings unset. To use a hosted provider such as **Groq** (OpenAI-compatible), set:

```ini
# .env
CITEFINDER_LLM_BASE_URL=https://api.groq.com/openai/v1
CITEFINDER_LLM_KEY=gsk_your_key_here
CITEFINDER_LLM_MODEL=llama-3.3-70b-versatile
```

Embeddings always run locally regardless of this setting — ingestion never sends data or costs tokens.

## Usage (Python API)

The web UI is the easiest way in, but every capability is a plain function you can script:

```python
from chats import create_chat
from add_source import add_source_folder
from query import answer

# A chat owns its corpus. Add a whole folder of PDFs (non-blocking, no prompts).
chat_id = create_chat(title="Thesis sources")
add_source_folder("data/my_pdfs", chat_id=chat_id)

# Ask a question — grounded only in this chat's material.
text, used = answer("How is user authentication handled?", chat_id=chat_id)
print(text)                       # tutor-style answer, or a refusal if not covered
for c in used:                    # Locators: where each part came from
    print(f"  {c['filename']} — p. {c['page']}")
```

Turn a source into a formatted citation **after** confirming its details:

```python
from sources import confirm_source, cite_source

# Lock real metadata (needs author + year + a real title). This is persisted.
confirm_source(source_id, author="Khan, H. M. H.",
               title="Encouraged Digital Academic Portal", year="2025")

# Now cite it in any style — the style is chosen at cite time, never stored.
print(cite_source(source_id, page=16, style="APA"))
# Khan, H. M. H. (2025). Encouraged Digital Academic Portal (p. 16).
```

## Evaluation

Retrieval quality is measured, not assumed. The harness scores page-level `recall@k` / `hit@k` / `MRR` over a labeled gold set and tunes the coverage threshold from data:

```bash
python evaluate.py                  # compare dense vs keyword vs hybrid
python evaluate.py --tune-floor     # set the relevance floor from gold + off-topic sets
```

On the current gold set, hybrid retrieval reaches **hit@5 = 1.00** and **recall@5 = 0.86**.

## Project layout

```
*.py        # backend + entry points (run from the repo root)
web/        # the vanilla SPA served by app.py
docs/       # CONTEXT.md (glossary), DEVLOG.md, and the ADRs (0001–0007)
data/       # your PDFs + uploads/  (gitignored)
```

Key modules: `app.py` (web API), `query.py` (retrieval + grounded answer), `add_source.py` (ingest), `sources.py` (confirm/cite), `chats.py` (chat lifecycle), `citations.py` (Locator + citation formatting).

## Design & decisions

The vocabulary lives in [docs/CONTEXT.md](docs/CONTEXT.md); the reasoning behind each major choice is recorded as ADRs in [docs/](docs/):

- **0002** — local by default, hosted opt-in
- **0003** — locate by default, cite on confirmation
- **0004** — grounded tutor, only from your material
- **0005** — a chat owns its corpus
- **0006** — single-user desktop app (multi-user/hosted rejected)
- **0007** — bundle Postgres, fetch the LLM on demand

## Status

Working end-to-end: folder ingest → hybrid retrieval → grounded answer with Locators → confirm → cite, behind a web UI. **Single-user by design** — CiteFinder is a personal desktop app, not a hosted service ([ADR 0006](docs/0006-single-user-desktop-app.md)); the next milestone packages it as a downloadable Windows app with a bundled database and a choice of local or bring-your-own-key cloud LLM ([ADR 0007](docs/0007-bundle-postgres-fetch-llm.md)). See [docs/DEVLOG.md](docs/DEVLOG.md) for the build history.
