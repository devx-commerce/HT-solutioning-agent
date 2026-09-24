# Email poller — ingestion design

Decisions only, consolidated out of chat. Implementation lives in
`ingestion.py`, `classify.py`, `sheet.py`, `labels.py`, `storage.py`,
`agent_client.py`. This doc is what to update if a decision here turns out
wrong — not a description of the code, the reasoning behind it.

## Polling

- Cadence: every 30 min, Cloud Scheduler → `/sweep`.
- Runs per onboarded mailbox (the multi-mailbox self-serve system stays
  exactly as built — this generalizes to N inboxes, not just solutioning's).
- No sender-domain filtering. Solutioning receives legitimate requests from
  HT's own sales teams, not only external clients/agencies — excluding
  `@hindustantimes.com` would drop real requests.
- No `is:unread` filtering. The mailbox is actively worked by a person;
  relying on read-state as a candidate filter means a human reading
  something before the sweep runs would silently remove it from
  consideration forever. Idempotency has to live entirely in our own
  registry, not in Gmail's visible state.

## Two query branches, every sweep

**Branch A — the normal flow.** Bounded by a **watermark**, not a fixed
"last 30 minutes": the timestamp of the last *successful* sweep is
persisted (`sweep_state` table, one row) and used as the next sweep's
`after:` cutoff, with a few minutes of overlap subtracted. In the normal
case this behaves like a ~30-35 minute rolling window. If a sweep is
skipped, fails, or Cloud Run is down for hours, the next successful sweep's
watermark is still that old — so it automatically covers the whole gap
instead of a fixed window silently losing whatever arrived during it.
First-run bootstrap default (no watermark yet): last 24 hours, not
unbounded history.

Every Branch A candidate goes through one cheap-model call
(`classify.classify_and_extract`) that does classification and extraction
in one structured pass — no keyword tier, no separate extraction call.

**Branch B — the manual override.** `label:solutioning-agent/generate-deck`,
**never time-bounded, ever** — this is the entire point of it. A human can
label an email from months ago and it still gets picked up on the next
sweep, specifically because this branch is exempt from the watermark.

Branch B skips classification entirely. The label is a direct instruction
("generate a deck from this"), not a hint to consider — asking the model
"is this a solutioning request" would just be second-guessing an explicit human
action. It goes straight to extraction (`classify.extract_only`). If
extraction finds genuinely nothing usable (label applied to something with
no real content), that outcome is still recorded, just without a deck or
Sheet row.

## Idempotency — internal registry only, never Gmail state

Two separate checks, at two different granularities:

- **Message-level** (`decisions` table, keyed by `message_id`): has this
  exact message already been looked at, whichever branch it came through?
  Checked before any model call. This is what stops a message from being
  reprocessed every 30 minutes — not a label, not read-state.
- **Thread-level** (`ingestion_threads` table, keyed by `thread_id`,
  intake-owned — separate from whatever the agent's own tools record about
  a built deck): has *this thread* already produced a brief? If yes, a new
  Branch A message on it doesn't retrigger classification or a duplicate
  Sheet row — it's logged as thread activity and left there. Auto-feeding a
  new reply into deck refinement is a real feature, not a default to guess
  at now.
- **Branch B is message-scoped, not thread-scoped**, deliberately: a human
  pointing at one specific message is a more specific instruction than
  "does this thread already have anything," so it can still act even if the
  thread already has an existing brief from Branch A.

## Labels — two, opposite directions

- **`solutioning-agent/generate-deck`** — human-applied. The Branch B trigger.
  Never removed by the agent; the human's own label stays exactly as they
  left it.
- **`solutioning-agent/deck-generated`** — agent-applied, **only** when a deck was
  actually built. Never applied for a rejection, never for "processed but
  nothing usable." This is the only thing that ever visibly touches the
  mailbox — the person working it should never be able to tell the poller
  looked at something unless a deck came out of it.

Requires the `gmail.labels` scope on the per-mailbox onboarding grant (not
just `gmail.readonly`), which means already-onboarded mailboxes need to
re-consent once this scope is added — a one-time cost, not an ongoing one.

## The sheet

Doesn't exist yet. Created once, by hand — same pattern as the Slides
template (`TEMPLATE_FILE_ID`): one human-made artifact, referenced by id
(`BRIEFS_SHEET_ID`), not generated from code. The agent only ever appends
rows to a sheet that already exists.

One row per thread, written exactly once, **never edited again** — no
column on an existing row is ever revisited, whatever a later email in the
same thread reveals. Two reasons: it can never clobber a value a person has
since corrected by hand, and "why does this row say what it says" always
traces to one write, not a history of silent edits.

Column-by-column:

| Column | Filled? | How |
|---|---|---|
| Client | Yes | Extracted from email content |
| Brief | Yes | Summarized from email content |
| Touchpoints | Only if unambiguous | Extracted against a fixed enum (Print / Digital / Integrated / Events); blank otherwise |
| Category | **Open, tentative** | Implemented the same as Touchpoints (fill only if unambiguous) as a defensible default — not confirmed. Easy one-line change to always-blank if that's wrong; see `classify.py`. |
| Month | Yes, but never LLM-derived | The month the *triggering email* arrived, read straight off its timestamp (`April'26` style) — not extracted, not inferred, not the AM's stated target flight date |
| AM/CH | **Never** | No reliable signal — mailbox arrival doesn't mean deal ownership, even across N onboarded mailboxes. A forward, a CC, a shared inbox all break the mapping. Human-filled only |
| GH | **Never** | Same reasoning as AM/CH |
| Closure Status | **Never** | A sales judgment that evolves over the deal's life; never knowable at intake, never agent-written at any point |
| Solution Pillar | **Never** | No taxonomy to classify against, and not attempted |

Dedup for the write itself: `ingestion_threads.sheet_row_written` is
checked before ever calling the Sheets API, so two overlapping sweeps can't
produce two rows for one thread. BigQuery is the source of truth for "does
a row exist," never a live read of the sheet itself.

## Why BigQuery, honestly

Three small tables: `decisions` (message-level idempotency + audit),
`ingestion_threads` (thread-level state), `sweep_state` (the watermark).

**For it:** already provisioned, free at this volume, and the entire
decision history becomes SQL-queryable for free — "every email rejected
this week and why" is a `WHERE` clause. Matches the audit-trail posture the
rest of this project cares about.

**Against it:** BigQuery is an analytical warehouse, not built for the
actual access pattern here — a point lookup by `message_id` before every
classification call, and single-row updates to the watermark. Each of those
is a full query-job dispatch (often 1-2+ seconds), where a document store
like Firestore would be a more natural fit for "does this key exist, read
one document, write one document."

**Why it stays anyway, for now:** at the volume one inbox actually produces
— a handful to a few dozen genuine candidates a day — that per-query
latency costs nothing real, and running two database systems for what this
is today is more operational surface than the theoretical gain is worth. If
sweep candidate volume ever grows into the hundreds per run, Firestore for
the hot-path lookups with BigQuery kept purely as the audit sink is the
right split — a later call, not a now one.

## The build-work queue

`/sweep` no longer invokes Agent Engine inline. It does the cheap half —
listing both branches, idempotency checks, one classification call per
candidate — and for anything that should produce a deck, *publishes* a
build task (`pubsub.publish_build_task`) instead of building it in the same
request. `/work`, the push subscription's target, does the expensive half:
the actual Agent Engine invocation, then the label, the notification, and
the sheet row.

**Why now, not later:** load here is roughly 100 emails/week, but "not
spread uniformly" means a single sweep can surface a burst well above that
average on a given day. Sequential-in-one-request doesn't absorb a burst,
it just makes that one `/sweep` call slower; unbounded parallel Agent
Engine calls trade that for the opposite risk, a burst spiking cost or
hitting quota all at once. The actual concurrency control is Cloud Run's
own `--max-instances` / `--concurrency` on the service (README step 7) —
Pub/Sub's job is only to hold the queue, not to limit how fast it drains.

**The deck-built notification is a send, not a draft, and goes to
solutioning, not the triggering thread.** This build's scope is
solutioning's own inbox — the notification is a *new* email to
`SOLUTIONING_NOTIFY_EMAIL`, never a reply on the original thread, which may
have external participants earlier in its history. That's what makes
`send` safe here where a reply-on-thread wouldn't have been: nothing in
this codebase ever addresses an external recipient. Placeholder body for
now (`notifications.send_deck_notification`); the deck link and its
evidence once deck generation exists.

## Testing the poller in isolation

Two flags on `/sweep`, both off by default, both meant for a terminal, not
Cloud Scheduler. Because building is now async, `/sweep`'s own response
only reports `queued_for_build` — to see the actual outcome (label applied,
sheet row written), either let a real Pub/Sub subscription deliver to
`/work`, or call `/work` directly with a hand-built envelope while
iterating.

- **`force=true`** — ignore the watermark (use the bootstrap cutoff instead)
  and ignore prior decisions, so every candidate gets reprocessed as if
  this were the first sweep ever. Never advances the real watermark — a
  test run must not affect the next genuine one. Expect duplicate rows if
  run against a mailbox already swept normally; that's the tradeoff for a
  cheap replay, not an oversight.
- **`dry_run=true`** — carried through in the published payload; `/work`
  skips the actual Agent Engine call and treats it as a deterministic
  success instead of the (currently fragile) text-matching check against
  the agent's reply. Everything downstream of that point still happens for
  real — the label gets applied, the notification gets sent, the sheet row
  gets written — so the poller's own mechanics are verifiable without a
  deployed, working agent behind them. The real fix for the non-dry-run
  path is still a structured tool result instead of string-matching a URL
  out of free text; this sidesteps needing that fix yet, deliberately,
  while the project is being built component by component.

## Not built here

Reply-triggered refinement (a Branch A message on an already-brief'd thread
automatically updating the deck), a Category taxonomy, an AM/GH lookup
table. All deliberately deferred rather than guessed at.
