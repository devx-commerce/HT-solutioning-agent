"""Summarise the eval results from this run, keep them, and email the summary.

    python -m evals.report --since <unix time> --label "after deploy"

Reads ADK's result files written since --since, stores them with the summary
in the eval results bucket, and emails the summary from the agent's account
to EVAL_SUMMARY_EMAIL. Failing cases are reported, never raised: an eval
result never blocks or fails a deploy.
"""

from __future__ import annotations

import argparse
import base64
import email.mime.text
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The agent's settings carry where its token lives (used to send the email).
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / "agents" / "solutioning_agent" / ".env")
HISTORY = ROOT / "agents" / "solutioning_agent" / ".adk" / "eval_history"
SHORT = {
    "rubric_based_final_response_quality_v1": "response",
    "rubric_based_tool_use_quality_v1": "research",
    "hallucinations_v1": "grounding",
    "deck_quality_gate": "deck",
    "no_competitor_sources": "sources",
    "component_names_consistent": "names",
}


def summarise(files: list[Path]) -> tuple[str, int, int]:
    """(plain-text summary, cases passed, cases run) across the result files."""
    lines, passed, total, misses = [], 0, 0, []
    for f in files:
        data = json.loads(f.read_text())
        data = json.loads(data) if isinstance(data, str) else data
        for case in data.get("eval_case_results") or []:
            total += 1
            ok = case.get("final_eval_status") == 1
            passed += ok
            scores = "  ".join(
                f"{SHORT.get(m['metric_name'], m['metric_name'])} "
                f"{'-' if m.get('score') is None else round(m['score'], 2)}"
                for m in case.get("overall_eval_metric_results") or []
            )
            lines.append(f"{'PASS' if ok else 'FAIL'}  {case['eval_id']:24} {scores}")
            for inv in case.get("eval_metric_result_per_invocation") or []:
                for metric in inv.get("eval_metric_results") or []:
                    for r in (metric.get("details") or {}).get("rubric_scores") or []:
                        if r.get("score") is not None and r["score"] < 1:
                            misses.append(f"- {case['eval_id']}: {r['rubric_id']}\n  {(r.get('rationale') or '').strip()[:400]}")
    text = "\n".join(lines) or "No eval results were produced; check the build log."
    if misses:
        text += "\n\nRubrics not met, with the judge's reasoning:\n" + "\n".join(misses)
    return text, passed, total


def _store(files: list[Path], summary: str, stamp: str) -> str:
    bucket = os.environ.get("EVAL_RESULTS_BUCKET", "")
    if not bucket:
        return ""
    from google.cloud import storage

    b = storage.Client().bucket(bucket)
    for f in files:
        b.blob(f"{stamp}/{f.name}").upload_from_filename(str(f))
    b.blob(f"{stamp}/summary.txt").upload_from_string(summary)
    return f"gs://{bucket}/{stamp}/"


def _email(to: str, subject: str, body: str) -> None:
    from googleapiclient.discovery import build

    from app.auth.oauth_creds import get_credentials

    message = email.mime.text.MIMEText(body)
    message["to"], message["subject"] = to, subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    build("gmail", "v1", credentials=get_credentials()).users().messages().send(
        userId="me", body={"raw": raw}).execute()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", type=float, required=True)
    parser.add_argument("--label", default="eval run")
    args = parser.parse_args()

    files = sorted(f for f in HISTORY.glob("*.json") if f.stat().st_mtime >= args.since) if HISTORY.exists() else []
    summary, passed, total = summarise(files)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M") + "_" + args.label.replace(" ", "-")
    where = _store(files, summary, stamp)
    body = f"{args.label}: {passed} of {total} eval cases passed.\n\n{summary}"
    if where:
        body += f"\n\nFull results: {where}"
    print(body)
    to = os.environ.get("EVAL_SUMMARY_EMAIL", "")
    if to:
        try:
            _email(to, f"Solutioning Agent evals ({args.label}): {passed} of {total} passed", body)
        except Exception as exc:  # noqa: BLE001 - a report that can't be emailed is still printed
            print(f"Could not email the summary: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
