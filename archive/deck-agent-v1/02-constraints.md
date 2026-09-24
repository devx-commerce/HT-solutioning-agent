# 02 — Slides API constraints that dictate the architecture

Seven load-bearing facts. Every design decision downstream traces to one of
them. Verified against the API reference, not remembered.

### 1. There is no "apply theme" request

The full `batchUpdate` request list contains nothing that applies a theme,
master, or layout from another presentation. Branding can only be inherited
by **copying a deck that already has it** — `drive.files.copy`.

→ Every run starts by copying `TEMPLATE_FILE_ID`. `tools/deck.py` already
does this. Slides are then created with `createSlide` +
`layoutReference.layoutId` pointing at the copy's own layouts, and
`placeholderIdMappings` to get predictable object ids.

→ Corollary: **do not hardcode fonts and hex colors** in `updateTextStyle`.
Let inheritance work, so a designer editing the template propagates to every
future deck.

### 2. No autofit control

`AutofitType` is readable, but the API will not recompute shrink-to-fit —
Slides only does that in the UI. Text set via the API overflows silently.

→ We measure text ourselves (`fonttools`/PIL against the actual font at the
resolved size, with real line-wrapping) and choose the size deterministically
before writing. This is the single most common defect in API-built decks.

### 3. Images need a public URL

`createImage` requires a publicly fetchable URL. PNG/JPEG/GIF, under 50 MB,
at most 25 megapixels.

→ Generated images go to GCS; we pass a **signed URL** (short TTL). See
[06](06-images.md) and the signing-permission risk in [10](10-risks.md).

### 4. Write quota is 60 requests/min/user

600/min/project, 60/min/user. But **one `batchUpdate` counts as one write**
and can carry hundreds of sub-requests, applied atomically — if any single
request is invalid, the whole batch fails and nothing is applied.

→ Compile the whole deck into a handful of large batches. Never one call per
element. Validate the IR hard, because a batch is all-or-nothing.

### 5. `getThumbnail` is an expensive read, and its URLs expire

Default lifetime ~30 minutes, and it is billed as an expensive read against
the read quota.

→ Fetch bytes immediately, never store the URL. Budget renders: the VLM loop
is capped at two passes and pass 2 re-renders only what pass 1 changed.

### 6. Object IDs are not stable across UI edits

You may assign ids on create (5–50 chars), but the docs are explicit that you
cannot depend on an id surviving a change made in the Slides UI.

→ This is exactly why we lock the deck ([08](08-lifecycle.md)). With the
human held at `commenter`, nothing but our compiler writes to the deck, so
ids stay valid and the IR remains the true state. Locking is not a UX
preference; it removes a whole class of bug.

### 7. A `SheetsChart` is an image

The page element exposes `contentUrl` — "the URL of an image of the embedded
chart." Linked charts are refreshable from the source sheet, but they are not
shape-editable in Slides.

→ Charts are composed from native shapes instead. See [05](05-charts.md).

## Two things that do work well

- **Speaker notes are fully writable.** Find the slide's
  `slideProperties.notesPage.notesProperties.speakerNotesObjectId` and insert
  text; the API creates the shape if it is missing.
- **`replaceImage`** swaps an image in place, preserving the object id,
  position, and size. Use it for image refinement instead of
  delete-and-recreate.
