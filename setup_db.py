import psycopg

CONN = "host=localhost dbname=citefinder user=postgres password=devpass"

def setup():
    conn = psycopg.connect(CONN)
    cur = conn.cursor()

    # sources table: one row per uploaded PDF (metadata for citations)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sources (
            id SERIAL PRIMARY KEY,
            user_id TEXT NOT NULL,
            title TEXT,
            author TEXT,
            year TEXT,
            filename TEXT,
            metadata_complete BOOLEAN DEFAULT FALSE
        );
    """)

    # chunks table: many rows per source, each with its vector + page
    # vector(384) MUST match the embedding model's output size
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id SERIAL PRIMARY KEY,
            source_id INTEGER REFERENCES sources(id),
            user_id TEXT NOT NULL,
            page_number INTEGER,
            chunk_text TEXT,
            embedding vector(384)
        );
    """)

    conn.commit()
    conn.close()
    print("Tables created: sources, chunks")

if __name__ == "__main__":
    setup()
    print("done running setup")