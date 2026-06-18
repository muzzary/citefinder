"""
Rigorous retrieval benchmark over the synthetic large-scale corpus
(bench_corpus.py). This is KNOWN-ITEM retrieval: each question has a unique
ground-truth entity, so a retrieved chunk is relevant iff it contains that
entity. We report hit@k and MRR (the right metrics for known-item search),
split by question type (lexical vs semantic), plus per-query latency, the
coverage-floor separation at scale, a candidate_k sweep, and an optional
hybrid-vs-multi-query comparison (LLM, sampled).

Run:  python bench_eval.py                  (dense/keyword/hybrid + floor + sweep)
      python bench_eval.py --with-multi     (adds the LLM multi-query path, sampled)
"""
import argparse
import json
import time

from query import (retrieve, retrieve_keyword, retrieve_hybrid, retrieve_multi,
                   MAX_DISTANCE, CANDIDATE_K)
from bench_corpus import BENCH_USER, GOLD_PATH

USER = BENCH_USER  # overridden by --user, so a variant corpus can be evaluated
NO_FLOOR = 2.0     # cosine distance never exceeds 2.0; disables the floor for ranking
DEPTH = 10


def _chunks(method, q, depth=DEPTH, candidate_k=CANDIDATE_K):
    if method == "dense":
        return retrieve(q, USER, top_k=depth, max_distance=NO_FLOOR)
    if method == "keyword":
        return retrieve_keyword(q, USER, top_k=depth)
    if method == "hybrid":
        return retrieve_hybrid(q, USER, top_k=depth,
                               candidate_k=candidate_k, max_distance=NO_FLOOR)
    if method == "multi":
        return retrieve_multi(q, USER, top_k=depth,
                              candidate_k=candidate_k, max_distance=NO_FLOOR)
    raise ValueError(method)


def _first_rel_rank(chunks, entity):
    e = entity.lower()
    for i, c in enumerate(chunks, 1):
        if e in (c["text"] or "").lower():
            return i
    return None


def evaluate(method, items, depth=DEPTH):
    agg = {"hit@1": 0, "hit@3": 0, "hit@5": 0, "hit@10": 0, "mrr": 0.0}
    lat = []
    for it in items:
        t0 = time.perf_counter()
        chunks = _chunks(method, it["q"], depth)
        lat.append((time.perf_counter() - t0) * 1000)
        r = _first_rel_rank(chunks, it["entity"])
        if r:
            agg["mrr"] += 1.0 / r
            if r <= 1: agg["hit@1"] += 1
            if r <= 3: agg["hit@3"] += 1
            if r <= 5: agg["hit@5"] += 1
            if r <= 10: agg["hit@10"] += 1
    n = len(items)
    out = {k: (v / n) for k, v in agg.items()}
    lat.sort()
    out["lat_ms_mean"] = sum(lat) / len(lat)
    out["lat_ms_p95"] = lat[int(len(lat) * 0.95)] if lat else 0.0
    return out


def _row(label, m):
    print("%-22s | %5.3f | %5.3f | %5.3f | %5.3f | %5.3f | %7.1f | %7.1f" % (
        label, m["hit@1"], m["hit@3"], m["hit@5"], m["hit@10"], m["mrr"],
        m["lat_ms_mean"], m["lat_ms_p95"]))


def floor_analysis(items, off_topic):
    """Best dense distance for covered (semantic) questions vs off-topic, to see
    if the tuned MAX_DISTANCE still separates them at scale."""
    def best(q):
        ch = retrieve(q, USER, top_k=1, max_distance=NO_FLOOR)
        return ch[0]["distance"] if ch else None

    cov = sorted(d for d in (best(it["q"]) for it in items) if d is not None)
    off = sorted(d for d in (best(q) for q in off_topic) if d is not None)

    def pct(xs, p):
        return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else float("nan")

    print("\n--- Coverage floor at scale (best dense distance) ---")
    print(f"covered  (semantic Qs, want BELOW floor): "
          f"min={cov[0]:.3f} p50={pct(cov,.5):.3f} p95={pct(cov,.95):.3f} max={cov[-1]:.3f}")
    print(f"off-topic              (want ABOVE floor): "
          f"min={off[0]:.3f} p50={pct(off,.5):.3f} p95={pct(off,.95):.3f} max={off[-1]:.3f}")
    cov_above = sum(1 for d in cov if d > MAX_DISTANCE)   # covered wrongly refused
    off_below = sum(1 for d in off if d <= MAX_DISTANCE)  # off-topic wrongly accepted
    print(f"current MAX_DISTANCE={MAX_DISTANCE}:  covered wrongly refused {cov_above}/{len(cov)} "
          f"({cov_above/len(cov):.0%})  |  off-topic wrongly accepted {off_below}/{len(off)} "
          f"({off_below/len(off):.0%})")
    gap = "SEPARABLE" if cov[-1] < off[0] else "OVERLAP"
    print(f"separation: covered max {cov[-1]:.3f} vs off-topic min {off[0]:.3f}  -> {gap}")
    if cov and off:
        print(f"suggested floor (midpoint of covered-p95 and off-topic-p5): "
              f"{(pct(cov,.95)+pct(off,.05))/2:.3f}")


def candidate_k_sweep(items):
    print("\n--- candidate_k sweep (hybrid, semantic Qs) ---")
    print("cand_k | hit@5 |  mrr  | lat_ms_mean")
    for ck in (10, 20, 40, 80):
        agg = {"hit@5": 0, "mrr": 0.0}
        lat = []
        for it in items:
            t0 = time.perf_counter()
            ch = _chunks("hybrid", it["q"], DEPTH, candidate_k=ck)
            lat.append((time.perf_counter() - t0) * 1000)
            r = _first_rel_rank(ch, it["entity"])
            if r:
                agg["mrr"] += 1.0 / r
                if r <= 5: agg["hit@5"] += 1
        n = len(items)
        print("%-6d | %5.3f | %5.3f | %7.1f" %
              (ck, agg["hit@5"]/n, agg["mrr"]/n, sum(lat)/len(lat)))


def multi_compare(sample):
    print(f"\n--- hybrid vs multi-query (LLM), sampled {len(sample)} semantic Qs ---")
    for method in ("hybrid", "multi"):
        agg = {"hit@5": 0, "mrr": 0.0}
        lat = []
        for it in sample:
            t0 = time.perf_counter()
            ch = _chunks(method, it["q"], DEPTH)
            lat.append((time.perf_counter() - t0) * 1000)
            r = _first_rel_rank(ch, it["entity"])
            if r:
                agg["mrr"] += 1.0 / r
                if r <= 5: agg["hit@5"] += 1
        n = len(sample)
        print("%-7s | hit@5 %5.3f | mrr %5.3f | lat_ms_mean %7.1f" %
              (method, agg["hit@5"]/n, agg["mrr"]/n, sum(lat)/len(lat)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-multi", action="store_true")
    ap.add_argument("--multi-sample", type=int, default=25)
    ap.add_argument("--user", default=BENCH_USER, help="evaluate a variant corpus")
    args = ap.parse_args()
    USER = args.user

    data = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    gold, off = data["gold"], data["off_topic"]
    lexical = [g for g in gold if g["qtype"] == "lexical"]
    semantic = [g for g in gold if g["qtype"] == "semantic"]
    from db import connect
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM chunks c JOIN sources s ON c.source_id=s.id "
                    "WHERE s.user_id=%s;", (USER,))
        n_chunks = cur.fetchone()[0]
    print(f"Corpus[{USER}]: {data['n_docs']} docs, {n_chunks} chunks, "
          f"{data['n_needles']} needles | gold {len(gold)} ({len(lexical)} lexical, "
          f"{len(semantic)} semantic), off-topic {len(off)}")

    header = "%-22s | hit@1 | hit@3 | hit@5 | hit@10|  mrr  | lat-mean| lat-p95" % "method (question set)"
    print("\n" + header)
    print("-" * len(header))
    for method in ("dense", "keyword", "hybrid"):
        _row(f"{method} (lexical)", evaluate(method, lexical))
    print("-" * len(header))
    for method in ("dense", "keyword", "hybrid"):
        _row(f"{method} (semantic)", evaluate(method, semantic))

    floor_analysis(semantic, off)
    candidate_k_sweep(semantic)

    if args.with_multi:
        multi_compare(semantic[:args.multi_sample])
