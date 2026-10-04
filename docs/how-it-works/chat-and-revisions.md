# Chat and revisions

In Gemini Enterprise, people talk to the same agent that builds decks from
email. Every change goes through the same rules as a new deck, and is
published to the same Slides file, so links already shared keep working.

## What it can do

| Request | What happens |
|---|---|
| Build a deck from a pasted brief | Researches and builds a new deck, as from email. If the client already has decks, it lists them and asks whether to change one or build a new one. |
| Find a deck | Looks it up by client name (partial names work: "Tata" finds "Tata Sampann"). If several match, it lists each with its title, when it last changed and its link, and asks which one. |
| Change text | Any text on any slide: headings, body, cards, table rows, timeline steps, figures. |
| Add a slide | In any approved layout, at any position, written to the same rules as a new deck. |
| Delete or move a slide | Any slide except the cover (always first) and the closing slide (always last). |
| Several changes at once | All applied together and published once; if one can't be made, none are. |
| New picture on a slide | A picture is generated from a description; only that slide's picture changes. |
| Place your own image | Attach it in chat and say where. It is checked first (below). |
| Replace the client logo | Attach the logo and say "use this as the client logo". |
| Add HT's credentials slides | Opener and scale slides, plus up to two market slides of your choice. Asking again replaces them. |

After each change it says what changed on which slide, and gives the link.

## Your own images

An attached image is used only if it fits; otherwise nothing changes and the
agent says exactly why, all reasons at once, and what would work.

| Requirement | Slide picture (right half) | Full-slide picture | Client logo |
|---|---|---|---|
| Format | PNG or JPEG | PNG or JPEG | PNG or JPEG |
| Size | Under 4 MB | Under 4 MB | Under 4 MB |
| Minimum pixels | 550 × 600 | 1280 × 720 | 120 on the longer side |
| Shape | Close to 12:13 (nearly square) | Close to 16:9 (widescreen) | Any |

"Close" means filling the frame would crop away no more than about a fifth
of the picture.

## What it can't do

- **Design.** Theme, colours, fonts, layout styling and logo placement are
  fixed. Content changes only.
- **Charts, video, animation or transitions.**
- **Prices.** It won't add rates, costs or discounts.
- **Undo.** There is no version history to return to; ask for the opposite
  change.
- **Start over.** It revises an existing deck; for a fresh one, ask for a new
  deck.
- **Edits by email.** Replies to the "deck drafted" email aren't read.
- **Hand-edited copies.** Decks are view-only. Anyone editing by hand works in
  their own copy (File > Make a copy), which later chat changes don't reach.
- **Break the rules.** A change that would break the deck rules (too many
  slides, text over a limit, a picture on a layout without a picture slot) is
  refused, and the agent explains why.

## Who can do what

Anyone with Gemini Enterprise access to the app can find and change any deck
the agent has built. Decks themselves are read-only in Drive for
`settings.deck_reader_domains`, and only the agent account can change the
originals.
