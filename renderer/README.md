# HT deck renderer

Deck JSON in, `.pptx` out. A private Cloud Run service the solutioning agent
calls from `tools/deck.py` when `RENDER_URL` is set.

It exists because the agent runs on Vertex AI Agent Engine, a managed Python
runtime with no Node, and presentation-md's renderer is Node. It is its own
service rather than part of `solutioning-agent`, so a renderer change never
redeploys the email pipeline, rendering never competes with `/work` for its
three capped instances, and a renderer crash only fails deck builds.

## What is in it

Only presentation-md's PPTX path: schema validation, theme loading and PPTX
export, bundled into one file (about 1.2 MB). No HTML, PDF, Studio, import or
MCP code. Three theme files: `ht-media` and the two themes it extends
(`blue-professional`, which gives it its frame and card style, and
`default-tech`, which supplies base values). Any other theme is rejected.

## API

| Request | Response |
|---|---|
| `POST /render`, body Deck JSON | `200` the `.pptx` |
| | `422 {"error":"invalid","details":[...]}` the deck is wrong; fix it |
| | `413` body over 25 MB |
| | `500 {"error":"render_failed"}` renderer fault, not the deck's |
| `GET /healthz` | `200 {"ok":true}` |

`deck.py` retries anything that isn't a 200 or a deck error (connection
errors, timeouts, 401/403, 429, 5xx) four times over about 25 seconds, then
tells the agent the renderer is unavailable and **not** to change the deck.

## Build and deploy

```bash
PRESENTATION_MD=~/Desktop/codebase/presentation-md renderer/build.sh
gcloud run deploy solutioning-agent-renderer --source renderer/ \
  --region us-central1 --no-allow-unauthenticated \
  --labels app=solutioning-agent --memory 1Gi --timeout 120
gcloud run services add-iam-policy-binding solutioning-agent-renderer \
  --region us-central1 --role roles/run.invoker \
  --member serviceAccount:<Agent Engine service account>
```

`dist/BUILD_INFO` records the presentation-md commit the bundle was built
from. Build from a clean checkout: the script warns if it isn't.

Run locally: `PORT=8791 node renderer/dist/server.mjs`, then
`RENDER_URL=http://localhost:8791` (no identity token is sent to localhost).
