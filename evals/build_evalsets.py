"""Build the eval sets: briefs (real emails) and refinement (scripted chats).

Each case's email is read from sales.agent@'s inbox and turned into exactly
the message the email pipeline sends the agent (app.pipeline.prompts), so
the eval measures what production does. Re-run after adding a case or
editing a rubric in cases.json or refinement_cases.json:

    python -m evals.build_evalsets               # both
    python -m evals.build_evalsets refinement    # just the chats (no Gmail needed)

Needs the same credentials as the agent (it reads the agent's OAuth token
from Secret Manager). The built eval set is committed; running the evals
does not need Gmail.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / "agents" / "solutioning_agent" / ".env")

from google.adk.evaluation.eval_case import EvalCase, Invocation, SessionInput  # noqa: E402
from google.adk.evaluation.eval_rubrics import Rubric, RubricContent  # noqa: E402
from google.adk.evaluation.eval_set import EvalSet  # noqa: E402
from google.genai import types  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402

from agents.solutioning_agent.oauth_creds import get_credentials  # noqa: E402
from agents.solutioning_agent.tools.research import EVAL_HIDDEN_DECKS_KEY  # noqa: E402
from app.pipeline import mail_utils, prompts  # noqa: E402

HERE = Path(__file__).resolve().parent
APP_NAME = "solutioning_agent"
# The agent's own messages in a thread (a reply, a re-forward) are never
# part of a brief; feeding them back would hand the agent its earlier answer.
AGENT_ADDRESS = "sales.agent@hindustantimes.com"


def _thread_text(gmail, thread_id: str) -> tuple[str, str]:
    """(subject, thread text) in fetch_thread_context's format, minus the agent's messages."""
    thread = gmail.users().threads().get(userId="me", id=thread_id, format="full").execute()
    blocks, subject = [], ""
    for msg in thread.get("messages", []):
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        subject = subject or headers.get("Subject", "")
        if AGENT_ADDRESS in headers.get("From", ""):
            continue
        body = mail_utils._walk_for_plain_text(msg["payload"])
        blocks.append(f"From: {headers.get('From', '')}\nDate: {headers.get('Date', '')}\n\n{body}")
    return subject, "\n\n---\n\n".join(blocks)


def build_refinement() -> None:
    """refinement.evalset.json: scripted chats against the seeded decks."""
    spec = json.loads((HERE / "refinement_cases.json").read_text())
    eval_cases = []
    for case in spec["cases"]:
        conversation = []
        for n, turn in enumerate(case["turns"], 1):
            parts = [types.Part(text=turn["text"])]
            if turn.get("image"):
                parts.append(types.Part(inline_data=types.Blob(
                    mime_type="image/jpeg", data=(HERE / turn["image"]).read_bytes())))
            conversation.append(Invocation(
                invocation_id=f"{case['eval_id']}-{n}",
                user_content=types.Content(role="user", parts=parts),
            ))
        eval_cases.append(EvalCase(
            eval_id=case["eval_id"],
            conversation=conversation,
            session_input=SessionInput(app_name=APP_NAME, user_id="eval"),
            rubrics=[
                Rubric(rubric_id=r["id"], rubric_content=RubricContent(text_property=r["text"]),
                       type=r["type"])
                for r in case["rubrics"]
            ],
        ))
        print(f"{case['eval_id']:24} {len(case['turns'])} turn(s)  {len(case['rubrics'])} rubrics")
    _write(EvalSet(
        eval_set_id="refinement",
        name="Refinement loop",
        description="Scripted chats revising seeded decks: structure edits, pictures, uploads, ambiguity.",
        eval_cases=eval_cases,
        creation_timestamp=time.time(),
    ), "refinement.evalset.json")


def _write(eval_set: EvalSet, name: str) -> None:
    out = HERE / name
    out.write_text(eval_set.model_dump_json(indent=2, exclude_none=True))
    print(f"wrote {out.relative_to(ROOT)}")


def build_briefs() -> None:
    cases = json.loads((HERE / "cases.json").read_text())["cases"]
    gmail = build("gmail", "v1", credentials=get_credentials())
    eval_cases = []
    for case in cases:
        subject, text = _thread_text(gmail, case["gmail_thread_id"])
        brief_id = f"eval-{case['eval_id']}"
        eval_cases.append(EvalCase(
            eval_id=case["eval_id"],
            conversation=[Invocation(
                invocation_id=f"{case['eval_id']}-1",
                user_content=types.Content(
                    role="user",
                    parts=[types.Part(text=prompts.brief_request(subject, text, brief_id))],
                ),
            )],
            session_input=SessionInput(
                app_name=APP_NAME, user_id="eval",
                state={EVAL_HIDDEN_DECKS_KEY: case["hidden_past_decks"]},
            ),
            # Typed, or the final-response judge silently ignores them.
            rubrics=[
                Rubric(rubric_id=r["id"], rubric_content=RubricContent(text_property=r["text"]),
                       type="FINAL_RESPONSE_QUALITY")
                for r in case["rubrics"]
            ],
        ))
        print(f"{case['eval_id']:22} {len(text):6} chars  {len(case['rubrics'])} rubrics")
    _write(EvalSet(
        eval_set_id="briefs",
        name="Real HT briefs",
        description="Real sales emails to HT's solutioning team, run through the email pipeline's prompt.",
        eval_cases=eval_cases,
        creation_timestamp=time.time(),
    ), "briefs.evalset.json")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "briefs"):
        build_briefs()
    if which in ("all", "refinement"):
        build_refinement()
