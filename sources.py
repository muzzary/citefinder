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
from db import connect
from citations import format_citation


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
                   s.author, s.year, COUNT(c.id) AS n_chunks
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
             "confirmed": r[4], "author": r[5], "year": r[6], "n_chunks": r[7]}
            for r in rows]


def get_source(source_id):
    """
    Return a source row as a dict, or None. The UI checks `confirmed` to decide
    whether the "cite this source" button needs the confirm step first.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, title, author, year, filename, kind, confirmed, chat_id "
            "FROM sources WHERE id = %s;",
            (source_id,),
        )
        r = cur.fetchone()
    if r is None:
        return None
    return {"id": r[0], "title": r[1], "author": r[2], "year": r[3],
            "filename": r[4], "kind": r[5], "confirmed": r[6], "chat_id": r[7]}


def confirm_source(source_id, author, title=None, year=None):
    """
    Confirm a Work's real metadata and LOCK it as confirmed (ADR 0003). After
    this a Citation can be rendered for the source and the system never asks for
    confirmation again — the `confirmed` flag is persisted.

    A citation needs author + year + a REAL title. The title falls back to the
    source's stored title if not given, but a title that is merely the filename
    (the locate-by-default fallback used at ingest) is NOT a bibliographic title:
    it does not unlock a citation, so the source stays **unconfirmed**
    (locator-only) until a proper title is supplied. Likewise a missing author or
    year keeps it unconfirmed. The provided metadata is still saved either way.

    Notes are locator-only by design and cannot be confirmed.

    Returns the updated source dict. Raises ValueError for an unknown source or
    a Notes source.
    """
    src = get_source(source_id)
    if src is None:
        raise ValueError(f"No source with id {source_id}.")
    if src["kind"] == "notes":
        raise ValueError("Notes are locator-only and cannot be confirmed (ADR 0003).")

    author = (author or "").strip()
    title = (title or "").strip() or (src["title"] or "")
    year = str(year).strip() if year is not None else ""

    # A citation needs author + year + a real title. A title equal to the stored
    # filename is the locate-by-default placeholder, not a citation title, so it
    # does not lock the source (ADR 0003: never cite from a non-real-metadata guess).
    has_real_title = bool(title) and title != (src["filename"] or "")
    confirmed = bool(author and year and has_real_title)

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE sources SET author=%s, title=%s, year=%s, "
            "confirmed=%s, metadata_complete=%s WHERE id=%s;",
            (author or None, title, year or None, confirmed, confirmed, source_id),
        )

    return get_source(source_id)


def cite_source(source_id, page, style="APA"):
    """
    Render a formatted Citation for a CONFIRMED source at a given page, in the
    chosen style (APA / Harvard / IEEE) — the "cite this source" button backend.
    Style is chosen here, at cite time, and never stored, so one confirmed
    source can be cited in any style.

    Returns the citation string. Raises ValueError if the source is unknown or
    not yet confirmed — in which case the UI should run the confirm step first
    (a Locator is always available regardless; only the Citation needs confirm).
    """
    src = get_source(source_id)
    if src is None:
        raise ValueError(f"No source with id {source_id}.")
    if not src["confirmed"]:
        raise ValueError(
            "This source is not confirmed yet — confirm its author, title and "
            "year before citing (Notes are locator-only and never cited)."
        )
    meta = {"author": src["author"], "title": src["title"],
            "year": src["year"], "filename": src["filename"]}
    return format_citation(meta, page=page, style=style)
