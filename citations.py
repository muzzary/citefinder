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


def format_citation(meta, page, style="APA"):
    """
    Build a formatted citation from REAL metadata fields (never from the LLM).
    meta = {"author":..., "title":..., "year":..., "filename":...}
    Missing fields are handled honestly, not invented.
    """
    author = (meta.get("author") or "").strip()
    title = (meta.get("title") or "").strip()
    year = (meta.get("year") or "").strip()
    filename = (meta.get("filename") or "").strip()

    # Honest fallback: if we have no real title, use the filename and flag it.
    if not title:
        title = filename or "Untitled source"

    # Track what's missing so we can warn the student.
    missing = [f for f in ("author", "year") if not meta.get(f)]

    style = style.upper()

    if style == "APA":
        # Author, A. (Year). Title (p. X).
        a = author if author else "[Author unknown]"
        y = year if year else "n.d."   # n.d. = "no date", standard APA
        citation = f"{a} ({y}). {title} (p. {page})."

    elif style == "HARVARD":
        # Author, Year. Title, p. X.
        a = author if author else "[Author unknown]"
        y = year if year else "n.d."
        citation = f"{a}, {y}. {title}, p. {page}."

    elif style == "IEEE":
        # Author, Title, Year, p. X.
        a = author if author else "[Author unknown]"
        y = year if year else "n.d."
        citation = f"{a}, {title}, {y}, p. {page}."

    else:
        citation = f"{title}, p. {page}."

    # Attach an honest note when data is incomplete.
    note = ""
    if missing:
        note = f"  [Incomplete citation - missing {', '.join(missing)}; please verify.]"

    return citation + note


# --- test ---
if __name__ == "__main__":
    full = {"author": "Khan, H. M. H.", "title": "Encouraged Digital Academic Portal",
            "year": "2025", "filename": "fyp_final.pdf"}
    partial = {"author": "", "title": "Some Paper", "year": "", "filename": "paper.pdf"}

    for style in ("APA", "Harvard", "IEEE"):
        print(f"\n{style}:")
        print("  full:   ", format_citation(full, page=16, style=style))
        print("  partial:", format_citation(partial, page=5, style=style))