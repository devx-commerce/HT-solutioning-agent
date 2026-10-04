# 2026-09-24/25 — First live run of the ingestion pipeline, end to end

The ingestion pipeline (`/sweep` → classify → enqueue → `/work` → label +
notify + Sheet row) had never actually been exercised against a real
mailbox before this session — every prior deploy tested individual pieces
(onboarding, deck placeholder) in isolation. This is the record of getting
it to actually run clean, plus a real incident along the way and the fix.

## Bugs found and fixed, in the order hit

Every one of these was confirmed live before being called "the cause" —
none were fixed on a guess.

1. **Per-mailbox secret had no IAM binding.** `gmail_oauth.py`'s onboarding
   flow creates a new Secret Manager secret per mailbox but never granted
   the runtime service account access to it — every first sweep for a
   newly-onboarded mailbox 403'd on Secret Manager, which the code
   mislabeled as `reauthorization_required` (a stale/revoked token) rather
   than what it actually was (a permissions gap). Fixed the specific
   secret's IAM by hand, then fixed `gmail_oauth.py::_store_refresh_token`
   to grant the binding automatically on every new secret going forward.
2. **`classify.py`'s model name was wrong.** `gemini-2.0-flash-lite` isn't
   a valid model in this project/region — 404s every time. Classification
   had never actually succeeded, ever, until this was found. Real model:
   `gemini-2.5-flash-lite`. Confirmed by testing several candidate model
   names directly against Vertex AI.
3. **`classify.py`'s Gemini client wasn't bound to a variable.**
   `_client().models.generate_content(...)` chains a temporary `Client`
   object inline — its refcount can hit zero mid-expression once `.models`
   is accessed, closing the internal `httpx` client before
   `generate_content()` actually runs. Manifested as `RuntimeError: Cannot
   send a request, as the client has been closed` — a genuinely confusing
   error that has nothing to do with credentials, scopes, or the API
   itself. Fixed by binding `client = _client()` first in both
   `classify_and_extract` and `extract_only`. Also disabled Automatic
   Function Calling (`automatic_function_calling=...disable=True`) on both
   calls, since neither ever passes tools and the SDK's own message says
   direct `generate_content` + AFC isn't recommended.
4. **`gmail.labels` doesn't cover applying a label to a message.** Only
   `gmail.labels`' own docs and the code's own (incorrect) assumption
   suggested it covered "create/list/apply." It only covers
   create/list/delete — `messages.modify` (used to attach a label id to a
   message) 403s under `gmail.labels` alone with "Insufficient
   Permission." Fixed by moving the per-mailbox scope to `gmail.modify`,
   which is a superset covering both label management and applying labels
   (and implies read, so `gmail.readonly` became redundant alongside it).
   Every already-onboarded mailbox needs to re-consent after this kind of
   scope change — same as any other scope change.
5. **`BRIEFS_SHEET_ID` was still the literal placeholder `"YOUR_SHEET_ID"`**
   from the README's example deploy command — never actually replaced with
   a real sheet, and `sheets.googleapis.com` was never enabled on the
   project either (missing from the original API-enable list). Created a
   real Sheet programmatically (using the shared identity's own
   credentials, now that it holds full `drive` scope) with the exact
   9-column header `sheet.py` expects, enabled the API, and pointed
   `BRIEFS_SHEET_ID` at the real sheet.
6. **`ingestion_threads` rows were written via the streaming insert API**
   (`insert_rows_json`), then updated via DML seconds later
   (`mark_thread_built`, `mark_thread_sheet_written`). BigQuery rejects
   UPDATE/DELETE on a row still in the streaming buffer (up to ~90
   minutes) — every build was silently stuck at `status: building`
   forever. Fixed by switching `storage.open_thread` to a DML
   `INSERT`/`MERGE`, which has no such buffer restriction.

Once all six were fixed, a clean sweep against the real inbox ran
end-to-end for the first time: correct classification (2 real solution
requests picked out of 7-18 candidates across different runs), correct
label, correct notification, correct Sheet row, zero errors.

## The notification-spam incident

While iterating on fixes 4-6 above, repeated `force=true` test sweeps
re-enqueued the same real messages on every call. The root cause, found
after cleanup: `force` was meant to let classification be re-run cheaply
for testing, but it also bypassed the **thread-level** lock
(`thread_status`), not just the message-level one
(`decision_exists`) — so every force-sweep re-triggered a real build for
threads that were already `building` or `built`, and `execute_build`
sends the real notification email *before* the step that was actually
still broken (label apply, then Sheet write) could fail. Compounded by
Pub/Sub's own at-least-once redelivery on top of the repeated manual
triggers.

Result: ~370 duplicate "Solution deck drafted" emails landed in
`sales.agent@hindustantimes.com`'s own inbox, and ~223 duplicate rows in
the briefs Sheet, before it was caught.

**Cleanup performed:**
- Purged the Pub/Sub subscription's backlog (`gcloud pubsub subscriptions
  seek ... --time=now`) to stop further deliveries immediately.
- User bulk-trashed the 374 matching emails via Gmail search (bulk-delete
  via the API was correctly blocked by the harness's own safety
  classifier as an unverifiable-scope destructive action — done manually
  instead).
- Cleared the Sheet's data rows (`values.clear`, header preserved).
- Cleared the duplicate `decisions` (40 rows) and `ingestion_threads` (16
  rows) records in BigQuery.

**Real fix, not just cleanup:** `force` no longer bypasses the thread
lock — only message-level re-classification. See the thread-locking
rules (below, and in `developer-docs/EMAIL-POLLER-DESIGN.md`) for what actually
gates a real build now, independent of `force`.

## Thread-locking redesign (2026-09-25)

Prompted by two follow-up questions once the pipeline was verified
working: what happens when a thread has several messages, and is a
`failed` thread ever retried? The answers exposed two real gaps:

- Every message after the first one that locked a thread was silently
  invisible forever — including materially different follow-up content
  (budget, timeline, scope) that a human might reasonably expect to be
  picked up.
- A single failure (e.g. a transient bug, an API hiccup) permanently
  blocked every future message in that thread, with no retry path at all.

Full rule table and reasoning now lives in `developer-docs/EMAIL-POLLER-DESIGN.md`
under "Thread locking." Short version: a human's manual `generate-deck`
label always overrides an already-`built` thread; a `failed` thread is
always retryable by either branch; classification and the build prompt
now use the whole thread's content, not just whichever message tripped
the filter. Covered by `tests/test_ingestion.py` (16 tests, all passing
against real mocked control flow, not just syntax).

## Labels renamed

`solutioning-agent/deck-generated` → `deck-generated`,
`solutioning-agent/generate-deck` → `generate-deck` — flattened out of the
`solutioning-agent/` namespace. The one label that already existed in the
mailbox was renamed via the Gmail API in place, not recreated, so nothing
was left orphaned.

## Schema cleanup, same session

Unrelated to the incident, but done in this window: `bigquery/schema.sql`
trimmed from 8 tables to 6 — `handoffs`, `retrieval_log`, and the original
narrow `audit_log` (all three confirmed zero references anywhere in the
codebase) merged into one generic `audit_log` (`event_type` + JSON
`detail`), rather than three separate unused, overlapping guesses. Live
BigQuery dataset migrated to match.
