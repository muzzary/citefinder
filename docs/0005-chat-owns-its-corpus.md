# A chat owns its corpus

The unit a student queries is a **chat**, not their whole account. Each chat
owns the files/folder added to it: those become Sources tagged with the chat's
id, and a question asked in a chat searches **only that chat's Sources**. A
student can keep several chats — "Biology notes", "Law thesis" — and each is an
isolated corpus with its own Q&A history.

Concretely: a `chats` table; `chat_id` on `sources`; retrieval scoped by
`chat_id` (with a fallback to `user_id` for pre-chat / evaluation Sources that
have no chat); a `messages` table holding each chat's turns so the sidebar can
replay them.

## Why

The product is "open a chat, add the folder you care about, ask about *that*".
The alternative — one shared per-user library where every question searches
everything ever uploaded — was rejected: it makes answers noisier (a Law
question could retrieve Biology pages), it cannot isolate "ask only within this
folder", and it has no natural home for the sidebar of distinct, replayable
conversations the UI is built around. Scoping at the user level is simpler but
the wrong unit; scoping at the chat level matches how the tool is actually used.

This is hard to reverse (it shapes the schema and every retrieval query) and
non-obvious to a future reader (why `chat_id` and not just `user_id`?), which is
why it is recorded here.

## Consequence

- Retrieval, the coverage gate, and `_has_material` all take a `chat_id` and
  scope to it; passing none falls back to `user_id` (keeps the Phase-7 eval and
  any pre-chat data working unchanged).
- Ingest (single file or folder) records which chat a Source belongs to.
- A Source belongs to exactly one chat in this model. If "reuse the same
  document across chats without re-ingesting" is ever wanted, it needs a
  separate decision (a many-to-many chat↔source link), not an edit to this one.
- Locators and Citations are unchanged — attribution is per Source as before;
  only *which* Sources are in scope changes.
