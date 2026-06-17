from sentence_transformers import SentenceTransformer
from db import connect

# loads once; downloads the model the first time (~80MB), then cached
model = SentenceTransformer("all-MiniLM-L6-v2")

def embed_texts(texts):
    """Turn a list of strings into a list of 384-dim vectors."""
    return model.encode(texts, show_progress_bar=True)

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
    vectors = embed_texts(texts)

    with connect(register_vec=True) as conn, conn.cursor() as cur:
        for c, vec in zip(chunks, vectors):
            cur.execute(
                "INSERT INTO chunks (source_id, user_id, page_number, chunk_text, embedding) "
                "VALUES (%s,%s,%s,%s,%s);",
                (c["source_id"], c["user_id"], c["page_number"], c["chunk_text"], vec),
            )

    print(f"Stored {len(chunks)} chunks.")


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