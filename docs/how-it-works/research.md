# Research

Before drafting, the agent decides what it needs to know about the brief and
uses four sources. It works iteratively: what it finds in HT's past decks
shapes what it searches for on the web, and the other way round.

## The sources

| Source | What it's for | How |
|---|---|---|
| HT's past pitch decks | What HT has proposed before for this client or similar briefs | Vertex AI Search over the "Past Pitch Decks" Drive folder. Searched from several angles: the client, the category, the objective, the audience, the formats asked for. |
| The web | The client's recent campaigns and news, its social media, competitors' campaigns, the market | Gemini with Google Search. Each search is a full question. |
| YouTube | The client's and competitors' video ads | YouTube Data API |
| A specific web page | Usually the client's own website | A plain page fetch |

Every call to every source is logged in BigQuery (`audit_log`), including the
ones that returned nothing.

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
- **Past decks need the folder and the licence.** If the folder is unshared
  or the agent account loses its Gemini Enterprise licence, past-decks
  searches return nothing.
- **Search results vary.** The same brief researched twice can surface
  different sources.
- **Attachments are not read.** See [email-loop.md](email-loop.md).
