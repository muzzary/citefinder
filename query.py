import psycopg
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer
from openai import OpenAI

CONN = "host=localhost dbname=citefinder user=postgres password=devpass"

# same embedding model as ingestion — MUST match, or vectors won't compare
embed_model = SentenceTransformer("all-MiniLM-L6-v2")

# local Ollama via OpenAI-compatible endpoint
llm = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")


def retrieve(question, user_id="user_1", top_k=3, max_distance=0.9):
    q_vec = embed_model.encode(question)
    conn = psycopg.connect(CONN)
    register_vector(conn)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT c.chunk_text, c.page_number, s.title,
               c.embedding <=> %s AS distance
        FROM chunks c
        JOIN sources s ON c.source_id = s.id
        WHERE c.user_id = %s
        ORDER BY c.embedding <=> %s
        LIMIT %s;
        """,
        (q_vec, user_id, q_vec, top_k),
    )
    rows = cur.fetchall()
    conn.close()
    # keep only chunks that are actually close (distance below threshold)
    results = [
        {"text": r[0], "page": r[1], "source": r[2], "distance": r[3]}
        for r in rows
        if r[3] <= max_distance
    ]
    return results


def answer(question, user_id="user_1", top_k=3):
    """Full RAG: retrieve chunks, then have the LLM answer ONLY from them."""
    chunks = retrieve(question, user_id, top_k)

    if not chunks:
        return "No material found. Have you ingested any documents?", []

    # build the context block the LLM will read, with source+page labels
    context = "\n\n".join(
        f"[Source: {c['source']}, page {c['page']}]\n{c['text']}"
        for c in chunks
    )

    system_prompt = (
        "You are a research assistant. Answer the question in 3-4 sentences "
        "USING ONLY the provided sources. If the answer is not in them, reply "
        "exactly: 'This is not covered in your material.' "
        "Do NOT list sources or page numbers yourself - that is handled "
        "separately. Just give the answer."
    )

    user_prompt = f"SOURCES:\n{context}\n\nQUESTION: {question}"

    response = llm.chat.completions.create(
        model="phi4-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,  # factual, deterministic
    )

    return response.choices[0].message.content, chunks


# --- test ---
if __name__ == "__main__":
    question = input("Ask a question about your document: ")
    ans, used = answer(question)

    print("\n=== ANSWER ===")
    print(ans)

    print("\n=== RETRIEVED FROM (page numbers for citation) ===")
    for c in used:
        print(f"- {c['source']}, page {c['page']}  (distance: {c['distance']:.3f})")