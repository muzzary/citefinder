"""
A/B different ONNX embedders for DENSE retrieval quality, OFFLINE and in-memory —
no production change, no DB schema change. We pull the benchmark corpus chunk
texts from the DB, embed them (and the gold questions) with each candidate model
via the SAME torch-free path production uses (tokenizers + onnxruntime + numpy),
rank by cosine in memory, and score with the benchmark's ground truth (a chunk is
relevant iff it contains the question's needle entity).

This isolates the embedding model — the lever the large-scale benchmark (T31)
identified for the weak SEMANTIC (paraphrase) recall. All candidates are 384-dim,
so a winner is a drop-in swap for embedder.REPO (plus its query/passage prefix &
pooling). Candidates use the Xenova ONNX mirrors (reliably ship onnx/model.onnx +
tokenizer.json). Passage embeddings are cached to app-data so re-runs are instant.

Run:  python bench_embedders.py [--user bench_user_small]
"""
import argparse
import json
import time

import numpy as np
import onnxruntime as ort
from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

from appdata import app_data_dir
from db import connect
from bench_corpus import BENCH_USER, GOLD_PATH

MAX_LEN = 256
BATCH = 64

# pool: 'mean' (MiniLM/gte/e5) or 'cls' (bge). qp/pp: query / passage prefixes the
# model was trained with — omitting e5/bge prefixes materially hurts retrieval.
MODELS = [
    {"name": "minilm-L6 (current)", "repo": "sentence-transformers/all-MiniLM-L6-v2",
     "file": "onnx/model.onnx", "pool": "mean", "qp": "", "pp": ""},
    {"name": "bge-small-en-v1.5", "repo": "Xenova/bge-small-en-v1.5",
     "file": "onnx/model.onnx", "pool": "cls",
     "qp": "Represent this sentence for searching relevant passages: ", "pp": ""},
    {"name": "gte-small", "repo": "Xenova/gte-small",
     "file": "onnx/model.onnx", "pool": "mean", "qp": "", "pp": ""},
    {"name": "e5-small-v2", "repo": "Xenova/e5-small-v2",
     "file": "onnx/model.onnx", "pool": "mean", "qp": "query: ", "pp": "passage: "},
]


def _cache():
    d = app_data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def _load(repo, file):
    onnx_path = hf_hub_download(repo, filename=file, cache_dir=_cache())
    tok_path = hf_hub_download(repo, filename="tokenizer.json", cache_dir=_cache())
    tk = Tokenizer.from_file(tok_path)
    tk.enable_truncation(max_length=MAX_LEN)
    tk.enable_padding()
    sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    names = {i.name for i in sess.get_inputs()}
    return tk, sess, names


def _embed(texts, tk, sess, names, pool, prefix, batch=BATCH):
    out = []
    for i in range(0, len(texts), batch):
        chunk = [prefix + t for t in texts[i:i + batch]]
        encs = tk.encode_batch(chunk)
        ids = np.array([e.ids for e in encs], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in names:
            feed["token_type_ids"] = np.array([e.type_ids for e in encs], dtype=np.int64)
        feed = {k: v for k, v in feed.items() if k in names}
        tok_emb = sess.run(None, feed)[0]                       # (b, seq, d)
        if pool == "cls":
            pooled = tok_emb[:, 0]
        else:
            m = mask[:, :, None].astype(np.float32)
            pooled = (tok_emb * m).sum(1) / np.clip(m.sum(1), 1e-9, None)
        norms = np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
        out.append((pooled / norms).astype(np.float32))
    return np.concatenate(out, axis=0)


def _passages(user):
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT c.chunk_text FROM chunks c JOIN sources s ON c.source_id=s.id "
                    "WHERE s.user_id=%s ORDER BY c.id;", (user,))
        return [r[0] for r in cur.fetchall()]


def _score(qvecs, pmat, texts, items, depth=10):
    sims = qvecs @ pmat.T                                       # (Q, N), both unit-norm
    agg = {"hit@1": 0, "hit@3": 0, "hit@5": 0, "hit@10": 0, "mrr": 0.0}
    for i, it in enumerate(items):
        top = np.argpartition(-sims[i], depth)[:depth]
        top = top[np.argsort(-sims[i][top])]
        e = it["entity"].lower()
        rank = None
        for r, idx in enumerate(top, 1):
            if e in texts[idx].lower():
                rank = r
                break
        if rank:
            agg["mrr"] += 1.0 / rank
            for k in (1, 3, 5, 10):
                if rank <= k:
                    agg[f"hit@{k}"] += 1
    n = len(items)
    return {k: v / n for k, v in agg.items()}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default="bench_user_small")
    args = ap.parse_args()

    data = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    gold = data["gold"]
    lexical = [g for g in gold if g["qtype"] == "lexical"]
    semantic = [g for g in gold if g["qtype"] == "semantic"]
    texts = _passages(args.user)
    print(f"Corpus[{args.user}]: {len(texts)} passages | {len(lexical)} lexical + "
          f"{len(semantic)} semantic questions\n")

    hdr = "%-22s | set      | hit@1 | hit@3 | hit@5 | hit@10|  mrr  | embed_s" % "model"
    print(hdr)
    print("-" * len(hdr))
    for m in MODELS:
        try:
            tk, sess, names = _load(m["repo"], m["file"])
        except Exception as e:
            print("%-22s | SKIPPED (%s)" % (m["name"], str(e)[:60]))
            continue
        cache_npy = app_data_dir() / f"bench_emb_{m['name'].split()[0]}_{args.user}.npy"
        t0 = time.perf_counter()
        if cache_npy.exists():
            pmat = np.load(cache_npy)
            embed_s = 0.0
        else:
            pmat = _embed(texts, tk, sess, names, m["pool"], m["pp"])
            np.save(cache_npy, pmat)
            embed_s = time.perf_counter() - t0
        for label, items in (("lexical", lexical), ("semantic", semantic)):
            qv = _embed([it["q"] for it in items], tk, sess, names, m["pool"], m["qp"])
            r = _score(qv, pmat, texts, items)
            print("%-22s | %-8s | %5.3f | %5.3f | %5.3f | %5.3f | %5.3f | %6.0f" % (
                m["name"], label, r["hit@1"], r["hit@3"], r["hit@5"], r["hit@10"],
                r["mrr"], embed_s))
            embed_s = 0.0  # only attribute the (one-time) passage-embed cost to row 1
