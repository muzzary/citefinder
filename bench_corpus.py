"""
Synthetic large-scale benchmark corpus for stress-testing retrieval.

Why synthetic with planted "needles": to measure retrieval rigorously we need
GROUND TRUTH — which chunk actually answers a question. We plant unique coined
entities (e.g. "Quaspine") in single fact sentences at known locations, buried in
topically-coherent filler (hard distractors on the same subject). A retrieved
chunk is "relevant" iff it contains that entity — exact, unambiguous labels.

Each needle yields two questions that probe different retrieval muscles:
  - lexical_q  : contains the coined entity  -> the keyword arm should nail it
  - semantic_q : a paraphrase, NO entity term -> only dense meaning-match can hit

Plus off-topic negatives (absent entities + real-world trivia) to test whether
the coverage floor still separates covered from off-topic AT SCALE.

This exercises the real pipeline (chunk -> embed -> pgvector + FTS) minus PDF
text extraction, which is separately tested. Ingests under BENCH_USER so it is
isolated from real data and trivially cleaned (`python bench_corpus.py --clean`).
"""
import argparse
import json
import random

from db import connect
from chunk import chunk_pages
from embed_store import store_source, store_chunks
from appdata import app_data_dir

BENCH_USER = "bench_user"
GOLD_PATH = app_data_dir() / "bench_gold.json"

# --- Topic vocabularies: real words so distractors cluster semantically -------
TOPICS = {
    "machine_learning": ["gradient", "training", "overfitting", "regularization",
        "neural network", "loss function", "backpropagation", "embedding",
        "convergence", "hyperparameter", "dataset", "generalization"],
    "databases": ["index", "transaction", "query planner", "normalization",
        "sharding", "replication", "isolation level", "B-tree", "join",
        "deadlock", "throughput", "schema"],
    "networking": ["latency", "packet", "congestion", "handshake", "routing",
        "bandwidth", "protocol", "firewall", "throughput", "topology",
        "retransmission", "load balancer"],
    "biology": ["mitochondria", "enzyme", "protein folding", "genome",
        "transcription", "cell membrane", "metabolism", "antibody",
        "synapse", "chromosome", "ribosome", "homeostasis"],
    "economics": ["inflation", "elasticity", "marginal cost", "equilibrium",
        "supply curve", "monetary policy", "liquidity", "externality",
        "comparative advantage", "deflation", "fiscal", "utility"],
    "climate": ["albedo", "carbon cycle", "aerosol", "radiative forcing",
        "permafrost", "ocean current", "greenhouse gas", "feedback loop",
        "glacier", "precipitation", "emission", "sea level"],
    "psychology": ["cognition", "reinforcement", "attachment", "heuristic",
        "working memory", "conditioning", "perception", "bias",
        "motivation", "neuroplasticity", "affect", "schema"],
    "law": ["precedent", "jurisdiction", "tort", "statute", "liability",
        "due process", "injunction", "plaintiff", "negligence",
        "constitutional", "contract", "evidence"],
}

# (distinctive phrase in the needle fact, paraphrase used in the semantic question)
CONCEPTS = [
    ("measures lexical drift across many corpora", "tracks how vocabulary shifts across large text collections"),
    ("reduces inference latency on small edge devices", "lowers response time for models running on constrained hardware"),
    ("balances write throughput against read consistency", "trades off how fast writes land versus how fresh reads are"),
    ("detects congestion before packets are dropped", "anticipates network overload prior to data loss"),
    ("stabilises protein folding under heat stress", "keeps molecular structure intact when temperature rises"),
    ("dampens inflation without raising unemployment", "curbs rising prices while keeping people employed"),
    ("estimates radiative forcing from aerosols", "quantifies how airborne particles change the heat balance"),
    ("predicts relapse from reinforcement schedules", "forecasts setbacks based on patterns of reward timing"),
    ("weighs precedent against statutory intent", "balances prior rulings versus what the written law meant"),
    ("compresses embeddings without losing recall", "shrinks vector representations while keeping retrieval quality"),
    ("schedules transactions to avoid deadlock", "orders database operations so they never block each other forever"),
    ("routes traffic around failed links", "steers data along working paths when connections break"),
    ("models metabolism from enzyme kinetics", "describes energy use from how fast enzymes react"),
    ("forecasts liquidity shortfalls in lending", "predicts when banks run short of ready cash"),
    ("maps permafrost thaw to methane release", "links frozen-ground melting with greenhouse gas escape"),
    ("explains bias from limited working memory", "accounts for skewed judgement caused by mental capacity limits"),
    ("allocates liability across multiple parties", "divides legal responsibility among several actors"),
    ("prunes neural connections to fight overfitting", "removes redundant model links to improve generalization"),
    ("indexes high-dimensional vectors for fast search", "organises long feature lists so lookups stay quick"),
    ("smooths congestion using adaptive backoff", "eases overload by dynamically slowing senders"),
]

_SYL_A = ["qua", "vel", "mor", "zen", "tri", "phos", "glav", "dren", "kor", "ulm",
          "yth", "brae", "scolt", "wyn", "thal", "grue", "plen", "azo", "nyx", "ferr"]
_SYL_B = ["spine", "thorn", "dingale", "with", "vex", "phor", "erton", " quine".strip(),
          "alis", "omar", "uxen", "ovia", "antle", "essa", " implore".strip(), " underal".strip(),
          "ratis", "endel", "oquine", "asper"]


def _coined(rng, used):
    """A unique nonce entity token (single alpha word, capitalised)."""
    while True:
        w = (rng.choice(_SYL_A) + rng.choice(_SYL_B)).replace(" ", "")
        w = w[0].upper() + w[1:]
        if w not in used:
            used.add(w)
            return w


def _filler_sentence(rng, kws):
    a, b, c = rng.sample(kws, 3)
    t = rng.choice([
        f"In practice, {a} is closely related to {b} when analysing {c}.",
        f"Researchers note that {a} can influence {b}, especially under {c}.",
        f"A common pitfall is to confuse {a} with {b} while studying {c}.",
        f"The relationship between {a} and {c} often depends on {b}.",
        f"Understanding {a} requires careful attention to {b} and {c}.",
        f"Several studies report that {b} moderates the effect of {a} on {c}.",
    ])
    return t


def _page_text(rng, kws, lines_per_page, needle=None):
    lines = [_filler_sentence(rng, kws) for _ in range(lines_per_page)]
    if needle is not None:
        lines.insert(rng.randint(0, len(lines)), needle)  # bury it mid-page
    return "\n".join(lines)


def generate(seed=7, n_docs=120, pages_per_doc=14, lines_per_page=14, n_needles=180,
             user=BENCH_USER, chunk_size=1200, overlap=300):
    rng = random.Random(seed)
    topic_names = list(TOPICS)
    used_entities = set()
    needles = []  # {entity, doc_idx, page, concept_idx}

    # Decide needle placements: spread over distinct (doc, page) slots.
    slots = [(d, p) for d in range(n_docs) for p in range(1, pages_per_doc + 1)]
    rng.shuffle(slots)
    for i in range(min(n_needles, len(slots))):
        d, p = slots[i]
        entity = _coined(rng, used_entities)
        phrase, paraphrase = CONCEPTS[i % len(CONCEPTS)]
        value = rng.randint(11, 99)
        needles.append({"entity": entity, "doc_idx": d, "page": p,
                        "phrase": phrase, "paraphrase": paraphrase, "value": value})

    by_doc_page = {(n["doc_idx"], n["page"]): n for n in needles}

    gold = []          # questions with ground-truth entity
    total_chunks = 0
    for d in range(n_docs):
        topic = topic_names[d % len(topic_names)]
        kws = TOPICS[topic]
        title = f"{topic.replace('_', ' ').title()} — Volume {d // len(topic_names) + 1}"
        filename = f"{topic}_{d:03d}.pdf"
        sid = store_source(title=title, filename=filename, user_id=user,
                           kind="work", confirmed=False)
        pages = []
        for p in range(1, pages_per_doc + 1):
            n = by_doc_page.get((d, p))
            needle_sentence = None
            if n is not None:
                needle_sentence = (f"The {n['entity']} method {n['phrase']}, "
                                   f"achieving a benchmark score of {n['value']}.")
            pages.append({"page_number": p, "is_scanned": False,
                          "page_text": _page_text(rng, kws, lines_per_page, needle_sentence)})
        chunks = chunk_pages(pages, source_id=sid, user_id=user,
                             chunk_size=chunk_size, overlap=overlap)
        store_chunks(chunks)
        total_chunks += len(chunks)

        for (dd, pp), n in by_doc_page.items():
            if dd != d:
                continue
            gold.append({"entity": n["entity"], "qtype": "lexical",
                         "q": f"What does the {n['entity']} method do?"})
            gold.append({"entity": n["entity"], "qtype": "semantic",
                         "q": f"Which approach {n['paraphrase']}?"})

    # Off-topic negatives: absent coined entities (same phrasing as lexical Qs)
    # + real-world trivia. None of these have a relevant chunk -> should refuse.
    off = []
    for _ in range(30):
        absent = _coined(rng, used_entities)  # never inserted into any doc
        off.append(f"What does the {absent} method do?")
    off += [
        "What is the capital of France?",
        "Who wrote the play Hamlet?",
        "What is the best recipe for sourdough bread?",
        "How tall is Mount Everest?",
        "When did the Roman Empire fall?",
        "What is the chemical symbol for gold?",
        "Who painted the Mona Lisa?",
        "What year did the Titanic sink?",
        "How do I change a car tyre?",
        "What is the offside rule in football?",
    ]

    # Gold (questions) is identical for any chunk size (same seed -> same text),
    # so only the canonical default corpus writes it; variants reuse that file.
    if user == BENCH_USER:
        GOLD_PATH.write_text(json.dumps(
            {"gold": gold, "off_topic": off, "n_docs": n_docs, "n_chunks": total_chunks,
             "n_needles": len(needles)}, indent=2), encoding="utf-8")
    return total_chunks, len(gold), len(off)


def clean(user=BENCH_USER):
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM sources WHERE user_id=%s;", (user,))
    print(f"Deleted bench corpus for user '{user}'.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true", help="delete the bench corpus and exit")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--docs", type=int, default=120)
    ap.add_argument("--pages", type=int, default=14)
    ap.add_argument("--lines", type=int, default=14)
    ap.add_argument("--needles", type=int, default=180)
    ap.add_argument("--user", default=BENCH_USER, help="DB user_id to ingest under (for variants)")
    ap.add_argument("--chunk-size", type=int, default=1200)
    ap.add_argument("--overlap", type=int, default=300)
    args = ap.parse_args()

    if args.clean:
        clean(args.user)
        raise SystemExit(0)

    # fresh start so counts are deterministic
    clean(args.user)
    import time
    t0 = time.perf_counter()
    n_chunks, n_gold, n_off = generate(args.seed, args.docs, args.pages, args.lines,
                                       args.needles, user=args.user,
                                       chunk_size=args.chunk_size, overlap=args.overlap)
    dt = time.perf_counter() - t0
    print(f"\nIngested {args.docs} docs -> {n_chunks} chunks in {dt:.1f}s "
          f"({n_chunks/dt:.0f} chunks/sec incl. DB inserts) "
          f"[user={args.user}, chunk_size={args.chunk_size}/{args.overlap}]")
    print(f"Gold questions: {n_gold} ({n_gold//2} needles x lexical+semantic) | "
          f"off-topic negatives: {n_off}")
    print(f"Gold written to {GOLD_PATH}")
