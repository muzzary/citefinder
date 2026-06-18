## What this project solves

Students writing a thesis collect many PDFs (books, papers, articles) and need to find **where** a topic is discussed in their own material, **understand it**, and — when they choose — **cite it correctly**. CiteFinder is a local-first RAG tool: ask a question in plain English, get a grounded, tutor-style answer drawn only from your own documents, with a **Locator** (file name + page + summary + context) pointing to where each part came from. For sources whose details the student confirms, it can also produce a formatted citation (APA / Harvard / IEEE).

Core rules:
- It answers **only** from the student's uploaded documents — a grounded tutor that explains *your* material and never fills gaps with the model's own knowledge.
- Attribution is a **Locator** by default (file name + page — always honest). A **formatted citation** is an optional extra, built in code only from metadata the student has **confirmed** — never auto-generated from a guess, and never invented by the model.

See [CONTEXT.md](docs/CONTEXT.md) for the project vocabulary (Source, Work, Notes, Citation, Locator, Covered, Answer) and the ADRs ([0001](docs/0001-works-cited-notes-located.md)–[0004](docs/0004-grounded-tutor-only-from-material.md)) for the decisions behind this design.

## Tech stack

- Python (backend logic)
- PostgreSQL + pgvector (vector storage), run via Docker
- `e5-small-v2` via ONNX Runtime (local embeddings, CPU, torch-free; 384-dim, asymmetric `query:`/`passage:` prefixes). Replaced all-MiniLM-L6-v2 after a benchmark A/B (DEVLOG T31/T32).
- Phi-4 Mini via Ollama, OpenAI-compatible endpoint (local LLM)
- pypdf (PDF text + metadata extraction)

Local by default: embeddings are always local and the corpus never leaves the machine; a hosted LLM is opt-in only, never an automatic fallback (see [ADR 0002](docs/0002-local-by-default-hosted-opt-in.md)).

## How the system works (two flows)

INGEST: PDF -> extract text per page -> chunk + tag with page/source/user -> embed -> store in pgvector. Metadata is auto-extracted as a guess; confirmation is optional and deferred, so the student can query immediately.

QUERY:  question -> hybrid retrieval (dense pgvector + Postgres full-text, fused with RRF) -> if that result looks weak, escalate to multi-query (LLM expands the question into variants, retrieve+fuse per variant) -> refuse if nothing is "covered" -> local LLM gives a grounded, tutor-style answer using only those chunks -> answer + a Locator for each part (+ a formatted citation for any confirmed Work).

Note: retrieval is ADAPTIVE (Phase 6, built 2026-06-17). The cheap hybrid path runs on every query with no extra LLM call; the costlier multi-query expansion (one extra LLM call) is gated by `should_expand` and fires only when the hybrid result is weak (very short question, or closest dense match too far). The distance floor, fusion `top_k`/`candidate_k`, and the routing thresholds (`SHORT_QUESTION_WORDS`, `WEAK_MATCH_DISTANCE`) are placeholders to be tuned by Phase-7 evaluation.

---

## Repository layout

Python source stays flat in the root (run scripts directly, e.g.
`python add_source.py`); docs and data live in their own folders.

```
*.py                 # all source + entry points (run from the repo root)
web/                  # the web UI (index.html, styles.css, app.js) served by app.py
data/                # PDFs (fyp_final.pdf, sample1.pdf, sample2.pdf) + uploads/ — gitignored
docs/                # CONTEXT.md, DEVLOG.md, and the ADRs (0001–0005)
README.md, CLAUDE.md # stay at root by convention
```

## Files and what each holds

- **db.py** — Single source of truth for the database connection. Exposes `CONN` (read from the `CITEFINDER_DB` env var, falling back to local Docker) and `connect()`, used as a context manager everywhere so connections always commit and close.

- **setup_db.py** — Creates the database tables (`sources`, `chunks`, `chats`, `messages`) and runs idempotent migrations. Schema: sources hold attribution metadata plus `kind` (work | notes), `confirmed`, and `chat_id` (which chat owns this source — see [ADR 0005](docs/0005-chat-owns-its-corpus.md)); chunks hold text + page + user_id + the embedding vector + a generated `tsvector` (`text_tsv`) with a GIN index for the keyword half of hybrid search; chats own a corpus; messages hold each chat's Q&A turns (with `attribution` JSONB). Safe to re-run.

- **chats.py** — Chat / collection helpers ([ADR 0005](docs/0005-chat-owns-its-corpus.md)): `create_chat` / `list_chats` / `rename_chat` / `delete_chat` (cascades the chat's chunks → sources → messages → the chat row), and `add_message` / `get_messages` (message `attribution` stored as JSONB so the sidebar replays exactly what was shown). A chat owns the corpus added to it.

- **ingest.py** — Opens a PDF and extracts text page by page, keeping each page number. Flags scanned/empty pages (v1 supports digital-text PDFs only). Page numbers are captured here because every attribution depends on them.

- **chunk.py** — Splits each page's text into overlapping chunks and tags every chunk with source_id, user_id, and page_number. Filters out table-of-contents junk. Chunks are the retrievable unit; metadata travels with them so we can attribute later.

- **embed_store.py** — Holds `store_source` (insert a source row) and `store_chunks` (embed each chunk with the local model and insert it with its vector + metadata). The embedding model lives here.

- **metadata.py** — Tries to auto-extract author/title/year from a PDF's embedded metadata. Treated as a guess to be confirmed, since PDF metadata is often unreliable.

- **add_source.py** — The "upload a document" entry point. `ingest_pdf` is the non-interactive core (extract -> reject scanned/empty -> chunk -> store source -> embed -> store, returns a result dict with a `status`); `add_source` is the interactive single-file wrapper (asks Work-vs-Notes, confirms metadata — confirming unlocks a Citation); `add_source_folder` is the batch, **non-blocking** "add a folder" path (ADR 0003: ingest-now, confirm-later) — globs `*.pdf`, ingests each into the chat as a Work with `confirmed=False` (Locator now, citable later), title from the metadata guess falling back to the filename, and skips an unusable file with a reason instead of aborting the batch. All paths take a `chat_id` (ADR 0005).

- **citations.py** — Pure formatting (no DB, no LLM): `format_locator` builds the always-honest Locator (file + page + summary + context) and `format_citation` builds an APA / Harvard / IEEE citation from real metadata fields + page. Per [ADR 0003](docs/0003-locate-by-default-cite-on-confirmation.md) the Citation is a confirmed-only extra, the Locator the default.

- **sources.py** — Source lifecycle (the "cite this source" backend; parallel to chats.py). `list_sources_for_chat(chat_id)` returns the files in a chat (filename/title, kind, confirmed, chunk count) for the "files in this chat" list; `get_source` returns a source row as a dict (the UI checks `confirmed`); `confirm_source(source_id, author, title, year)` stores real metadata and **locks** the Source as confirmed (persisted — never re-asked; needs author + year or it stays unconfirmed; Notes can't be confirmed); `cite_source(source_id, page, style)` renders the Citation via `format_citation` for a confirmed Source, with **style chosen at cite time, never stored** (one confirmed Source cites in any style), and refuses on an unconfirmed Source. See [ADR 0003](docs/0003-locate-by-default-cite-on-confirmation.md).

- **query.py** — The query pipeline (Phase 6). `retrieve` (dense pgvector + distance) and `retrieve_keyword` (Postgres full-text via `websearch_to_tsquery` + `ts_rank`) are the two retrievers; `rrf_fuse` merges ranked lists by position (k=60); `retrieve_hybrid` fuses the two; `expand_query` rewrites the question into variants with the local LLM (degrades to the original if it's down); `retrieve_multi` runs hybrid per variant and fuses everything in one RRF pass. `answer` is the adaptive entry point: empty library → refuse before any LLM call; else run `retrieve_hybrid` (cheap, no extra LLM) and escalate to `retrieve_multi` only when `should_expand` judges the result weak (cheap signals only — `SHORT_QUESTION_WORDS`, `WEAK_MATCH_DISTANCE`; `expand` arg can force/disable it); then feed the chunks to a strict grounded-tutor prompt. `is_refusal` recognises a refusal even when the LLM rewords the canonical string. Emits Locators by default; a Citation only for confirmed Works. All retrieval/`answer` take an optional `chat_id` and scope to that chat's corpus ([ADR 0005](docs/0005-chat-owns-its-corpus.md)); passing none falls back to `user_id` (eval / pre-chat data).

- **app.py** — The Phase-8 web server (FastAPI): a thin JSON layer over the pipeline with no logic of its own. Serves the `web/` SPA and exposes chats (create / list / rename / **delete** / messages / **sources**), `upload` (multipart → `ingest_pdf` per PDF, non-blocking), `ask` (→ `query.answer`, builds the attribution list, stores both turns — runs `answer` first and returns a clean 503 if the local model is down so no orphaned turn is stored), and cite (`get_source` / `confirm_source` / `cite_source`). Delete also `rmtree`s the chat's upload folder. Ingest/ask are sync `def` so FastAPI runs them in a threadpool (slow: local embeddings + the Ollama call). `user_id` is fixed to `user_1` (single-user v1; multi-user is purely an auth layer since the data already partitions by `user_id`). Run `python app.py`, open http://localhost:8000.

- **web/** — The browser UI (vanilla SPA, no build step — see the Phase-8 DEVLOG for why not React). `index.html` (SVG icon library + fonts), `styles.css` (near-black theme, one emerald accent, Space Grotesk / Outfit / JetBrains Mono), `app.js` (hash router: `#/` home with the two start options, `#/chats` list, `#/chat/:id` chat with the previous-chats sidebar; file + folder upload; the full cite flow — confirm-state check → confirm form → style picker → rendered citation; toasts; loading/empty/error states).

- **inspect_retrieval.py** — Debugging tool: prints the top retrieved chunks for a question so retrieval quality can be eyeballed.

- **eval_questions.py** — Phase-7 gold set: 12 questions over `fyp_final.pdf` with page-level relevance labels (verified against content) + 6 off-topic negatives for floor tuning.

- **evaluate.py** — Phase-7 evaluation harness. Page-level `hit@k`/`recall@k`/`MRR`; compares dense/keyword/hybrid (`--with-multi` adds the LLM path); `--tune-floor` and `--sweep-candidate-k` set the thresholds from data. `--test-metrics` unit-tests the metrics. Ingests the eval corpus under `eval_corpus` if absent.

- **test_setup.py** — Phase 0 connectivity check: verifies Ollama (LLM) and Postgres + pgvector are reachable.

- **docs/CONTEXT.md** — The project glossary (ubiquitous language). Implementation-free.

- **docs/DEVLOG.md** — Running development log: work done, tests (pre/input/output/post), and debugging, newest first.

- **docs/0001–0005 \*.md** — Architecture Decision Records.

---

## Phases completed

**Phase 0 — Environment**: Docker + Postgres/pgvector, Ollama + Phi-4 Mini, Python venv, all verified connected.

**Phase 1 — PDF ingest**: extract text page by page, keep page numbers.

**Phase 2 — Chunking**: split into overlapping chunks tagged with page/source/user; filter junk.

**Phase 3 — Embed + store**: embed chunks locally, store vectors + metadata in pgvector.

**Phase 4 — Basic RAG**: question -> semantic retrieval -> grounded answer with page numbers.

**Phase 5 — Citation engine**: metadata extract-and-confirm cascade + APA/Harvard/IEEE formatting built from real data. Being repositioned to a confirmed-only extra (see [ADR 0003](docs/0003-locate-by-default-cite-on-confirmation.md)), not the default output.

**Phase 6 — Reliability + retrieval upgrade** (built 2026-06-16/17):
- Locate-by-default, cite-on-confirmation ([ADR 0003](docs/0003-locate-by-default-cite-on-confirmation.md)): `kind` (work | notes) + `confirmed` on `sources`; Locators always, Citations only for confirmed Works.
- Refusal contract: empty library and off-topic both refused before any LLM call — coverage is decided by dense distance vs the (Phase-7-tuned) floor; the grounded-tutor prompt's second layer (recognised via `is_refusal`, robust to the LLM rewording the refusal) is a backstop.
- Grounded tutor answers ([ADR 0004](docs/0004-grounded-tutor-only-from-material.md)); LLM boundary local-by-default ([ADR 0002](docs/0002-local-by-default-hosted-opt-in.md)).
- Retrieval (adaptive): hybrid (dense pgvector + Postgres full-text `tsvector`/`ts_rank`) fused with RRF on every query; escalates to multi-query expansion only when the hybrid result is weak, so a well-phrased question costs one LLM call, not two.

**Phase 7 — Evaluation** (built 2026-06-17): `eval_questions.py` (12-question gold set + 6 off-topic negatives, page-level labels verified against content) and `evaluate.py` (recall@k / hit@k / MRR, method comparison, floor + candidate_k tuning). Findings: hybrid beats the dense baseline at depth (hit@5 1.000, recall@5 0.861); multi-query *hurt* on this well-phrased set (kept conditional). Set from data, not guessed: `CANDIDATE_K=20` and the dense coverage floor. NOTE: the floor is embedder-specific — after the e5-small-v2 swap (DEVLOG T32) it was re-tuned from `0.69` (MiniLM scale) to `MAX_DISTANCE=0.22` (e5 scale: covered 0.11–0.21 vs off-topic 0.18–0.25, overlapping, so the floor preserves recall and the LLM refusal is the backstop). The routing thresholds (`SHORT_QUESTION_WORDS`, `WEAK_MATCH_DISTANCE`) remain reasoned — proving selective expansion needs a vaguer question set.

**Large-scale benchmark + embedder upgrade** (DEVLOG T31/T32): a synthetic ground-truthed corpus (`bench_corpus.py` / `bench_eval.py`, up to ~7k chunks) showed dense retrieval was bottlenecked by the embedding model; an A/B (`bench_embedders.py`) picked **e5-small-v2**, lifting Phase-7 hybrid hit@1 0.50→0.92 / MRR 0.72→0.94. Switching embedders requires RE-INDEXING all corpora (query/passage vectors must share one model).

---

## What comes next

**Phase 6 — complete.** Folder/batch non-blocking ingest (`add_source_folder`), cite-on-demand (`sources.py`: `confirm_source` / `cite_source`), the retrieval upgrade, and locate-by-default are all done (see "Phases completed"). The remaining product surface is the Phase-8 web UI that calls these.

**Phase 7 — remaining**
- Grow the gold set with short/vague questions to test whether the router's *selective* multi-query expansion ever beats hybrid (it loses as an always-on default). Tune `SHORT_QUESTION_WORDS` / `WEAK_MATCH_DISTANCE` once that data exists.

**Phase 7 — Evaluation (original plan, now done)**
- Fixed question set; measure recall@k; show before/after improvement from hybrid search. The relevance floor (and fusion `top_k`/`candidate_k`) is set by evaluation, not by guess.

**Phase 8 — Frontend + demo** (web UI built 2026-06-17)
- Web UI done: `app.py` (FastAPI) + `web/` (vanilla SPA) — home → new chat / previous chats, sidebar, add file/folder, ask → Locators, "cite this source". Verified end-to-end over HTTP (T21): create chat, ingest, refusal path, confirm, cite in all three styles. The *answered* LLM path needs Ollama with enough free memory (a demo blocker on a low-memory machine, not a code issue — see DEVLOG D8).
- Remaining: README polish, architecture diagram, short demo video.

**Phases 9–14 — Desktop-app re-architecture** (planned): ship CiteFinder as a single-user downloadable Windows app ([ADR 0006](docs/0006-single-user-desktop-app.md)) with a bundled Postgres, an ONNX embedder, self-managed services, and a runtime choice of local (Ollama) or bring-your-own-key cloud LLM ([ADR 0007](docs/0007-bundle-postgres-fetch-llm.md)). Current scope is the backend re-architecture (still browser-based); the native window + installer (Phases 15–17) are deferred. Full phase plan in [docs/ROADMAP.md](docs/ROADMAP.md).

---

## How to run (current state)

1. Start Docker, ensure the `citefinder-db` container is running.
2. Activate the venv (run with `venv/Scripts/python.exe` — see DEVLOG; the global Python may lack deps / hit memory limits).
3. **Web UI (recommended):** `python app.py`, then open http://localhost:8000.
4. CLI alternative — add a document: `python add_source.py`; ask questions: `python query.py`.
