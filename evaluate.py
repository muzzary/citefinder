"""
Phase-7 retrieval evaluation harness.

Runs each retrieval method over the fixed gold set (eval_questions.py) and
reports recall@k / hit@k / MRR at PAGE granularity, so we can (a) show the
before/after gain from hybrid over the dense baseline and (b) set the placeholder
thresholds (distance floor, candidate_k, routing thresholds) from data rather
than guesses.

Run:  python evaluate.py
"""

# ----------------------------------------------------------------------------
# Metrics (pure functions; page-level; no DB/LLM).
#
# Each takes `retrieved_pages` — the page number of each retrieved chunk, in
# rank order (duplicates allowed: several chunks can share a page) — and
# `relevant` — the set/list of gold page numbers for the question.
# ----------------------------------------------------------------------------

def hit_at_k(retrieved_pages, relevant, k):
    """1.0 if ANY relevant page appears in the top-k retrieved, else 0.0.
    (a.k.a. success@k — 'did we find the right page at all?')."""
    return 1.0 if set(retrieved_pages[:k]) & set(relevant) else 0.0


def recall_at_k(retrieved_pages, relevant, k):
    """Fraction of the question's relevant pages found within the top-k."""
    rel = set(relevant)
    if not rel:
        return 0.0
    return len(set(retrieved_pages[:k]) & rel) / len(rel)


def reciprocal_rank(retrieved_pages, relevant, k=None):
    """1 / rank of the FIRST relevant page (0 if none in the top-k). Averaged
    over questions this is MRR — it rewards putting a right page near the top."""
    rel = set(relevant)
    seq = retrieved_pages if k is None else retrieved_pages[:k]
    for i, p in enumerate(seq, start=1):
        if p in rel:
            return 1.0 / i
    return 0.0


# ----------------------------------------------------------------------------
# Metric self-test (no DB/LLM): `python evaluate.py --test-metrics`
# ----------------------------------------------------------------------------

def _test_metrics():
    cases = [
        # (retrieved, relevant, checks{name: (fn, expected)})
        ("first relevant at rank 3", [5, 3, 7, 2], [7], [
            ("hit@1", hit_at_k([5, 3, 7, 2], [7], 1), 0.0),
            ("hit@3", hit_at_k([5, 3, 7, 2], [7], 3), 1.0),
            ("recall@3", recall_at_k([5, 3, 7, 2], [7], 3), 1.0),
            ("RR", reciprocal_rank([5, 3, 7, 2], [7]), 1/3),
        ]),
        ("nothing relevant", [1, 2], [9], [
            ("hit@5", hit_at_k([1, 2], [9], 5), 0.0),
            ("recall@5", recall_at_k([1, 2], [9], 5), 0.0),
            ("RR", reciprocal_rank([1, 2], [9]), 0.0),
        ]),
        ("two relevant, dup pages", [4, 4, 8], [4, 8], [
            ("hit@1", hit_at_k([4, 4, 8], [4, 8], 1), 1.0),
            ("recall@2", recall_at_k([4, 4, 8], [4, 8], 2), 0.5),
            ("recall@3", recall_at_k([4, 4, 8], [4, 8], 3), 1.0),
            ("RR", reciprocal_rank([4, 4, 8], [4, 8]), 1.0),
        ]),
    ]
    ok = True
    for label, _r, _rel, checks in cases:
        for name, got, exp in checks:
            passed = abs(got - exp) < 1e-9
            ok = ok and passed
            print("  [%s] %-22s %-9s got=%.4f exp=%.4f" %
                  ("PASS" if passed else "FAIL", label, name, got, exp))
    print("ALL PASS:", ok)
    return ok


# ----------------------------------------------------------------------------
# Eval runner
# ----------------------------------------------------------------------------

# Distance value that keeps every chunk: we evaluate RANKING quality, so the
# production floor must not silently drop candidates and confound the
# comparison. (Floor tuning is a separate analysis — see tune_floor().)
NO_FLOOR = 2.0          # cosine distance is in [0, 2]; 2.0 lets everything through
RETRIEVE_DEPTH = 10     # pull this many chunks per query, then score @ K_VALUES
K_VALUES = (1, 3, 5)


def ensure_corpus():
    """Ingest the eval PDF under EVAL_USER if it isn't already loaded."""
    from eval_questions import CORPUS_PDF, EVAL_USER
    from db import connect
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM chunks WHERE user_id=%s;", (EVAL_USER,))
        n = cur.fetchone()[0]
    if n:
        return n
    from ingest import extract_pdf_pages
    from chunk import chunk_pages
    from embed_store import store_source, store_chunks
    pages = extract_pdf_pages(CORPUS_PDF)
    sid = store_source(title="Encouraged Digital Academic Portal", filename=CORPUS_PDF,
                       user_id=EVAL_USER, author="Khan, H. M. H.", year="2025",
                       kind="work", confirmed=True)
    chunks = chunk_pages(pages, source_id=sid, user_id=EVAL_USER)
    store_chunks(chunks)
    return len(chunks)


def retrieved_pages(method, question, user_id, depth=RETRIEVE_DEPTH,
                    candidate_k=20):
    """Return the page numbers of the top-`depth` chunks for a method, in rank
    order (duplicates kept — they reflect the real ranked list)."""
    from query import retrieve, retrieve_keyword, retrieve_hybrid, retrieve_multi
    if method == "dense":
        chunks = retrieve(question, user_id, top_k=depth, max_distance=NO_FLOOR)
    elif method == "keyword":
        chunks = retrieve_keyword(question, user_id, top_k=depth)
    elif method == "hybrid":
        chunks = retrieve_hybrid(question, user_id, top_k=depth,
                                 candidate_k=candidate_k, max_distance=NO_FLOOR)
    elif method == "multi":
        chunks = retrieve_multi(question, user_id, top_k=depth,
                                candidate_k=candidate_k, max_distance=NO_FLOOR)
    else:
        raise ValueError("unknown method: " + method)
    return [c["page"] for c in chunks]


def evaluate_method(method, gold, user_id):
    """Aggregate metrics for one method over the whole gold set."""
    agg = {("hit", k): 0.0 for k in K_VALUES}
    agg.update({("recall", k): 0.0 for k in K_VALUES})
    mrr = 0.0
    for item in gold:
        pages = retrieved_pages(method, item["q"], user_id)
        rel = item["relevant_pages"]
        for k in K_VALUES:
            agg[("hit", k)] += hit_at_k(pages, rel, k)
            agg[("recall", k)] += recall_at_k(pages, rel, k)
        mrr += reciprocal_rank(pages, rel)
    n = len(gold)
    out = {f"{m}@{k}": agg[(m, k)] / n for (m, k) in agg}
    out["MRR"] = mrr / n
    return out


def print_table(results):
    """results: dict method -> metrics dict."""
    cols = ["hit@1", "hit@3", "hit@5", "recall@3", "recall@5", "MRR"]
    print("\n%-9s | %s" % ("method", " | ".join("%8s" % c for c in cols)))
    print("-" * (11 + len(cols) * 11))
    for method, m in results.items():
        print("%-9s | %s" % (method, " | ".join("%8.3f" % m[c] for c in cols)))


def _best_dense_distance(question, user_id):
    """Smallest dense distance for a question (its closest chunk)."""
    from query import retrieve
    chunks = retrieve(question, user_id, top_k=1, max_distance=NO_FLOOR)
    return chunks[0]["distance"] if chunks else None


def tune_floor(gold, off_topic, user_id):
    """
    Find a distance floor that separates covered questions from off-topic ones.
    Reports the worst (largest) best-distance among covered questions and the
    best (smallest) best-distance among off-topic questions; a floor between
    them refuses off-topic BEFORE the LLM. If they overlap, no single floor
    separates them and the LLM layer stays necessary — we report that honestly.
    """
    covered = sorted(_best_dense_distance(q["q"], user_id) for q in gold)
    negatives = sorted(_best_dense_distance(q, user_id) for q in off_topic)

    print("\n--- Distance floor analysis (best dense distance per question) ---")
    print("covered  (want BELOW floor): min=%.3f  max=%.3f" % (covered[0], covered[-1]))
    print("off-topic (want ABOVE floor): min=%.3f  max=%.3f" % (negatives[0], negatives[-1]))
    gap_lo, gap_hi = covered[-1], negatives[0]
    if gap_lo < gap_hi:
        suggested = round((gap_lo + gap_hi) / 2, 3)
        print("SEPARABLE: covered max %.3f < off-topic min %.3f" % (gap_lo, gap_hi))
        print("Suggested floor (midpoint): %.3f  (current placeholder 0.9)" % suggested)
    else:
        print("OVERLAP: covered max %.3f >= off-topic min %.3f" % (gap_lo, gap_hi))
        print("No single floor separates them; the LLM refusal layer stays required.")
        print("A floor near covered-max %.3f trims the worst off-topic without "
              "dropping covered questions." % gap_lo)


def sweep_candidate_k(gold, user_id, values=(10, 20, 30, 40)):
    """Show how hybrid's metrics move with candidate_k, to pick a value."""
    print("\n--- candidate_k sweep (hybrid) ---")
    saved = {}
    header = "%-12s | %8s | %8s | %8s | %8s" % ("candidate_k", "hit@3", "hit@5", "recall@5", "MRR")
    print(header)
    print("-" * len(header))
    from query import retrieve_hybrid
    for ck in values:
        agg = {"hit@3": 0.0, "hit@5": 0.0, "recall@5": 0.0, "MRR": 0.0}
        for item in gold:
            chunks = retrieve_hybrid(item["q"], user_id, top_k=RETRIEVE_DEPTH,
                                     candidate_k=ck, max_distance=NO_FLOOR)
            pages = [c["page"] for c in chunks]
            rel = item["relevant_pages"]
            agg["hit@3"] += hit_at_k(pages, rel, 3)
            agg["hit@5"] += hit_at_k(pages, rel, 5)
            agg["recall@5"] += recall_at_k(pages, rel, 5)
            agg["MRR"] += reciprocal_rank(pages, rel)
        n = len(gold)
        agg = {kk: vv / n for kk, vv in agg.items()}
        saved[ck] = agg
        print("%-12d | %8.3f | %8.3f | %8.3f | %8.3f" %
              (ck, agg["hit@3"], agg["hit@5"], agg["recall@5"], agg["MRR"]))
    return saved


if __name__ == "__main__":
    import sys
    if "--test-metrics" in sys.argv:
        _test_metrics()
        sys.exit(0)

    from eval_questions import GOLD, EVAL_USER, OFF_TOPIC

    n = ensure_corpus()
    print("Eval corpus: %d chunks under user '%s'  |  %d gold questions"
          % (n, EVAL_USER, len(GOLD)))

    methods = ["dense", "keyword", "hybrid"]
    if "--with-multi" in sys.argv:
        methods.append("multi")   # uses the LLM (expansion) - slower
        print("(multi-query included - this makes LLM calls)")

    results = {}
    for method in methods:
        print("running:", method, "...")
        results[method] = evaluate_method(method, GOLD, EVAL_USER)

    print_table(results)
    print("\nBaseline = dense (Phase 4). 'hybrid' / 'multi' are the Phase-6 upgrades.")

    if "--tune-floor" in sys.argv:
        tune_floor(GOLD, OFF_TOPIC, EVAL_USER)
    if "--sweep-candidate-k" in sys.argv:
        sweep_candidate_k(GOLD, EVAL_USER)
