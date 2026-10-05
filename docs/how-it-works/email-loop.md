# From an email to a deck

## The steps

1. **Every 5 minutes** (`settings.sweep_schedule`), Cloud Scheduler calls the
   pipeline's `/sweep`.
2. **For each connected inbox**, the pipeline lists email received since
   that inbox's last check (on its first check after connecting, the last
   `settings.new_inbox_lookback_hours`, 24 by default), leaving out:
   - promotions, social and chat messages;
   - the agent's own emails (sales.agent@hindustantimes.com) and the inbox
     owner's own sent mail;
   - `settings.excluded_senders` (HR, payroll, IT, newsletters).

   It also lists every email labelled **generate-deck**, however old.
3. **Each email is checked once.** An email already decided, or a thread
   already being built, is skipped. A thread that already has a deck is
   skipped too, unless someone applied the `generate-deck` label, which
   always forces a fresh build.
4. **Is it a brief?** `gemini-2.5-flash-lite` reads the whole thread and
   decides whether it asks for a proposal, campaign or solution. If so, it
   extracts the client, a one-paragraph brief, the touchpoints and a category.
   A labelled email skips this decision; only the extraction runs.
5. **The brief is queued** on Pub/Sub. The pipeline builds one brief at a
   time per instance, at most three at once; a brief that fails is retried,
   up to five times.
6. **The agent researches and builds the deck** (see [research.md](research.md)
   and [decks.md](decks.md)). Usually 5 to 15 minutes.
7. **The pipeline then:**
   - labels the email **deck-generated** in the inbox it came from;
   - emails that inbox "Solution deck drafted: <client>", from
     sales.agent@hindustantimes.com, with the deck link, the evidence and its
     sources, which sources returned nothing, and the gaps;
   - adds a row to the briefs sheet (client, brief, touchpoints, category,
     month).

From the email arriving to the "deck drafted" email: typically 10 to 20
minutes, depending on where in the 5-minute cycle it arrived.

## What it never does

- It never replies on the client thread, never emails anyone outside HT, and
  never sends from a team member's inbox.
- It never moves, deletes or marks email read. The only visible change is the
  `deck-generated` label.
- It never reads attachments. The brief has to be in the email text. A brief
  that is only in an attached PDF or Word file gets a deck based on the email
  text alone.

## The sources section can be trusted

The list of sources checked, and which returned nothing, is built from the
log of the research tools' actual calls, not from the agent's description of
its own work. Any claim in the email citing a link that no research tool
returned is removed, and the email says how many were removed.
