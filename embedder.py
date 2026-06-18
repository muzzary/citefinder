"""
Local embedding model (`e5-small-v2`) via ONNX Runtime.

Embeddings are ALWAYS local (ADR 0002) and must ship inside the desktop app
(ADR 0007), so this path deliberately uses `tokenizers` + `onnxruntime` + numpy
and NOT sentence-transformers / `transformers` — those pull in PyTorch (hundreds
of MB to multiple GB), and `transformers.AutoTokenizer` in particular imports
torch.

MODEL CHOICE: a large-scale benchmark (DEVLOG T31/T32) A/B'd MiniLM vs bge-small,
gte-small, and e5-small-v2 over a 7.2k-chunk ground-truthed corpus. e5-small-v2
won decisively on dense retrieval — lexical MRR 0.56 -> 0.89, semantic hit@5
0.094 -> 0.156 — so it replaced all-MiniLM-L6-v2. It is also 384-dim, a drop-in
for the existing vector(384) column. (Cost: ~2x slower ingest, 12 vs 6 layers.)

ASYMMETRIC PREFIXES: e5 was trained with "query: " on questions and "passage: "
on documents; omitting them materially hurts retrieval. So embed() takes a `kind`
("query" | "passage") and prepends the right prefix. This is why switching models
requires RE-INDEXING: query vectors must be compared against passage vectors made
by the SAME model+prefix scheme.

Pipeline: prefix -> tokenize -> ONNX transformer -> attention-mask-weighted mean
pooling -> L2 normalize -> 384-dim float32 vector.

The model files (`onnx/model.onnx`, `tokenizer.json`) are pulled once from the
HF hub (the Xenova ONNX mirror) and cached under the app-data dir, so a packaged
build can pre-seed them there and run fully offline.
"""
import numpy as np
import onnxruntime as ort
from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

from appdata import app_data_dir

REPO = "Xenova/e5-small-v2"   # ONNX mirror of intfloat/e5-small-v2 (ships onnx/model.onnx)
MAX_LEN = 256          # cap sequence length (e5 supports 512; 256 matches the eval)
EMBED_DIM = 384        # MUST match the vector(384) column in setup_db.py
BATCH_SIZE = 64        # cap peak memory: at most this many chunks per ONNX run
_PREFIX = {"query": "query: ", "passage": "passage: "}   # e5 asymmetric prefixes

_session = None
_tokenizer = None
_input_names = None


def _model_cache_dir():
    d = app_data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _load():
    """Load the ONNX session + tokenizer once (lazy, process-global)."""
    global _session, _tokenizer, _input_names
    if _session is not None:
        return
    cache = _model_cache_dir()
    onnx_path = hf_hub_download(REPO, filename="onnx/model.onnx", cache_dir=cache)
    tok_path = hf_hub_download(REPO, filename="tokenizer.json", cache_dir=cache)

    tk = Tokenizer.from_file(tok_path)
    tk.enable_truncation(max_length=MAX_LEN)
    tk.enable_padding()                      # pad to the longest item in the batch
    _tokenizer = tk

    _session = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    _input_names = {i.name for i in _session.get_inputs()}


def _embed_batch(texts):
    """Embed ONE batch -> (len(texts), 384) L2-normalized float32. Padding is
    per-batch (to this batch's longest item), so peak memory is bounded by the
    batch, not the whole corpus."""
    encs = _tokenizer.encode_batch(texts)
    ids = np.array([e.ids for e in encs], dtype=np.int64)
    mask = np.array([e.attention_mask for e in encs], dtype=np.int64)

    feed = {"input_ids": ids, "attention_mask": mask}
    if "token_type_ids" in _input_names:
        feed["token_type_ids"] = np.array([e.type_ids for e in encs], dtype=np.int64)
    feed = {k: v for k, v in feed.items() if k in _input_names}

    token_emb = _session.run(None, feed)[0]                 # (b, seq, 384)
    m = mask[:, :, None].astype(np.float32)
    pooled = (token_emb * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
    norms = np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
    return (pooled / norms).astype(np.float32)


def embed(texts, kind="query"):
    """
    Embed text into L2-normalized 384-dim vectors.

    kind: "query" for a question, "passage" for a document chunk — selects the e5
    prefix (see module docstring). Defaults to "query" because the frequent
    runtime call embeds a question; ingestion passes kind="passage" explicitly.

    Accepts a single string or a list of strings. Returns a 1-D float32 array
    (384,) for a single string, or a 2-D array (N, 384) for a list — matching
    how the call sites expect `.encode()` to behave.

    Large inputs are embedded in fixed-size batches (BATCH_SIZE) so ingesting a
    big document or folder never builds one giant (N, seq, 384) tensor in memory
    — the dominant cost on the "drop a folder" path. Results concatenate in order.
    """
    single = isinstance(texts, str)
    if single:
        texts = [texts]
    _load()
    if not texts:
        return np.zeros((0, EMBED_DIM), dtype=np.float32)

    prefix = _PREFIX[kind]
    prefixed = [prefix + t for t in texts]
    out = [_embed_batch(prefixed[i:i + BATCH_SIZE]) for i in range(0, len(prefixed), BATCH_SIZE)]
    vecs = out[0] if len(out) == 1 else np.concatenate(out, axis=0)

    return vecs[0] if single else vecs
