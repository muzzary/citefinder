"""
Source lifecycle: confirm a Source's metadata (the "cite this source" flow) and
render a formatted Citation on demand. See ADR 0003 (locate-by-default,
cite-on-confirmation) and ADR 0005 (a chat owns its corpus).

Confirmation is per Source and **persisted**: once a student confirms a Work's
author/title/year it is LOCKED as confirmed — the system never asks again. The
UI reads the `confirmed` flag (via get_source) to decide whether the "cite this
source" button needs the confirm step first or can cite straight away. A student
may re-enter the flow to fix a typo, but only by their own choice.

Style (APA / Harvard / IEEE) is NOT stored here: it is chosen at cite time, so
the same confirmed Source can be cited in any style (see the three scenarios in
the DEVLOG — only the paper-writer needs a Citation, and the style is their
per-paper choice). The LLM is never involved in citations.
"""
from psycopg.types.json import Json

from db import connect
from citations import format_citation, WORK_TYPES


def list_sources_for_chat(chat_id):
    """
    Every Source in a chat's corpus (ADR 0005), for the "files in this chat"
    list: filename + title, kind, confirmed state, and chunk count. The UI uses
    `confirmed` to show whether a Work is citable yet or still locator-only.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id, s.title, s.filename, s.kind, s.confirmed,
                   s.author, s.year, s.work_type, s.cite_meta, s.doi,
                   COUNT(c.id) AS n_chunks
            FROM sources s
            LEFT JOIN chunks c ON c.source_id = s.id
            WHERE s.chat_id = %s
            GROUP BY s.id
            ORDER BY s.id;
            """,
            (chat_id,),
        )
        rows = cur.fetchall()
    return [{"id": r[0], "title": r[1], "filename": r[2], "kind": r[3],
             "confirmed": r[4], "author": r[5], "year": r[6],
             "work_type": r[7], "cite_meta": r[8], "doi": r[9], "n_chunks": r[10]}
            for r in rows]


def get_source(source_id):
    """
    Return a source row as a dict, or None. The UI checks `confirmed` to decide
    whether the "cite this source" button needs the confirm step first.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, title, author, year, filename, kind, confirmed, chat_id, "
            "work_type, cite_meta, doi FROM sources WHERE id = %s;",
            (source_id,),
        )
        r = cur.fetchone()
    if r is None:
        return None
    return {"id": r[0], "title": r[1], "author": r[2], "year": r[3],
            "filename": r[4], "kind": r[5], "confirmed": r[6], "chat_id": r[7],
            "work_type": r[8], "cite_meta": r[9], "doi": r[10]}


# cite_meta keys we accept per work_type — anything else in the posted dict is
# ignored, so the API can't be used to stuff arbitrary JSON onto a source.
_META_FIELDS = {
    "book":    ("publisher", "place", "edition"),
    "article": ("journal", "volume", "issue", "pages", "doi"),
    "website": ("site_name", "url", "pub_date", "accessed"),
}


def confirm_source(source_id, author, title=None, year=None,
                   work_type="book", meta=None):
    """
    Confirm a Work's real metadata and LOCK it as confirmed (ADR 0003). After
    this a Citation can be rendered for the source and the system never asks for
    confirmation again — the `confirmed` flag is persisted.

    `work_type` (book | article | website) picks the citation shape, and `meta`
    carries the type-specific fields for it (see _META_FIELDS / citations.py).
    Only the fields valid for the chosen type are kept; blanks are dropped, so a
    re-confirm with a different type doesn't leave a stale field behind.

    A citation needs author + year + a REAL title. The title falls back to the
    source's stored title if not given, but a title that is merely the filename
    (the locate-by-default fallback used at ingest) is NOT a bibliographic title:
    it does not unlock a citation, so the source stays **unconfirmed**
    (locator-only) until a proper title is supplied. Likewise a missing author or
    year keeps it unconfirmed. The provided metadata is still saved either way.
    The type-specific fields (publisher, journal, …) are optional and never gate
    confirmation — they only enrich the rendered citation.

    Notes are locator-only by design and cannot be confirmed.

    Returns the updated source dict. Raises ValueError for an unknown source, a
    Notes source, or an invalid work_type.
    """
    src = get_source(source_id)
    if src is None:
        raise ValueError(f"No source with id {source_id}.")
    if src["kind"] == "notes":
        raise ValueError("Notes are locator-only and cannot be confirmed (ADR 0003).")

    work_type = (work_type or "book").lower()
    if work_type not in WORK_TYPES:
        raise ValueError(f"work_type must be one of {WORK_TYPES}.")

    author = (author or "").strip()
    title = (title or "").strip() or (src["title"] or "")
    year = str(year).strip() if year is not None else ""

    # Keep only the fields that belong to this type, trimmed, blanks dropped.
    meta = meta or {}
    cite_meta = {k: str(meta.get(k)).strip()
                 for k in _META_FIELDS[work_type]
                 if str(meta.get(k) or "").strip()}

    # A citation needs author + year + a real title. A title equal to the stored
    # filename is the locate-by-default placeholder, not a citation title, so it
    # does not lock the source (ADR 0003: never cite from a non-real-metadata guess).
    has_real_title = bool(title) and title != (src["filename"] or "")
    confirmed = bool(author and year and has_real_title)

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE sources SET author=%s, title=%s, year=%s, work_type=%s, "
            "cite_meta=%s, confirmed=%s, metadata_complete=%s WHERE id=%s;",
            (author or None, title, year or None, work_type,
             Json(cite_meta) if cite_meta else None,
             confirmed, confirmed, source_id),
        )

    return get_source(source_id)


def delete_source(source_id):
    """
    Remove a single Source from a chat's corpus — the per-file "remove" action in
    the Files menu (a more granular sibling of chats.delete_chat, which drops the
    whole chat). The chunks FK is ON DELETE CASCADE (setup_db.py), so deleting the
    source row removes its chunks atomically; no hand-ordered child delete.

    Past answers are unaffected: their attribution is stored as a JSONB snapshot
    on the message (ADR 0003/0005), not a live join, so a replayed Locator still
    shows what the student saw even after the file is gone.

    Returns the deleted source dict (so the caller can remove the PDF on disk via
    its filename + chat_id), or None if there was no such source.
    """
    src = get_source(source_id)
    if src is None:
        return None
    with connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM sources WHERE id = %s;", (source_id,))
    return src


def cite_source(source_id, style="APA"):
    """
    Render a formatted Citation for a CONFIRMED source in the chosen style
    (APA / Harvard / IEEE) — the "cite this source" button backend. Style is
    chosen here, at cite time, and never stored, so one confirmed source can be
    cited in any style.

    Returns a dict {"in_text", "reference"} (both forms a student needs). Raises
    ValueError if the source is unknown or not yet confirmed — in which case the
    UI should run the confirm step first (a Locator is always available
    regardless; only the Citation needs confirm).
    """
    src = get_source(source_id)
    if src is None:
        raise ValueError(f"No source with id {source_id}.")
    if not src["confirmed"]:
        raise ValueError(
            "This source is not confirmed yet — confirm its author, title and "
            "year before citing (Notes are locator-only and never cited)."
        )
    meta = {"author": src["author"], "title": src["title"], "year": src["year"],
            "filename": src["filename"], "work_type": src["work_type"],
            "cite_meta": src["cite_meta"]}
    return format_citation(meta, style=style)
