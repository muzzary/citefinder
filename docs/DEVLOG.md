# CiteFinder — Development Log

A running record of work done, decisions made, tests run, and bugs fixed.
Newest entries at the top. For the *why* behind decisions see [CONTEXT.md](CONTEXT.md)
and the ADRs (`0001`–`0004`).

---

## UI additions: rename/delete chat, files-in-chat, slim Locators

### 1. What was done

Three requested UI improvements, with their backend support.

- **Rename + delete a chat.** `chats.delete_chat(chat_id)` cascades a chat's
  corpus (its chunks → sources → messages → the chat row; FKs have no ON DELETE
  CASCADE so children go first). `app.py`: `DELETE /api/chats/{id}` (also
  `rmtree`s `data/uploads/{id}`) and a hardened `PATCH` (rejects an empty
  title → 400). UI: rename is an inline title edit in the chat header
  (Enter/blur saves, Esc cancels); delete is a confirm modal that warns it's
  irreversible, then routes back to the chats list.
- **Files in this chat.** `sources.list_sources_for_chat(chat_id)` returns each
  Source's filename/title, kind, confirmed state, and chunk count;
  `GET /api/chats/{id}/sources`. UI: a "Files · N" button in the header opens a
  modal listing them, each with a citable / confirm-to-cite / notes badge and
  an inline "Confirm details" action for unconfirmed Works (reuses the shared
  `confirmForm`). The button label shows the live file count.
- **Slimmer Locators.** "Where this comes from" was too bulky (summary +
  context per source). Now each Locator is one compact row: file name + page +
  "Cite this source" — the honest minimum, with the full cite flow unchanged
  behind the button. (The summary/context are still stored on the message
  attribution for any future use; just not rendered.)
- Refactor: the confirm form is now a single reusable `confirmForm({source,
  onConfirmed, onCancel})` used by both the locator cite flow and the files
  modal; added a `modal()` helper.

### 2. Tests

**T23 — New endpoints, live over HTTP**
- upload 2 PDFs → `GET sources` lists both (`work`, `confirmed=False`, 8 + 6
  chunks). PASS.
- `PATCH` rename → new title; rename with blank title → **400**. PASS.
- `DELETE` chat → `{deleted}`; `GET sources` then returns **0 rows** and the
  `data/uploads/<id>` directory is **removed**. PASS.
- `node --check web/app.js` → OK.

---

## LLM endpoint made env-configurable (hosted opt-in, e.g. Groq)

### 1. What was done

The PC under test can't hold the local Ollama model in memory (DEVLOG D8), so
the demo needs a hosted LLM. Per ADR 0002 (local-by-default, hosted opt-in) the
LLM boundary in `query.py` is now env-driven instead of hardcoded to Ollama:

- `CITEFINDER_LLM_BASE_URL` (default `http://localhost:11434/v1`)
- `CITEFINDER_LLM_KEY` (default `ollama`)
- `CITEFINDER_LLM_MODEL` (default `phi4-mini`)

Both LLM call sites (`expand_query`, `answer`) use `LLM_MODEL`. Groq is
OpenAI-compatible, so pointing the base URL at `https://api.groq.com/openai/v1`
with a key and a Groq model id (e.g. `llama-3.3-70b-versatile`) runs the whole
pipeline on Groq with no code change. Embeddings stay local — ingestion costs
zero LLM tokens regardless.

### 2. Tests

**T22 — Env config switches the client (no live call)**
- Default: `LLM_BASE_URL=http://localhost:11434/v1`, `LLM_MODEL=phi4-mini`.
- With the three env vars set: client base becomes `https://api.groq.com/openai/v1`,
  model `llama-3.3-70b-versatile`. PASS.

### 3. Load note (30 files × 100 pages on Groq)

- DB: ~3,000 pages → ~5–9k chunks → ~10–15 MB of 384-d vectors; exact pgvector
  search is single-digit ms at this scale, no ANN index needed. Not a concern.
- Ingestion: **zero** LLM tokens (local embeddings).
- Querying: ~1.5–1.8k tokens/question (top_k=3 context + answer); 1 LLM call
  normally, 2 when expansion fires. Binding Groq limit is TPM, not request
  counts: ~3 Q/min on llama-3.1-8b-instant (6k TPM, 500k TPD ≈ 280 Q/day),
  ~6 Q/min on llama-3.3-70b (12k TPM, 100k TPD ≈ 55 Q/day). Fine for a live
  demo; only a tight scripted question-loop would trip a 429.

---

## Phase 8: web UI (FastAPI + vanilla SPA)

### 1. What was done

The product surface from the scenario — home → new chat / previous chats →
sidebar of chats → add a file/folder → ask → answer with Locators → "cite this
source". Built as a thin web layer over the existing pipeline (no new logic).

- **Stack decision: FastAPI + a vanilla SPA**, not React/Next. This is a Python,
  local-first, single-user repo with no Node toolchain; a built JS app would add
  a heavy build step for no benefit. The design skill's *aesthetic* rules are
  honoured regardless of framework (near-black palette — never pure black, one
  desaturated-emerald accent, Space Grotesk + Outfit + JetBrains Mono, real
  loading/empty/error states, tactile :active, SVG icons, zero emojis).
- **`app.py`** (new) — FastAPI. Serves the SPA and exposes the pipeline as JSON:
  chats (create/list/rename/messages), `upload` (multipart → `ingest_pdf` per
  PDF, non-blocking), `ask` (→ `answer`, builds attribution, stores both turns),
  and the cite endpoints (`get_source` / `confirm` / `cite`). Ingest/ask are
  sync `def` so FastAPI runs them in a threadpool (slow: local embeddings + the
  Ollama call) and the event loop stays responsive. `user_id` fixed to `user_1`
  (single-user v1 — multi-user is purely an auth layer; data already partitions
  by user_id).
- **`web/`** (new) — `index.html` (SVG icon lib, font links), `styles.css`
  (the black theme), `app.js` (hash router: `#/` home, `#/chats` list,
  `#/chat/:id` chat; upload via file picker + `webkitdirectory` folder picker;
  the full cite flow — fresh confirm-state check → confirm form if needed →
  style picker → rendered citation; toasts; skeleton loaders).
- **`query.py`** — added `c.source_id` to both retrieval SELECTs and
  `_row_to_chunk` so a chunk carries its Source id; the UI wires the "cite this
  source" button straight to it. (Score-column index shifted 9→10 accordingly.)

### 2. Tests (live, over HTTP against the running server)

**T21 — Web API end-to-end**
- **Precondition:** server up (model loaded in ~8–12s); empty library.
- **Input/Output:**
  - create chat → returns id; `GET /api/chats` → 200.
  - `upload` sample1.pdf → `{stored:1, chunks:8, source_id:22}`; fyp_final.pdf →
    `{stored:1, chunks:76}`.
  - `ask` an off-topic/meta question → **200**, `{refused:true, attribution:[]}`
    (coverage gate refuses before the LLM — no Ollama needed).
  - cite BEFORE confirm → **409** (UI's signal to show the confirm step).
  - `confirm` (author/title/year) → `confirmed:true`, locked.
  - `cite` in APA / Harvard / IEEE → three correct strings from one Source.
- **Postcondition:** PASS. Chat → ingest → ask(refusal) → confirm → cite all
  work over HTTP; attribution carries `source_id`/`confirmed` for the button.

### 3. Debugging

- **D8 — `answer()` LLM failure left an orphaned user turn + opaque 500.**
  Ollama hit an out-of-memory loading phi4-mini (`failed to allocate CPU buffer
  of size ~1.18GB` — the machine was under memory pressure, same cause as a
  scipy paging-file error seen earlier). `api_ask` stored the user message
  *before* calling `answer()`, so the failure orphaned it and returned an empty
  body. **Fix:** run `answer()` first, persist both turns only on success, and
  wrap it to return a clean **503** ("local model unavailable… check Ollama has
  memory, then retry"). Verified: ask now returns that 503, not an empty body.
- **Environmental note:** the *answered* (non-refused) LLM path could not be
  exercised live because Ollama remained OOM. The plumbing for it (attribution
  build, message storage, Locator render) is in place and structurally
  validated via the refusal path's response shape and the T20 cite flow; it
  needs Ollama with enough free memory to demo a full grounded answer.

- **Interpreter note:** two Pythons are on PATH (global Python313 + the project
  `venv`). Only the `venv` has the full stack reliably; run the server with
  `venv/Scripts/python.exe`. Global Python313 hit the paging-file error.

---

## Cite-on-demand: confirm locks a Source, cite renders any style

### 1. What was done

The "cite this source" button backend, and the design behind it. Three user
scenarios clarified the contract: (1) a student making notes and (3) a worker
writing a report only ever need the **Locator** (file + page) — they never touch
confirmation; only (2) a student writing a paper needs a **formatted Citation**,
which requires confirming the facts and choosing a style. So 2 of 3 users never
confirm anything; the Locator is the universal default and the Citation is the
explicit opt-in.

Two consequences, both now built in **`sources.py`** (new — Source lifecycle,
parallel to `chats.py`):

- **`confirm_source(source_id, author, title=None, year=None)`** — stores the
  real metadata and **locks** the Source as `confirmed=True`. Persisted: the
  system never asks again (the UI reads the flag). A citation needs author +
  year, so an incomplete confirm saves what's given but stays *unconfirmed*.
  Notes are locator-only and cannot be confirmed.
- **`cite_source(source_id, page, style="APA")`** — renders the Citation for a
  confirmed Source via the pure `format_citation`. **Style is chosen here, at
  cite time, never stored** — so one confirmed Source can be cited in APA /
  Harvard / IEEE interchangeably. Refuses on an unconfirmed Source (the UI shows
  the confirm step first).
- **`get_source(source_id)`** — row as a dict; the UI checks `confirmed` to
  decide confirm-then-cite vs cite-straight-away.

Design notes: style is per-citation at the API level (a paper-writer's choice),
not stored on the chat/source; the LLM is never involved (citations are built in
code from confirmed facts only).

### 2. Tests

**T20 — Cite-on-demand, live (real ingest + DB)**
- **Precondition:** a fresh chat; `fyp_final.pdf` ingested as an unconfirmed
  Work; a separate Notes source.
- **Input:** get_source → confirm_source(author/title/year) → cite_source in
  three styles; plus negative cases.
- **Output:** (1) unconfirmed on ingest; (2) cite-before-confirm **refused**;
  (3) confirm flips to True with stored author/year; (4) lock **persists** on a
  fresh read; (5) same Source cites correctly in **APA / Harvard / IEEE**; (6)
  confirming Notes **refused**; (7) confirm without a year **stays unconfirmed**.
- **Postcondition:** PASS (7/7). Confirm-once-then-locked holds; Locator stays
  the always-available default, Citation the confirmed-only extra.

---

## Folder ingest: "add a folder", non-blocking, into a chat

### 1. What was done

The "add a folder" step of the UI vision: a student drops a folder and every PDF
in it is ingested + embedded into the current chat, with **no prompts** — so they
can ask questions immediately and confirm metadata later (ADR 0003: ingest-now,
confirm-later).

- **`add_source.py` refactor** — pulled the pipeline out of the interactive
  `add_source` into a non-interactive core **`ingest_pdf()`** (extract → guard →
  chunk → store → embed, returns a result dict with a `status`). The interactive
  single-file path and the new folder path now share one pipeline — no
  duplication. `add_source` also threads `chat_id` through now.
- **`add_source_folder(folder, user_id, chat_id, recursive=False)`** (new) —
  globs `*.pdf`, ingests each into the chat as a **Work with `confirmed=False`**
  (Locator now; citable later via the "cite this source" button). Title comes
  from the PDF's metadata guess, falling back to the filename — never confirmed.
  A scanned / text-less / unreadable file is **skipped with a reason**, not
  fatal: one bad file can't abort the batch. Returns a per-file report and
  prints a summary (`N stored, M skipped`).
- `__main__` now offers file-or-folder.

### 2. Tests

**T19 — Folder ingest, live (real embeddings + real DB)**
- **Precondition:** a brand-new empty chat (`_has_material` False).
- **Input:** `add_source_folder("data", user_id='test_folder', chat_id=...)`
  over `fyp_final.pdf`, `sample1.pdf`, `sample2.pdf`.
- **Output:** 3 files stored (76 + 8 + 6 = **90 chunks**), 0 skipped; all
  sources `kind='work', confirmed=False`; `_has_material` flips to True; a
  second empty chat stays False (**isolation**); scoped `retrieve` returns
  chunks with **real page numbers** (pages 6/3/40, not null).
- **Postcondition:** PASS. Folder dropped → corpus queryable, scoped to its
  chat, no prompts, citable-later.

### 3. Debugging

- **D7 (non-bug):** the test probe first read `hit["page_number"]` and saw
  `None`; `retrieve` returns the key as **`page`**. Confirmed pages populate
  correctly (re-probed with `hit["page"]` → 6/3/40). No code change.

---

## Chat scoping: a chat owns its corpus (toward the UI vision)

### 1. What was done

Clarified the product/UI vision (home → new chat with a sidebar of past chats →
add a file/folder → ask → answer with file+page+summary → "cite this source"
button). The architectural consequence: **the unit you query is a chat, not the
whole user**. Built the foundation for that, scoping first.

- **[ADR 0005](0005-chat-owns-its-corpus.md)** — a chat owns its corpus
  (decided "chat owns corpus" over "shared per-user library").
- **Schema** (`setup_db.py`): new `chats` and `messages` tables; `chat_id` on
  `sources` (nullable, FK to chats) + index. Idempotent.
- **`chats.py`** (new): `create_chat` / `list_chats` / `rename_chat`,
  `add_message` / `get_messages` (attribution stored as JSONB).
- **`embed_store.store_source`**: new `chat_id` param so an ingested source is
  tagged with its chat.
- **`query.py`**: `_scope(chat_id, user_id)` helper; `retrieve`,
  `retrieve_keyword`, `retrieve_hybrid`, `retrieve_multi`, `_has_material`, and
  `answer` all take an optional `chat_id` and scope to it, falling back to
  `user_id` when absent (keeps Phase-7 eval and pre-chat data working).

### 2. Tests

**T17 — Chat scoping isolation (no LLM)**
- **Precondition:** schema migrated; throwaway `user_id='chatscope_test'`.
- **Input:** create chat_a + chat_b; ingest `data/fyp_final.pdf` into chat_a
  only; query "scalability and security" in each.
- **Output:** `_has_material` True for chat_a, False for chat_b; `retrieve` and
  `retrieve_hybrid` return 3 chunks in chat_a, **0** in chat_b; `list_chats`
  newest-first; `add_message`/`get_messages` round-trip with the `attribution`
  JSONB returned as a dict.
- **Postcondition:** PASS. A question sees only its own chat's corpus.

**T18 — Legacy/eval path unaffected (no LLM)**
- **Input:** `retrieve` / `_has_material` on `eval_corpus` with no chat_id;
  `evaluate.py --test-metrics`.
- **Output:** user-scoped retrieval still returns chunks; metrics 11/11 pass.
- **Postcondition:** PASS. `chat_id=None` falls back to `user_id` cleanly; no
  regression in the Phase-7 harness.

### 3. Debugging done
None — built clean. (One care point: `messages.attribution` written via
`psycopg.types.json.Json(...)` and read back as a parsed dict, verified in T17.)

### 4. Files changed this session
`setup_db.py` (chats/messages/chat_id), `chats.py` (new), `embed_store.py`
(`store_source` chat_id), `query.py` (`_scope` + chat_id throughout),
`docs/0005-chat-owns-its-corpus.md` (new ADR), `CLAUDE.md`, `DEVLOG.md`.

### 5. Open / next
- Folder ingest (`add_source_folder`) into a chat — batch, non-blocking.
- Cite-on-demand: a `confirm_source(source_id, author, title, year)` that flips
  `confirmed` and returns the formatted Citation (the "cite this source" button).
- Web UI (Phase 8) that calls these: new chat, add folder, ask, cite.

---

## Housekeeping: repository layout

Tidied the flat root for easier navigation (light-touch — Python source stays in
root so run commands are unchanged):
- `data/` ← the three PDFs (`fyp_final.pdf`, `sample1.pdf`, `sample2.pdf`).
- `docs/` ← `CONTEXT.md`, `DEVLOG.md`, and the four ADRs. `README.md` / `CLAUDE.md`
  stay at root by convention.
- Fixed hardcoded PDF paths to `data/...` (`eval_questions.CORPUS_PDF` plus the
  `__main__` test blocks in `ingest`/`chunk`/`embed_store`/`metadata`).
- Fixed cross-boundary markdown links (root→`docs/`, DEVLOG→`../CLAUDE.md`).
- Fixed a latent bug: `inspect_retrieval.py` ran `input()` at import time; wrapped
  it in an `inspect()` function under `if __name__ == "__main__"`.

**Verified:** all modules import; `CORPUS_PDF` resolves; `evaluate.py
--test-metrics` passes; link audit finds no broken relative links.

---

## Phase 7 evaluation: measured the upgrades, tuned the placeholders

### 1. What was done

Built a retrieval evaluation harness and used it to (a) measure the Phase-6
upgrades against the dense baseline and (b) replace guessed thresholds with
data-set values. New files: `eval_questions.py` (the gold set) and
`evaluate.py` (metrics + runner + tuning).

- **Gold set** — 12 questions over `fyp_final.pdf`, each labelled with the
  page(s) that genuinely answer it; labels verified against the actual chunk
  text (a page-by-page topic map), not generated. Plus 6 OFF_TOPIC negatives.
- **Metrics** — page-level `hit@k` (found any right page), `recall@k` (fraction
  of right pages found), `MRR`. Pure functions, unit-tested.
- **Runner** — runs dense / keyword / hybrid (and `--with-multi`) over the set
  and prints a before/after table. `--tune-floor` and `--sweep-candidate-k`
  drive the threshold analysis.

**Headline results** (corpus = 76 chunks; eval depth 10):

```
method    | hit@1 | hit@3 | hit@5 | recall@5 |  MRR
dense     | 0.833 | 0.917 | 0.917 |   0.819  | 0.875
keyword   | 0.500 | 0.917 | 1.000 |   0.792  | 0.725
hybrid    | 0.583 | 0.917 | 1.000 |   0.861  | 0.757
multi     | 0.583 | 0.750 | 0.917 |   0.708  | 0.700
```

Read honestly: dense is a strong precision-at-1 baseline; **hybrid** wins at
depth (hit@5 1.000, best recall@5 0.861) — which is how `answer()` actually
uses retrieval (top-3 chunks to the tutor). **Multi-query is the worst**: LLM
paraphrases drift off the precise terms and dilute the original-query signal —
strong evidence for keeping expansion conditional, not default.

**Tuning outcomes (applied to query.py):**
- Distance floor `MAX_DISTANCE`: covered questions have best dense distance
  <= 0.621, off-topic >= 0.763 — cleanly separable, so set **0.69** (was the
  loose 0.9 placeholder). Off-topic is now refused structurally, before the LLM.
- `CANDIDATE_K`: recall@5 rose 0.819 -> 0.861 from 10 -> 20, then plateaued, so
  set **20** (was 10).
- Re-architected the coverage gate: coverage is now decided by DENSE distance vs
  the floor, NOT by keyword overlap (OR-keyword matches almost anything and
  can't tell covered from off-topic). Hybrid is used only to rank once coverage
  is established.

### 2. Tests

Environment as prior entries. Eval corpus ingested under `user_id='eval_corpus'`
(76 chunks) and kept as a reusable fixture (`evaluate.py::ensure_corpus`
re-ingests if absent).

**T13 — Metric functions (unit, no DB/LLM)** — `python evaluate.py --test-metrics`
- **Input:** 3 synthetic (retrieved, relevant) cases incl. dup pages + a miss.
- **Output:** 11/11 sub-checks correct (hit@k, recall@k, RR).
- **Postcondition:** PASS.

**T14 — Method comparison over the gold set (dense/keyword/hybrid/multi)**
- **Input:** `python evaluate.py --with-multi`.
- **Output:** the table above.
- **Postcondition:** PASS (ran clean). Finding: hybrid best at depth; multi worst.

**T15 — Threshold tuning** — `python evaluate.py --tune-floor --sweep-candidate-k`
- **Output:** floor SEPARABLE (covered max 0.621 < off-topic min 0.763,
  midpoint 0.692); candidate_k plateau at 20.
- **Postcondition:** PASS. Values applied to `query.py` (`MAX_DISTANCE=0.69`,
  `CANDIDATE_K=20`).

**T16 — Structural refusal with the tuned floor (end-to-end, real LLM, instrumented)**
- **Precondition:** eval corpus ingested; `expand_query` + LLM `create` wrapped
  with counters.
- **Input:** `answer()` on a covered question, an off-topic question, an empty user.
- **Output:** covered → answered, **1 LLM call**; off-topic → `This is not
  covered in your material.`, **0 LLM calls**; empty → `No material found…`,
  **0 LLM calls**.
- **Postcondition:** PASS. Off-topic is now refused before any LLM call — the
  fix for the T4 weakness (where 0.9 let it through to the LLM layer).

### 3. Debugging done

**D6 — Keyword search ANDed every query term (recall collapse).**
- **Symptom:** first eval run, keyword scored **0.083** across all metrics and
  dragged hybrid *below* dense.
- **Diagnosis:** `retrieve_keyword` passed the whole question to
  `websearch_to_tsquery`, which ANDs terms (`a & b & c`) — a chunk had to
  contain EVERY word, so almost nothing matched. Earlier Phase-6 tests used
  1–2-word queries, which hid it.
- **Resolution:** added `_or_query()` — join the question's content tokens with
  the websearch OR operator (`a or b or c`). Keyword jumped to hit@5 1.000,
  MRR 0.725; hybrid's recall@5 rose to 0.861.

### 4. Files changed this session
`eval_questions.py` (new), `evaluate.py` (new), `query.py` (`_or_query` + OR
keyword fix, `MAX_DISTANCE`/`CANDIDATE_K` constants from eval, dense-only
coverage gate in `answer`), `DEVLOG.md` (this entry), `CLAUDE.md` (Phase 7).

### 5. Open / next
- Multi-query's value is unproven (it hurt on this well-phrased set). To know
  whether the router's *selective* expansion helps, the gold set needs more
  short/vague questions — future eval work.
- Routing thresholds (`SHORT_QUESTION_WORDS`, `WEAK_MATCH_DISTANCE`) still
  reasoned, not yet eval-set (no labelled "needed expansion" data).
- Confirm-later folder/batch ingest (Phase 6 remaining); web UI (Phase 8).
- Optional: UTF-8 stdout for the Windows CLI (D4, still open).

---

## Phase 6 retrieval upgrade: multi-query + hybrid (RRF)

### 1. What was done

Built the Phase-6 retrieval pipeline in five tested steps, replacing the naïve
single dense search with **multi-query expansion + hybrid (dense + keyword)
search fused by Reciprocal Rank Fusion**. See [CLAUDE.md](../CLAUDE.md) "What comes
next" and the ADRs for the *why*.

1. **FTS infrastructure** — `setup_db.py`: added a `GENERATED ALWAYS … STORED`
   `tsvector` column (`text_tsv`) on `chunks` plus a GIN index, with an
   idempotent migration. The generated column self-maintains (back-fills on
   creation, updates on every insert) so no app code touches it.
2. **Keyword retrieval** — `query.py::retrieve_keyword()`: Postgres full-text
   search via `websearch_to_tsquery` (safe on arbitrary user text) + `ts_rank`.
   Both retrievers now also return the chunk `id` so fusion can match by identity.
3. **RRF fusion** — `query.py::rrf_fuse()` (k=60) + `retrieve_hybrid()`: fuse
   ranked lists by position, not raw score (cosine distance and ts_rank aren't
   comparable directly). Fields merged across lists so a fused chunk keeps both
   its `distance` and `rank`.
4. **Multi-query** — `query.py::expand_query()` (local LLM rewrites the question
   into variants; degrades to the original if the LLM is down) + `retrieve_multi()`
   (hybrid per variant, one RRF pass over all lists; dense floor applied per variant).
5. **Wiring** — `answer()` now uses `retrieve_multi`; the empty-library check
   moved BEFORE retrieval so we refuse before *any* LLM call (expansion included).

### 2. Tests

Environment: same as prior entry. Test corpus = `fyp_final.pdf` ingested under a
throwaway `user_id='phase6_test'` (confirmed Work, 76 chunks); deleted after the run.

---

**T6 — FTS schema migration + generated-column back-fill**
- **Precondition:** `chunks` table exists without `text_tsv`.
- **Input:** `setup_db.setup()`, then ingest 76 chunks, then inspect schema + counts.
- **Output:** column `text_tsv tsvector` present; index `chunks_text_tsv_idx` (GIN)
  present; **76/76** chunks have a non-null tsvector (auto-populated on insert).
- **Postcondition:** PASS. Migration idempotent; generated column self-maintains.

---

**T7 — Keyword retrieval (FTS)**
- **Precondition:** test corpus ingested.
- **Input:** `retrieve_keyword("scalability")`; `retrieve_keyword("chatbot grading")`;
  `retrieve_keyword("zzzxq purple banana")`.
- **Output:** "scalability" → the "Security & Scalability" section (p43) + a
  scalability requirement (p22), ranked by `ts_rank`. Nonsense query → **0 rows**.
- **Postcondition:** PASS. `@@` requires a real lexical match, so off-vocabulary
  queries return nothing rather than noise.

---

**T8 — RRF hybrid fusion**
- **Precondition:** test corpus ingested.
- **Input:** `retrieve("How does the system handle scalability and security?")`
  vs `retrieve_keyword(...)` vs `retrieve_hybrid(...)`, top-5 each.
- **Output:** chunk **id=741 (p22)** is #1 in both lists → RRF gives it the top
  score (0.0328 = 1/61 + 1/61). Chunk **id=728 (p14)**, keyword-#2 but absent
  from dense top-5, is **promoted into the fused top-3** — a lexical hit dense
  missed, rescued by fusion.
- **Postcondition:** PASS. Scores match the RRF formula; agreement and lexical
  recall both behave as designed.

---

**T9 — Multi-query expansion (real LLM)**
- **Precondition:** Ollama up; test corpus ingested.
- **Input:** `expand_query("How is the app kept reliable under load?")`, then
  `retrieve_hybrid` (single) vs `retrieve_multi` (top-5).
- **Output:** LLM produced 3 distinct paraphrases ("peak usage", "high user
  demand", …). Multi-query promoted **id=741 (p22)** — the scalability section —
  to **#1**, though single-query hybrid had it nowhere in its top-5.
- **Postcondition:** PASS. Variants reached material the original phrasing missed
  (the recall win multi-query exists for).

---

**T10 — Full pipeline end-to-end (real LLM): covered / off-topic / empty**
- **Precondition:** Ollama up; test corpus ingested; empty `user_id` for the last case.
- **Input:** `answer("How does the system handle scalability and security?")`;
  `answer("What is the boiling point of liquid mercury in kelvin?")`;
  `answer("anything", user_id="nobody_here_xyz")`.
- **Output:**
  - Covered → grounded tutor answer (Firebase elastic scalability, 2FA, HTTPS,
    role-based access) naming the integration gap; Locators p22 + p44 with APA
    Citations (confirmed Work).
  - Off-topic → refusal, detected by `is_refusal()` (see D5).
  - Empty → `No material found…`, **0 chunks, no LLM call**.
- **Postcondition:** PASS. All three branches behave per the refusal contract.

**Cleanup:** all `phase6_test` rows deleted after the run.

### 2b. Follow-up — adaptive retriever routing (same day)

Reviewed the cost of running multi-query on *every* call: it adds a whole extra
LLM round-trip (expansion), the scarce resource on this local/CPU setup. Decided
multi-query should be **conditional**, not always-on. `answer()` now tries the
cheap **hybrid** path first (no extra LLM call) and escalates to **multi-query**
only when `should_expand()` judges the hybrid result weak — using cheap signals
only (NO LLM): very short question, or closest dense match farther than
`WEAK_MATCH_DISTANCE` (or keyword-only hits). New `expand` arg on `answer()`:
`"auto"` (default), `True` (force), `False` (never). Thresholds
(`SHORT_QUESTION_WORDS=5`, `WEAK_MATCH_DISTANCE=0.6`) are Phase-7-tunable
placeholders, like the distance floor.

**T11 — Routing decision (unit, no DB/LLM)**
- **Precondition:** `should_expand()` importable.
- **Input:** 5 fabricated (question, hybrid_chunks) cases.
- **Output:** empty hybrid → False; short question → True; long+strong match
  (dist 0.49) → False; long+weak match (dist 0.75) → True; long+keyword-only → True.
- **Postcondition:** PASS (5/5). Decision uses only length + best distance.

**T12 — Routing changes LLM-call count (end-to-end, real LLM)**
- **Precondition:** Ollama up; test corpus ingested; `expand_query` and the LLM
  `create` call wrapped with counters.
- **Input:** `answer()` on a strong long question (auto); a short question
  "authentication?" (auto); the same short question with `expand=False`.
- **Output:** strong/auto → **expand=0, LLM calls=1** (hybrid only); short/auto →
  **expand=1, LLM calls=2** (escalated); short/forced-off → **expand=0, calls=1**.
  All produced grounded answers; the escalated short query gave a better 2FA
  answer than the forced-off one — live evidence the trigger is well-placed.
- **Postcondition:** PASS. Well-phrased questions dropped from 2 LLM calls to 1;
  expansion paid only when warranted.

**Cleanup:** `phase6_test` rows deleted; temp test script removed.

### 3. Debugging done

**D5 — LLM paraphrases the canonical refusal string.**
- **Symptom:** off-topic question was correctly refused by the LLM, but the
  exact-match check `ans == REFUSAL` returned False. Across three runs the model
  emitted three *different* wordings: `"This information is not covered in your
  material."`, `"This not covered in your material."`, and the canonical one.
- **Diagnosis:** the refusal contract relied on the LLM reproducing an exact
  string, which a small local model does not do reliably. A genuine refusal
  would have slipped through and printed Locators.
- **Resolution:** added `is_refusal()` — matches the core phrase `"not covered
  in your material"` AND requires a short (≤60-char) single sentence, so a long
  grounded answer that legitimately *names* a coverage gap (ADR 0004) is not
  misread as a full refusal. Unit-tested on 5 cases incl. the gap-naming case;
  strengthened the prompt to demand the exact sentence "word for word". The
  structural distance floor (Phase 7) remains the real long-term fix.

### 4. Files changed this session
`setup_db.py` (FTS column + GIN index), `query.py` (`retrieve_keyword`,
`rrf_fuse`, `retrieve_hybrid`, `expand_query`, `retrieve_multi`, `is_refusal`,
rewired `answer`), `DEVLOG.md` (this entry), `CLAUDE.md` (Phase 6 marked built).

### 5. Open / next
- Tune the distance floor + RRF `candidate_k`/`top_k` via Phase-7 recall@k eval
  (T10 off-topic still relied on the LLM layer — the 0.9 floor stays loose).
- Confirm-later folder/batch ingest (CLI still confirms per file).
- Optional: UTF-8 stdout for the Windows CLI (D4, still open).

---

## Locate-by-default redesign + first build

### 1. What was done

**a) Codebase review.** Reviewed the Phase-5 codebase against [CLAUDE.md](../CLAUDE.md).
Found and fixed four issues in an earlier pass: duplicate tail chunk in
`chunk.py`, silent zero-chunk source in `add_source.py`, duplicated DB
credentials (centralized into `db.py`), and connection leaks (moved to
`with connect()` context managers).

**b) Design grilling.** Stress-tested the plan and resolved the domain language.
Outcomes recorded as:
- Glossary [CONTEXT.md](CONTEXT.md): **Source, Work, Notes, Citation, Locator, Covered, Answer**.
- [ADR 0001](0001-works-cited-notes-located.md) — Works cited / Notes located *(superseded)*.
- [ADR 0002](0002-local-by-default-hosted-opt-in.md) — Local by default; hosted LLM opt-in only.
- [ADR 0003](0003-locate-by-default-cite-on-confirmation.md) — **Locate by default; cite only on confirmation** (the pivot).
- [ADR 0004](0004-grounded-tutor-only-from-material.md) — Grounded tutor, never a knowledgeable one.

**c) Build (5 steps).** Implemented the locate-by-default design:
1. `setup_db.py` — added `kind` (`work`|`notes`) + `confirmed` columns, with an
   idempotent `ALTER TABLE … ADD COLUMN IF NOT EXISTS` migration for existing DBs.
2. `embed_store.py` — `store_source()` now accepts/stores `kind` + `confirmed`
   (Notes can never be confirmed).
3. `citations.py` — added `format_locator()` / `render_locator()` (the default,
   always-honest attribution: file + page + summary + context); kept
   `format_citation()` for confirmed Works only.
4. `query.py` — refusal-before-LLM contract, grounded-tutor system prompt,
   retrieval now carries `kind`/`confirmed`, output is **Locator by default +
   Citation only for confirmed Works**.
5. `add_source.py` — fast Work/Notes choice; Notes skip metadata; a Work is
   `confirmed` only when it actually has author + year.

### 2. Tests

Environment: Windows 10, Postgres+pgvector in Docker (`citefinder-db`, port 5432),
Ollama (port 11434) serving `phi4-mini`, Python venv. All tests run from repo root.

---

**T1 — Attribution rendering (unit, no DB/LLM)**
- **Precondition:** `citations.py` imports cleanly; ASCII fixes applied.
- **Input:** `format_locator("fyp_final.pdf", 16, "<excerpt>", title="Encouraged Digital Academic Portal")` and `format_citation({author,title,year,filename}, page=16, style="APA")`.
- **Output:**
  - Locator → `[from] Encouraged Digital Academic Portal - p. 16` + summary line + quoted context.
  - Citation → `Khan, H. M. H. (2025). Encouraged Digital Academic Portal (p. 16).`
- **Postcondition:** PASS. Locator and Citation render with no non-ASCII crash.

---

**T2 — Schema migration**
- **Precondition:** `sources` table exists without `kind`/`confirmed` (pre-redesign DB).
- **Input:** `setup_db.setup()` then read `information_schema.columns` for `sources`.
- **Output:** columns = `id, user_id, title, author, year, filename, metadata_complete, kind, confirmed`.
- **Postcondition:** PASS. Migration is idempotent (safe to re-run); both new columns present.

---

**T3 — Covered question, full pipeline with real LLM**
- **Precondition:** DB + Ollama (`phi4-mini`) up. Throwaway `user_id="verify_test"`
  cleared. `fyp_final.pdf` ingested as a **confirmed Work** (author `Khan, H. M. H.`,
  year `2025`); 76 chunks stored (source #8).
- **Input:** `answer("What is this project about?", user_id="verify_test")`.
- **Output:** Grounded tutor answer describing the "Encouraged Digital Academic
  Portal" (auth, course management, grading, chatbot, document conversion,
  scalability) — all traceable to the document. Followed by 3 **Locators**
  (pages 3, 40, 6) each with an **APA Citation** (source is a confirmed Work).
- **Postcondition:** PASS. Answer grounded in real chunks; Locator + Citation
  emitted only because `kind='work'` and `confirmed=True`.

---

**T4 — Off-topic question, refusal (LLM second layer)**
- **Precondition:** same ingested confirmed Work as T3.
- **Input:** `answer("What is the boiling point of liquid mercury in kelvin?", user_id="verify_test")`.
- **Output:** `This is not covered in your material.`
- **Postcondition:** PASS. Chunks passed the loose 0.9 distance floor but the
  grounded-tutor prompt's second-layer refusal correctly rejected them. (Live
  evidence the floor is loose — to be tuned by Phase-7 eval — and that the
  two-layer refusal contract is necessary.)

---

**T5 — Empty library, refuse-before-LLM**
- **Precondition:** `user_id="nobody_here_xyz"` has no chunks.
- **Input:** `answer("anything", user_id="nobody_here_xyz")`.
- **Output:** `No material found. Have you ingested any documents?` (chunks=0).
- **Postcondition:** PASS. Returned **without** calling the LLM (`_has_material`
  false → structural refusal), confirming the refuse-before-LLM path.

**Cleanup:** all `verify_test` rows deleted after the run; real data untouched.

### 3. Debugging done

**D1 — Ollama out-of-memory loading phi4-mini.**
- **Symptom:** `openai.InternalServerError 500 … failed to allocate buffer of
  size 1302331392 … unable to allocate CPU_REPACK buffer`.
- **Diagnosis:** Environment, not code. `phi4-mini` (2.5 GB on disk) could not
  allocate its ~1.3 GB weight buffer; only ~3.8 GB RAM free of 12 GB (VS Code
  2 GB, svchost 1 GB, this CLI 1 GB, Edge 0.7 GB, Docker ~0.6 GB). It failed
  even standalone (`ollama run phi4-mini`), confirming a hard RAM wall. Lowering
  context would not help — the failing buffer is model weights, not KV cache.
- **Resolution:** Freed RAM (closed Edge), then re-ran. Model loaded; T3–T5
  passed. (No code change — the pipeline was correct up to the LLM boundary.)

**D2 — Windows console UnicodeEncodeError.**
- **Symptom:** `UnicodeEncodeError: 'charmap' codec can't encode character
  '\U0001f4c4'` when printing a Locator; later `—` and `…` rendered as `�`.
- **Diagnosis:** Output contained non-ASCII glyphs (📄 emoji, `…` ellipsis, `—`
  em dash) that the Windows cp1252 console cannot encode — a real bug for
  Windows CLI users.
- **Resolution:** Made `citations.py` output ASCII-safe: `📄`→`[from]`,
  `…`→`...`, `—`→`-`. Re-tested (T1) clean.

**D3 — Leftover test data after a failed run.**
- **Symptom:** First integration run crashed at the LLM call (D1) before its
  cleanup step, leaving `verify_test` rows (source #7, 76 chunks) in the DB.
- **Resolution:** Test scripts now DELETE `verify_test` rows at both the start
  and end of the run, so a mid-run failure cannot poison the next run.

**D4 — (minor) Non-ASCII inside PDF excerpts.**
- **Symptom:** a `�` appeared inside a quoted Locator *context* (a `•` bullet
  from the source PDF) when printed to the cp1252 console.
- **Status:** Not a storage/logic bug — the data stores correctly; it is only a
  console-print artifact. Open item: optionally force UTF-8 stdout for the CLI;
  the planned web UI renders it correctly as-is.

### 4. Files changed this session
`db.py` (new, earlier), `setup_db.py`, `embed_store.py`, `citations.py`,
`query.py`, `add_source.py`, `CONTEXT.md` (new), `0001`–`0004` ADRs (new),
`README.md`, `CLAUDE.md`, `DEVLOG.md` (this file).

### 5. Open / next
- Phase 6 retrieval upgrade: multi-query + hybrid (dense + Postgres FTS) with RRF.
- Tune the distance floor via Phase-7 recall@k eval (T4 showed 0.9 is loose).
- Confirm-later folder ingest (CLI still confirms per file).
- Optional: UTF-8 stdout for the Windows CLI (D4).
