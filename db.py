import psycopg

from settings import db_conn_string

# Single source of truth for the database connection. settings.db_conn_string()
# resolves it with precedence env (CITEFINDER_DB / .env) > config.json > local
# Docker default, and also loads .env on import — so every module that pulls
# connect() from here gets the resolved config without touching os.environ.
CONN = db_conn_string()


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
