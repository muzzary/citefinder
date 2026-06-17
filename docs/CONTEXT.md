# CiteFinder

A local-first RAG tool over a student's own PDFs: ask a question in plain English, get an answer grounded only in your uploaded material, attributed to where it came from.

## Language

**Source**:
Any single PDF a student ingests. Exactly one PDF per Source, and every Source is either a Work or Notes.
_Avoid_: Document, material, file, upload.

**Work**:
A Source the student has confirmed as a citable publication, with a real author, title, and year. The only kind of Source that can yield a Citation. Confirmation is optional and can happen any time after ingest.
_Avoid_: Paper, reference, citation.

**Notes**:
A Source with no single citable origin: the student's own compiled or mixed-origin material. Produces Locators, never Citations.
_Avoid_: Compilation, misc, document.

**Citation**:
An optional formatted bibliographic reference (APA / Harvard / IEEE), built in code and offered only for a Work whose metadata the student has confirmed. Never auto-generated from guessed metadata and never invented by the model.
_Avoid_: Reference, attribution.

**Locator**:
The default attribution shown for every answer: the file name, page number, a short summary of what is on that page, and the surrounding context — a tutor-style pointer to where in the student's own material the answer lives. Always available; needs no confirmed metadata.
_Avoid_: Citation, reference, source link.

**Covered**:
A question is _covered_ when at least one retrieved chunk clears the relevance floor. If nothing is covered, CiteFinder refuses ("This is not covered in your material.") without ever calling the model.
_Avoid_: Relevant, found, matched, hit.

**Answer**:
A grounded, tutor-style explanation built only from retrieved chunks: it explains what the student's own material says — and names what it does not cover — never drawing on the model's outside knowledge. Every claim traces to a Locator.
_Avoid_: Response, summary, completion, output.
