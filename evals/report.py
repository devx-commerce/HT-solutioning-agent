"""Summarise the eval results from this run, keep them, and email the summary.

    python -m evals.report --since <unix time> --label "after deploy"
    python -m evals.report --since <unix time> --label weekly --with-usage

--with-usage adds the week's usage report (bigquery/weekly_report.sql) to the
email as tables.

Reads ADK's result files written since --since, stores them with the summary
in the eval results bucket, and emails the summary from the agent's account
to EVAL_SUMMARY_EMAIL. Failing cases are reported, never raised: an eval
result never blocks or fails a deploy.
"""

from __future__ import annotations

import argparse
import base64
import email.mime.multipart
import email.mime.text
import html
import json
import os
import re
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


def usage_report() -> list[tuple[str, list[str], list[tuple]]]:
    """Each query of the weekly report: (its title, column names, rows).

    The file runs as one BigQuery script; each SELECT in it is a child job,
    titled by the numbered comment above it in the file.
    """
    from google.cloud import bigquery

    sql = (ROOT / "bigquery" / "weekly_report.sql").read_text()
    titles = re.findall(r"^-- \d+\. (.+?)$", sql, flags=re.M)
    client = bigquery.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    job = client.query(sql)
    job.result()
    selects = [c for c in client.list_jobs(parent_job=job.job_id) if c.statement_type == "SELECT"]
    selects.sort(key=lambda c: c.created)
    report = []
    for title, child in zip(titles, selects):
        result = child.result()
        report.append((title, [f.name for f in result.schema], [tuple(r.values()) for r in result]))
    return report


_INK, _MUTED, _ACCENT, _RULE, _BAND = "#17212b", "#5a6672", "#0e6e8c", "#d5dde3", "#eef2f4"
_FONT = "font-family:Arial,Helvetica,sans-serif"


def _table_html(columns: list[str], rows: list[tuple]) -> str:
    def cell(value, header=False):
        numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
        style = (f"{_FONT};font-size:13px;padding:7px 12px;border-bottom:1px solid {_RULE};"
                 f"text-align:{'right' if numeric else 'left'};"
                 + (f"background:{_BAND};color:{_MUTED};font-weight:bold;font-size:12px;" if header else f"color:{_INK};"))
        text = "" if value is None else (f"{value:,}" if isinstance(value, int) else str(value))
        if not text and not header:
            text = "-"
        tag = "th" if header else "td"
        return f'<{tag} style="{style}">{html.escape(text)}</{tag}>'

    head = "".join(cell(c.replace("_", " ").capitalize(), header=True) for c in columns)
    body = "".join("<tr>" + "".join(cell(v) for v in row) + "</tr>" for row in rows)
    if not body:
        body = (f'<tr><td colspan="{len(columns)}" style="{_FONT};font-size:13px;padding:7px 12px;'
                f'color:{_MUTED}">Nothing in this period.</td></tr>')
    return (f'<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;margin:4px 0 20px;'
            f'border:1px solid {_RULE}"><tr>{head}</tr>{body}</table>')


def _html_email(headline: str, summary: str, usage) -> str:
    parts = [
        f'<div style="{_FONT};font-size:14px;line-height:1.5;color:{_INK};max-width:720px">',
        f'<div style="width:44px;height:4px;background:{_ACCENT};margin:0 0 12px"></div>',
        f'<div style="font-size:11px;font-weight:bold;letter-spacing:1px;text-transform:uppercase;color:{_ACCENT}">'
        f'Solutioning Agent</div>',
        f'<h2 style="font-size:20px;margin:4px 0 18px">{html.escape(headline)}</h2>',
    ]
    if usage:
        parts.append(f'<h3 style="font-size:16px;margin:0 0 4px">Usage this week</h3>')
        for title, columns, rows in usage:
            parts.append(f'<p style="margin:12px 0 4px;font-weight:bold;font-size:13px">{html.escape(title)}</p>')
            parts.append(_table_html(columns, rows))
    parts.append(f'<h3 style="font-size:16px;margin:8px 0 4px">Evals</h3>')
    parts.append(f'<pre style="font-family:Menlo,Consolas,monospace;font-size:12px;white-space:pre-wrap;'
                 f'background:{_BAND};padding:12px;margin:4px 0">{html.escape(summary)}</pre></div>')
    return "".join(parts)


def _usage_text(usage) -> str:
    out = []
    for title, columns, rows in usage:
        out.append(title)
        out += ["  " + ", ".join(f"{c}: {v}" for c, v in zip(columns, row)) for row in rows] or ["  No data."]
    return "\n".join(out)


def _email(to: str, subject: str, body: str, html_body: str | None = None) -> None:
    from googleapiclient.discovery import build

    from app.auth.oauth_creds import get_credentials

    if html_body:
        message = email.mime.multipart.MIMEMultipart("alternative")
        message.attach(email.mime.text.MIMEText(body, "plain"))
        message.attach(email.mime.text.MIMEText(html_body, "html"))
    else:
        message = email.mime.text.MIMEText(body)
    message["to"], message["subject"] = to, subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
    build("gmail", "v1", credentials=get_credentials()).users().messages().send(
        userId="me", body={"raw": raw}).execute()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", type=float, required=True)
    parser.add_argument("--label", default="eval run")
    parser.add_argument("--with-usage", action="store_true")
    args = parser.parse_args()

    files = sorted(f for f in HISTORY.glob("*.json") if f.stat().st_mtime >= args.since) if HISTORY.exists() else []
    summary, passed, total = summarise(files)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M") + "_" + args.label.replace(" ", "-")
    where = _store(files, summary, stamp)
    headline = f"{args.label}: {passed} of {total} eval cases passed."
    if where:
        summary += f"\n\nFull results: {where}"
    usage = []
    if args.with_usage:
        try:
            usage = usage_report()
        except Exception as exc:  # noqa: BLE001 - the eval summary still goes out
            summary += f"\n\nThe usage report could not be run: {exc}"
    body = headline + ("\n\nUsage this week\n" + _usage_text(usage) if usage else "") + "\n\n" + summary
    print(body)
    to = os.environ.get("EVAL_SUMMARY_EMAIL", "")
    if to:
        try:
            subject = ("Solutioning Agent weekly report" if args.with_usage else "Solutioning Agent evals") \
                + f" ({args.label}): {passed} of {total} evals passed"
            _email(to, subject, body, _html_email(headline, summary, usage) if usage else None)
        except Exception as exc:  # noqa: BLE001 - a report that can't be emailed is still printed
            print(f"Could not email the summary: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
