# 05 — Charts

**Rectangles and text boxes. Nothing else.**

A `SheetsChart` renders as an image ([02](02-constraints.md) §7), so it fails
the editability requirement. We compose charts from native shapes: every bar
is a rectangle, every label a text box, the whole thing wrapped in one
`groupObjects` call. The user drags it as one object and can ungroup to edit
any piece.

## The unifying abstraction

> Every chart is a list of `(label, value, emphasis)` rows placed on a linear
> scale inside a rect.

Column is bar transposed. Stacked is one row with segments. KPI is a row with
bar thickness zero. **Four families, one function.**

| Family | Shape |
|---|---|
| `bar` | rows stacked vertically, length ∝ value |
| `column` | rows across horizontally, height ∝ value |
| `stacked` | one row, segments along the track (raw or 100%) |
| `kpi` | 2–4 big numbers, no bars |

## What we cut, and why each cut is also better design

| Cut | Replaced by | Why it is not a loss |
|---|---|---|
| Axes, ticks, gridlines | Direct data labels on every bar | What good decks already do. Removes tick "nice number" algorithms, axis collision, axis titles |
| Donut / pie | 100% `stacked`, or a `kpi` | A three-slice donut is worse than a stat block. Removes all arc maths |
| Line / area | `column` (≤6 points) | The only real loss. `createLine` needs sign-flipped scale on its bounding box to control direction — fiddly for the value |
| Legends | Inline segment labels | Legends need their own layout pass |
| Auto-shrink, rotated labels | Truncate at word boundary | One rule instead of a fitting loop |

## Caps that delete edge cases

- **≤6 categories.** More → the *planner* aggregates to top-5 + "Other"
  before the spec reaches the renderer. There is no 40-row branch.
- **≤4 stack segments**, drawn from `palette.ramp`.
- **≤4 KPIs.**
- **Two colors:** `neutral` for all bars, `accent` for `emphasis`. Semantics
  from `direction_is_good` when a delta is shown. No other colour logic.
- **Scale always from zero to `max(values)`.** No domain negotiation.

## Spec

The model emits this. It never emits coordinates.

```jsonc
{
  "type": "column",
  "rows": [{"label": "Q1", "value": 12.4},
           {"label": "Q2", "value": 10.1},
           {"label": "Q3", "value": 8.6}],
  "value_format": "pct1",
  "emphasis": [2],
  "annotation": {"at": 2, "text": "post-redesign"},
  "direction_is_good": "down"
}
```

## The renderer

```python
def render_chart(spec, box, tokens) -> list[Request]:
    spec = clamp(spec)                                  # caps above
    horiz = spec.type in ("bar", "stacked")

    pitch = (box.h if horiz else box.w) / len(spec.rows)
    thick = pitch * tokens.chart.bar_fill               # 0 for kpi
    label_w = box.w * tokens.chart.label_col
    track   = box.w - label_w - box.w * tokens.chart.value_gutter
    vmax    = max(r.value for r in spec.rows) or 1

    reqs = []
    for i, row in enumerate(spec.rows):
        length = track * (row.value / vmax)
        color  = tokens.palette.accent if i in spec.emphasis \
                 else tokens.palette.neutral
        reqs += rect(bar_rect(i, length, thick, horiz, box), color)
        reqs += textbox(label_rect(i, box), row.label, tokens.type.caption)
        reqs += textbox(value_rect(i, length, box),
                        fmt(row.value, spec.value_format), tokens.type.body)
    if spec.annotation:
        reqs += textbox(anno_rect(spec.annotation.at, box),
                        spec.annotation.text, tokens.type.micro)
    return reqs + [group_objects([r.object_id for r in reqs])]
```

Pure arithmetic — no solver, no layout negotiation. **~250 lines for all four
families, roughly two days.** `stacked` varies the inner loop (segments march
along one track); `kpi` sets `thick = 0` and swaps the value style for
`type.display`.

Labels use the fixed token size; if a label exceeds `label_w` when measured,
truncate at a word boundary with an ellipsis. One rule, no rotation, no
wrapping, no shrinking.

## Unsupported types degrade, they do not fail

A ten-line lookup, applied at validation:

| Planner asks for | Renders as | Flagged to user? |
|---|---|---|
| line, area | `column` | No — equivalent at ≤6 points |
| pie, donut | `stacked` (100%) | No |
| scatter, bubble, waterfall, combo | native **table** | Yes, in `flags` |

The planner's prompt only advertises the four supported types, so
degradation should be rare. The Sheets-linked fallback returns in v2 only if
we find we actually need it — it costs a `spreadsheets` scope we do not
currently hold, and it produces an image.

## Free benefit

Because a chart is just rects with known geometry, the Tier A auditor sees
*inside* it. Label overlap, bars past the track, colliding value labels — all
caught by the same bbox check already written for text
([07](07-quality.md)). A Sheets chart would be an opaque rectangle.
