"""
CrossRef metadata lookup — the "find" in CiteFinder's citations.

Given a DOI (auto-detected from a PDF at ingest, or pasted by the student), this
fetches the work's AUTHORITATIVE bibliographic metadata from CrossRef's free,
key-less API and maps it onto CiteFinder's citation schema (work_type + the
fields confirm_source/citations.py expect). The student still confirms it — this
just turns "type everything from an unreliable guess" into "verify what we found".

This is the one place CiteFinder talks to the network for citations, and only on
an explicit user action (the "Auto-fill from DOI" button). Only the DOI string
leaves the machine — never the document or its text — consistent with the
local-by-default rule (ADR 0002). stdlib only (urllib), so no new dependency.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

_API = "https://api.crossref.org/works/"
# CrossRef etiquette: identify the caller in the User-Agent.
_UA = "CiteFinder/1.0 (local desktop citation tool)"

# CrossRef `type` -> our work_type. Anything not listed defaults to "article"
# (the overwhelming majority of DOIs, and the case students hit most).
_TYPE = {
    "journal-article": "article", "proceedings-article": "article",
    "posted-content": "article", "dissertation": "article",
    "book": "book", "monograph": "book", "reference-book": "book",
    "edited-book": "book", "book-chapter": "book",
}


def clean_doi(doi):
    """Normalise what a user might paste: a bare DOI, a doi.org URL, or a 'doi:'
    prefix all reduce to the bare '10.xxxx/...' form."""
    d = (doi or "").strip()
    for p in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/",
              "http://dx.doi.org/", "doi:", "DOI:"):
        if d.lower().startswith(p.lower()):
            d = d[len(p):]
    return d.strip().rstrip(").,;:]}>")


def _fmt_authors(items):
    """CrossRef gives [{given, family}, ...]; render a reference-style string
    'Family, G. I.' joined for multiple authors. The last is joined with ', & '
    so the in-text parser (citations._split_authors) sees individual surnames."""
    names = []
    for a in items or []:
        fam = (a.get("family") or "").strip()
        giv = (a.get("given") or "").strip()
        if fam and giv:
            initials = " ".join(f"{part[0]}." for part in giv.replace(".", " ").split() if part)
            names.append(f"{fam}, {initials}".strip())
        elif fam:
            names.append(fam)
        elif a.get("name"):
            names.append(a["name"].strip())
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]}, & {names[1]}"
    return ", ".join(names[:-1]) + f", & {names[-1]}"


def _year(message):
    for key in ("published", "published-print", "published-online", "issued"):
        parts = (message.get(key) or {}).get("date-parts") or []
        if parts and parts[0] and parts[0][0]:
            return str(parts[0][0])
    return ""


def _first(seq):
    return (seq or [""])[0] if seq else ""


def _normalize(message, doi):
    """Map a CrossRef `message` onto our confirm-form schema:
       {work_type, author, title, year, doi, meta:{...type-specific...}}.
    Blank fields are dropped from meta so the form only pre-fills what we know."""
    wt = _TYPE.get(message.get("type", ""), "article")
    out = {
        "work_type": wt,
        "author": _fmt_authors(message.get("author")),
        "title": _first(message.get("title")).strip(),
        "year": _year(message),
        "doi": doi,
        "meta": {},
    }
    if wt == "article":
        meta = {
            "journal": _first(message.get("container-title")).strip(),
            "volume": (message.get("volume") or "").strip(),
            "issue": (message.get("issue") or "").strip(),
            "pages": (message.get("page") or "").strip(),
            "doi": doi,
        }
    else:  # book
        meta = {
            "publisher": (message.get("publisher") or "").strip(),
            "place": _first(message.get("publisher-location")).strip()
                     if message.get("publisher-location") else "",
        }
    out["meta"] = {k: v for k, v in meta.items() if v}
    return out


def lookup_doi(doi, timeout=12):
    """Resolve a DOI to normalized citation metadata via CrossRef.

    Raises ValueError for a missing/unknown DOI (a 'not found' the UI shows as a
    gentle hint) and RuntimeError for a network/transport failure (offline, etc.).
    """
    doi = clean_doi(doi)
    if not doi:
        raise ValueError("Enter a DOI to look up (e.g. 10.1016/j.x.2019.01.002).")
    url = _API + urllib.parse.quote(doi, safe="")
    req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ValueError(f"No record found for DOI “{doi}”. Check it and try again.")
        raise RuntimeError(f"CrossRef lookup failed (HTTP {e.code}). Try again later.")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"Couldn't reach CrossRef ({e}). Check your connection.")
    return _normalize(payload.get("message", {}), doi)


# --- test ---
if __name__ == "__main__":
    import sys
    sample = sys.argv[1] if len(sys.argv) > 1 else "10.1016/j.ijnurstu.2019.103412"
    from pprint import pprint
    pprint(lookup_doi(sample))
