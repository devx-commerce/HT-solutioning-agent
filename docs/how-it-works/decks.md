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
   wrote for it.
4. **The renderer** turns the deck into PowerPoint in HT's theme.
5. **Drive** converts it to Google Slides, in the "Agent Generated Decks"
   folder, read-only for `settings.deck_reader_domains`.
6. **The deck is saved** in BigQuery (`briefs`) with its link.

## The rules (HT master deck)

- **Structure:** cover, then the brief, then the solution, then next steps,
  then the closing slide. If HT's credentials ("Why HT") slides are added,
  they go straight after the brief.
- **Length:** 7 to `settings.max_slides` slides (credentials slides don't
  count).
- **Layouts:** only HT's approved layouts, each with character limits per
  field so text never overflows.
- **Pictures:** a new deck needs at least `settings.min_images`. Pictures go on
  two-column slides (right half) and full-slide image slides only, never
  inside cards.
- **Grids** always fill every row (4 cards in 2 columns, 3 or 6 in 3).
- **No em dashes**, and quote slides state the idea with no attribution line.
- **Statistics** appear only with their source on the slide.
- **No prices.** Next steps say commercials will come from HT's pricing team.

## What's in a typical deck

The brief, the insight, the big idea, an overview of the solution's parts,
then each part in turn, a plan or timeline, a deliverables table, next steps.
Each part keeps one name from the overview to its own slides.

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
- **Generated pictures are illustrative.** They can show invented signage or
  imperfect details; check them before a deck goes to a client.
- **Client logos** are found only when the client's own site offers a PNG or
  JPEG logo (SVG and WebP aren't used). Otherwise there is a labelled
  placeholder to fill.
