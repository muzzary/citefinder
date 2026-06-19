import re

import pypdf

# A DOI: "10." + a registrant code + "/" + an opaque suffix. We stop the suffix at
# whitespace or characters that can't be in a DOI but often trail it in running
# text (quotes, brackets). Case-insensitive; the prefix is always 10.xxxx.
_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>]+", re.I)


def extract_doi(text):
    """Find the first DOI in some text (e.g. a PDF's first page). Returns '' if
    none. Trailing sentence punctuation that isn't part of the DOI is trimmed.
    A DOI is the single most reliable key for looking up a paper's real metadata
    (see crossref.py), and most journal PDFs print it on page 1."""
    m = _DOI_RE.search(text or "")
    if not m:
        return ""
    return m.group(0).rstrip(").,;:]}>")


def extract_metadata(pdf_path):
    """
    Attempt to pull author/title/year from a PDF's embedded metadata.
    PDFs often have empty or junk metadata, so treat everything as a GUESS
    the student will confirm. Never trust these blindly.
    """
    reader = pypdf.PdfReader(pdf_path)
    info = reader.metadata or {}

    title = (info.get("/Title") or "").strip()
    author = (info.get("/Author") or "").strip()

    # Year: PDF dates look like "D:20250115..." - try to find a 4-digit year.
    year = ""
    raw_date = (info.get("/CreationDate") or "")
    import re
    match = re.search(r"(19|20)\d{2}", str(raw_date))
    if match:
        year = match.group(0)

    return {
        "title": title,
        "author": author,
        "year": year,
    }


# --- test ---
if __name__ == "__main__":
    guess = extract_metadata("data/fyp_final.pdf")
    print("Extracted (guess):")
    for k, v in guess.items():
        print(f"  {k}: {v if v else '(empty - will ask user)'}")