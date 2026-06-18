def _pack_lines(text, chunk_size, overlap):
    """
    Pack a page's text into chunk strings at LINE boundaries, not arbitrary
    character offsets. Whole lines accumulate until adding the next would exceed
    chunk_size; then a new chunk starts, carrying the previous chunk's trailing
    lines (up to `overlap` chars) for continuity. A single line longer than
    chunk_size is hard-split as a fallback.

    Why line-aware: the old fixed-window slice cut sections at an arbitrary index,
    stranding a heading like "3.3 Design Description" at a chunk edge so its body
    was split off and answers came back "cut off" (Phase-7b). Breaking at line
    boundaries keeps a heading with the text that follows it.
    """
    chunks, cur, cur_len = [], [], 0

    def flush():
        if cur:
            chunks.append("\n".join(cur))

    for line in text.split("\n"):
        if len(line) > chunk_size:        # oversized single line: flush + hard-split
            flush(); cur.clear(); cur_len = 0
            for i in range(0, len(line), chunk_size):
                chunks.append(line[i:i + chunk_size])
            continue
        add = len(line) + (1 if cur else 0)
        if cur_len + add <= chunk_size:
            cur.append(line); cur_len += add
        else:
            flush()
            # carry trailing lines (up to `overlap` chars) into the next chunk
            keep, klen = [], 0
            for ln in reversed(cur):
                if klen + len(ln) + 1 > overlap:
                    break
                keep.insert(0, ln); klen += len(ln) + 1
            cur[:] = keep + [line]
            cur_len = sum(len(x) + 1 for x in cur)
    flush()
    return chunks


def _is_toc_line(line):
    """A table-of-contents / dotted-index line ('Introduction ........ 5') is
    mostly dot leaders — >25% dots. Filtered out as navigation noise, not content.
    Applied PER LINE (before packing) so a single TOC line can't survive by being
    diluted inside a chunk of real prose, and a real heading isn't dropped because
    it shared a chunk with TOC junk."""
    s = line.strip()
    return bool(s) and s.count(".") / len(s) > 0.25


def chunk_pages(pages, source_id, user_id="user_1", chunk_size=1200, overlap=300):
    """
    Split each page's text into overlapping chunks at line boundaries (see
    _pack_lines), tagging every chunk with page_number, source_id, and user_id
    so we can attribute it later. TOC/dotted-index lines are filtered out per line
    (see _is_toc_line) before packing.

    chunk_size / overlap are in CHARACTERS.
    """
    chunks = []
    for page in pages:
        if page["is_scanned"]:
            continue  # v1: skip scanned/empty pages

        text = "\n".join(ln for ln in page["page_text"].split("\n")
                         if not _is_toc_line(ln))
        for chunk_text in _pack_lines(text, chunk_size, overlap):
            chunk_text = chunk_text.strip()
            if not chunk_text:
                continue
            chunks.append({
                "source_id": source_id,
                "user_id": user_id,
                "page_number": page["page_number"],
                "chunk_text": chunk_text,
            })

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