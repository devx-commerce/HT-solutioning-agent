# Solutioning Agent: user guide

This guide is for solutioning team members using the Solutioning Agent for
the first time. It takes about five minutes to read.

## What it is, in one paragraph

When a client brief lands in your inbox, the Solutioning Agent drafts a first
version of the solution deck for you. It reads the brief, researches the
client and its competitors, looks through HT's past pitch decks for similar
work, and builds a Google Slides deck in HT's house style. About 10 to 20
minutes after the brief arrives, you get an email with a link to the deck and
the research behind it. You can also build and change decks by chatting with
the agent in Gemini Enterprise.

The deck is a starting point. Expect to review it, correct it and add pricing
before anything goes to a client.

## Before you start

You need:

- an HT Google account on **hindustantimes.com** or **htdigital.in**;
- for chat only: a **Gemini Enterprise** licence (ask your manager if you're
  not sure you have one).

## Step 1: Connect your inbox (one time, about a minute)

1. Open this link in the browser where you use your HT email:
   **https://solutioning-agent-onboarding-296974829876.us-central1.run.app/oauth/gmail/start**
2. Choose your HT account.
3. Google asks you to allow the Solutioning Agent to read your email and
   manage labels. Click **Allow**.
4. You'll see "Onboarded your.name@hindustantimes.com. You can close this
   tab." That's it.

The agent only reads your email to look for client briefs. It never sends
email from your account, never replies to anyone, and never deletes or moves
your messages. The only visible change in your inbox is a label (below).

If the page says your account can't connect, check you chose your HT account,
not a personal one.

## Step 2: Let briefs arrive as usual

You don't need to do anything differently. Every 5 minutes the agent checks
for new email in your inbox and decides whether each message is a client
brief (a request for a proposal, campaign idea or solution). Newsletters,
internal HR and IT mail, and ordinary conversation are ignored.

When it finds a brief:

1. It researches and builds the deck. This usually takes 5 to 15 minutes.
2. It adds the label **deck-generated** to the brief in your inbox.
3. You receive an email titled **"Solution deck drafted: <client>"** from
   sales.agent@hindustantimes.com. It contains:
   - a summary of the brief as the agent understood it;
   - a button to open the deck;
   - the evidence it found, each point with a link to its source;
   - which sources it checked and which returned nothing;
   - the gaps: things it could not establish and you should check.

### If the agent missed a brief

Apply the label **generate-deck** to the email. The first time, create the
label in Gmail (Settings > Labels > Create new label, named exactly
`generate-deck`). The agent picks the email up at its next check, within 30
minutes, and builds a deck even if it had decided the email wasn't a brief,
or had already built one for that thread.

## Step 3: Open and use the deck

The deck opens in Google Slides. It is **view-only**: everyone on
hindustantimes.com and htdigital.in can open it, but nobody can edit the
original. This keeps the agent's later revisions from overwriting your work.

- **To change it by hand:** File > Make a copy, and edit your copy. Changes
  you later ask the agent to make in chat apply to the original, not your
  copy.
- **To change it through the agent:** use chat (below).

### What a deck contains

- Cover with HT's and the client's logos (a labelled placeholder where the
  client's logo couldn't be found).
- The brief, the insight and the big idea.
- An overview of the solution, then a slide or two on each part of it. Custom
  activations (on-ground events, contests, mystery shopper programmes and so
  on) get concrete detail: cities, scale, timing, how it runs, how it's
  reported.
- A plan or timeline, a deliverables table, and next steps.

Figures the agent estimated rather than found are marked **(indicative)**.
Figures it found carry their source. It never states prices.

## Using chat in Gemini Enterprise

Open **https://vertexaisearch.cloud.google.com/us/home/cid/b9cac80f-5f8a-4ebf-926c-980e78d0782a**
and choose the **Solutioning Agent**.

Things you can ask, in plain words:

| You want to | Say something like |
|---|---|
| Build a deck from a brief | "Build a deck for this brief:" and paste the email |
| Find a deck | "Find the Tata Sampann deck" (it lists all matches if there are several) |
| Change wording | "On the big idea slide, change the heading to 'Every haat is a stage'" |
| Add, remove or reorder slides | "Delete the market context slide and move the timeline after the big idea" |
| Add a slide | "Add a slide after the mystery shopper slide about a WhatsApp recipe contest, with a picture" |
| Change a picture | "Change the picture on the haat activation slide to a nukkad natak at dusk" |
| Use your own picture | Attach the image and say "Use this on the big idea slide" |
| Add HT's credentials | "Add the Why HT slides for the Hindi heartland" |

Each change updates the same deck link, so a link you've already shared keeps
working and shows the latest version.

## What it can't do

Knowing these saves time:

- **No design changes.** It can't change colours, fonts, the theme or slide
  layouts' styling. Every deck uses HT's approved design.
- **No pricing.** It never states rates, costs or discounts. Commercials come
  from HT's pricing team.
- **No undo.** A change can't be reversed by asking; ask for the opposite
  change instead.
- **No edits by email reply.** Replying to the "deck drafted" email does
  nothing. Use chat to change a deck.
- **Your own images must fit.** A picture you attach must be a PNG or JPEG
  under 4 MB, large enough and roughly the right shape for its slot. If it
  isn't, the agent says what's wrong and what would work.
- **Research has limits.** It can't see paywalled articles, private data or
  anything not on the public web or in HT's past decks folder. It names
  what it couldn't find in the email's gaps section.
- **Competitor newspapers are never cited** (Times of India, Economic Times,
  Dainik Jagran, Dainik Bhaskar, Amar Ujala, Indian Express, The Hindu, The
  Tribune).

## If something goes wrong

- **No email after an hour** for a brief you expected to be picked up: apply
  the **generate-deck** label. If there's still nothing after the next check,
  tell the UAT coordinator.
- **"Please reconnect your inbox" email:** the agent lost access to your
  inbox. Click the link in that email (it's the same as Step 1).
- **The deck has a mistake:** fix it in chat or in your copy, and note it in
  the daily UAT check-in so the team can improve the agent.
