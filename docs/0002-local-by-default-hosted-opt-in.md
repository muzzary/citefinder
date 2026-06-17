# Local by default; hosted LLM is opt-in only

CiteFinder is local-first. The boundary between local and hosted compute is
deliberately asymmetric:

- **Embeddings are always local.** Every chunk and every question is embedded
  by the local model (`all-MiniLM-L6-v2`). The corpus itself never leaves the
  machine under any configuration.
- **The answering LLM is local by default** (Phi-4 Mini via Ollama). A hosted
  model may be used **only when the student explicitly enables it**, with a
  one-time warning that doing so sends retrieved passages plus the question to
  a third-party provider. There is **no automatic fallback** — quality-based or
  otherwise.

## Why

A thesis student's PDFs are often copyrighted or unpublished, and their
questions reveal their research direction. Silently shipping that to a hosted
API — especially as an automatic "the local answer looked weak, try hosted"
fallback — would break the privacy promise at exactly the unpredictable moments
the student isn't watching. Keeping embeddings local is cheap and makes the
strong half of the guarantee absolute; making the hosted LLM a conscious,
warned opt-in preserves the option to trade privacy for answer quality without
ever doing it behind the student's back.

## Consequence

The README's current wording ("no data leaving the machine") is unconditionally
false once the switch exists and must be reworded to: *"Local by default —
nothing leaves your machine unless you explicitly enable a hosted model."* The
LLM client selection must be a deliberate, surfaced configuration, not an
internal retry path.
