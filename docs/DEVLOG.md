# CiteFinder — Development Log

A running record of work done, decisions made, tests run, and bugs fixed.
Newest entries at the top. For the *why* behind decisions see [CONTEXT.md](CONTEXT.md)
and the ADRs (`0001`–`0007`).

---

## Embedder upgrade: all-MiniLM-L6-v2 → e5-small-v2

### 1. What was done

Acting on the T31 finding that the embedding model — not chunking — is the lever
for dense retrieval quality, A/B'd four 384-dim ONNX embedders over the
ground-truthed benchmark (`bench_embedders.py`, offline + in-memory, no DB/prod
change): current MiniLM vs bge-small-en-v1.5, gte-small, e5-small-v2. All loaded
through the same torch-free path (tokenizers + onnxruntime + numpy), with each
model's correct pooling (bge=CLS, others=mean) and training prefixes.

**e5-small-v2 won decisively** (dense, 7,204-passage corpus):

| model | lexical hit@5 | lexical MRR | semantic hit@5 | semantic MRR |
|---|---|---|---|---|
| MiniLM (old) | 0.750 | 0.558 | 0.094 | 0.056 |
| bge-small | 0.883 | 0.797 | 0.072 | 0.042 |
| gte-small | 0.906 | 0.798 | 0.106 | 0.064 |
| **e5-small-v2** | **0.972** | **0.892** | **0.156** | **0.094** |

Swapped it into production behind the existing `embed()` interface:
- `embedder.REPO` → `Xenova/e5-small-v2` (ONNX mirror; same onnx/model.onnx +
  tokenizer.json files, still 384-dim → drop-in for the vector(384) column).
- **Asymmetric prefixes**: e5 needs `query: ` on questions and `passage: ` on
  documents. `embed(texts, kind="query"|"passage")` now prepends the right one;
  `query.py` (questions) uses the default `query`, `embed_store` (chunks) passes
  `passage`. Omitting prefixes materially hurts e5, so this is required.
- **Re-tuned the coverage floor**: e5 distances are on a far smaller scale, so the
  MiniLM-era `MAX_DISTANCE=0.69` passed everything (never refused). `--tune-floor`
  on the e5-indexed eval corpus gave covered 0.109–0.211 vs off-topic 0.178–0.249
  (overlapping), so set `MAX_DISTANCE=0.22` — just above covered-max to preserve
  recall, with the LLM grounded-tutor refusal as the backstop for the overlap.

**Breaking change — re-indexing required.** e5 query-vectors can't be compared to
MiniLM passage-vectors, so every stored corpus must be re-embedded. Fresh desktop
installs are unaffected; existing dev/user corpora (the eval corpus, the benchmark
corpora, any real chats) must be re-ingested. No new dependencies (same ONNX
stack). Ingest is ~2× slower (e5 is 12-layer vs MiniLM's 6) — acceptable given
ingest is embed-bound and the quality jump is large; a quantized ONNX can recover
most of the speed later.

### 2. Tests

**T32 — embedder A/B + production swap (real embedder, real LLM)**
- Offline A/B table above (`bench_embedders.py`, in-memory dense ranking against
  ground truth; passage embeddings cached to app-data).
- **Real Phase-7 eval, eval corpus re-ingested with e5** (`evaluate.py
  --tune-floor`): dense hit@1 0.667→**0.750**, recall@5 0.819→**0.931**, MRR
  0.792→**0.875**; hybrid hit@1 0.500→**0.917**, MRR 0.715→**0.944** vs the MiniLM
  baseline — large gains on real data, no regression.
- **End-to-end `answer()` (real LLM)**: covered eval question → grounded answer (5
  chunks, not a refusal); all 6 off-topic → refused (3 by the 0.22 floor, 3 by the
  LLM backstop on the overlap) — the two-layer refusal contract holds under e5.
- **Large-scale production-path re-validation** (7,204-chunk bench re-ingested with
  e5, queried through `query.py`): reproduces the offline A/B exactly — dense
  lexical hit@5 0.972 / MRR 0.892, semantic hit@5 0.156 — confirming the
  query/passage prefix wiring is correct. Bonus: **hybrid lexical is now perfect
  (hit@1/MRR 1.000)** — e5's stronger dense arm means RRF no longer demotes
  keyword's exact hits (the T31 fusion-dilution finding resolves itself). The
  coverage floor still OVERLAPS at scale (off-topic absent-entity negatives are
  phrased identically to covered Qs — no distance can separate them; the LLM
  refusal backstop remains required, as designed). Ingest with e5: ~20 chunks/sec
  vs ~33 for MiniLM (the expected ~1.6× cost of a 12- vs 6-layer model).
- `py_compile` on `embedder.py`, `embed_store.py`, `query.py` → OK.

---

## Large-scale retrieval benchmark (synthetic, ground-truthed)

### 1. What was done

Built a synthetic large-scale benchmark to stress retrieval far beyond the
78-chunk Phase-7 set, with exact ground truth. `bench_corpus.py` generates topical
filler (hard, same-subject distractors) and plants unique coined "needle" entities
in single fact sentences at known locations; a chunk is relevant iff it contains
that entity. Each needle yields a **lexical** question (contains the entity → tests
keyword) and a **semantic** paraphrase (no entity → tests dense meaning-match),
plus off-topic negatives (absent entities + real trivia). `bench_eval.py` reports
hit@k / MRR by question type, per-query latency, coverage-floor separation, a
candidate_k sweep, and an optional LLM multi-query comparison. Corpus is isolated
under `bench_user*` (`python bench_corpus.py --clean`).

Ran two corpora over the SAME questions: default chunking (1200/300, 120 docs →
2,409 chunks) and a small-chunk variant (350/80 → 7,204 chunks).

### 2. Findings (data-backed)

- **Semantic (paraphrase) retrieval is the real weakness.** All methods get
  hit@5 ≈ 0.07–0.11 on paraphrased questions among same-topic distractors. Cause
  isolated to the **embedding model** (384-d MiniLM), not chunking: shrinking
  chunks 1200→350 barely moved it (0.072→0.094 dense, 0.078→0.111 hybrid).
- **Chunk granularity strongly helps lexical/dense, though.** dense lexical
  hit@5 0.483→0.750, MRR 0.326→0.558; hybrid lexical MRR 0.773→0.933 going
  1200→350. (Big chunks dilute a specific fact's signal under filler.)
- **RRF hybrid demotes perfect keyword hits.** keyword is perfect on exact-term
  questions (hit@1/MRR 1.000); hybrid drops it (MRR 0.77 at 1200, 0.93 at 350) by
  blending in dense's weaker ranking. Fusion should weight/boost exact matches.
- **The coverage floor does NOT generalize.** `MAX_DISTANCE=0.69` (tuned on one
  small PDF) wrongly accepts 57% (1200) / 75% (350) of off-topic at scale; covered
  vs off-topic distances now OVERLAP. An absolute distance gate is insufficient at
  scale — needs relative-gap / per-corpus calibration / rerank score.
- **Ingest is embedding-bound, NOT insert-bound** (corrected an initial wrong
  read). Re-ingesting the same 2,409 chunks row-by-row (232 s) vs batched
  `executemany` (244 s) showed no gain; the two corpora took near-equal time for
  near-equal TOTAL tokens (≈617k vs ≈576k) despite 3× the row count. Cost ≈ total
  tokens / CPU ONNX throughput (~2,500 tok/s here). Kept `executemany` (good
  practice, fewer round trips) but the real ingest lever is the embedder.
- **Dense latency grows with corpus size** (seq scan, no ANN index): ~90 ms @
  2.4k → ~130 ms @ 7.2k chunks. Fine now; an HNSW index is the lever for large
  libraries (and would re-introduce approximate recall — re-tune the floor then).
- **Multi-query expansion stays a weak lever at scale** (real LLM, 40 semantic
  Qs): hit@5 0.20→0.25 but no MRR gain and ~11× latency (170 ms → 1.9 s). Confirms
  the Phase-7 decision to keep it conditional/off by default.

### 3. Tests

**T31 — synthetic benchmark (real embedder + real LLM for the multi pass)**
- Corpora: `bench_user` 2,409 chunks (1200/300), `bench_user_small` 7,204 chunks
  (350/80), 120 docs, 180 needles, 360 gold + 40 off-topic; deterministic (seed 7).
- Numbers above are the harness output; `executemany` correctness confirmed by the
  bench retrieving correctly (keyword lexical hit@1 1.000 on both corpora).
- No production retrieval code changed in this round (only `store_chunks` insert
  shape), so the Phase-7 eval is unaffected.

### 4. Recommended improvements (prioritised, not yet built)

1. **Stronger ONNX embedder** (e.g. bge-small/base-en-v1.5, e5-small, gte-small)
   — the single highest-leverage fix for semantic recall; stays torch-free.
2. **Cross-encoder reranker** over the fused candidate pool — lifts both lexical
   and semantic at modest latency; also gives a better coverage signal than raw
   distance.
3. **Coverage gate beyond an absolute floor** — relative gap / per-corpus
   calibration / rerank score (the 0.69 floor mis-fires at scale).
4. **Fusion that respects exact matches** — boost/weight the keyword arm so a
   verbatim rare-term hit isn't demoted by dense.
5. **HNSW index** once per-corpus chunk counts grow large.

---

## Pipeline review fixes: efficiency + accuracy + hygiene

### 1. What was done

A focused review of the chunk → embed → retrieve pipeline (correctness,
efficiency, cleanliness) surfaced redundant work on the hot path and a few
accuracy/cleanup edges. Fixed the actionable ones; two were deliberately left as
documented trade-offs (below).

**Efficiency**
- **One dense scan per covered query, not two.** `answer()` used to run a
  `retrieve(top_k=1)` coverage gate *and then* a second dense scan inside
  `retrieve_hybrid`. The gate result is a strict subset of the hybrid dense pool,
  so the gate now pulls the full `CANDIDATE_K` pool once and reuses it (empty pool
  ⇒ refuse). `retrieve_hybrid` / `retrieve_multi` gained optional `dense`/`keyword`
  params so the precomputed lists pass straight through (standalone callers like
  `evaluate.py` are unaffected — they still compute their own).
- **No recompute on expansion.** When the router escalates to multi-query, the
  original question's dense+keyword lists are reused instead of re-queried; only
  the expansion variants hit the DB.
- **Batched embedding.** `embedder.embed` now embeds in `BATCH_SIZE`(=64) groups
  instead of one `encode_batch` over the whole document/folder, so ingesting a big
  folder no longer builds one giant `(N, seq, 384)` tensor. Verified bit-identical
  to the single-batch result (max abs diff `0.0`).
- **Indexed `chunks.source_id`** (Postgres doesn't auto-index FKs): speeds the
  chunks→sources join, the page-intent `MAX(page)` subquery, and `ON DELETE CASCADE`.

**Accuracy**
- **Page intent no longer hijacks content questions.** A page reference now routes
  to the whole-page summariser only when the question is *about* the page
  (`_PAGE_FOCUS_RE`); "explain the method on page 5" falls through to normal
  retrieval (which can span pages) instead of being answered from page 5 alone.
- **Per-line TOC filter.** Dotted table-of-contents lines are dropped per line
  before packing (`_is_toc_line`), matching the original intent — a TOC line can't
  survive by being diluted in a prose chunk, and a real heading isn't dropped for
  sharing a chunk with TOC junk.

**Hygiene**
- Moved `import re` and `from sources import ...` to the top of `query.py` (they
  sat mid-file; `is_refusal` used `re` above its old import — worked only at
  runtime). Dropped the `embed_store.embed_texts` one-line pass-through (callers
  use `embed` directly).

**Deliberately NOT changed (trade-offs, not omissions):**
- *No ANN index on `chunks.embedding`.* Exact cosine is the most accurate option
  and per-chat corpora are small; an HNSW index trades recall for speed and would
  need re-tuning the Phase-7 thresholds. Revisit only when per-chat corpora grow.
- *Chunk size stays 1200 chars* even though it can exceed the model's 256-token
  limit (the tail of a few chunks isn't embedded). Sizing by token count regressed
  recall in a prior experiment (1400/200) and the current size is eval-validated.

### 2. Tests

**T30 — pipeline review fixes (real LLM, eval harness, real PDF)**
- Precondition: eval corpus re-ingested from scratch (78 chunks) so the chunker
  (A3) + batched embedder are exercised on a fresh ingest.
- Routing unit check (pure, no DB): `_page_target` — "what is on page 2"→2,
  "first page"→first, "the last page"→last, "summarise page 3"→3, "page 2?"→2,
  **"explain the method described on page 5"→None**, "how is page ranking
  computed"→None; `_is_corpus_meta` — file-list phrasings True, "explain
  backpropagation" False. All pass.
- Embedding parity: single→`(384,)` norm 1.0; list→`(N,384)`; one-batch vs
  3-batch (BATCH_SIZE=2) and batched vs per-item both max abs diff `0.0`.
- Eval harness (re-ingested): hybrid **hit@5 1.000, recall@5 0.861, dense
  recall@5 0.819 — identical to the Phase-7 baseline, no regression**; floor
  analysis still separable (covered ≤0.620 < off-topic ≥0.772), suggested floor
  0.696 ≈ the tuned `MAX_DISTANCE=0.69`.
- End-to-end `answer()` (real LLM via the configured endpoint): covered eval
  question → grounded answer, 5 chunks, not a refusal; off-topic ("sourdough
  recipe") → canonical refusal, 0 chunks, refused before any LLM call (new
  dense-pool gate intact); "list all the files" (chat 24) → real file list, 0
  chunks, no LLM; "what is on page 1" (chat 24) → title-page summary, 1 chunk.
- `py_compile` on all changed files → OK.
- Postcondition: retrieval results unchanged vs the Phase-7 baseline; the ask path
  does one fewer dense scan per covered query (and avoids the original-question
  recompute on expansion); ingest embeds in bounded-memory batches.

---

## Retrieval quality + structural (meta/page) questions

### 1. What was done

User testing on a real thesis PDF surfaced three failure types. Diagnosed against
the live data (the content was indexed — it was retrieval/handling), then fixed.

**Tier 1 — structural intents (deterministic, no retrieval, answered from real
data so nothing is hallucinated):**
- **Corpus-meta** ("list the files", "what are the file names", "how many
  documents") → answered from `list_sources_for_chat` (real filenames + count).
  Conservative regex (`_is_corpus_meta`); content questions don't trigger it.
- **Page-scoped** ("what's on page 2 / the first page / the last page") → fetch
  that page's actual chunks (`_fetch_page`) and summarise, bypassing the semantic
  gate (the user named the page). Fixes the previously-refused "first page"
  question — it now returns the title-page authors/supervisor.
- Both wired into `answer()` before the RAG path; meta runs before the
  empty-library check, page after it.

**Tier 2 — retrieval recall/ranking:**
- **Unfloored keyword arm.** `retrieve_keyword` no longer floors lexical hits by
  dense distance (the floor was silently dropping the real "3.3 Design
  Description" chunk, whose dense distance exceeds the coverage floor). Off-topic
  refusal is still enforced by the separate dense coverage gate, so grounding is
  unchanged. Effect: on the test chat, the Design Description page jumped from
  not-in-top-5 / out-ranked-by-"Dedication" to **#1**.
- **Line-aware chunking.** `chunk.py` now packs whole lines up to the size budget
  (`_pack_lines`) instead of slicing at a fixed character offset, so a heading
  stays with the text that follows it. Kept the original 1200/300 size+overlap —
  an experiment at 1400/200 regressed dense recall (bigger chunks dilute the
  embedding), so size/overlap were restored.
- **answer() top_k 3 → 5.** Feeds the LLM more of a section; turned the partial
  "the description is cut off" reply into a full, correct explanation of the
  Design Description's five layers.

### 2. Tests

**T29 — retrieval quality before/after (real LLM, real PDF + eval harness)**
- Diagnosis (chat 24): "what's on the first page" → REFUSED; "Explain Design
  Description" → p6 "Dedication" ranked #1, real p35 section absent from top-5;
  title-page author names beyond the 0.69 floor.
- After Tier 1: "list all the files" / "names of the files" → the real file list;
  "what's on the first page" → title-page authors + supervisor with a p.1 Locator.
- After Tier 2 (chat 24 re-indexed with the new chunker): "Explain Design
  Description" → grounded answer naming the five architecture layers; p35 now
  retrieved. Off-topic ("capital of France") still → canonical refusal (gate
  intact).
- **Eval harness (re-ingested eval corpus, new chunker + unfloored keyword):**
  hybrid **hit@5 1.000, recall@5 0.861, MRR 0.757 — identical to the Phase-7
  baseline (no regression)**; keyword recall@5 improved 0.708 → 0.792.
- Known/limits: multi-query expansion can still re-surface a generic chunk
  (the "Dedication" page) high in attribution on well-phrased questions — the
  Phase-7 finding that selective expansion needs a vaguer gold set to tune
  (`SHORT_QUESTION_WORDS` / `WEAK_MATCH_DISTANCE`) still stands. Existing chats
  need a re-index to get the chunker change; new uploads get it automatically.
- `node --check web/app.js` → OK.

---

## Phase 14: local LLM provisioning (Ollama detect-and-guide)

### 1. What was done

The "Local" option is now self-provisioning (ADR 0007): the app detects Ollama,
offers a small RAM-sized model catalog, and pulls a chosen model with a live
progress bar — no terminal.

- New `local_llm.py` (stdlib only — no new dependency): `status()` (installed /
  running / pulled models + a curated `CATALOG` with size + RAM hints +
  download URL) and `pull(model)` (streams Ollama's native `/api/pull` progress
  as dicts with a computed `percent`). Talks to Ollama's API on :11434; the
  answer path still reaches the model via the OpenAI-compatible :11434/v1
  endpoint.
- `app.py`: `GET /api/ollama/status` and `POST /api/ollama/pull` (a
  `StreamingResponse` of newline-delimited JSON for the download bar).
- `web/`: when the provider is **Local (Ollama)**, the Settings modal shows a
  panel instead of a free-text model field — Ollama status (with install/start
  guidance + a Re-check when it's down), a model list (catalog ∪ already-pulled)
  with size/RAM hints, a per-model **Download** that streams progress into a bar,
  and a radio-style pick. Cloud providers keep the base_url/model/key fields. The
  panel keeps hidden `f-base`/`f-model` inputs so Save/Test stay uniform.
- `query.test_connection` timeout 20s → **90s**: a local model cold-loads into
  memory on its first request (a one-token ping measured 17.4s cold here), so a
  short timeout falsely reported a working-but-slow local model as broken.

### 2. Tests

**T28 — Ollama provisioning over HTTP + real local generation**
- `GET /api/ollama/status` → `installed:true, running:true`, sees
  `phi4-mini:latest`, catalog ids `[gemma2:2b, llama3.2:3b, qwen2.5:3b,
  phi4-mini]`. PASS.
- `POST /api/ollama/pull {phi4-mini}` (already present) → streamed
  `verifying sha256 → writing manifest → success`. PASS (stream proven).
- Direct probe: a cold phi4-mini generation returned in **17.4s** — local IS
  viable on this low-memory machine, just slow to cold-load (DEVLOG D8 context).
- After the 20s→90s timeout fix: `POST /api/settings/test` against
  `localhost:11434/v1` + `phi4-mini` → `{ok:true, "Connected to phi4-mini."}`.
  PASS (a real local generation, no `.env` change).
- `node --check web/app.js` → OK. `.env` left on Groq per the user's choice.

---

## Phase 13: runtime LLM settings + Settings UI

### 1. What was done

The LLM is no longer wired once at import; the user chooses it at runtime and the
choice takes effect on the next question with no restart (ADR 0007). Two LLM
options only — pull-local (Ollama) or bring-your-own OpenAI-compatible key — with
no built-in/embedded key (ADR 0006/0007).

- **Per-call client (`query._llm()`).** `expand_query` and `answer` now build the
  `OpenAI` client + model from `settings.llm_config()` on each call instead of a
  module-global. Cheap (the client just holds base_url + key), so flipping
  Local↔Cloud in Settings is picked up by the very next question.
- **Settings backend (`settings.py`).** `is_llm_configured()` (env override OR an
  explicit `llm.mode` in config.json), `llm_public()` (UI shape — never the raw
  key; carries `has_key` + `env_locked`), `save_llm()` (writes config.json; only
  overwrites the key when a new one is supplied). `query.test_connection()` does
  one cheap call to verify an endpoint.
- **API (`app.py`).** `GET/PUT /api/settings`, `POST /api/settings/test`. The
  `ask` endpoint now gates on config: if no LLM is configured it returns
  `{needs_setup:true}` and persists nothing (ingest never gated — embeddings are
  local; only answering needs the LLM).
- **Settings UI (`web/`).** A gear icon on home / list / chat opens a Model
  settings modal: provider presets (Local · Groq · OpenAI · OpenRouter · Custom)
  that fill base_url + model, a key field (hidden for local; Groq shows a
  free-key hint), a Test-connection button, and a Local-keeps-it-private /
  Cloud-sends-excerpts notice (ADR 0002). New icons: gear / cpu / cloud. The
  ask-gate opens this same modal titled "Choose how to answer" on the first
  question and retries automatically once saved.

### 2. Tests

**T27 — settings + gate end-to-end over HTTP, real LLM**
- Precondition: server launched on an isolated app-data dir with the `.env` LLM
  vars neutralised (empty), i.e. a genuine fresh-install state.
- Input/Output:
  - `GET /api/settings` → `configured:false, env_locked:false` (raw key absent
    from the payload). PASS.
  - ask before configuring → `{needs_setup:true}`, no turn persisted. PASS.
  - `PUT /api/settings` (cloud Groq + real key) → `configured:true, mode:cloud,
    provider:groq, has_key:true`; key never echoed. PASS.
  - `POST /api/settings/test` → `{ok:true}` against the real Groq endpoint. PASS.
  - upload `sample1.pdf` (ONNX ingest, 8 chunks) → ask → `needs_setup:false,
    refused:false`, a real grounded answer + 2 Locators — proving the per-call
    client read config.json (env was cleared) and answered live. PASS.
- Postcondition: test chat deleted (cascade), scratch app-data removed.
- `node --check web/app.js` → OK.

---

## Desktop-app re-architecture: decisions, Phase 9 spikes, Phase 10–11

### 1. What was done

A request to add multi-user accounts (register/verify-email/login) was explored
and **rejected**: email verification forces a hosted service, which breaks the
local-first promise (the corpus would leave the machine). We pivoted the other
way — ship CiteFinder as a **single-user desktop app** the user downloads and
runs ([ADR 0006](0006-single-user-desktop-app.md)), with a **bundled Postgres**,
an **ONNX embedder**, self-managed services, and a runtime choice of **local
(Ollama)** or **bring-your-own-key cloud** LLM ([ADR 0007](0007-bundle-postgres-fetch-llm.md)).
Full plan in [ROADMAP.md](ROADMAP.md) (Phases 9–14 in scope; native window +
installer deferred). Corrected the now-false "multi-user is just an auth layer"
wording in `app.py` and the README.

**Phase 9 — de-risk spikes (go/no-go):**
- **9a (ONNX embeddings) — PASS.** Proved a torch-free embedding path
  (`tokenizers` + `onnxruntime` + numpy mean-pool/normalize, using the
  pre-exported `onnx/model.onnx` in the `all-MiniLM-L6-v2` HF repo) reproduces
  the sentence-transformers vectors to FP precision. Phase-11 note: use
  `tokenizers.Tokenizer`, not `transformers.AutoTokenizer` (the latter imports
  torch).
- **9b (portable Postgres + pgvector on Windows) — deferred to Phase 12.** No
  official Windows pgvector binary and no MSVC compiler on this machine; the
  extension needs a one-time MSVC build (CI / build machine) — known effort, not
  unknown feasibility. Phases 10–11 don't need bundled Postgres (dev keeps
  Docker), so the bundling proof waits for Phase 12. SQLite + `sqlite-vec` stays
  the fallback.

**Phase 10 — app-data + config foundation:**
- New `appdata.py`: cross-platform per-user data dir (`%APPDATA%\CiteFinder` on
  Windows), overridable via `CITEFINDER_HOME`; resolvers for `uploads/` and
  `config.json`. Nothing mutable is written into the install dir (ADR 0006).
- New `settings.py`: single config resolver with precedence **env (/.env) >
  config.json > built-in default** for both the DB connection string and the LLM
  endpoint. `.env` loading moved here from `db.py`.
- Rewired `db.py` (`CONN = db_conn_string()`), `query.py` (LLM endpoint from
  `llm_config()`; `import os` dropped — unused), and `app.py` (`UPLOAD_DIR` now
  the app-data uploads dir). Dev behaviour is unchanged: `.env` + Docker still win.

**Phase 11 — embedder torch → ONNX:**
- New `embedder.py`: the local `all-MiniLM-L6-v2` embedding via `tokenizers` +
  `onnxruntime` + numpy (tokenize → ONNX → mean-pool → L2-normalize), model files
  cached under `<app-data>/models`. One `embed()` handles a single string
  (returns `(384,)`) or a list (returns `(N, 384)`), matching the old
  `.encode()` call shapes.
- `embed_store.embed_texts` and `query.py`'s four `embed_model.encode(...)` sites
  now call `embedder.embed`; `SentenceTransformer` removed from both. Runtime is
  now torch-free.
- `requirements.txt`: dropped `sentence-transformers`; added `onnxruntime`,
  `tokenizers`, `huggingface_hub`, `numpy`. (torch is no longer a runtime dep; it
  remains only as a build-time tool if we ever re-export the ONNX model.)

### 2. Tests

**T24 — config precedence (settings.py)**
- Precondition: `.env` defines `CITEFINDER_LLM_*` (Groq) but no `CITEFINDER_DB`.
- Input: resolve config with real env; then with an isolated `CITEFINDER_HOME`
  holding a `config.json` (db + llm) and DB env cleared.
- Output: real env → LLM = Groq (env wins), DB = Docker default (not in env/json).
  Isolated → DB came from config.json (`dbname=fromjson`); LLM still from `.env`
  (correct: env > config.json, and `.env` carries only the LLM vars).
- Postcondition: precedence env > config.json > default holds for both keys. PASS.

**T25 — import chain + live DB after rewire**
- `import app` succeeds; `app.UPLOAD_DIR` = `%APPDATA%\CiteFinder\uploads`. PASS.
- `db.connect()` → `SELECT 1` returns 1 against the Docker instance;
  `chats.list_chats('user_1')` runs. PASS.

**T26 — ONNX embedder parity + retrieval unchanged (Phase 9a / 11)**
- Precondition: 9a proved ONNX vectors == sentence-transformers to FP precision
  (cosine 1.000000) on a torch-free path.
- Input: `embed()` smoke test (single + batch + empty string); full `evaluate.py`
  run (dense / keyword / hybrid) over the 76-chunk eval corpus + 12 gold
  questions, with query vectors now produced by ONNX.
- Output: `embed('...')` → `(384,)` unit-norm float32; `embed([...])` →
  `(N, 384)` all finite. evaluate.py reproduced the **exact** Phase-7 numbers —
  hybrid hit@5 = 1.000, recall@5 = 0.861; dense recall@5 = 0.819.
- Postcondition: retrieval quality is unchanged by the embedder swap; `import
  embed_store, query` leaves `torch` and `sentence_transformers` absent from
  `sys.modules`. PASS.

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
