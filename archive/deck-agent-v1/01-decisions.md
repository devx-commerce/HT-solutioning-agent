# 01 — Build vs. integrate

**Verdict: build, on Google's open stack. Integrate only at the transport
layer.**

## The landscape, as it actually is

| Option | Editable Slides? | Programmable? | Verdict |
|---|---|---|---|
| **Gemini in Google Slides** (GA Jun 2026) | Yes — native, editable, outline-first, style-matched from a reference deck, Drive-grounded | **No public API** | Best quality available. Unusable as a component |
| **Gemini app → Slides** | Yes | No API | Same wall |
| **Official Workspace MCP** (`slidesmcp.googleapis.com/mcp/v1`) | Yes, thin CRUD | Yes — OAuth, callable from GE, Gemini CLI, ADK | Reusable transport. No design intelligence |
| **Google Slides API v1** | Yes, everything | Yes | **The foundation.** All real work is `presentations.batchUpdate` |
| **ADK** (Apache 2.0) | — | Yes | **Our framework.** Already in `requirements.txt` |
| **Agent Engine** | — | Yes | **Our runtime.** Already the deploy target (README step 9) |
| **Google Agent Skills** (`github.com/google/skills`) | — | Yes | Real standard, **no Slides/Workspace skill exists** |
| **GE Workflow Builder** | via MCP | Yes | Right for orchestration, wrong altitude for deck design |

The finding that settles it: Google shipped exactly this product, into the
Slides UI only. The June 2026 announcement describes outline-review-then-
generate, Drive grounding, reference-deck style matching, fully editable
output — and mentions **no API surface**. It is also English-US only and
license-gated. We cannot integrate the good one. We can only rebuild its
shape.

## Reused, not written

- **`googleworkspace/md2googleslides`** — Google's own Markdown→Slides tool.
  Not an agent, but a working reference for layout selection and
  `batchUpdate` compilation. Read it; steal the patterns, not the tool.
- **`taylorwilsdon/google_workspace_mcp`** (MIT) — 7 Slides tools, mature
  auth. Only if we later want an MCP boundary; direct SDK calls are simpler
  and are what `tools/deck.py` already does.
- **PPTEval rubric** (content / design / coherence, 1–5) — our VLM judge
  prompt. Its authors measured Pearson 0.71 against human raters, far above
  ROUGE-L and friends.
- **AeSlides' four verifiable checks** (aspect ratio, whitespace, collision,
  imbalance) — our Tier A auditor. Their result matters: **rule-based
  metrics beat VLM detection on both accuracy and cost**, and post-hoc visual
  reflection was expensive for marginal gain. That is why our VLM loop is
  terminal and capped.
- **`fonttools` / PIL** — text measurement, because the Slides API will not
  do it for us (see [02](02-constraints.md)).

## Written by us

Deck IR schema · archetype library + branded template deck · layout solver
with real font metrics · IR→`batchUpdate` compiler · chart renderer ·
image pipeline · Tier A auditor · VLM judge and revision loop · refinement
scope resolver · deck lifecycle and locking.

## Rejected

**PPTX bridge** (`python-pptx` → Drive import with
`mimeType: application/vnd.google-apps.presentation`). It does produce
editable Slides and would let us preview offline. Rejected because it loses
theme inheritance, drifts on conversion, and cannot do targeted incremental
edits — which kills the refinement loop, the thing we most need to be good.

**Sheets-linked charts as the default.** A `SheetsChart` page element is a
linked chart *rendered as an image* — the API exposes it as a `contentUrl`.
Editing means opening the source spreadsheet and clicking Update. That fails
the "editable, not images" requirement. Kept only as a possible v2 escape
hatch; see [05](05-charts.md).

## Sources

Gemini in Slides GA · Slides API overview, `Request` reference, add-chart,
add-image, notes, `getThumbnail`, usage limits · Drive content restrictions ·
Drive manage-comments · Workspace MCP codelab · `google/skills` ·
PPTAgent (arXiv 2501.03936) · AeSlides (arXiv 2604.22840) · Nano Banana Pro.
