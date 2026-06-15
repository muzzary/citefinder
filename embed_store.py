import psycopg
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

CONN = "host=localhost dbname=citefinder user=postgres password=devpass"

# loads once; downloads the model the first time (~80MB), then cached
model = SentenceTransformer("all-MiniLM-L6-v2")

def embed_texts(texts):
    """Turn a list of strings into a list of 384-dim vectors."""
    return model.encode(texts, show_progress_bar=True)

def store_source(title, filename, user_id="user_1", author=None, year=None):
    """Insert one source row, return its new id."""
    conn = psycopg.connect(CONN)
    cur = conn.cursor()
    complete = author is not None and year is not None
    cur.execute(
        "INSERT INTO sources (user_id, title, author, year, filename, metadata_complete) "
        "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id;",
        (user_id, title, author, year, filename, complete),
    )
    source_id = cur.fetchone()[0]
    conn.commit()
    conn.close()
    return source_id

def store_chunks(chunks):
    """Embed each chunk's text and insert it with its vector + metadata."""
    conn = psycopg.connect(CONN)
    register_vector(conn)   # lets psycopg handle the vector type
    cur = conn.cursor()

    texts = [c["chunk_text"] for c in chunks]
    vectors = embed_texts(texts)

    for c, vec in zip(chunks, vectors):
        cur.execute(
            "INSERT INTO chunks (source_id, user_id, page_number, chunk_text, embedding) "
            "VALUES (%s,%s,%s,%s,%s);",
            (c["source_id"], c["user_id"], c["page_number"], c["chunk_text"], vec),
        )

    conn.commit()
    conn.close()
    print(f"Stored {len(chunks)} chunks.")


# --- quick test: full ingest of one PDF ---
if __name__ == "__main__":
    from ingest import extract_pdf_pages
    from chunk import chunk_pages

    # 1. create the source row first (so we have a real source_id)
    source_id = store_source(title="My Fyp Paper", filename="fyp_final.pdf")

    # 2. extract + chunk, tagging chunks with that source_id
    pages = extract_pdf_pages("fyp_final.pdf")
    chunks = chunk_pages(pages, source_id=source_id)

    # 3. embed + store
    store_chunks(chunks)

    # 4. verify what's in the DB
    conn = psycopg.connect(CONN)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM chunks WHERE source_id = %s;", (source_id,))
    print("Chunks in DB for this source:", cur.fetchone()[0])
    conn.close()