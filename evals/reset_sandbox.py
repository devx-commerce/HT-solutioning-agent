"""Reset the eval sandbox before a run.

    python -m evals.reset_sandbox                    # empty the briefs table
    python -m evals.reset_sandbox --seed-refinement  # ...then seed the refinement decks

Only ever touches the eval dataset and the eval Drive folder, whatever the
shell's BQ_DATASET or DECK_FOLDER_ID say, so it can never clear or write
production.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
EVAL_DATASET = "solutioning_agent_eval"
EVAL_DECK_FOLDER_ID = "11THzL-7Z1Cw4PdlFY6Ct4VtMR74amTZd"

# Before the agent's modules are imported: they read these at import time.
os.environ["BQ_DATASET"] = EVAL_DATASET
os.environ["DECK_FOLDER_ID"] = os.environ.get("EVAL_DECK_FOLDER_ID", EVAL_DECK_FOLDER_ID)
os.environ["DECK_READER_DOMAIN"] = ""
load_dotenv(HERE.parent / "agents" / "solutioning_agent" / ".env")

from google.cloud import bigquery  # noqa: E402


def clear() -> None:
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    bigquery.Client(project=project).query(
        f"DELETE FROM `{project}.{EVAL_DATASET}.briefs` WHERE TRUE"
    ).result()
    print(f"cleared {EVAL_DATASET}.briefs")


def seed_refinement() -> None:
    """One published deck per seed, built from the fixture without generating images."""
    from agents.solutioning_agent.tools import deck as deck_tools
    from agents.solutioning_agent.tools import visuals

    spec = json.loads((HERE / "refinement_cases.json").read_text())
    fixture = json.loads((HERE / "fixtures" / "pentonic_deck.json").read_text())
    for seed in spec["seeds"]:
        deck = copy.deepcopy(fixture)
        deck["slides"][0]["eyebrow"] = f"HT Media × {seed['client_name']}"
        deck["slides"][0]["heading"] = seed["title"]
        # Placeholders are kept as they are: generating pictures for a fixture
        # is slow, costs money, and isn't what these cases test.
        with patch.object(visuals, "fill_images", return_value={"generated": 0, "placeholders": 0}):
            result = deck_tools.build_solution_deck(
                json.dumps(deck), seed["client_name"], seed["brief_id"], "")
        if "error" in result:
            sys.exit(f"seeding {seed['brief_id']} failed: {result}")
        print(f"seeded {seed['brief_id']:28} {seed['client_name']:20} {result['link']}")


if __name__ == "__main__":
    clear()
    if "--seed-refinement" in sys.argv:
        seed_refinement()
