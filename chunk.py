def chunk_pages(pages, source_id, user_id="user_1", chunk_size=1200, overlap=300):
    """
    Splits page text into overlapping chunks.
    Each chunk keeps its page_number, source_id, and user_id
    so we can cite it later and scale to multi-user later.

    chunk_size / overlap are in CHARACTERS (simple to start).
    Overlap means consecutive chunks share some text, so a sentence
    split across a boundary isn't lost.
    """
    chunks = []

    for page in pages:
        if page["is_scanned"]:
            continue  # v1: skip scanned/empty pages

        text = page["page_text"]
        start = 0

        while start < len(text):
            end = start + chunk_size
            chunk_text = text[start:end].strip()

            if chunk_text:  # skip empty fragments
                # skip table-of-contents / dotted-line junk
                dot_ratio = chunk_text.count(".") / max(len(chunk_text), 1)
                if dot_ratio <= 0.25:     # >25% dots = a TOC/index line
                    chunks.append({
                        "source_id": source_id,
                        "user_id": user_id,
                        "page_number": page["page_number"],
                        "chunk_text": chunk_text,
                    })

            # The slice already covered the rest of the page: stop here so we
            # don't emit a near-duplicate tail chunk (the leftover < overlap).
            if end >= len(text):
                break

            # move forward, but step back by `overlap` so chunks overlap
            start = end - overlap

    return chunks


# --- quick test ---
if __name__ == "__main__":
    from ingest import extract_pdf_pages

    pages = extract_pdf_pages("data/fyp_final.pdf")
    chunks = chunk_pages(pages, source_id=1)

    print(f"Total chunks created: {len(chunks)}")
    print(f"\n--- First chunk ---")
    first = chunks[0]
    print(f"source_id: {first['source_id']}, user_id: {first['user_id']}, page: {first['page_number']}")
    print(f"text ({len(first['chunk_text'])} chars):")
    print(first["chunk_text"][:300])

    # show that page numbers are preserved across chunks
    pages_seen = sorted(set(c["page_number"] for c in chunks))
    print(f"\nPages represented in chunks: {pages_seen}")