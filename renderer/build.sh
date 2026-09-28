#!/usr/bin/env bash
# Build the slim renderer into renderer/dist from a presentation-md checkout.
#
#   PRESENTATION_MD=~/Desktop/codebase/presentation-md renderer/build.sh
#
# Bundles only what the PPTX path imports (esbuild tree-shakes the rest of
# presentation-md away) and copies only the ht-media theme chain. The
# presentation-md commit is recorded in dist/BUILD_INFO so a deployed image can
# be traced back to the exact renderer code.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
PMD="${PRESENTATION_MD:?set PRESENTATION_MD to a presentation-md checkout}"
OUT="$HERE/dist"

# presentation-md's own dist/ must be current; it is what gets bundled.
if [[ "${SKIP_PMD_BUILD:-0}" != "1" ]]; then
  (cd "$PMD/packages/core" && pnpm build >/dev/null)
  (cd "$PMD/packages/export" && pnpm build >/dev/null)
fi

ESBUILD="${ESBUILD:-$(find "$PMD/node_modules/.pnpm" -path "*@esbuild+*/bin/esbuild" -type f | head -1)}"
[[ -x "$ESBUILD" ]] || { echo "esbuild binary not found under $PMD/node_modules" >&2; exit 1; }

rm -rf "$OUT" && mkdir -p "$OUT/themes"
"$ESBUILD" "$HERE/src/server.mjs" \
  --bundle --platform=node --format=esm --target=node20 \
  --outfile="$OUT/server.mjs" \
  --alias:@presentation-md/core="$PMD/packages/core/dist" \
  --alias:@presentation-md/export="$PMD/packages/export/dist/index.js" \
  --resolve-extensions=.js,.mjs,.json \
  --banner:js="import { createRequire as __cr } from 'node:module'; const require = __cr(import.meta.url);" \
  --log-level=warning

# The theme chain ht-media extends, and nothing else.
for t in ht-media blue-professional; do
  mkdir -p "$OUT/themes/$t" && cp "$PMD/packages/themes/$t/theme.json" "$OUT/themes/$t/"
done
mkdir -p "$OUT/themes/default-tech" && cp "$PMD/packages/core/themes/default-tech/theme.json" "$OUT/themes/default-tech/"

{
  echo "presentation-md $(git -C "$PMD" rev-parse --short HEAD) ($(git -C "$PMD" rev-parse --abbrev-ref HEAD))"
  git -C "$PMD" diff --quiet HEAD -- packages/core packages/export packages/themes || echo "WARNING: presentation-md had uncommitted changes"
  echo "built $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$OUT/BUILD_INFO"

echo "renderer built: $(du -sh "$OUT/server.mjs" | cut -f1) bundle, themes: $(ls "$OUT/themes" | tr '\n' ' ')"
cat "$OUT/BUILD_INFO"
