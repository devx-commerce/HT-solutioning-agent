# Solutioning Agent

The Solutioning Agent drafts first-version solution decks for HT Media's
solutioning team. When a client brief arrives in a team member's inbox, the
agent reads it, researches the client, its competitors and HT's own past
proposals, and builds a Google Slides deck in HT's house style. It then
emails that inbox a link to the deck with the evidence behind it.
The same agent is available in Gemini Enterprise chat, where people can build
decks from a pasted brief and revise them in conversation.

It is a first draft, not a finished proposal: the solutioning team reviews,
edits and prices every deck before it goes to a client.

## What it does

- **Reads briefs from email.** Solutioning team members connect their inbox once.
  Every 30 minutes the agent checks connected inboxes for new client briefs,
  ignores everything else, and builds a deck for each brief it finds.
- **Researches before drafting.** It searches HT's library of past pitch
  decks, the web (news, campaigns, social media), YouTube and the client's
  own website. Every fact in the deck and the email carries its source, and
  competitor newspapers are never cited.
- **Builds the deck.** A fixed structure (brief, insight, big idea, solution,
  plan, next steps), HT's and the client's logos on the cover, generated
  pictures, and dedicated slides with concrete detail for each custom
  activation. It never states prices; commercials are left to HT's pricing
  team.
- **Revises in chat.** In Gemini Enterprise, people can change text, add,
  remove or reorder slides, ask for new pictures, place their own image, and
  add HT's credentials slides.

## Where to go next

| You want to | Read |
|---|---|
| Start using it (solutioning team members) | [docs/uat/user-guide.md](docs/uat/user-guide.md) |
| Run the UAT week | [docs/uat/uat-plan.md](docs/uat/uat-plan.md) |
| Understand how it fits together | [docs/architecture.md](docs/architecture.md) |
| Know exactly what it can and can't do | [docs/how-it-works/](docs/how-it-works/) |
| Change a setting | [docs/operations/configuration.md](docs/operations/configuration.md) |
| Deploy a change | [docs/operations/deploy.md](docs/operations/deploy.md) |
| See every cloud resource it uses | [docs/operations/resources.md](docs/operations/resources.md) |
| Check quality with evals | [docs/operations/evals.md](docs/operations/evals.md) |
| Report on usage | [docs/operations/weekly-report.md](docs/operations/weekly-report.md) |
| Fix something that isn't working | [docs/operations/troubleshooting.md](docs/operations/troubleshooting.md) |

## How it runs

Everything runs in HT's Google Cloud project `academic-diode-477405-m3`:

- **Cloud Run** hosts the email pipeline, the inbox onboarding page and the
  deck renderer.
- **Vertex AI Agent Engine** hosts the agent itself, built with Google's
  Agent Development Kit (ADK) on Gemini models.
- **Gemini Enterprise** gives people the chat interface.
- **BigQuery** keeps every deck, every decision and every research call, which
  is what the weekly report reads.
- **Google Drive** holds the generated decks (as Google Slides) and the past
  decks the agent learns from.

## Changing and deploying

All settings live in one file, [`config.yaml`](config.yaml), with safe
defaults. A deploy is one command (or one click in Cloud Build), checks the
config first, and updates everything in place. See
[docs/operations/deploy.md](docs/operations/deploy.md).

## Repository layout

| Path | What's in it |
|---|---|
| `agents/solutioning_agent/` | The agent: its instructions and tools (research, decks, pictures) |
| `app/` | The email pipeline and the inbox onboarding page (Cloud Run) |
| `renderer/` | The deck renderer (Cloud Run) |
| `evals/` | Quality evals on real HT briefs |
| `deploy/` | The deploy pipeline and its scripts |
| `bigquery/` | Table definitions and the weekly report query |
| `config.yaml` | Every setting |
| `docs/` | This documentation |
| `tests/` | Automated tests, run on every deploy |
