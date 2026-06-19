import os
import glob

import psycopg

from metadata import extract_metadata, extract_doi
from ingest import extract_pdf_pages
from chunk import chunk_pages
from embed_store import store_source_and_chunks


def ingest_pdf(pdf_path, user_id="user_1", chat_id=None, kind="work",
               title=None, author=None, year=None, confirmed=False, pages=None):
    """
    Non-blocking core: run one PDF through extract -> guard -> chunk -> store ->
    embed, with NO prompts. Shared by the interactive single-file path and the
    folder path. Returns a result dict:

        {"status": ..., "source_id": int|None, "chunks": int, "title": str,
         "filename": str, "reason": str|None}

    status is one of:
        "stored"             - chunks embedded and stored
        "skipped_scanned"    - no extractable text (scanned/empty PDF)
        "skipped_no_chunks"  - text found but nothing survived filtering (TOC)
        "error"              - the PDF couldn't be read at all

    pages: already-extracted pages (from extract_pdf_pages). The interactive path
    extracts once to peek for scanned PDFs, so it passes them in to avoid parsing
    the same (possibly large) PDF a second time here.

    Metadata is never confirmed here (confirmed defaults to False): an ingested
    Work yields a Locator now and becomes citable only once the student confirms
    its details. See ADR 0003 (ingest-now, confirm-later).
    """
    base = os.path.basename(pdf_path)

    # 0. Extract text first so we bail BEFORE creating a source row for a PDF we
    #    can't retrieve from. Reuse the caller's pages if it already extracted them.
    try:
        if pages is None:
            pages = extract_pdf_pages(pdf_path)
    except Exception as e:
        return {"status": "error", "source_id": None, "chunks": 0,
                "title": title or base, "filename": base, "reason": str(e)}

    if not any(not p["is_scanned"] for p in pages):
        return {"status": "skipped_scanned", "source_id": None, "chunks": 0,
                "title": title or base, "filename": base,
                "reason": "no extractable text (scanned/empty PDF)"}

    # 1. Chunk now so we confirm there's something to store before committing a
    #    source row.
    chunks = chunk_pages(pages, source_id=None, user_id=user_id)
    if not chunks:
        return {"status": "skipped_no_chunks", "source_id": None, "chunks": 0,
                "title": title or base, "filename": base,
                "reason": "no usable chunks survived filtering (mostly TOC)"}

    # 2. Title falls back to the filename if we have nothing better.
    if not title:
        title = base

    if kind == "notes":
        author = year = None
        confirmed = False

    # 2b. Detect a DOI on the first pages (cheap, offline) so the confirm UI can
    #     offer a one-click CrossRef auto-fill. Most journal PDFs print it up front.
    doi = ""
    try:
        head = " ".join(p["page_text"] for p in pages[:2] if not p["is_scanned"])
        doi = extract_doi(head)
    except Exception:
        pass

    # 3. Embed + store the source and its chunks atomically (one transaction), so
    #    the source never appears half-ingested and a delete (of the file or its
    #    chat) racing this ingest can't orphan the chunk insert. A delete that wins
    #    the race makes the source/chat FK fail here — we report it cleanly instead
    #    of crashing the request.
    try:
        source_id = store_source_and_chunks(
            title=title, filename=base, user_id=user_id,
            author=author or None, year=year or None,
            kind=kind, confirmed=confirmed, chat_id=chat_id,
            doi=doi or None, chunks=chunks,
        )
    except psycopg.errors.ForeignKeyViolation:
        return {"status": "error", "source_id": None, "chunks": 0,
                "title": title, "filename": base,
                "reason": "the chat was removed while this file was being added"}

    return {"status": "stored", "source_id": source_id, "chunks": len(chunks),
            "title": title, "filename": base, "reason": None}


def add_source(pdf_path, user_id="user_1", chat_id=None):
    """
    Interactive single-file ingest: ask whether this is a citable Work or your
    own Notes, gather/confirm metadata, then ingest. Confirming a Work's
    metadata here unlocks a formatted Citation. See ADR 0003.

    For the batch, no-prompt path used by the folder UI, see add_source_folder.
    """
    base = os.path.basename(pdf_path)

    # Peek at the text first so we don't interrogate the user about a PDF we
    # can't use anyway.
    pages = extract_pdf_pages(pdf_path)
    if not any(not p["is_scanned"] for p in pages):
        print("No extractable text found - this looks like a scanned PDF. "
              "v1 supports digital-text PDFs only. Nothing was stored.")
        return None

    # 1. What kind of Source is this?
    kind_in = input("Is this a citable (w)ork or your own (n)otes? [w/n]: ").strip().lower()
    kind = "notes" if kind_in.startswith("n") else "work"

    # 2. Gather metadata. Works get a confirm step (which unlocks citations);
    #    Notes skip it and are stored locator-only.
    if kind == "work":
        guess = extract_metadata(pdf_path)
        print("\n--- Metadata found in PDF (a GUESS - Enter to keep, or type to correct) ---")
        title = input(f"Title [{guess['title']}]: ").strip() or guess["title"]
        author = input(f"Author [{guess['author']}]: ").strip() or guess["author"]
        year = input(f"Year [{guess['year']}]: ").strip() or guess["year"]
        if not title:
            title = base
            print(f"  (No title given - using filename '{title}' as fallback.)")
        # Confirmation must be EXPLICIT (ADR 0003): a citation is built only from
        # metadata the student affirms is correct, never auto-locked just because
        # the (often unreliable) PDF guess happened to fill author + year. A title
        # that is just the filename is not a real citation title.
        confirmed = False
        citable = bool(author and year and title and title != base)
        if citable:
            ans = input("Lock these details as a citable source - you confirm they are correct? [y/N]: ")
            confirmed = ans.strip().lower().startswith("y")
        if not confirmed:
            print("  Stored locator-only. You can confirm its details later to cite it; "
                  "no citation will be shown until you do.")
    else:
        title = input(f"Name for these notes [{base}]: ").strip() or base
        author = year = None
        confirmed = False
        print("  Stored as notes - answers will point to it by name + page, "
              "never as a citation.")

    result = ingest_pdf(
        pdf_path, user_id=user_id, chat_id=chat_id, kind=kind,
        title=title, author=author, year=year, confirmed=confirmed,
        pages=pages,   # reuse the peek extraction; don't parse the PDF twice
    )

    if result["status"] != "stored":
        print(f"Nothing was stored: {result['reason']}.")
        return None

    print(f"\nDone. Stored {kind} #{result['source_id']} ('{result['title']}') "
          f"with {result['chunks']} chunks.")
    return result["source_id"]


def add_source_folder(folder, user_id="user_1", chat_id=None, recursive=False):
    """
    Batch, non-blocking folder ingest for the "add a folder" UI: every PDF in
    `folder` is ingested into this chat with NO prompts (ADR 0003). Each file is
    stored as a Work with confirmed=False - it gets a Locator now and can be
    confirmed/cited later via the "cite this source" button. Title comes from
    the PDF's metadata guess, falling back to the filename.

    A file that can't be used (scanned, no text, unreadable) is skipped with a
    reason rather than aborting the batch. Returns a list of per-file result
    dicts (the same shape ingest_pdf returns) so the UI can show what happened.
    """
    pattern = "**/*.pdf" if recursive else "*.pdf"
    paths = sorted(glob.glob(os.path.join(folder, pattern), recursive=recursive))

    if not paths:
        print(f"No PDFs found in {folder!r}.")
        return []

    print(f"Found {len(paths)} PDF(s) in {folder!r}. Ingesting into "
          f"{'chat ' + str(chat_id) if chat_id else 'user ' + user_id}...\n")

    results = []
    for path in paths:
        base = os.path.basename(path)
        print(f"-> {base}")
        # Title from the metadata guess (never confirmed - just a better label
        # than the raw filename). Falls back to filename inside ingest_pdf.
        guess_title = ""
        try:
            guess_title = extract_metadata(path).get("title", "")
        except Exception:
            pass  # a bad metadata block shouldn't stop ingest; title falls back

        result = ingest_pdf(
            path, user_id=user_id, chat_id=chat_id, kind="work",
            title=guess_title or None, confirmed=False,
        )
        results.append(result)

        if result["status"] == "stored":
            print(f"   stored source #{result['source_id']} "
                  f"('{result['title']}') - {result['chunks']} chunks")
        else:
            print(f"   skipped: {result['reason']}")

    stored = [r for r in results if r["status"] == "stored"]
    skipped = [r for r in results if r["status"] != "stored"]
    total_chunks = sum(r["chunks"] for r in stored)
    print(f"\nDone. {len(stored)} stored ({total_chunks} chunks), "
          f"{len(skipped)} skipped, {len(paths)} total.")
    return results


if __name__ == "__main__":
    choice = input("Add a (f)ile or a f(o)lder? [f/o]: ").strip().lower()
    if choice.startswith("o"):
        folder = input("Path to folder: ").strip()
        add_source_folder(folder)
    else:
        path = input("Path to PDF: ").strip()
        add_source(path)
