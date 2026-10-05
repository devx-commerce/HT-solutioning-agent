"""Run the brief classifier on real threads and compare with the agreed answers.

    python -m evals.check_classifier            # each case once
    python -m evals.check_classifier --runs 3   # repeated, to see how stable it is

Read-only: nothing is recorded, queued or labelled. Uses the cheap
classification model, a fraction of a rupee per case.
"""

from __future__ import annotations

import argparse
import email.utils
import json
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "agents" / "solutioning_agent" / ".env")

from app.auth.gmail_client import active_users, get_service_for_user  # noqa: E402
from app.pipeline import classify, ingestion  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()
    cases = json.loads((ROOT / "evals" / "classify_cases.json").read_text())["cases"]
    services = {u["email"]: get_service_for_user(u["gmail_secret"]) for u in active_users()}
    passed = 0
    for case in cases:
        gmail, owner = services[case["inbox"]], case["inbox"]
        thread = gmail.users().threads().get(userId="me", id=case["thread"], format="metadata",
                                             metadataHeaders=["From", "Subject"]).execute()
        msgs = thread["messages"]
        def sender(m):
            return email.utils.parseaddr(next((h["value"] for h in m["payload"]["headers"] if h["name"] == "From"), ""))[1].lower()
        if "new_id" in case:  # the email that was actually classified at the time
            new = next(m for m in msgs if m["id"] == case["new_id"])
        elif "new" in case:
            new = msgs[case["new"]]
        else:
            new = next(m for m in reversed(msgs) if sender(m) != owner)
        subject = next((h["value"] for h in new["payload"]["headers"] if h["name"] == "Subject"), "")
        if ingestion._CALENDAR_SUBJECT.match(subject):
            answers, reason = [False] * args.runs, "calendar invitation"
        else:
            text = ingestion._thread_text(gmail, case["thread"], owner, new["id"], only_new=True)
            results = [classify.decide(subject, text) for _ in range(args.runs)]
            answers, reason = [r.is_solution_request for r in results], results[0].reason
        ok = all(a == case["expect"] for a in answers)
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {sum(answers)}/{len(answers)} brief, expected "
              f"{'brief' if case['expect'] else 'not a brief':11} | {case['what']}\n      {reason[:220]}")
    print(f"\n{passed} of {len(cases)} cases right on every run")


if __name__ == "__main__":
    main()
