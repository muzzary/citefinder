# CiteFinder

A local-first RAG (Retrieval-Augmented Generation) tool that helps students find **where** a topic is discussed across their own collection of PDFs and returns answers **grounded in the source with page numbers** — so they can reference their material correctly.

It answers questions using *only* the student''s uploaded documents. It never pulls from the open web and never invents citations.

## Why

Students writing a thesis collect many papers, books, and articles. Finding which source discusses a given idea — and citing it correctly — is slow and error-prone. CiteFinder makes that searchable: ask a question in plain English, get an answer drawn from your own material with the source and page.

## How it works
PDF → extract text (per page) → chunk + tag with page/source

Embed (local model) → store in pgvector

Question → embed → semantic search (pgvector) → retrieve top chunks

Local LLM answers ONLY from those chunks → answer + page numbers
## Tech stack

- **Backend:** Python
- **Vector DB:** PostgreSQL + pgvector (via Docker)
- **Embeddings:** sentence-transformers (`all-MiniLM-L6-v2`), runs locally on CPU
- **LLM:** Phi-4 Mini via Ollama, called through an OpenAI-compatible endpoint (swappable to a hosted model)
- **PDF parsing:** pypdf

Runs fully locally — no API costs, no data leaving the machine.

## Status

Work in progress. Current build: end-to-end RAG pipeline (ingest → embed → retrieve → grounded answer with page numbers).

**Planned next:** citation engine (APA / Harvard / IEEE), hybrid search (dense + BM25), retrieval evaluation (recall@k), and a web UI.

## Design notes

- **Integrity by design:** the tool finds and attributes sources; it does not write submittable text or fabricate citations.
- **Local-first:** built to run on modest hardware (no GPU required).
- **Scalable schema:** data is tagged per-user from the start, so multi-user support is a small extension.
