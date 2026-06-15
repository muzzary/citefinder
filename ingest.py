import pypdf

def extract_pdf_pages(pdf_path):
    """
    Opens a PDF and extracts text page by page.
    Returns a list of {page_number, page_text, is_scanned}.
    Page numbers are captured here so we can cite them later.
    """
    reader = pypdf.PdfReader(pdf_path)
    pages = []

    for index, page in enumerate(reader.pages):
        page_number = index + 1  # human-friendly: page 1, not page 0
        text = page.extract_text() or ""
        text = text.strip()

        # A digital-text page returns real text; a scanned page returns nothing.
        is_scanned = len(text) == 0

        pages.append({
            "page_number": page_number,
            "page_text": text,
            "is_scanned": is_scanned,
        })

    return pages


# --- quick test (run this file directly) ---
if __name__ == "__main__":
    path = "fyp_final.pdf"   # put a PDF named sample.pdf in this folder, or change this path
    pages = extract_pdf_pages(path)

    total = len(pages)
    scanned = [p["page_number"] for p in pages if p["is_scanned"]]
    good = total - len(scanned)

    print(f"Total pages: {total}")
    print(f"Pages with extractable text: {good}")
    if scanned:
        print(f"Scanned/empty pages (skipped in v1): {scanned}")

    # show a preview of the first text page so you can eyeball quality
    for p in pages:
        if not p["is_scanned"]:
            print(f"\n--- Page {p['page_number']} preview ---")
            print(p["page_text"][:400])
            break