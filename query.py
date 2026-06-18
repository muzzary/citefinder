import re

from openai import OpenAI
from db import connect
from settings import llm_config
from embedder import embed
from sources import list_sources_for_chat

# same embedding model as ingestion — MUST match, or vectors won't compare.
# Embeddings are ALWAYS local (ONNX all-MiniLM-L6-v2, CPU) — they never use the
# hosted LLM, so ingestion costs zero LLM tokens regardless of the config below.

# The LLM boundary: local-by-default, hosted opt-in (ADR 0002). The endpoint is
# resolved by settings.llm_config() with precedence env (CITEFINDER_LLM_*/.env) >
# config.json > local Ollama default — one config story for dev and the packaged
# app. To run on a hosted OpenAI-compatible provider (e.g. Groq) set:
#   CITEFINDER_LLM_BASE_URL=https://api.groq.com/openai/v1
#   CITEFINDER_LLM_KEY=<your key>
#   CITEFINDER_LLM_MODEL=llama-3.3-70b-versatile   (or llama-3.1-8b-instant, ...)
# Only querying uses the LLM (expansion + the grounded answer); ingestion does not.


def _llm():
    """
    Build the OpenAI-compatible client + model name from LIVE settings on every
    call (Phase 13). This is what lets the Settings UI switch Local<->Cloud and
    have the very next question use it, with no process restart — the client is
    cheap to construct (it only holds the base_url + key). settings.llm_config()
    still applies env > config.json > default, so dev (.env) is unchanged.
    """
    cfg = llm_config()
    client = OpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"])
    return client, cfg["model"]


def test_connection(base_url=None, api_key=None, model=None, timeout=90):
    """
    One cheap LLM call to verify an endpoint works — backs the Settings
    'Test connection' button so a bad key or an un-pulled local model is caught
    at config time, not on the student's first question. Any omitted argument
    falls back to live settings. Returns (ok: bool, detail: str).

    The timeout is generous (90s) because a LOCAL model cold-loads into memory on
    its first request, which can take far longer than a warm cloud call — a short
    timeout would falsely report a working-but-slow local model as broken.
    """
    cfg = llm_config()
    base_url = base_url or cfg["base_url"]
    api_key = api_key or cfg["api_key"]
    model = model or cfg["model"]
    try:
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            temperature=0,
        )
        return True, f"Connected to {model}."
    except Exception as e:
        return False, str(e)

# The single canonical refusal. Must match CONTEXT.md ("Covered").
REFUSAL = "This is not covered in your material."
# Core phrase used to RECOGNISE a refusal even when the LLM paraphrases the
# canonical string (e.g. inserts "information"). See is_refusal().
_REFUSAL_CORE = "not covered in your material"


def is_refusal(text):
    """
    True if an answer is *just* a refusal — robust to the LLM rewording the
    canonical REFUSAL string (observed: "This information is not covered...").

    A refusal is a single short sentence. A grounded answer that merely NAMES a
    coverage gap (per ADR 0004) is long and multi-sentence and will exceed the
    length guard, so this won't mistake gap-naming for a full refusal. Callers
    use this to decide whether to show Locators.
    """
    t = " ".join((text or "").split()).lower().rstrip(".")
    if _REFUSAL_CORE not in t:
        return False
    # A refusal is a single short sentence. A grounded answer that merely NAMES a
    # coverage gap (ADR 0004) is multi-sentence AND long, so it won't match. This
    # is robust to the LLM rewording the refusal into a longer single sentence
    # (e.g. "I'm sorry, but this topic is not covered in your material ...").
    sentences = [s for s in re.split(r"[.!?]+", t) if s.strip()]
    return len(t) <= 80 or len(sentences) <= 1


# --- Retrieval tuning, SET FROM EVALUATION (evaluate.py --tune-floor), not guessed.
# MAX_DISTANCE: dense coverage floor. RE-TUNED for the e5-small-v2 embedder (T32):
#   e5's cosine distances live on a far smaller scale than MiniLM's (covered best
#   distance 0.109-0.211, off-topic 0.178-0.249), so the old MiniLM-era 0.69 floor
#   passed EVERYTHING and never refused. The two ranges OVERLAP (no value cleanly
#   separates them — the large-scale benchmark T31 showed the same), so we set the
#   floor just above covered-max to PRESERVE recall (never wrongly refuse a covered
#   question) and rely on the LLM grounded-tutor refusal as the backstop for the
#   off-topic that slips through the overlap. The floor is model-specific: changing
#   the embedder requires re-running --tune-floor.
# CANDIDATE_K: per-retriever pool size before fusion. recall plateaued past 20
#   (Phase-7 sweep; confirmed on the large-scale bench), so 20 is the sweet spot.
MAX_DISTANCE = 0.22
CANDIDATE_K = 20


def _row_to_chunk(r, score_key, score):
    """Map a SELECT row (chunk id, source_id, text, page, title, author, year,
    filename, kind, confirmed) plus a retrieval score into our standard chunk
    dict. source_id travels through so a UI can wire the "cite this source"
    action straight to the Source it came from (ADR 0003)."""
    return {
        "id": r[0], "source_id": r[1], "text": r[2], "page": r[3],
        "source": r[4], "author": r[5], "year": r[6], "filename": r[7],
        "kind": r[8], "confirmed": r[9], score_key: score,
    }


def _scope(chat_id, user_id):
    """
    Return the (sql_column, value) that scopes a query.

    A chat OWNS its corpus (ADR 0005): when chat_id is given we scope to that
    chat's sources so a question searches only that folder/files. When it isn't
    (pre-chat callers, the Phase-7 eval), we fall back to the user's whole
    library. Both columns live on `sources s`, so the WHERE clause is uniform.
    The column name is an internal constant (never user input), so f-string
    interpolation here is safe; the value is always parameterised.
    """
    if chat_id is not None:
        return "s.chat_id", chat_id
    return "s.user_id", user_id


def retrieve(question, user_id="user_1", top_k=3, max_distance=MAX_DISTANCE,
             chat_id=None, q_vec=None):
    """
    Dense (semantic) retrieval: embed the question and pull the closest chunks
    from pgvector by cosine distance, scoped to a chat (or a user — see _scope).

    The max_distance floor is the structural grounding guard: chunks too far
    from the question are dropped here, before the LLM ever sees them. Dense
    distance is our coverage signal — it answers "is this passage *about* the
    same thing?" — so it (not keyword overlap) gates the refusal contract.

    q_vec: a precomputed question embedding. The same question is retrieved
    several times in one answer() (coverage gate, hybrid dense arm), so the
    caller embeds once and passes it here to avoid recomputing a CPU-bound encode.
    """
    col, val = _scope(chat_id, user_id)
    if q_vec is None:
        q_vec = embed(question)
    with connect(register_vec=True) as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT c.id, c.source_id, c.chunk_text, c.page_number, s.title,
                   s.author, s.year, s.filename, s.kind, s.confirmed,
                   c.embedding <=> %s AS distance
            FROM chunks c
            JOIN sources s ON c.source_id = s.id
            WHERE {col} = %s
            ORDER BY c.embedding <=> %s
            LIMIT %s;
            """,
            (q_vec, val, q_vec, top_k),
        )
        rows = cur.fetchall()
    return [
        _row_to_chunk(r, "distance", r[10])
        for r in rows
        if r[10] <= max_distance
    ]


def _or_query(question):
    """
    Turn a natural-language question into an OR keyword query.

    Phase-7 eval finding (D6): passing a whole question to
    websearch_to_tsquery ANDs every term ('a & b & c'), so a chunk had to
    contain ALL words — almost nothing matched and keyword recall collapsed.
    A student's question is not a boolean AND of required words; the lexical
    arm should fire on ANY salient term. We join the content tokens with the
    websearch OR operator ('a or b or c') so ts_rank still ranks by how many
    terms (and how often) a chunk matches, but a single strong term can hit.

    websearch_to_tsquery handles stemming/stopwords/safety; we only pre-split
    into alphanumeric tokens. Falls back to the raw question if it tokenises
    to nothing.
    """
    words = [w for w in re.findall(r"[A-Za-z0-9]+", question.lower()) if len(w) >= 2]
    return " or ".join(dict.fromkeys(words)) if words else question


def retrieve_keyword(question, user_id="user_1", top_k=3, chat_id=None,
                     q_vec=None, max_distance=None):
    """
    Keyword retrieval: the lexical half of hybrid search. Uses Postgres
    full-text search (the generated tsvector + GIN index from setup_db.py) so
    exact terms — names, acronyms, jargon, headings — are matched even when the
    dense embedding drifts. The question is converted to an OR query (see
    _or_query); websearch_to_tsquery parses it safely and ts_rank scores by how
    many terms match and how often. Scoped to a chat (or user) — see _scope.

    NO dense floor by default (max_distance=None). The whole point of the lexical
    arm is to surface exact-term passages the dense embedding drifts away from —
    e.g. the real "3.3 Design Description" heading chunk, whose dense distance
    actually exceeds the coverage floor (Phase-7b). Flooring keyword by dense
    distance dropped exactly those, defeating its purpose. Off-topic refusal is
    still enforced separately by the dense coverage gate in answer(); within an
    already-covered query, surfacing strong lexical matches only helps recall.
    Pass a max_distance (and q_vec) to re-enable the floor.
    """
    col, val = _scope(chat_id, user_id)
    floor = max_distance is not None
    if floor and q_vec is None:
        q_vec = embed(question)
    with connect(register_vec=floor) as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT c.id, c.source_id, c.chunk_text, c.page_number, s.title,
                   s.author, s.year, s.filename, s.kind, s.confirmed,
                   ts_rank(c.text_tsv, query) AS rank
            FROM chunks c
            JOIN sources s ON c.source_id = s.id,
                 websearch_to_tsquery('english', %s) query
            WHERE {col} = %s AND c.text_tsv @@ query
                  {"AND c.embedding <=> %s <= %s" if floor else ""}
            ORDER BY rank DESC
            LIMIT %s;
            """,
            (_or_query(question), val, q_vec, max_distance, top_k) if floor
            else (_or_query(question), val, top_k),
        )
        rows = cur.fetchall()
    return [_row_to_chunk(r, "rank", r[10]) for r in rows]


# Reciprocal Rank Fusion constant. 60 is the value from the original RRF paper
# (Cormack et al., 2009) and the de-facto default; it damps the influence of
# any single list's top ranks so no one retriever dominates the fusion.
RRF_K = 60


def rrf_fuse(ranked_lists, k=RRF_K, top_k=None):
    """
    Reciprocal Rank Fusion: combine several ranked chunk lists into one order
    using only each chunk's POSITION in each list, not its raw score. This is
    why RRF is ideal for hybrid search — cosine distance and ts_rank live on
    different scales and can't be added directly, but ranks always can.

    score(chunk) = sum over lists of 1 / (k + rank_in_that_list)   (rank is 1-based)

    Chunks are matched across lists by id; their fields are merged so the fused
    chunk keeps whatever signals it picked up (dense `distance`, keyword `rank`).
    """
    scores, merged = {}, {}
    for lst in ranked_lists:
        for rank, c in enumerate(lst, start=1):
            cid = c["id"]
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
            if cid not in merged:
                merged[cid] = dict(c)
            else:
                # carry over any score field this list contributes
                merged[cid].update(
                    {key: c[key] for key in ("distance", "rank") if key in c}
                )

    fused = []
    for cid, s in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
        c = merged[cid]
        c["rrf_score"] = s
        fused.append(c)
    return fused[:top_k] if top_k else fused


def retrieve_hybrid(question, user_id="user_1", top_k=3,
                    candidate_k=CANDIDATE_K, max_distance=MAX_DISTANCE,
                    chat_id=None, q_vec=None, dense=None, keyword=None):
    """
    Hybrid retrieval: run dense (semantic) and keyword (lexical) search in
    parallel and fuse them with RRF. Dense catches paraphrase/meaning; keyword
    catches exact terms dense embeddings drift away from. Each retriever pulls a
    wider `candidate_k` pool so fusion has room to promote chunks that rank
    middling in one list but appear in both. The question is embedded once here
    and shared by both arms (q_vec).

    `dense` / `keyword`: precomputed candidate lists for THIS question. answer()
    already runs the dense arm as its coverage gate, so it passes both in here to
    avoid re-querying — without them this recomputes both, as standalone callers
    (evaluate.py) expect.
    """
    if q_vec is None and dense is None:
        q_vec = embed(question)
    if dense is None:
        dense = retrieve(question, user_id, top_k=candidate_k,
                         max_distance=max_distance, chat_id=chat_id, q_vec=q_vec)
    # Keyword arm runs UNFLOORED (see retrieve_keyword): the dense arm already
    # contributes only covered chunks, and the coverage gate guards refusal, so
    # the lexical arm is free to surface exact-term passages dense drifts past.
    if keyword is None:
        keyword = retrieve_keyword(question, user_id, top_k=candidate_k, chat_id=chat_id)
    return rrf_fuse([dense, keyword], top_k=top_k)


def expand_query(question, n=3):
    """
    Multi-query expansion: ask the local LLM to rewrite the question into a few
    alternative phrasings (synonyms, broader/narrower wording). A single vague
    question often misses passages that phrase the idea differently; retrieving
    for several variants and fusing widens recall.

    Always returns the ORIGINAL question first, then up to n variants. If the
    LLM is unavailable we degrade gracefully to just the original — retrieval
    must never hard-depend on query expansion.
    """
    prompt = (
        f"Rewrite the following study question into {n} alternative search "
        "queries that mean the same thing but use different words, synonyms, or "
        "level of detail. Output ONLY the queries, one per line, with no "
        "numbering, bullets, or commentary.\n\n"
        f"Question: {question}"
    )
    try:
        client, model = _llm()
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        raw = resp.choices[0].message.content or ""
    except Exception as e:
        print(f"  (query expansion unavailable: {e}; using original question only)")
        return [question]

    variants = []
    for line in raw.splitlines():
        # strip common list prefixes (1.  -  *  •) and surrounding quotes
        line = line.strip().lstrip("0123456789.)-*• ").strip().strip('"').strip()
        if line and line.lower() != question.lower() and line not in variants:
            variants.append(line)

    return [question] + variants[:n]


def retrieve_multi(question, user_id="user_1", top_k=3, n_variants=3,
                   candidate_k=CANDIDATE_K, max_distance=MAX_DISTANCE,
                   chat_id=None, q_vec=None, dense=None, keyword=None):
    """
    Full Phase-6 retrieval: expand the question into variants, run HYBRID
    (dense + keyword) search for each, and fuse every resulting list with one
    RRF pass. The dense distance floor is applied per variant inside retrieve()
    and retrieve_keyword(), so each variant contributes only its own grounded
    candidates. The original question's embedding (q_vec) is reused; each
    variant is a different string and is embedded on its own.

    `dense` / `keyword`: precomputed lists for the ORIGINAL question, reused
    instead of re-querying it (answer() already has them from the hybrid pass).
    """
    queries = expand_query(question, n=n_variants)
    ranked_lists = []
    for q in queries:
        if q == question and dense is not None:
            ranked_lists.append(dense)
        else:
            qv = q_vec if q == question else None
            ranked_lists.append(retrieve(q, user_id, top_k=candidate_k,
                                         max_distance=max_distance, chat_id=chat_id, q_vec=qv))
        if q == question and keyword is not None:
            ranked_lists.append(keyword)
        else:
            ranked_lists.append(retrieve_keyword(q, user_id, top_k=candidate_k, chat_id=chat_id))
    return rrf_fuse(ranked_lists, top_k=top_k)


def _has_material(user_id="user_1", chat_id=None):
    """True if there is anything ingested in scope (this chat, or this user)."""
    col, val = _scope(chat_id, user_id)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT EXISTS (SELECT 1 FROM chunks c "
            f"JOIN sources s ON c.source_id = s.id WHERE {col} = %s);",
            (val,),
        )
        return cur.fetchone()[0]


# --- Retrieval routing -------------------------------------------------------
# Multi-query expansion costs a whole extra LLM call, so we only escalate to it
# when the cheap hybrid result looks weak. These thresholds are PLACEHOLDERS to
# be tuned by Phase-7 evaluation (like the distance floor) — not magic numbers.
SHORT_QUESTION_WORDS = 5    # a very short question has little to match on
WEAK_MATCH_DISTANCE = 0.6   # closest dense hit farther than this = weak match


def _best_distance(chunks):
    """Smallest dense distance among chunks (keyword-only chunks have none)."""
    dists = [c["distance"] for c in chunks if "distance" in c]
    return min(dists) if dists else None


def should_expand(question, hybrid_chunks):
    """
    Decide — using cheap signals only, NO LLM — whether the hybrid result is
    weak enough to justify the extra LLM call for multi-query expansion.

    - Empty hybrid result -> do NOT expand: nothing to rescue, treat as a
      refusal rather than burning the LLM on a likely off-topic question.
    - Very short question -> expand: too little context to match well.
    - Closest dense match still far (or only keyword hits) -> expand: the
      question may be phrased unlike the source, where paraphrases can help.
    """
    if not hybrid_chunks:
        return False
    if len(question.split()) <= SHORT_QUESTION_WORDS:
        return True
    best = _best_distance(hybrid_chunks)
    return best is None or best > WEAK_MATCH_DISTANCE


# --- Structural intents -------------------------------------------------------
# Some questions are not about the semantic CONTENT of the material but about the
# corpus itself ("list the files") or a specific PAGE ("what's on page 2"). Dense
# similarity can't answer these — they were refused or mis-answered — so we detect
# them with conservative patterns and answer DETERMINISTICALLY from real data (the
# file list / the actual page text), bypassing retrieval and the coverage gate.
# Never hallucinated: the file names and page text are pulled straight from the DB.

_CORPUS_NOUN = r"(files?|documents?|sources?|pdfs?|readings?)"
_META_RE = re.compile(
    rf"\bhow many {_CORPUS_NOUN}\b"
    rf"|\b(list|show)\b.{{0,20}}\b{_CORPUS_NOUN}\b"   # list / show ... files
    rf"|\b(what|which)\s+{_CORPUS_NOUN}\b"            # what files / which documents
    rf"|\bnames?\s+of\b.{{0,12}}{_CORPUS_NOUN}\b"     # names of (the) files
    rf"|\bfile names?\b",
    re.I,
)
_LAST_PAGE_RE = re.compile(r"\b(last|final)\s+page\b", re.I)
_FIRST_PAGE_RE = re.compile(r"\b(first|front|title|cover)\s+page\b|\bpage\s+(?:1|one)\b", re.I)
_PAGE_NUM_RE = re.compile(r"\bpage\s+(\d{1,4})\b", re.I)

# A page reference is treated as page-INTENT (summarise that whole page) only when
# the question is actually ABOUT the page — "what's on page 2", "show page 3",
# "summarise the first page", or a near-bare "page 5?". A content question that
# merely cites a page ("explain the method on page 5") must fall through to normal
# retrieval, which can span pages, instead of being answered from that one page.
_PAGE_FOCUS_RE = re.compile(
    r"\bwhat(?:'s| is| are)?\b.{0,30}\bpage\b"                  # what is on (the) page ...
    r"|\b(show|list|summari[sz]e|describe|contents? of)\b.{0,30}\bpage\b"
    r"|\b(first|last|final|front|title|cover)\s+page\b"         # the first/last page (unambiguous)
    r"|^\W*page\b",                                             # "page 2", "page 2?"
    re.I,
)


def _is_corpus_meta(question):
    """True for questions enumerating the corpus ('list files', 'file names')."""
    return bool(_META_RE.search(question))


def _page_target(question):
    """The page a question explicitly asks about: 'last', 'first', an int, or None.

    Returns None unless the question is page-FOCUSED (see _PAGE_FOCUS_RE), so a
    content question that only cites a page falls through to normal retrieval."""
    if not _PAGE_FOCUS_RE.search(question):
        return None
    if _LAST_PAGE_RE.search(question):
        return "last"
    if _FIRST_PAGE_RE.search(question):
        return "first"
    m = _PAGE_NUM_RE.search(question)
    return int(m.group(1)) if m else None


def _answer_meta(chat_id):
    """Answer a corpus-meta question from the chat's real source list (no LLM)."""
    srcs = list_sources_for_chat(chat_id)
    if not srcs:
        return "No files have been added to this chat yet.", []
    n = len(srcs)
    lines = [
        f"{i}. {s['filename']}" + (f" ({s['n_chunks']} sections)" if s["n_chunks"] else "")
        for i, s in enumerate(srcs, 1)
    ]
    body = f"This chat contains {n} file{'s' if n != 1 else ''}:\n" + "\n".join(lines)
    return body, []


def _fetch_page(chat_id, user_id, target):
    """Chunks for an explicitly requested page, scoped to the chat (or user).
    'first' -> page 1; 'last' -> each source's max page; an int -> that page."""
    col, val = _scope(chat_id, user_id)
    cols = ("c.id, c.source_id, c.chunk_text, c.page_number, s.title, s.author, "
            "s.year, s.filename, s.kind, s.confirmed")
    with connect() as conn, conn.cursor() as cur:
        if target == "last":
            cur.execute(
                f"SELECT {cols} FROM chunks c JOIN sources s ON c.source_id = s.id "
                f"WHERE {col} = %s AND c.page_number = "
                f"(SELECT MAX(c2.page_number) FROM chunks c2 WHERE c2.source_id = s.id) "
                f"ORDER BY s.id, c.id;",
                (val,),
            )
        else:
            page = 1 if target == "first" else int(target)
            cur.execute(
                f"SELECT {cols} FROM chunks c JOIN sources s ON c.source_id = s.id "
                f"WHERE {col} = %s AND c.page_number = %s ORDER BY s.id, c.id;",
                (val, page),
            )
        rows = cur.fetchall()
    return [_row_to_chunk(r, "page_hit", 0.0) for r in rows]


def _answer_page(question, chat_id, user_id, target):
    """Summarise what is on an explicitly requested page, from that page's actual
    text (bypasses the semantic gate — the user named the page)."""
    chunks = _fetch_page(chat_id, user_id, target)
    if not chunks:
        where = ("the first page" if target == "first"
                 else "the last page" if target == "last" else f"page {target}")
        return f"There is no extractable text on {where} in your material.", []
    context = "\n\n".join(
        f"[{c['filename']}, page {c['page']}]\n{c['text']}" for c in chunks
    )
    system_prompt = (
        "You are a study assistant. The student asked what is on a specific page "
        "of their own material. Using ONLY the provided page text, describe and "
        "explain what is on that page in clear, simple terms. Never add facts from "
        "outside the text. Do not list file names or page numbers yourself — that "
        "is shown separately."
    )
    user_prompt = f"PAGE TEXT:\n{context}\n\nQUESTION: {question}"
    client, model = _llm()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
    )
    return response.choices[0].message.content, chunks


def answer(question, user_id="user_1", top_k=5, expand="auto", chat_id=None):
    """
    Grounded-tutor RAG with an adaptive retriever, scoped to a chat's corpus
    when chat_id is given (else the user's whole library — see _scope).

    Always tries the cheap path first — HYBRID (dense + keyword, one RRF pass,
    no extra LLM call). It escalates to the costlier MULTI-QUERY path (which
    adds an LLM expansion call) only when the hybrid result looks weak.

    expand: "auto" (default — let should_expand decide), True (force
            multi-query), or False (hybrid only, never expand).

    If nothing is covered, refuse BEFORE the answer LLM call.
    """
    # Structural intents first (chat-scoped): a question about the corpus itself
    # ("list the files") is answered from the real source list, no retrieval.
    if chat_id is not None and _is_corpus_meta(question):
        return _answer_meta(chat_id)

    # Structural refusal, layer one: nothing ingested in scope is refused before
    # ANY LLM call — so we never spin up the model for an empty chat/library.
    if not _has_material(user_id, chat_id=chat_id):
        return "No material found. Have you ingested any documents?", []

    # Page-scoped intent ("what's on page 2 / the first page"): answer from that
    # page's actual text, bypassing the semantic gate (the user named the page).
    page_target = _page_target(question)
    if chat_id is not None and page_target is not None:
        return _answer_page(question, chat_id, user_id, page_target)

    # Embed the question ONCE here and reuse it everywhere. The encode is the
    # dominant CPU cost on the ask path; recomputing it per retriever was waste.
    q_vec = embed(question)

    # Coverage gate (structural refusal, layer two) AND the hybrid dense arm in
    # ONE scan: pull the dense candidate pool once. Coverage is decided by DENSE
    # distance against the tuned MAX_DISTANCE floor — NOT by keyword overlap,
    # which (with OR semantics) matches almost any question sharing a common word
    # and so can't tell "covered" from "off-topic". An empty pool means nothing
    # is within the floor, so we refuse before any answer/expansion LLM. (This
    # pool is exactly what retrieve_hybrid's dense arm needs, so we don't rescan.)
    dense = retrieve(question, user_id, top_k=CANDIDATE_K, chat_id=chat_id, q_vec=q_vec)
    if not dense:
        return REFUSAL, []

    # Covered: rank with the cheap hybrid path (no extra LLM call), reusing the
    # dense pool from the gate so the only new query is the keyword arm. Keyword
    # chunks may be semantically farther, but coverage is already established, so
    # they only help surface exact-term passages within the covered material.
    keyword = retrieve_keyword(question, user_id, top_k=CANDIDATE_K, chat_id=chat_id)
    chunks = retrieve_hybrid(question, user_id, top_k=top_k, chat_id=chat_id,
                             q_vec=q_vec, dense=dense, keyword=keyword)

    # Escalate to multi-query only when the hybrid result looks weak (this is the
    # only LLM cost retrieval can add, so we gate it deliberately). Reuse the
    # already-fetched dense/keyword lists for the original question — only the
    # expansion variants need fresh queries.
    if expand is True or (expand == "auto" and should_expand(question, chunks)):
        chunks = retrieve_multi(question, user_id, top_k=top_k, chat_id=chat_id,
                                q_vec=q_vec, dense=dense, keyword=keyword)

    if not chunks:
        return REFUSAL, []

    # build the context block the LLM will read, with source+page labels
    context = "\n\n".join(
        f"[Source: {c['source']}, page {c['page']}]\n{c['text']}"
        for c in chunks
    )

    system_prompt = (
        "You are a study tutor. Using ONLY the provided sources, explain the "
        "answer to the student's question clearly and simply, the way a teacher "
        "would. Ground every statement in the sources — never add facts from your "
        "own knowledge. If the sources cover the question only partially, answer "
        "what they cover and say plainly what they do not. If the sources do not "
        "address the question at all, reply with EXACTLY this sentence and "
        f"nothing else, word for word: {REFUSAL} "
        "Do NOT list sources, file names, or page numbers yourself — that is "
        "handled separately."
    )

    user_prompt = f"SOURCES:\n{context}\n\nQUESTION: {question}"

    client, model = _llm()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,  # factual, deterministic
    )

    out = response.choices[0].message.content
    # LLM-backstop refusal: if the model refused despite being handed chunks,
    # normalise to the canonical refusal with NO attribution, so callers never
    # attach Locators to a non-answer (the structural refusals above already
    # return []). is_refusal is robust to the model rewording the refusal.
    if is_refusal(out):
        return REFUSAL, []
    return out, chunks


# --- test ---
if __name__ == "__main__":
    from citations import format_citation, format_locator, render_locator

    style = input("Citation style for confirmed works (APA / Harvard / IEEE): ").strip() or "APA"
    question = input("Ask a question about your material: ")
    ans, used = answer(question)

    print("\n=== ANSWER ===")
    print(ans)

    # If the system refused (or the library is empty), there is nothing to attribute.
    if used and not is_refusal(ans):
        print("\n=== WHERE THIS COMES FROM ===")
        seen = set()
        for c in used:
            key = (c["filename"], c["page"])
            if key in seen:        # one attribution per (file, page)
                continue
            seen.add(key)

            # Locator is the default attribution for EVERY source.
            loc = format_locator(c["filename"], c["page"], c["text"], title=c["source"])
            print(render_locator(loc))

            # A formatted Citation is offered ONLY for a confirmed Work.
            if c["kind"] == "work" and c["confirmed"]:
                meta = {"author": c["author"], "title": c["source"],
                        "year": c["year"], "filename": c["filename"]}
                print(f"   {style.upper()}: {format_citation(meta, page=c['page'], style=style)}")
            elif c["kind"] == "work":
                print("   (Citation available once you confirm this work's details.)")
            print()
