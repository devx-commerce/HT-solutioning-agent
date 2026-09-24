# 06 — Images

Generated imagery is for **backgrounds, metaphors, and section dividers**.
Never for text, never for data. Anything a rasteriser touches stops being
editable, which is the one property this whole system exists to preserve.

## Pipeline

```
image_brief (in IR)
   → content hash → cache hit? → reuse gs:// object
   → Gemini 3 Pro Image (Nano Banana Pro)
   → post-process (crop to slot aspect, compress)
   → gs://{ASSET_BUCKET}/img/{sha256}.png
   → signed URL (short TTL)
   → createImage
   → updatePageElementAltText
```

```jsonc
"visual": {
  "type": "generated_image",
  "role": "section_divider",     // full_bleed | contained | section_divider | texture
  "brief": "abstract layered glass planes, cool neutral, shallow depth",
  "aspect": "16:9",
  "safe_zone": "left_40pct",     // where text will sit — keep it quiet there
  "alt": "Abstract layered glass forms suggesting system layers"
}
```

## The four rules that decide whether it looks professional

### 1. One style token per deck

Derive a fixed style preamble once from the brand tokens — palette words,
medium, lighting, composition constraints — and prefix **every** brief with
it. Optionally pass the first accepted image back as a reference for the
rest.

Without this you get a deck of visually unrelated images, which is the single
most recognisable tell of an AI-generated deck. This is one string in
`design_tokens.json` and it does more for perceived quality than anything
else in this file.

### 2. No text inside generated images

Nano Banana Pro renders text well, which is exactly the trap. Text baked into
a raster cannot be edited, translated, corrected, or restyled. **All text goes
in real text boxes on top of the image.** Every brief carries an explicit
"no text, no labels, no words" instruction.

### 3. No alpha channel exists

Gemini image models output flat RGB. There is no transparency, so no cutout
objects, no logos-on-any-background, no floating product shots.

Design around it: full-bleed backgrounds, images contained in a rectangle,
section dividers. (Alpha can be recovered by generating the same image on
white and on black and comparing — v2, if ever.)

Real logos come from `DeckBrief.assets`, pre-approved, never generated.

### 4. Contrast guard, applied automatically

Where text overlays an image, compute mean luminance and variance in the
text's bbox region. If it fails WCAG AA, insert a scrim — a semi-transparent
brand-colored rectangle between image and text — and re-check.

Deterministic, cheap, and it fixes the most common defect on image slides.
It runs in the compiler, not the VLM loop.

## Caching

Images are content-addressed by `sha256(style_token + brief + aspect)`. A
refinement that does not change the brief reuses the object and costs
nothing. This matters: regeneration during refinement is where image spend
quietly runs away, and the refinement policy in [08](08-lifecycle.md)
therefore treats `IMAGERY` as a high-cost dimension.

## Accessibility

`updatePageElementAltText` on every image, from `visual.alt`. Free to do, and
it also gives the VLM judge a stated intent to compare the rendering against.

## Cost

Roughly $0.13 per image. A 10-slide deck with 3 images is about $0.40 —
immaterial next to the model calls, *provided* the cache works and the
refinement loop is conservative about regenerating.
