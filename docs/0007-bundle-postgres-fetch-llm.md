# Bundle Postgres; fetch the LLM on demand

To deliver CiteFinder as an installable app ([ADR 0006](0006-single-user-desktop-app.md))
the heavyweight runtime has to come *with* the app instead of being assembled by
hand (Docker, a venv, a manual `setup_db`, a separately-run Ollama). We split that
runtime into **bundled** and **fetched-on-demand** parts:

- **Bundled in the installer:** the app code + Python runtime, a **portable
  PostgreSQL + pgvector** the app boots itself, and the embedding model
  (`all-MiniLM-L6-v2`), run via **ONNX Runtime** rather than the full PyTorch
  stack. The app owns a private Postgres cluster in its per-user app-data
  directory, on a private loopback port, and runs the idempotent `setup_db`
  migrations on every launch.
- **Fetched on demand (never bundled):** the answering **LLM**. The user picks, in
  Settings, either **Local** (the app detects/guides an Ollama install and pulls a
  chosen model with a progress bar) or **Bring-your-own cloud key** (any
  OpenAI-compatible provider; Groq is the recommended free option, with an in-app
  guide). There is **no built-in / embedded cloud key**.

## Why

**Bundle Postgres, don't rewrite onto SQLite.** Retrieval depends on two
Postgres-specific features that are already evaluated and tuned (Phase 7:
`hit@5 = 1.00`): pgvector for dense search and Postgres full-text search
(`tsvector`/`ts_rank`) for the keyword half of hybrid retrieval. Bundling a
portable Postgres keeps every query, the RRF fusion, and the cascade FKs
**unchanged**. Moving to SQLite + `sqlite-vec` + FTS5 would be a rewrite of `db.py`,
`setup_db.py`, and `query.py`, and would invalidate the Phase-7 tuning — ongoing
risk to a proven system to save one-time packaging effort. Packaging pain is paid
once; a query-engine rewrite is paid forever.

**Don't bundle the LLM.** A single local model (~2.5 GB) is larger than everything
else combined, and roughly half of users will choose a cloud key and never run it.
Bundling it would mean a ~3 GB installer most of which is dead weight. Fetching it
only when the user opts into Local keeps the installer to ~250–350 MB and instantly
usable with a cloud key.

**No built-in cloud key.** A "works out of the box, provide nothing" cloud option
would require either an embedded shared key (extractable from the binary, billed to
us, throttled by one shared per-account rate limit) or a hosted proxy (re-introduces
the server, cost, and abuse surface that [ADR 0006](0006-single-user-desktop-app.md)
deliberately rejected). Both break the model, so the option is dropped.

**ONNX over PyTorch for embeddings.** Embeddings are mandatory and always local
([ADR 0002](0002-local-by-default-hosted-opt-in.md)), so the embedder must ship in
the installer. Running MiniLM via ONNX Runtime produces the same `vector(384)`
output as the current sentence-transformers path while shedding the hundreds-of-MB-
to-multi-GB PyTorch dependency.

## Consequence

- The app owns its Postgres data dir; it is **precious user data**. App updates must
  never `initdb` over an existing cluster, and crash recovery must only clear a
  stale `postmaster.pid`, never touch data. A single-instance guard prevents two
  launches from corrupting the shared data dir.
- The LLM client is built **at answer time** from live settings (`config.json` in
  app-data), not from env-vars-at-import, so switching Local↔Cloud takes effect on
  the next question without a restart. Env vars remain a dev-only override.
- Ingest needs no LLM and is never gated; only "ask" prompts for LLM setup if none
  is configured (embeddings are local, refusal/empty-library checks run before any
  LLM call).
- The ONNX swap must be verified to produce vectors matching the Phase-7-tuned
  thresholds before it replaces the sentence-transformers path.
- v1 targets **Windows only**; path/app-data handling is written cross-platform so
  macOS/Linux are a later port, not a rewrite.
