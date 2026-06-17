"""
Chat / collection helpers.

A chat owns the corpus added to it (see ADR 0005): the folder or files a student
adds in a chat become sources tagged with that chat's id, and a question in the
chat searches only those sources. This module manages chats and their message
history; retrieval scoping lives in query.py (the `chat_id` argument there).
"""
from psycopg.types.json import Json

from db import connect


def create_chat(user_id="user_1", title=None):
    """Start a new chat; return its id. Title can be filled in later (e.g. from
    the first question or the folder name)."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO chats (user_id, title) VALUES (%s, %s) RETURNING id;",
            (user_id, title),
        )
        return cur.fetchone()[0]


def list_chats(user_id="user_1"):
    """All of a user's chats, newest first — for the sidebar."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, title, created_at FROM chats WHERE user_id = %s "
            "ORDER BY created_at DESC;",
            (user_id,),
        )
        return [{"id": r[0], "title": r[1], "created_at": r[2]} for r in cur.fetchall()]


def rename_chat(chat_id, title):
    """Set a chat's title (e.g. once we know what it's about)."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE chats SET title = %s WHERE id = %s;", (title, chat_id))


def delete_chat(chat_id):
    """
    Delete a chat and everything it owns (ADR 0005 — a chat owns its corpus):
    its messages, and its sources + their chunks. Irreversible.

    The FKs are declared ON DELETE CASCADE in setup_db.py, so the database
    removes the chat's sources (-> their chunks) and messages atomically when the
    chat row goes — no hand-ordered child deletes to keep in sync as the schema
    grows. (The uploaded PDF files on disk are removed by the caller in app.py,
    which knows the upload directory.)
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM chats WHERE id = %s;", (chat_id,))


def add_message(chat_id, role, content, attribution=None):
    """
    Append a turn to a chat. role is 'user' or 'assistant'. attribution (for an
    assistant turn) is the list of Locators/Citations shown, stored as JSONB so
    a replay shows exactly what the student saw — no re-running retrieval.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO messages (chat_id, role, content, attribution) "
            "VALUES (%s, %s, %s, %s) RETURNING id;",
            (chat_id, role, content, Json(attribution) if attribution is not None else None),
        )
        return cur.fetchone()[0]


def get_messages(chat_id):
    """A chat's turns in order — to replay it in the UI."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT role, content, attribution, created_at FROM messages "
            "WHERE chat_id = %s ORDER BY id;",
            (chat_id,),
        )
        return [{"role": r[0], "content": r[1], "attribution": r[2], "created_at": r[3]}
                for r in cur.fetchall()]
