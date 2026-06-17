# Works are cited, Notes are located

> **Status:** Superseded by [ADR-0003](0003-locate-by-default-cite-on-confirmation.md). The Work/Notes/Citation/Locator vocabulary still holds, but citations are no longer the default output for Works — see 0003.

Every ingested Source is one PDF, declared at ingest as either a **Work**
(a citable publication — research paper, book, article) or **Notes** (the
student's own compiled or mixed-origin material).

- A **Work** yields a **Citation**: a formatted APA / Harvard / IEEE reference
  built in code from the Work's real metadata plus the page number. Never
  invented by the model.
- **Notes** yield only a **Locator**: a name + page-number pointer (e.g.
  `My Notes — p. 5`). A Locator is never formatted as a Citation and is never
  flagged "incomplete" — it is complete by definition.

Answers pool retrieved chunks from both Works and Notes, and label **each
attribution** by kind, so the student can see at a glance which parts of an
answer are citable in a thesis and which are only a pointer back to their own
material.

## Why

This is the project's integrity principle made concrete: never fabricate a
reference the student can't legitimately use. We weighed two alternatives and
rejected both:

- **Works-only answers** (answer purely from Works so every attribution is
  citable): cleaner, but throws away the student's own well-summarized Notes
  and doubles the query surface into two modes.
- **Works-ranked-above-Notes** (pool, but break score ties toward Works):
  a reasonable later refinement, but adds ranking complexity before we have
  shipped one good retrieval pass.

We chose **pool-and-label** to answer from *all* of the student's material
while keeping the citation guarantee honest.

## Consequence

The schema needs a per-Source `kind` (`work | notes`), and the answer path must
branch on `kind` to emit a Citation or a Locator per attribution — rather than
running every result through one citation formatter. A Notes Source must not
trigger the "incomplete metadata" warning that a Work with missing author/year
does.
