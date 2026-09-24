# 08 — Deck lifecycle and conservative refinement

The failure this chapter exists to prevent: someone says *"make the chart on
slide 4 blue"* and the agent rebuilds the deck, losing their work.

Two mechanisms, in order of importance. The first one removes most of the
problem rather than managing it.

## 1 — The deck is locked while the agent owns it

`sales.agent@hindustantimes.com` already owns every deck we create
(`oauth_creds.py`, the shared identity). We keep it that way and grant the
human **`commenter`**, not `writer`.

```python
drive.permissions().create(fileId=deck_id, body={
    "type": "user", "emailAddress": requester, "role": "commenter",
}).execute()

drive.files().update(fileId=deck_id, body={
    "copyRequiresWriterPermission": False,   # keep "Make a copy" available
}).execute()
```

The human cannot edit. They *can* comment, and they can take a copy. Because
`sales.agent@` owns the file, they cannot lift the restriction either.

### Why permissions rather than Drive content restrictions

Drive supports a real lock — `contentRestrictions[].readOnly = true` with a
`reason` string, and Google documents Slides support explicitly ("all content
in Slides, as well as the order and number of the slides"). It shows a lock
icon and the reason in the UI, which is the clearest possible signal.

**But `readOnly` also blocks comments** — "comments may not be added or
modified" — and it blocks our own writes, so every turn would need an
unlock/edit/relock cycle.

| | `commenter` permission | `contentRestrictions.readOnly` |
|---|---|---|
| Human can edit | no | no |
| Human can **comment** | **yes** | no |
| Agent edits | free | needs unlock/relock per turn |
| UI signal | "Comment only" mode, toolbar hidden | lock icon + custom reason |
| Human can bypass | only if they own it — they do not | any writer, unless `ownerRestricted` |
| **Use for** | **default working state** | **FINAL freeze** |

So: permissions for the working state, content restriction for the approved
freeze. Both, at different times.

### What this deletes

**The entire live-state reconciler.** No diffing the live deck against the
IR, no `HUMAN_EDITED` / `HUMAN_ADDED` classification, no protected-element
negotiation, no "your edit conflicts with mine". Nothing but our compiler can
write to the deck, so **the IR is guaranteed to be the true state**.

It also makes the object-id instability in [02](02-constraints.md) §6 moot:
ids only drift when a human edits in the UI, which is now impossible.

This is worth roughly a week of build time and a permanent class of bugs.

## 2 — Lifecycle

```
DRAFTING    sales.agent@ owns · nobody else has access
   │
   ▼
REVIEW  ◄────┐   user = COMMENTER · copy allowed
   │         │   agent edits freely · human comments only
   │ refine ─┘
   │
   ├─► FINAL     + contentRestrictions{readOnly, ownerRestricted,
   │               reason: "Approved <date> — ask the agent to reopen"}
   │             Hard freeze. Nobody edits, including us.
   │
   └─► HANDOFF   user promoted to WRITER
                 ONE-WAY. Deck marked HUMAN_OWNED. The agent refuses
                 further refinement:
                 "You've taken over editing — I can start a fresh
                  version from your changes, but I won't edit this one."
```

`finalize_deck(deck_id, mode)` performs the transition and writes the new
state to `decks`.

### Making it obvious, in the order the user hits it

1. The deck opens in **"Comment only"** mode — toolbar gone, unmistakable.
2. The link handover message says it plainly:
   > This deck is agent-managed — comment on any slide, or tell me here what
   > to change. To edit it by hand, use **File → Make a copy**, or say *"let
   > me take over"* and I'll hand you the keys (I'll stop editing after that).
3. On `FINAL`, the lock icon and reason string appear.

## 3 — Comments as the refinement channel

Because `commenter` preserves commenting, the human's natural "change this"
gesture is a comment, and it carries its own target.

**Verified caveat:** Google editors treat comments created via the Drive API
as *unanchored*, and anchor handling for Slides is unreliable. **Do not parse
anchors.**

**The fallback needs no anchors and is more robust anyway.**
`comments.list` returns `quotedFileContent` — the text the human highlighted.
Match that string against text nodes in the deck IR to resolve slide and
element exactly:

```
comment: "this number is stale"   quotedFileContent: "78%"
  → IR text-node match → slides[key=s3].slots.stat.value
  → dimension = DATA, blast radius = 1 element → act
```

See [10](10-risks.md) for the one-hour spike on whether the Slides API's
developer-preview comment requests carry proper slide anchors — if they do we
also get in-thread replies, which is a better UX than a chat message.

## 4 — Conservative refinement

**Every refinement is a patch to the deck IR. Everything not named in the
patch is frozen.** Never a regeneration.

### Step 1 — resolve scope on two axes

**Target (which):** explicit reference ("slide 4", "the pricing slide") →
comment `quotedFileContent` match → semantic search over IR headlines →
*unresolved*.

**Dimension (what):** a closed taxonomy, because each has a different blast
radius, a different cost, and a different tool.

| Dimension | Radius | Cost | Regenerates assets? |
|---|---|---|---|
| `COPY` — headline, body, notes | 1 element | trivial | no |
| `DATA` — values, chart type, labels | 1 chart group | trivial | chart only |
| `LAYOUT` — archetype swap, position, density | 1 slide | low | no |
| `IMAGERY` — regenerate, restyle, replace, remove | 1 image | **high** | yes |
| `STYLE` — palette, type scale, template | **whole deck** | high | possibly all |
| `STRUCTURE` — add, remove, reorder, split, merge | **deck** | medium | no |

### Step 2 — blast-radius policy

```
resolved target + single dimension + ≤3 slides
    → ACT. Report what changed afterwards.

>3 slides, or STYLE, or STRUCTURE
    → SHOW THE PLAN FIRST (slides in, and slides explicitly NOT touched),
      then act.

destructive — delete slides, swap template, drop images
    → CONFIRM.

target unresolved, or dimension genuinely ambiguous
    → ASK ONE QUESTION, 2–4 concrete options. Never "what would you like?"
```

**Ask only when the readings lead to materially different work.** Otherwise
take the narrowest plausible reading, do it, and state the assumption in one
line. The user can always widen; they cannot easily un-widen.

| User says | Right move |
|---|---|
| "make slide 4 punchier" | **Act.** Narrowest reading: headline + body on slide 4. Chart, image, layout untouched. Say so |
| "the chart is wrong" | **Act** if one chart is in scope and the defect is evident. Ask only if data-vs-charttype is genuinely unclear |
| "make it more visual" | **Ask.** Add images / bullets→chart / swap layouts differ 10× in radius and cost |
| "punch up the whole deck" | **Plan first.** Enumerate affected slides, name what stays frozen |
| "use our new brand colours" | **Plan + confirm.** `STYLE` is deck-wide and may mean a new template |
| "slide 7's image doesn't fit the others" | **Act.** `IMAGERY`, one slide, regenerate with the deck style token |

### Step 3 — minimal-edit compilation

Prefer in-place mutation over delete-and-recreate, so object ids, comments,
and any animations survive.

| Change | Use | Not |
|---|---|---|
| Text | `deleteText` + `insertText` on the same shape | delete shape, recreate |
| Image swap | **`replaceImage`** | `deleteObject` + `createImage` |
| Recolour, restyle | `updateShapeProperties`, `updateTextStyle` | rebuild |
| Move, resize | `updatePageElementTransform` | rebuild |
| Chart values | mutate the existing group's rects | rebuild the group |

Only an archetype swap justifies rebuilding a slide's elements — and even
then the slide object and its speaker notes survive.

### Step 4 — report the diff, keep the journal

Every turn returns: what changed · what was deliberately left alone · what it
cost. Every turn writes a row to `deck_revisions` with the IR before and
after, which makes *"undo that"* a single operation and a bad refinement
explainable afterwards.

### Step 5 — no VLM on refinement turns

Tier A runs on touched slides. The VLM loop runs only if the user asked for a
visual check, or if the turn changed more than half the deck. Polish is not
free and the user did not ask for it.
