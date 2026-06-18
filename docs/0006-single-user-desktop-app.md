# CiteFinder ships as a single-user desktop app

CiteFinder is delivered as a **downloadable single-user desktop application** that
one person installs and runs on their own PC. There are no accounts, no login, no
email — the "user" is whoever is sitting at the machine. A multi-user / hosted
version was explicitly considered and **rejected**.

## Why

The trigger was a request to add multi-user accounts: register with email + name +
password, verify the email before login, edit profile. Designing it surfaced an
irreconcilable conflict with the product's founding promise. Email verification
requires the app to **send mail and be reachable by many people** — i.e. a hosted
service on a public host with SMTP, secure session cookies, password hashing, and
rate limiting. But CiteFinder's whole identity is **local-first**: the corpus
never leaves the machine ([ADR 0002](0002-local-by-default-hosted-opt-in.md)). A
hosted service means every user's PDFs live on *our* server — the exact opposite
guarantee. There is no honest "verify your email" loop on a pure-localhost app, so
the two cannot coexist.

Rather than dilute the privacy promise to bolt on accounts, we went the other way:
make the local-first nature *concrete* by shipping CiteFinder as an installable
personal app. This matches how the tool is actually used (one researcher, their own
readings) and keeps the strong guarantee — your documents stay on your machine —
absolute by construction.

## Considered options

- **Hosted multi-user service** (register/verify/login). Rejected: breaks
  local-first; the corpus would leave the machine; introduces an auth, email, and
  infrastructure surface unrelated to the product's value.
- **Multiple local accounts on one shared install.** Rejected: email verification
  is meaningless on a localhost-only app, and per-PC account separation solves a
  problem (shared machines) the target user doesn't have.
- **Single-user desktop app** (chosen).

## Consequence

- The README and `app.py` previously framed multi-user as "purely an auth layer
  since the data already partitions by `user_id`." That framing is now **false**
  and is corrected: CiteFinder is single-user *by design*, not pending an auth
  layer.
- `user_id` (`DEFAULT_USER = "user_1"`) becomes a **vestigial frozen constant**.
  Every chat/source/chunk already scopes by `chat_id`
  ([ADR 0005](0005-chat-owns-its-corpus.md)), so `user_id` no longer partitions
  anything. It is kept (not removed) because the evaluation harness still scopes
  pre-chat data by it, and dropping a `NOT NULL` column from a shipped app's
  database — which now holds precious user data — is migration risk for no gain.
- The app must manage its own runtime (database, embedding model, LLM choice) on
  the user's machine. How that is packaged is recorded in
  [ADR 0007](0007-bundle-postgres-fetch-llm.md).
