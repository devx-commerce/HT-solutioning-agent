# How decks are built

The agent writes each deck as structured data (Deck JSON): a list of slides,
each with a layout and its text. That data, not the Slides file, is the deck
of record. It is kept in BigQuery, and every revision starts from it.

## The steps

1. **The agent drafts** the deck from its research.
2. **The rules are checked.** If any is broken, the deck goes back to the
   agent with the exact problems ("slide 6: heading is 82 characters; the
   limit is 75"), and nothing is published until it's fixed. Text is never cut
   off to fit; the agent rewrites it.
3. **Logos and pictures are added.** HT's logo comes from the HT brand assets
   folder; the client's logo from the client's own website (or a labelled
   placeholder). Each picture is generated from the description the agent
   wrote for it, then checked (below).
4. **The renderer** turns the deck into PowerPoint in HT's theme.
5. **Drive** converts it to Google Slides, in the "Agent Generated Decks"
   folder, read-only for `settings.deck_reader_domains`.
6. **The deck is saved** in BigQuery (`briefs`) with its link, its brief
   reference and the research report from the build. The reference is the
   client and a number, "Tata Sampann 3" for Tata Sampann's third deck, and
   the Slides file is named after it. It is given once and never changes;
   the brief ID stays the key underneath. A deck joins a client's existing
   series only when the client name matches it exactly (ignoring case,
   punctuation, plurals and suffixes like "Pvt Ltd"); otherwise it starts a
   new one, so a deck is never numbered into another client's series.

## The rules (HT master deck)

- **Structure:** cover, then the brief, then the solution, then why this
  works, then the closing slide. There is no next-steps slide and no slide
  about work for other clients, as in HT's own decks, and the closing slide
  never talks about next steps, the way forward or commercials. If HT's credentials
  ("Why HT") slides are added, they go straight after the brief.
- **Length:** 7 to `settings.max_slides` slides (40 by default; credentials
  slides don't count). Decks are as long as the solution needs.
- **Layouts:** only HT's approved layouts, each with character limits per
  field so text never overflows.
- **Pictures:** a new deck has one picture for every two to three content
  slides (title, section and closing slides don't count), and never more
  than `settings.max_images` (15 by default). Pictures go on two-column
  slides (right half), facts slides (beside the facts), innovation slides
  and full-slide image slides, never inside cards. Generated pictures are
  scenes only (people, places, events), with no text, logos, mastheads or
  brand names in them. The agent never generates HT-branded artwork: a slide
  that needs an HT page, jacket or masthead mock-up gets a placeholder
  starting "For the design team:" that says what belongs there.
- **Every picture is checked.** A second model reads each generated picture;
  any masthead, logo, brand name, competitor name or readable or garbled
  text fails it. A failed picture is generated once more; if it fails again
  the slide keeps a placeholder with its description and a short note that
  the agent couldn't generate a clean picture. Slides with a generated
  picture say in small print that visuals are for representation only.
- **Grids** always fill every row (4 cards in 2 columns, 3 or 6 in 3), and
  cards fill the slide.
- **The right form for each text.** An overview or the idea is a short
  paragraph of one or two sentences; a list is plain bullets; a list of
  named things uses labels ("**Classroom Called Nature:** a slip contest
  before the panels"), but never inside cards, whose text is a sentence or
  two or plain bullets. No line over 35 words.
- **Highlights** (the accent background) mark only a figure, a name or
  place, or the one idea the client must remember: at most two a slide.
- **Layout variety.** At most about a third of a new deck's slides are
  two-column (one idea with its picture or takeaway). Facts go on
  at-a-glance slides (4 to 6 label: value rows), choices on options slides
  (numbered columns), parallel parts on card grids. A print or digital
  innovation gets an innovation slide (a large mock-up beside Idea, How it
  works and Why it works); a series of articles or episodes gets numbered
  rows; a scale worth showing big gets big numbers beside their story; the
  whole plan by phase fits on one campaign matrix (channels against phases).
- **HT's own IPs are marked.** The first slide about an HT property or IP
  (HT PACE, Fresh on Campus, Anokhee Club, Weekend Sorted, an HT or Mint
  summit) carries an "HT MEDIA IP" badge in its top-right corner; the
  slides that follow on the same IP don't repeat it. HT's publications
  themselves (HT, Mint, HT City) are not IPs.
- **No em dashes**, and quote slides state the idea with no attribution line.
- **Statistics** from research carry their source in small print at the
  foot of the slide, the way HT's decks cite IRS or Comscore. Slide text
  never talks about sources or names a past deck, and never presents what an
  earlier pitch proposed as something HT delivered.
- **No prices.** Decks don't mention prices or costing; HT's sales and
  pricing teams handle commercials.

## What's in a typical deck

The brief, the insight, the big idea, an overview of the solution's parts,
then each part in turn, the plan by phase, and why this works.
When the brief names several audiences, each gets its own slide and two or
more HT options. Each part keeps one name from the overview to its own
slides, and gets at least three: what it is, how it works, then its facts at a glance (the ones that matter
from platform, format, frequency, duration, geography, scale, who runs it,
how it is measured), one of them with a picture, plus samples, options or a plan as it needs. Every channel the
brief asks for gets its own part. HT's own properties and IPs are named as
HT's, and proven HT formats sit next to new ideas for the client.

Custom activations (on-ground events, nukkad natak, mystery shopper,
sampling, contests, campus and school programmes, influencer programmes,
microsites) get their own slide or two with concrete detail: where it runs and
how many, the theme or hook, how it works, who it reaches, timing, who runs
it, amplification, and how it is proved and reported. Figures the agent
estimated are marked "(indicative)".

## HT's credentials ("Why HT")

Added only on request. Fixed slides with HT's reach and rankings, copied from
HT's own credentials decks with their sources. The agent never writes HT's
figures itself. An opener and a scale slide always, plus up to two market
slides: English print, Hindi heartland, digital, Delhi NCR.

## Limits

- **One theme.** Every deck uses HT's theme; colours, fonts and layout styling
  can't be changed per deck.
- **No charts.** Figures appear as stat rows or tables.
- **HT mock-ups come from the design team.** Fill the "For the design team:"
  placeholders before a deck goes to a client. Real HT mock-ups from past
  decks, one per format, are in the agent's Drive under Brand Assets ›
  Mockup references.
- **Generated pictures are illustrative.** The check catches text and
  logos, but give pictures a quick look before a deck goes to a client.
- **Client logos** are found only when the client's own site offers a PNG or
  JPEG logo (SVG and WebP aren't used). Otherwise there is a labelled
  placeholder to fill.
