from db import connect

def setup():
    with connect() as conn, conn.cursor() as cur:
        # sources table: one row per uploaded PDF.
        #   kind      = 'work' (a citable publication) or 'notes' (the
        #               student's own / mixed-origin material). See ADR 0001/0003.
        #   confirmed = the student has verified this Work's metadata, so a
        #               formatted Citation may be offered. Until then attribution
        #               is Locator-only. See ADR 0003.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT,
                author TEXT,
                year TEXT,
                filename TEXT,
                metadata_complete BOOLEAN DEFAULT FALSE,
                kind TEXT NOT NULL DEFAULT 'work',
                confirmed BOOLEAN NOT NULL DEFAULT FALSE
            );
        """)

        # Migration for databases created before kind/confirmed existed.
        cur.execute("ALTER TABLE sources ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'work';")
        cur.execute("ALTER TABLE sources ADD COLUMN IF NOT EXISTS confirmed BOOLEAN NOT NULL DEFAULT FALSE;")

        # Citation typing (see citations.py). work_type picks the reference shape
        #   book | article | website
        # and cite_meta (JSONB) holds the fields that shape needs but the common
        # columns (author/title/year) don't carry — publisher/place/edition for a
        # book, journal/volume/issue/pages/doi for an article, site_name/url/
        # pub_date/accessed for a website. JSONB (not 10 sparse columns) keeps the
        # per-type fields together and lets the set grow without a migration each
        # time. author/title/year stay as columns: they're shared by all types and
        # gate `confirmed`.
        cur.execute("ALTER TABLE sources ADD COLUMN IF NOT EXISTS work_type TEXT NOT NULL DEFAULT 'book';")
        cur.execute("ALTER TABLE sources ADD COLUMN IF NOT EXISTS cite_meta JSONB;")

        # The DOI auto-detected from the PDF's first pages at ingest (metadata.py).
        # Not a citation field itself — it's the KEY the confirm UI uses to offer a
        # one-click CrossRef lookup that fills the citation fields (see crossref.py).
        cur.execute("ALTER TABLE sources ADD COLUMN IF NOT EXISTS doi TEXT;")

        # chunks table: many rows per source, each with its vector + page
        # vector(384) MUST match the embedding model's output size
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                id SERIAL PRIMARY KEY,
                source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL,
                page_number INTEGER,
                chunk_text TEXT,
                embedding vector(384)
            );
        """)

        # Full-text search column for the keyword half of hybrid retrieval
        # (Phase 6). A GENERATED STORED tsvector stays in sync with chunk_text
        # automatically — it back-fills existing rows on creation and updates on
        # every insert/update, so no application code has to maintain it.
        # The GIN index makes ts_rank / @@ lookups fast. See ADR 0003 / CLAUDE.md.
        cur.execute("""
            ALTER TABLE chunks ADD COLUMN IF NOT EXISTS text_tsv tsvector
            GENERATED ALWAYS AS (to_tsvector('english', coalesce(chunk_text, '')))
            STORED;
        """)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS chunks_text_tsv_idx "
            "ON chunks USING GIN (text_tsv);"
        )

        # Index the FK column chunks.source_id (Postgres does NOT auto-index FKs).
        # Speeds the chunks->sources join, the page-intent MAX(page) subquery, and
        # ON DELETE CASCADE when a source/chat is removed.
        cur.execute(
            "CREATE INDEX IF NOT EXISTS chunks_source_id_idx ON chunks(source_id);"
        )

        # chats: a chat OWNS the corpus added to it (see ADR 0005). Sources are
        # scoped to a chat via chat_id, so a question searches only that chat's
        # folder/files — not everything the user ever uploaded.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                id SERIAL PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)

        # messages: the Q&A history of a chat, so the sidebar can replay it.
        # attribution holds the Locators/Citations shown with an assistant turn
        # (stored, not re-derived, so a replay is exactly what the student saw).
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                attribution JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
        """)

        # Scope sources to a chat. Nullable so pre-chat / eval sources (scoped by
        # user_id) keep working; new app ingests set it.
        cur.execute("ALTER TABLE sources ADD COLUMN IF NOT EXISTS chat_id INTEGER REFERENCES chats(id);")
        cur.execute("CREATE INDEX IF NOT EXISTS sources_chat_id_idx ON sources(chat_id);")

        # Make deletes cascade at the DB layer so deleting a chat removes its
        # corpus + history atomically (chat -> its sources -> their chunks, and
        # chat -> its messages). delete_chat then only deletes the chat row, and
        # a future child table can't be silently orphaned by a stale manual
        # delete order. Idempotent: re-point the FKs to ON DELETE CASCADE.
        # (CREATE TABLE above already sets this for fresh DBs; these ALTERs
        # upgrade databases created before the cascade existed.)
        for table, col, ref in (
            ("chunks", "source_id", "sources(id)"),
            ("messages", "chat_id", "chats(id)"),
            ("sources", "chat_id", "chats(id)"),
        ):
            constraint = f"{table}_{col}_fkey"
            cur.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint};")
            cur.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
                f"FOREIGN KEY ({col}) REFERENCES {ref} ON DELETE CASCADE;"
            )

    print("Tables created: sources, chunks, chats, messages (+ indexes, cascade FKs)")

if __name__ == "__main__":
    setup()
    print("done running setup")