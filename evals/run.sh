#!/usr/bin/env bash
# Run the ADK eval set against the agent in this checkout.
#
#   evals/run.sh                          the real briefs, every case, once
#   evals/run.sh briefs pentonic,uber     just these cases
#   evals/run.sh briefs "" 3              every case, three times
#   evals/run.sh refinement               the scripted revision chats
#
# The agent runs locally with its real tools and models, pointed at a sandbox
# so evals never touch production: the solutioning_agent_eval BigQuery
# dataset, the "Eval Decks" Drive folder, and no domain sharing. See
# evals/README.md for what each metric means and how to read the results.
set -euo pipefail
cd "$(dirname "$0")/.."

SET="${1:-briefs}"
CASES="${2:-}"
REPEATS="${3:-1}"
case "$SET" in
  briefs)     CONFIG=evals/test_config.json;       RESET="" ;;
  refinement) CONFIG=evals/refinement_config.json; RESET="--seed-refinement" ;;
  *) echo "unknown eval set '$SET': use briefs or refinement" >&2; exit 2 ;;
esac

export BQ_DATASET=solutioning_agent_eval
export SOLUTIONING_RUN=eval           # billing labels: eval spend, not live use
export DECK_FOLDER_ID="${EVAL_DECK_FOLDER_ID:-11THzL-7Z1Cw4PdlFY6Ct4VtMR74amTZd}"
export DECK_READER_DOMAIN=""          # eval decks are never shared
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"

# A renderer: the deployed one if RENDER_URL is set (Cloud Build), else a
# local one from renderer/dist (needs Node and renderer/build.sh run once).
if [[ -z "${RENDER_URL:-}" ]]; then
  export RENDER_URL=http://localhost:8791
  if ! curl -sf "$RENDER_URL/health" >/dev/null 2>&1; then
    PORT=8791 node renderer/dist/server.mjs >/tmp/solutioning-eval-renderer.log 2>&1 &
    trap 'kill $! 2>/dev/null || true' EXIT
    for _ in $(seq 20); do curl -sf "$RENDER_URL/health" >/dev/null 2>&1 && break; sleep 0.5; done
  fi
fi

EVAL_SET="evals/$SET.evalset.json${CASES:+:$CASES}"
for run in $(seq 1 "$REPEATS"); do
  # Each case's brief_id is fixed, so a deck left from an earlier run would
  # be found by lookup_deck and edited instead of built afresh; refinement
  # cases also need their decks back in their starting state.
  python -m evals.reset_sandbox $RESET
  echo "=== $SET eval run $run of $REPEATS"
  adk eval agents/solutioning_agent "$EVAL_SET" \
    --config_file_path "$CONFIG" \
    --print_detailed_results \
    ${EVAL_STORAGE_URI:+--eval_storage_uri "$EVAL_STORAGE_URI"}
done
