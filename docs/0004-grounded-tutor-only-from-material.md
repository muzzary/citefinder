# Grounded tutor, never a knowledgeable one

The Answer is a **grounded tutor**: it explains the student's *own* material
clearly and pedagogically, and names what the material does and does not cover,
but it **never draws on the model's world knowledge**. Every claim traces to a
Locator. "Tutor" is a change in *delivery* (clearer phrasing, connecting
passages, naming coverage gaps) — not a change in *sourcing*.

A world-knowledge "explain mode" — teaching the subject from the model's own
training rather than the student's material — is explicitly out of scope. If it
is ever added, it must be a **separate, visually-walled mode** ("from your
material" vs. "general explanation — not from your sources"), never blended
into a grounded Answer.

## Why

The "tutor / teacher" framing actively invites the wrong move: a future change
that lets the LLM "explain the concept better" using its own knowledge. Any
sentence sourced from world knowledge has no file and no page to attach, so it
cannot get a Locator — which breaks the find-and-attribute premise and the
refusal contract (only answer when the material covers the question). Keeping
grounding absolute is what makes the tutor promise honest: *"I teach you what is
in YOUR material and show you exactly where,"* not *"I teach you the subject."*

## Consequence

The answer system prompt stays strict ("answer only from the provided
sources"). Making the Answer more tutor-like is a prompting/formatting change
(clearer explanation, explicit "covered vs. not covered"), never a loosening of
the sourcing rule. Any future general-explanation feature is a distinct mode
with distinct UI labeling, never mixed with attributed content.
