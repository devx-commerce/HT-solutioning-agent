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
4. **Is it a brief?** `gemini-2.5-flash-lite` judges the new email on its
   own: its sender and recipients, its text (including any email forwarded
   in it) and its attachments. It is a brief only if it **asks the inbox
   owner for a solution** (ideas, a plan, a proposal or a deck), and the ask
   is in this email. Not briefs:
   - the owner's own requests to colleagues, and replies that deliver or
     update them (mocks, a page, a deck someone else made);
   - asks only for rates, costing, pricing, approvals, scheduling or
     information;
   - replies that only follow up on an older ask;
   - calendar invitations (recognised by their subject, without asking the
     model).

   If it is a brief, the model then reads the whole thread, with
   attachments, and extracts the client, a one-paragraph brief, the
   touchpoints and a category. A labelled email skips the decision; only the
   extraction runs. The rules are checked against real threads with
   `python -m evals.check_classifier`.
5. **The brief is queued** on Pub/Sub. The pipeline builds one brief at a
   time per instance, at most three at once; a brief that fails is retried,
   up to five times.
6. **The agent researches and builds the deck** (see [research.md](research.md)
   and [decks.md](decks.md)). Usually 5 to 15 minutes.
7. **The pipeline then:**
   - labels the email **deck-generated** in the inbox it came from;
   - emails that inbox "Solution deck drafted: <client>", from
     sales.agent@hindustantimes.com, with the deck link, the evidence and its
     sources, which sources returned nothing, and the gaps. A "Refine this
     deck" button is the deep link to the refinement loop: it opens the
     Solutioning Agent in Gemini Enterprise with the deck's name and brief ID
     already typed in (`infrastructure.gemini_enterprise_agent_url`);
   - adds a row to the briefs sheet (client, brief, touchpoints, category,
     month).

From the email arriving to the "deck drafted" email: typically 10 to 20
minutes, depending on where in the 5-minute cycle it arrived.

## What it never does

- It never replies on the client thread, never emails anyone outside HT, and
  never sends from a team member's inbox.
- It never moves, deletes or marks email read. The only visible change is the
  `deck-generated` label.
- It reads the text of Word, Excel, PowerPoint, PDF, text and CSV
  attachments (up to about 6,000 characters each), not images. A brief sent
  only as a scanned image isn't read.

## The email's layout

The agent's report always has the same sections in the same order: **Ideas
in the deck** (each part of the deck by its exact name, split into what came
from HT's past decks, naming the deck, and what is new for this client),
then what it found from HT's past decks, from the web and social, from
YouTube and from the client's website, then the gaps. Every link is named
the same way whatever the agent wrote: the website for a web page, "HT past
deck: <name>" for a past deck, "YouTube" for a video.

## The sources section can be trusted

The list of sources checked, and which returned nothing, is built from the
log of the research tools' actual calls, not from the agent's description of
its own work. Any claim in the email citing a link that no research tool
returned is removed, and the email says how many were removed.
