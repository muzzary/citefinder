from db import connect
from embedder import embed

def store_source(title, filename, user_id="user_1", author=None, year=None,
                 kind="work", confirmed=False, chat_id=None):
    """
    Insert one source row, return its new id.

    kind      : 'work' (citable) or 'notes' (locator-only). See ADR 0001/0003.
    confirmed : student has verified this Work's metadata, so a Citation may be
                offered. Notes are never confirmed (they are locator-only).
    chat_id   : the chat this source belongs to (see ADR 0005). None for
                pre-chat / eval sources that are scoped by user_id instead.
    """
    complete = author is not None and year is not None
    if kind == "notes":
        confirmed = False
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sources "
            "(user_id, title, author, year, filename, metadata_complete, kind, confirmed, chat_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id;",
            (user_id, title, author, year, filename, complete, kind, confirmed, chat_id),
        )
        source_id = cur.fetchone()[0]
    return source_id

def store_chunks(chunks):
    """Embed each chunk's text and insert it with its vector + metadata."""
    if not chunks:
        print("No chunks to store (no extractable text survived).")
        return

    texts = [c["chunk_text"] for c in chunks]
    vectors = embed(texts, kind="passage")   # e5 document prefix (see embedder.py)

    # Batched insert (executemany) over one connection. NOTE: benchmarking showed
    # ingest is embedding-bound (CPU ONNX, ~proportional to total tokens), not
    # insert-bound — batching here is good practice and trims round trips, but the
    # dominant cost is embed() above. The real ingest lever is the embedder, not SQL.
    params = [
        (c["source_id"], c["user_id"], c["page_number"], c["chunk_text"], vec)
        for c, vec in zip(chunks, vectors)
    ]
    with connect(register_vec=True) as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO chunks (source_id, user_id, page_number, chunk_text, embedding) "
            "VALUES (%s,%s,%s,%s,%s);",
            params,
        )

    print(f"Stored {len(chunks)} chunks.")


def store_source_and_chunks(*, title, filename, user_id="user_1", author=None,
                            year=None, kind="work", confirmed=False, chat_id=None,
                            doi=None, chunks):
    """
    Atomic ingest: embed the chunks, then insert the source row AND all its chunks
    in ONE transaction, returning the new source_id.

    Why atomic: store_source used to commit the source row on its own, then
    store_chunks ran a second (slow) transaction to embed + insert. That left a
    window where a half-ingested source was already visible — deleting it (or its
    chat) mid-ingest removed the row, and the in-flight chunk insert then hit a
    foreign-key violation. Committing source + chunks together closes the window:
    the source only becomes visible once its chunks are in, so it can't be deleted
    underneath the insert, and any failure (e.g. the chat was deleted during the
    embed) rolls back cleanly with nothing orphaned.

    Embedding happens BEFORE the transaction opens, so the DB connection isn't held
    during the slow CPU work — only the fast writes are inside the transaction.
    """
    complete = author is not None and year is not None
    if kind == "notes":
        confirmed = False

    texts = [c["chunk_text"] for c in chunks]
    vectors = embed(texts, kind="passage") if texts else []   # slow; outside the txn

    with connect(register_vec=True) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO sources "
            "(user_id, title, author, year, filename, metadata_complete, kind, confirmed, chat_id, doi) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id;",
            (user_id, title, author, year, filename, complete, kind, confirmed, chat_id, doi or None),
        )
        source_id = cur.fetchone()[0]
        if chunks:
            params = [
                (source_id, c["user_id"], c["page_number"], c["chunk_text"], vec)
                for c, vec in zip(chunks, vectors)
            ]
            cur.executemany(
                "INSERT INTO chunks (source_id, user_id, page_number, chunk_text, embedding) "
                "VALUES (%s,%s,%s,%s,%s);",
                params,
            )
    return source_id


# --- quick test: full ingest of one PDF ---
if __name__ == "__main__":
    from ingest import extract_pdf_pages
    from chunk import chunk_pages

    # 1. create the source row first (so we have a real source_id)
    source_id = store_source(title="My Fyp Paper", filename="fyp_final.pdf")

    # 2. extract + chunk, tagging chunks with that source_id
    pages = extract_pdf_pages("data/fyp_final.pdf")
    chunks = chunk_pages(pages, source_id=source_id)

    # 3. embed + store
    store_chunks(chunks)

    # 4. verify what's in the DB
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM chunks WHERE source_id = %s;", (source_id,))
        print("Chunks in DB for this source:", cur.fetchone()[0])