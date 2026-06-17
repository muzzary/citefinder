# Locate by default; cite only on confirmation

> **Supersedes** [ADR-0001](0001-works-cited-notes-located.md).

Every answer attributes its material with a **Locator** by default: the file
name, page number, a short summary of what is on that page, and the surrounding
context — a tutor-style pointer to where in the student's own material the
answer lives. A formatted **Citation** (APA / Harvard / IEEE) is an **optional
extra**, offered only for a **Work** whose bibliographic metadata the student
has explicitly confirmed. CiteFinder never auto-generates a formatted citation
from guessed or extracted metadata.

## Why

The original design (ADR 0001) made a formatted Citation the default output for
any Work. But a Work's `kind` cannot be auto-detected and PDF metadata is
routinely junk, so auto-formatting produced confidently-wrong references —
worse than none, and fatal in a thesis. The reliable facts are the file name
and page number; everything else is a guess until the student confirms it. So
we attribute with an always-honest Locator by default and gate the formatted
Citation behind explicit confirmation, the only trustworthy basis for one. This
also reframes the product from "citation generator" toward "a tutor that finds
the passage, explains it, and points you to where it lives."

## Consequence

- Metadata confirmation becomes **optional and lazy**, never an ingest gate —
  which removes the folder-ingest friction (drop a folder, query immediately,
  confirm metadata later only for the sources you want a formatted citation
  from).
- The answer path emits a Locator for **every** attribution and a Citation
  **only** for a confirmed Work; it no longer runs every result through one
  citation formatter.
- The headline framing in `README.md` and `CLAUDE.md` ("cite it correctly")
  must be reworded toward "find it, understand it, and — once you confirm the
  details — cite it."
