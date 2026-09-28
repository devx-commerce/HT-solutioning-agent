// HT deck renderer: Deck JSON in, .pptx out.
//
// A slim build of presentation-md's PPTX path only (schema validation, theme
// loading, PPTX export) with the ht-media theme and its parents. No HTML,
// PDF, Studio, import or MCP code, and none of the other themes. See
// renderer/README.md for why this is a separate Cloud Run service.
//
// Responses are shaped so the agent can tell "your deck is wrong, fix it"
// from "I couldn't render right now, retry":
//   200  application/vnd...presentation  the deck
//   422  {"error": "invalid", "details": [...]}   deck rejected, fix and resend
//   413  {"error": "too_large"}                   deck body over the limit
//   500  {"error": "render_failed", "message"}    renderer fault, not the deck's

import { createServer } from "node:http";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { validateDeckJson } from "@presentation-md/core/validate-deck";
import { loadTheme } from "@presentation-md/core/theme-loader";
import { deckToPptxBuffer } from "@presentation-md/export";

const HERE = dirname(fileURLToPath(import.meta.url));
const THEMES_DIR = process.env.THEMES_DIR ?? join(HERE, "themes");
const THEME = "ht-media";
const PORT = Number(process.env.PORT ?? 8080);
// Decks carry embedded images (the HT logo, photos) as data URIs.
const MAX_BODY = 25 * 1024 * 1024;
const PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation";

function send(res, status, body, type = "application/json") {
  const payload = type === "application/json" ? JSON.stringify(body) : body;
  res.writeHead(status, { "content-type": type, "content-length": Buffer.byteLength(payload) });
  res.end(payload);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (c) => {
      size += c.length;
      if (size > MAX_BODY) {
        reject(Object.assign(new Error("too_large"), { tooLarge: true }));
        req.destroy();
      } else {
        chunks.push(c);
      }
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf-8")));
    req.on("error", reject);
  });
}

async function render(deckJson) {
  const validation = validateDeckJson(deckJson);
  if (!validation.valid) return { status: 422, body: { error: "invalid", details: validation.errors } };
  const deck = JSON.parse(deckJson);
  const name = deck.meta?.theme ?? THEME;
  if (name !== THEME) {
    return { status: 422, body: { error: "invalid", details: [`/meta/theme must be "${THEME}", got "${name}"`] } };
  }
  const theme = await loadTheme(THEME, { themesDir: THEMES_DIR });
  const warnings = [];
  const pptx = await deckToPptxBuffer(deck, theme, {
    attribution: false,
    // Images arrive embedded or as placeholders; nothing is fetched.
    prefetchImages: false,
    onWarn: (m) => warnings.push(m),
  });
  if (warnings.length) console.log(JSON.stringify({ severity: "INFO", event: "render.warnings", warnings }));
  return { status: 200, body: pptx };
}

export const server = createServer(async (req, res) => {
  if (req.method === "GET" && req.url === "/healthz") return send(res, 200, { ok: true });
  if (req.method !== "POST" || req.url !== "/render") return send(res, 404, { error: "not_found" });
  const started = Date.now();
  try {
    const out = await render(await readBody(req));
    console.log(JSON.stringify({ severity: "INFO", event: "render.done", status: out.status, ms: Date.now() - started }));
    return out.status === 200 ? send(res, 200, out.body, PPTX_MIME) : send(res, out.status, out.body);
  } catch (err) {
    if (err.tooLarge) return send(res, 413, { error: "too_large", message: `Deck body is over ${MAX_BODY} bytes.` });
    console.error(JSON.stringify({ severity: "ERROR", event: "render.failed", message: String(err?.stack ?? err) }));
    return send(res, 500, { error: "render_failed", message: String(err?.message ?? err) });
  }
});

if (process.env.RENDERER_NO_LISTEN !== "1") {
  server.listen(PORT, () => console.log(JSON.stringify({ severity: "INFO", event: "renderer.listening", port: PORT })));
}
