# CiteFinder — Desktop-app roadmap (Phases 9+)

The decision to ship CiteFinder as a single-user desktop app is recorded in
[ADR 0006](0006-single-user-desktop-app.md); the packaging strategy (bundle
Postgres, fetch the LLM on demand, ONNX embedder, no embedded cloud key,
Windows-first) is in [ADR 0007](0007-bundle-postgres-fetch-llm.md). This file is
the build plan that turns those decisions into work.

**Current scope: Phases 9–14** — the backend re-architecture. The app stays
browser-based (open `localhost`) through this scope; the native window and the
packaged installer (Phases 15–17) are deferred and listed at the bottom for
context, not yet scheduled.

Phases are ordered by dependency and risk: the riskiest unknowns first, so we
never build on a foundation that turns out not to hold. Each of Phases 10–14
keeps the app runnable, so every step is verifiable end-to-end with the real LLM.

---

## Phase 9 — De-risk spikes (go / no-go gate)

Prove the two unknowns ADR 0007 flagged before committing to them. Nothing else
is built until these hold.

- **9a — ONNX embeddings parity.** Run `all-MiniLM-L6-v2` via ONNX Runtime;
  verify its vectors match the current sentence-transformers output within
  tolerance; re-run a slice of the Phase-7 eval to confirm `MAX_DISTANCE=0.69`
  and `CANDIDATE_K=20` still hold.
- **9b — Portable Postgres + pgvector on Windows.** Get a portable Postgres
  running pgvector as a loadable extension, on a private port with a data dir in
  a temp app-data folder; run `setup_db`; ingest + query end-to-end.
- **Done when:** both proven on Windows — or we hit a wall and adopt the
  documented fallback (SQLite + `sqlite-vec`/FTS5 rewrite, or a different embed
  runtime). If 9b fails, the "bundle Postgres" decision flips and Phases 11–12
  change shape, which is exactly why this is first.

### Outcome

- **9a — PASS.** A torch-free path (`tokenizers` (Rust) + `onnxruntime` + numpy
  mean-pool + L2-normalize, loading the pre-exported `onnx/model.onnx` shipped in
  the `all-MiniLM-L6-v2` HF repo) reproduces the sentence-transformers vectors to
  floating-point precision (cosine 1.000000, ~0 element diff across normal,
  short, and empty-string inputs). The Phase-7 thresholds are unaffected.
  **Phase-11 note:** use `tokenizers.Tokenizer`, *not* `transformers.AutoTokenizer`
  — the latter transitively imports torch; `tokenizers`/`onnxruntime`/`hf_hub`
  do not.
- **9b — RESOLVED at Phase 12.** pgvector ships no official Windows binary, so it
  must be built once with MSVC. This machine now has Visual Studio Community 2026,
  so pgvector 0.8.3 was built (`nmake /F Makefile.win`) against portable PostgreSQL
  16.9 and proven end-to-end (see Phase 12). The SQLite + `sqlite-vec` fallback is
  no longer needed.

## Phase 10 — App-data + config foundation ✅ done

- An `appdata` path resolver (`%APPDATA%\CiteFinder\`) and a `settings.py` over a
  `config.json`.
- `db.py` and `query.py` read resolved config instead of env-vars-at-import (env
  kept as a dev-only override); uploads, DB data dir, embedding model, and config
  all relocate under app-data.
- **Done when:** the existing web app runs unchanged but reads/writes everything
  from the app-data directory rather than the repo.

## Phase 11 — Embedder swap (torch → ONNX) ✅ done

- Replace the embedding call behind its current interface with the ONNX path
  proven in 9a; drop PyTorch from the runtime dependencies.
- **Done when:** ingest + retrieve behave identically and `evaluate.py` still
  passes against the Phase-7 thresholds.

## Phase 12 — Bundled Postgres lifecycle ✅ done

- A process manager that init/start/stops the portable Postgres on a private
  loopback port + app-data data dir; a single-instance guard; auto-run of the
  idempotent `setup_db` migrations on every launch; conservative crash recovery
  (clear only a stale `postmaster.pid`, never touch data).
- **Done when:** launching the app boots its own Postgres with no Docker, and a
  hard-kill + relaunch recovers cleanly. The app-data cluster is treated as
  precious user data (never `initdb` over an existing one).
- **Outcome:** `pgserver.py` (lifecycle) + pgvector 0.8.3 built with MSVC against
  portable PostgreSQL 16.9. `app.py` boots it when `CITEFINDER_PG=bundled` (dev
  still defaults to Docker). Verified end-to-end incl. crash recovery (DEVLOG T34).

## Phase 13 — Runtime LLM settings + Settings UI ✅ done

- `GET/PUT /api/settings`; the LLM client built **per-call** from live config (so
  Local↔Cloud switches take effect on the next question, no restart); a Settings
  panel in the SPA (provider presets + Custom + key field + a Test-connection
  button); the "Choose how to answer" modal that gates only *ask*; the
  Cloud-sends-data notice ([ADR 0002](0002-local-by-default-hosted-opt-in.md)).
- **Done when:** you can switch Local↔Cloud in the UI and the next question
  respects it without a restart, and ingest is never gated on LLM config.

## Phase 14 — Local LLM provisioning (Ollama detect-and-guide) ✅ done

- Detect whether Ollama is installed/running; guide the install if missing; pull
  the chosen model with a progress bar; show a RAM hint per model; point
  `base_url` at the local endpoint.
- **Done when:** a fresh machine can go from "pick Local" → model pulled →
  answered question, all from inside the app.

---

## Later (deferred — recorded for context, not in current scope)

- **Phase 15 — Native window (`pywebview`).** Launch FastAPI on a private
  loopback port inside a native window; app icon/title; closing the window stops
  the server and Postgres; single-instance focuses the existing window.
- **Phase 16 — Packaging (PyInstaller, Windows).** Bundle app + Python + portable
  Postgres + pgvector + ONNX model into a Windows installer; first-run `initdb`;
  document the SmartScreen "Run anyway" step; smoke-test on a clean profile.
- **Phase 17 — Docs + demo.** Rewrite README install to "download & run"; DEVLOG
  entries per phase; screenshots / short demo.
