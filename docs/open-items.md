# Deprioritized and open items

Running catch-all so deferred/unresolved things don't get lost. Not a status
tracker for in-progress work — see `docs/research-and-rendering-decisions.md`
for the active plan. Add to this file whenever something comes up that isn't
being acted on right now.

## Deprioritized (explicitly deferred, not being built now)

- **Competitor Analysis Tool integration** (named in the SOW as a source for
  competitor print activity/category share of voice). Tertiary — revisit
  only if the client asks. Vendor not yet identified.
- **Salesforce integration** (account/opportunity/conversation history, also
  named in the SOW). Tertiary, same as above.
- **AI image generation in decks.** No SOW basis for this at all. If picked
  up later: gate on "only if no real brand/client asset exists," fixed
  aspect-ratio/resolution enum enforced by the tool call (not model-chosen
  dimensions), no text baked into the image.
- **Client/HT brand asset lookup** (logo fetch by domain, HT's own fixed
  brand assets). Designed conceptually — domain-based logo API with a
  favicon fallback, "don't guess" if nothing resolves — but not built. Not
  required for the first working demo.
- **User-uploaded image → attach to a specific slide** (someone hands the
  agent an icon/logo mid-chat and asks it to place it). Fits the existing
  patch-and-reupload revision pipeline conceptually, but depends on an
  unconfirmed platform capability — see the open question below.

## Open questions (need an answer, not yet answered)

- **Does Gemini Enterprise's chat surface forward an uploaded file's bytes
  (or a storage reference) to the agent in a form a tool function can
  access?** Not confirmed by anything in this repo or the SOW — no
  file-upload handling exists in the code. Test directly in the GE chat
  (attach an image, ask the agent to describe what was sent) before building
  the attach-to-slide tool around an assumption.
- **How does the vendor/tool for the Competitor Analysis Tool integration
  expose its data** — API, scheduled export, or UI-only? SOW (§10.3.viii)
  says this needs confirming with the client; not yet done. Blocks that
  integration whenever it's picked back up.

## Housekeeping

- **Label every resource that's ours with `solutioning-agent`.** This runs in
  HT Media's own GCP project (`academic-diode-477405-m3`), which already
  contains unrelated internal HT work — Discovery Engine data stores
  (`drive_*`, `drive-done_*`, `sfdc-new-test_*_opportunity`, two
  `*-gcs-connector_*` stores), and possibly more. Nothing of theirs may be
  modified. Needs: a full inventory (Cloud Run services, secrets, BigQuery
  dataset, Pub/Sub topics/subscriptions, service accounts, scheduler jobs,
  any data stores we create), then a label applied to each one that is
  verifiably ours. When in doubt about ownership, leave it alone.

## Ideas not yet designed

- **Sender blacklist for ingestion.** Certain email addresses (e.g. HR,
  internal-only senders) should never even reach classification — filtered
  out at intake, before the Gemini call, not rejected by it. Needs: where
  the blacklist lives (BigQuery table vs. a config list), whether it's
  address-exact or domain-level, and who maintains it. Not designed yet,
  just captured so it isn't lost.

## Log

- 2026-09-25: file created, seeded from items deprioritized during the
  research/rendering planning conversation.
