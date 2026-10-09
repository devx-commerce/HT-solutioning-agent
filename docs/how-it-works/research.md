# Research

Before drafting, the agent decides what it needs to know about the brief and
uses four sources. It works iteratively: what it finds in HT's past decks
shapes what it searches for on the web, and the other way round.

## The sources

| Source | What it's for | How |
|---|---|---|
| HT's past pitch decks | What HT has proposed before for this client or similar briefs: its formats, IPs and their scale | The past-deck index (below). Searched from several angles: the client, the category, the objective, the audience, the formats asked for. Every deck the agent builds on is then read in full. |
| The web | The client's recent campaigns and news, its social media, competitors' campaigns, the market | Gemini with Google Search. Each search is a full question. |
| YouTube | The client's and competitors' video ads | YouTube Data API |
| A specific web page | Usually the client's own website | A plain page fetch |

Every call to every source is logged in BigQuery (`audit_log`), including the
ones that returned nothing.

## The past-deck index

Every deck and PDF in `settings.past_decks_folder_ids` is read into the
BigQuery table `past_deck_slides`, one row per slide:

- **The text of each slide.** A PDF page that is only a picture is read by
  Gemini, so scanned or image-only decks are searchable too.
- **The part of the deck it belongs to,** such as "HT PACE Principals' Meet
  integration". Gemini reads the whole deck to mark its parts, so a slide
  that continues an idea without naming it still belongs to it.
- **HT's own IPs** in that part (HT PACE, Fresh on Campus, Anokhee Club, ...)
  and its **solution types** (print, digital, video and social, audio,
  events and on-ground, content IP, research, social impact).
- Its **print innovations**, by the deck's own name for them (French Window,
  Gatefold, Emboss Jacket, Text Bending, ...). Ad sizes such as a half page
  are not counted.
- A **summary** of the whole deck.

A search finds slides both by meaning and by exact words (an IP's name, a
city), groups them by deck and returns the best decks first, each marked a
strong or a weak match. `read_past_deck` then gives the agent one deck whole.
`list_print_formats` lists every print innovation in the index with the
decks that show it; the agent uses it only when print is part of the brief.

The index refreshes daily at `settings.past_decks_refresh_schedule` (only
new and changed files are read again; a removed file drops out). After
adding decks, run it by hand: Cloud Build > Triggers >
`solutioning-agent-past-decks` > Run. It takes a minute when nothing has
changed and a few minutes per new deck.

`settings.past_decks_source` chooses where the agent searches: `bigquery`
(this index) or `vertex` (the older Gemini Enterprise Drive connector).

## Rules it follows

- **Every claim carries its source.** A finding without a source link is
  dropped before the agent sees it. The agent may report only what a tool
  returned in this conversation, never its own background knowledge.
- **Links are permanent.** Google's temporary redirect links are resolved to
  the real page address, so a source still works when someone opens the email
  weeks later.
- **No competitor newspapers.** Findings resting only on the publications in
  `settings.competitor_outlets` are dropped, and the agent is told not to name
  them.
- **No guessed websites.** The agent can only open a page on a site that
  appeared in the email or in an earlier search result.
- **Only HT's past decks count as prior work.** Past-deck results are limited
  to the past decks folder, even though the agent account can see more of
  Drive.
- **Gaps are reported, not filled.** Anything it couldn't establish is listed
  as a gap in the email.

## Limits

- **Public information only.** Nothing behind a paywall or login, and no
  internal HT data beyond the past decks folder (no rate cards, no CRM).
- **Some sites block it.** Many company websites refuse automated page
  fetches; the agent reports the fetch as failed and works from search
  results instead.
- **Past decks are as fresh as the last index run.** A deck added today is
  searchable after the next daily run, or straight away after a manual run.
  If the folder is unshared from the agent account, runs fail and the
  index keeps what it had.
- **Search results vary.** The same brief researched twice can surface
  different sources.
- **Attachments are not read.** See [email-loop.md](email-loop.md).
