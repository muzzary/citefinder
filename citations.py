def format_locator(filename, page, excerpt, title=None):
    """
    Build a Locator — the DEFAULT, always-honest attribution for every answer.
    It points to WHERE in the student's own material something came from, using
    only facts we can trust (file name + page) plus a tutor-style summary and
    the surrounding context drawn straight from the retrieved chunk. It never
    claims to be a bibliographic citation. See ADR 0003.

    Returns a dict so callers (CLI today, web UI later) can render it however
    they like; str() gives a sensible default rendering.
    """
    label = (title or filename or "your material").strip()
    text = " ".join((excerpt or "").split())  # collapse whitespace

    # Summary = first sentence of the passage (cheap, and guaranteed grounded).
    summary = text.split(". ")[0][:160].strip()
    if summary and not summary.endswith((".", "!", "?")):
        summary += "..."

    context = text[:320].strip()
    if len(text) > 320:
        context += "..."

    return {
        "label": f"{label} - p. {page}",
        "summary": summary,
        "context": context,
    }


def render_locator(loc):
    """Render a Locator dict as a multi-line string for the CLI."""
    lines = [f"[from] {loc['label']}"]
    if loc["summary"]:
        lines.append(f"   {loc['summary']}")
    if loc["context"]:
        lines.append(f'   "{loc["context"]}"')
    return "\n".join(lines)


from datetime import date, datetime

WORK_TYPES = ("book", "article", "website")
STYLES = ("APA", "Harvard", "IEEE")


# --- author-name helpers -----------------------------------------------------
# The student enters `author` in reference-list form ("Smith, J." or, for several
# authors, "Taylor, R., & Jones, M."). Reference lists use that form verbatim;
# in-text needs surnames only, and IEEE needs initials-first. These helpers
# derive those views WITHOUT inventing anything — they only reshape what's typed.

def _split_authors(author):
    """Split a multi-author string into individual authors. Authors are separated
    by '&', ';', or ' and ' (the comma inside 'Smith, J.' is NOT a separator)."""
    s = (author or "").strip()
    if not s:
        return []
    for sep in (" & ", " and ", "&", ";"):
        s = s.replace(sep, "|")
    return [p.strip().strip(",").strip() for p in s.split("|") if p.strip()]


def _surname(one):
    """Surname of a single author. 'Smith, J.' -> 'Smith'; a group/org with no
    comma ('World Health Organization') -> itself."""
    return one.split(",")[0].strip()


def _intext_authors(author, conj):
    """The author part of an in-text citation: one surname, two joined by `conj`
    ('&' APA / 'and' Harvard), or 'First et al.' for three or more."""
    names = [_surname(a) for a in _split_authors(author)]
    if not names:
        return "[Author unknown]"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} {conj} {names[1]}"
    return f"{names[0]} et al."


def _ieee_one(one):
    """IEEE reshapes 'Smith, J.' to initials-first 'J. Smith'. A no-comma group
    name is left as-is."""
    if "," in one:
        surname, rest = one.split(",", 1)
        return f"{rest.strip()} {surname.strip()}".strip()
    return one.strip()


def _ieee_authors(author):
    """IEEE author list: 'A', 'A and B', or 'A, B, and C' (initials-first)."""
    names = [_ieee_one(p) for p in _split_authors(author)]
    if not names:
        return "[Author unknown]"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _accessed(meta):
    """The access date for online sources. Uses the stored YYYY-MM-DD if present,
    else today (the date the citation is generated). Returns a date object."""
    raw = (meta.get("accessed") or "").strip()
    if raw:
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            pass
    return date.today()


def _harvard_date(d):
    return f"{d.day} {d.strftime('%B')} {d.year}"          # 19 June 2026


def _ieee_date(d):
    return f"{d.strftime('%b')}. {d.day}, {d.year}"        # Jun. 19, 2026


def _join(parts, sep=" "):
    """Join only the non-empty parts — so an absent optional field (edition,
    issue, DOI) leaves no dangling separator."""
    return sep.join(p for p in parts if p)


def format_citation(meta, style="APA"):
    """
    Build a formatted citation from REAL, student-confirmed metadata (never the
    LLM). Returns BOTH forms the student needs:

        {"in_text": "(Smith, 2020)", "reference": "Smith, J. (2020). ..."}

    `meta` carries the shared fields plus the type that picks the shape:
        work_type : "book" | "article" | "website"
        author, title, year, filename
        cite_meta : {publisher, place, edition,            # book
                     journal, volume, issue, pages, doi,   # article
                     site_name, url, pub_date, accessed}   # website

    Only `author`, `title`, `year` are required (enforced upstream in
    confirm_source); every cite_meta field is optional and simply omitted when
    blank — matching templates like "Edition (if not first)". Nothing is invented.
    """
    style = (style or "APA").upper()
    wt = (meta.get("work_type") or "book").lower()
    if wt not in WORK_TYPES:
        wt = "book"

    author = (meta.get("author") or "").strip()
    title = ((meta.get("title") or "").strip()
             or (meta.get("filename") or "").strip() or "Untitled source")
    year = (meta.get("year") or "").strip() or "n.d."   # n.d. = no date (all styles)
    cm = meta.get("cite_meta") or {}

    def g(k):
        return (cm.get(k) or "").strip()

    # --- in-text -------------------------------------------------------------
    if style == "IEEE":
        in_text = "[1]"            # IEEE in-text is the bracketed reference number
    else:
        conj = "&" if style == "APA" else "and"
        in_text = f"({_intext_authors(author, conj)}, {year})"

    # --- reference list ------------------------------------------------------
    ref = _reference(style, wt, author, title, year, g)

    # Honest note when a required field is missing (citation still rendered).
    missing = [f for f in ("author", "year") if not (meta.get(f) or "").strip()]
    if missing:
        ref += f"  [Incomplete - missing {', '.join(missing)}; please verify.]"

    return {"in_text": in_text, "reference": ref}


def _reference(style, wt, author, title, year, g):
    """The reference-list entry for one (style, work_type). `g(field)` reads a
    cite_meta field (already trimmed)."""
    a = author or "[Author unknown]"

    if style == "APA":
        if wt == "book":
            # Author, A. A. (Year). Title of book. Publisher.
            return _join([f"{_ap(a)} ({year}).", f"{title}.", _dot(g('publisher'))])
        if wt == "article":
            # Author (Year). Title. Journal, Vol(Issue), pages. DOI
            vol = _vol_issue(g('volume'), g('issue'))
            loc = _join([g('journal') + ',' if g('journal') else '',
                         vol, g('pages')], " ").strip()
            return _join([f"{_ap(a)} ({year}).", f"{title}.", _dot(loc), g('doi')])
        # website: Author (Year, Month Day). Title. Site. URL
        yp = f"({year}, {g('pub_date')})" if g('pub_date') else f"({year})"
        return _join([f"{_ap(a)} {yp}.", f"{title}.", _dot(g('site_name')), g('url')])

    if style == "HARVARD":
        if wt == "book":
            # Author, Year. Title. Edition. Place: Publisher.
            place_pub = _place_pub(g('place'), g('publisher'))
            return _join([f"{a}, {year}.", f"{title}.", _dot(g('edition')), place_pub])
        if wt == "article":
            # Author, Year. Title. Journal, Vol(Issue), pp. pages. Available at: DOI [Accessed date].
            vol = _vol_issue(g('volume'), g('issue'))
            loc = _join([g('journal') + ',' if g('journal') else '', vol,
                         f"pp. {g('pages')}." if g('pages') else ''], " ").strip()
            avail = (f"Available at: {g('doi')} [Accessed {_harvard_date(_accessed({'accessed': g('accessed')}))}]."
                     if g('doi') else '')
            return _join([f"{a}, {year}.", f"{title}.", loc, avail])
        # website: Author, Year. Title. Site. Available at: URL [Accessed date].
        avail = (f"Available at: {g('url')} [Accessed {_harvard_date(_accessed({'accessed': g('accessed')}))}]."
                 if g('url') else '')
        return _join([f"{a}, {year}.", f"{title}.", _dot(g('site_name')), avail])

    # IEEE — entries carry a reference number; we emit [1] for a single citation.
    ia = _ieee_authors(author)
    if wt == "book":
        # [1] A. Author, Title[, Edition]. Place: Publisher, Year.
        place_pub = _place_pub(g('place'), g('publisher'), trail=f", {year}.")
        head = f"{title}, {g('edition')}." if g('edition') else f"{title}."
        return _join([f"[1] {ia},", head, place_pub or f"{year}."])
    if wt == "article":
        # [1] A. Author, "Title," Journal, vol. x, no. x, pp. xxx, Year, doi: xxx.
        bits = _join([f'{g("journal")},' if g('journal') else '',
                      f"vol. {g('volume')}," if g('volume') else '',
                      f"no. {g('issue')}," if g('issue') else '',
                      f"pp. {g('pages')}," if g('pages') else '',
                      f"{year},", f"doi: {g('doi')}." if g('doi') else ''], " ").strip()
        return f'[1] {ia}, "{title}," {bits}'.rstrip()
    # website: [1] A. Author. "Title." Site. URL (accessed Mon. Day, Year).
    acc = f"(accessed {_ieee_date(_accessed({'accessed': g('accessed')}))})." if g('url') else ''
    return _join([f"[1] {ia}.", f'"{title}."', _dot(g('site_name')), g('url'), acc])


def _dot(s):
    """End a non-empty fragment with a period."""
    return f"{s}." if s else ""


def _ap(author):
    """APA puts a period after the author element. A personal name typed as
    'Smith, J.' already ends in one (the initial); a group like 'World Health
    Organization' does not, so add it. Avoids a doubled '..'."""
    a = author or "[Author unknown]"
    return a if a.endswith((".", "?", "!")) else a + "."


def _vol_issue(volume, issue):
    """'14(2)' / '14' / '' from volume + issue."""
    if volume and issue:
        return f"{volume}({issue}),"
    if volume:
        return f"{volume},"
    return ""


def _place_pub(place, publisher, trail="."):
    """'Place: Publisher.' / 'Publisher.' / 'Place.' / '' (with an optional trail
    like ', 2020.' for IEEE)."""
    if place and publisher:
        return f"{place}: {publisher}{trail}"
    if publisher:
        return f"{publisher}{trail}"
    if place:
        return f"{place}{trail}"
    return ""


# --- test ---
if __name__ == "__main__":
    samples = {
        "book": {"author": "Smith, J.", "title": "Digital communication principles",
                 "year": "2020",
                 "cite_meta": {"publisher": "Academic Press", "place": "New York"}},
        "article": {"author": "Taylor, R., & Jones, M.", "title": "Networks today",
                    "year": "2019",
                    "cite_meta": {"journal": "Journal of Technology", "volume": "14",
                                  "issue": "2", "pages": "112-125", "doi": "doi.org"}},
        "website": {"author": "World Health Organization", "title": "Global health risks",
                    "year": "2023",
                    "cite_meta": {"site_name": "WHO", "url": "who.int",
                                  "pub_date": "March 15", "accessed": "2026-06-19"}},
    }
    for wt, base in samples.items():
        print(f"\n=== {wt.upper()} ===")
        for style in STYLES:
            c = format_citation({**base, "work_type": wt}, style=style)
            print(f"  {style:8} in-text : {c['in_text']}")
            print(f"  {' ':8} ref     : {c['reference']}")