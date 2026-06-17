import os
import psycopg

# Load a local .env (if present) before anything reads os.environ. db.py is the
# universal import — every module pulls connect() from here — so loading here
# guarantees CITEFINDER_* vars (DB + the LLM endpoint in query.py) are populated
# before they're read. A missing python-dotenv or .env is a no-op: real shell
# env vars still work, so nothing hard-depends on the file.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ModuleNotFoundError:
    pass

# Single source of truth for the database connection.
# Override via the CITEFINDER_DB env var (e.g. to point at a hosted Postgres
# when swapping local -> hosted). Falls back to the local Docker dev instance.
CONN = os.environ.get(
    "CITEFINDER_DB",
    "host=localhost dbname=citefinder user=postgres password=devpass",
)


def connect(register_vec=False):
    """
    Open a database connection.

    Use as a context manager so the connection is always committed and closed,
    even if an error is raised mid-query:

        with connect() as conn, conn.cursor() as cur:
            cur.execute(...)

    Set register_vec=True when the work reads or writes embedding vectors.
    """
    conn = psycopg.connect(CONN)
    if register_vec:
        from pgvector.psycopg import register_vector
        register_vector(conn)
    return conn
