"""Summarise the eval results from this run, keep them, and email the summary.

    python -m evals.report --since <unix time> --label "after deploy"
    python -m evals.report --since <unix time> --label "full run" --with-usage
    python -m evals.report --usage-only            # the weekly usage email

--with-usage adds the last 7 days' usage report (bigquery/weekly_report.sql) to the
email as one table of key figures.

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
from datetime import datetime, timedelta, timezone
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


def _results(files: list[Path]) -> tuple[list[tuple[str, bool, dict]], list[tuple[str, str, str]]]:
    """(each case's id, pass, scores by short metric name), and every rubric not met."""
    cases, misses = [], []
    for f in files:
        data = json.loads(f.read_text())
        data = json.loads(data) if isinstance(data, str) else data
        for case in data.get("eval_case_results") or []:
            scores = {SHORT.get(m["metric_name"], m["metric_name"]): m.get("score")
                      for m in case.get("overall_eval_metric_results") or []}
            cases.append((case["eval_id"], case.get("final_eval_status") == 1, scores))
            for inv in case.get("eval_metric_result_per_invocation") or []:
                for metric in inv.get("eval_metric_results") or []:
                    for r in (metric.get("details") or {}).get("rubric_scores") or []:
                        if r.get("score") is not None and r["score"] < 1:
                            misses.append((case["eval_id"], r["rubric_id"], (r.get("rationale") or "").strip()[:400]))
    return cases, misses


def _score(value) -> str:
    return "-" if value is None else f"{value:.2f}".rstrip("0").rstrip(".")


def summarise(files: list[Path]) -> tuple[str, int, int]:
    """(plain-text summary, cases passed, cases run) across the result files."""
    cases, misses = _results(files)
    lines = [f"{'PASS' if ok else 'FAIL'}  {case_id}: " + ", ".join(f"{k} {_score(v)}" for k, v in scores.items())
             for case_id, ok, scores in cases]
    text = "\n".join(lines) or "No eval results were produced; check the build log."
    if misses:
        text += "\n\nRubrics not met, with the judge's reasoning:\n" + "\n".join(
            f"- {case_id}: {rubric}\n  {why}" for case_id, rubric, why in misses)
    return text, sum(ok for _, ok, _ in cases), len(cases)


def _store(files: list[Path], summary: str, stamp: str) -> str:
    bucket = os.environ.get("EVAL_RESULTS_BUCKET", "")
    if not bucket:
        return ""
    from google.cloud import storage

    b = storage.Client().bucket(bucket)
    for f in files:
        b.blob(f"{stamp}/{f.name}").upload_from_filename(str(f))
    b.blob(f"{stamp}/summary.txt").upload_from_string(summary)
    return f"https://console.cloud.google.com/storage/browser/{bucket}/{stamp}"


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


def key_figures(usage) -> list[tuple[str, list[tuple[str, str]]]]:
    """The usage report as a few sections of (label, value) rows, for one table.

    Reads the queries of bigquery/weekly_report.sql in their order there.
    """
    emails, briefs, timing, decks, sources, grounding, inboxes = (
        [dict(zip(columns, row)) for row in rows] for _, columns, rows in usage)

    def total(rows, column, **match):
        return sum(r[column] for r in rows if all(r.get(k) == v for k, v in match.items()))

    built, failed = total(briefs, "briefs", outcome="Built"), total(briefs, "briefs", outcome="Failed")
    by_label = total(briefs, "briefs", outcome="Built", picked_up="generate-deck label")
    t, g = timing[0], grounding[0]
    reconnect = total(inboxes, "inboxes", status="Needs reconnecting")
    return [
        ("Email", [
            ("Emails checked", f"{total(emails, 'emails')}, of which {total(emails, 'emails', decision='A brief')} were briefs"),
            ("Decks built from email", f"{built}" + (f" ({by_label} from the generate-deck label)" if by_label else "")),
            ("Briefs that failed", str(failed)),
            ("Time to first draft", f"Median {_score(t['median_hours'])} hours; {_score(t['percent_within_4_hours'])}% "
                                    "within 4 hours (target 70%)" if t["briefs_built"] else "No timed briefs yet"),
        ]),
        ("Decks", [
            ("Decks built, email and chat", str(decks[0]["decks_built"])),
            ("Decks revised afterwards", str(decks[0]["decks_revised"])),
            ("With a cited source", f"{_score(g['percent_with_a_cited_source'])}% (target 100%)" if g["drafts"] else "-"),
            ("With their sources logged", f"{_score(g['percent_with_sources_logged'])}% (target 100%)" if g["drafts"] else "-"),
        ]),
        ("Research calls", [
            (r["source"], f"{r['calls']} calls: {r['returned_results']} with results, {r['returned_nothing']} empty, "
                          f"{r['failed']} failed") for r in sources
        ] or [("All sources", "None")]),
        ("Inboxes", [
            ("Connected", str(total(inboxes, "inboxes", status="Connected"))
             + (f"; {reconnect} need reconnecting" if reconnect else "")),
        ]),
    ]


_INK, _MUTED, _RULE, _PASS, _FAIL = "#1d2329", "#66707a", "#dfe3e7", "#1e7b45", "#b3261e"
_FONT = "font-family:Arial,Helvetica,sans-serif"
_TD = f"{_FONT};font-size:13px;padding:6px 10px;border-bottom:1px solid {_RULE};vertical-align:top;"
_TH = f"{_FONT};font-size:11px;padding:14px 10px 6px;color:{_MUTED};font-weight:bold;letter-spacing:.5px;" \
      f"text-transform:uppercase;text-align:left;border-bottom:1px solid {_RULE};"
_TABLE = '<table cellspacing="0" cellpadding="0" style="border-collapse:collapse;width:100%;margin:0 0 8px">'


def _figures_html(figures) -> str:
    rows = []
    for section, items in figures:
        rows.append(f'<tr><th colspan="2" style="{_TH}">{html.escape(section)}</th></tr>')
        rows += [f'<tr><td style="{_TD}color:{_MUTED};width:42%">{html.escape(label)}</td>'
                 f'<td style="{_TD}color:{_INK}">{html.escape(value)}</td></tr>' for label, value in items]
    return _TABLE + "".join(rows) + "</table>"


def _evals_html(cases, misses) -> str:
    if not cases:
        return f'<p style="color:{_MUTED}">No eval results were produced; check the build log.</p>'
    metrics = list(dict.fromkeys(k for _, _, scores in cases for k in scores))
    head = "".join(f'<th style="{_TH}{"" if i < 2 else "text-align:right;"}">{html.escape(c)}</th>'
                   for i, c in enumerate(["Case", "Result", *metrics]))
    body = "".join(
        f'<tr><td style="{_TD}color:{_INK}">{html.escape(case_id)}</td>'
        f'<td style="{_TD}color:{_PASS if ok else _FAIL};font-weight:bold">{"Pass" if ok else "Fail"}</td>'
        + "".join(f'<td style="{_TD}color:{_INK};text-align:right">{"-" if scores.get(m) is None else f"{scores[m]:.2f}"}</td>' for m in metrics)
        + "</tr>" for case_id, ok, scores in cases)
    out = _TABLE + f"<tr>{head}</tr>{body}</table>"
    if misses:
        out += '<p style="margin:18px 0 6px;font-weight:bold">Rubrics not met</p>' + "".join(
            f'<p style="margin:0 0 10px"><b>{html.escape(case_id)}</b> '
            f'<span style="color:{_MUTED}">{html.escape(rubric)}</span><br>{html.escape(why)}</p>'
            for case_id, rubric, why in misses)
    return out


def _html_email(title: str, period: str, evals_line: str, cases, misses, figures, footer: str) -> str:
    h3 = "font-size:16px;margin:26px 0 4px"
    parts = [f'<div style="{_FONT};font-size:14px;line-height:1.5;color:{_INK};max-width:680px">',
             f'<h2 style="font-size:20px;margin:0">{html.escape(title)}</h2>',
             f'<div style="color:{_MUTED};margin:2px 0 0">{html.escape(period)}</div>']
    if figures:
        parts += [f'<h3 style="{h3}">This week</h3>', _figures_html(figures)]
    if evals_line:
        parts += [f'<h3 style="{h3}">Evals</h3>', f'<p style="margin:0 0 4px">{html.escape(evals_line)}</p>',
                  _evals_html(cases, misses)]
    if footer:
        parts.append(f'<p style="color:{_MUTED};font-size:12px;margin:20px 0 0">{html.escape(footer)}</p>')
    return "".join(parts) + "</div>"


def _figures_text(figures) -> str:
    return "\n".join(f"{section}\n" + "\n".join(f"  {label}: {value}" for label, value in items)
                     for section, items in figures)


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
    parser.add_argument("--since", type=float, help="eval results written since this unix time")
    parser.add_argument("--label", default="eval run")
    parser.add_argument("--with-usage", action="store_true")
    parser.add_argument("--usage-only", action="store_true", help="the weekly usage email, no evals")
    args = parser.parse_args()
    if args.since is None and not args.usage_only:
        parser.error("--since is required unless --usage-only")

    now = datetime.now(timezone.utc)
    period = f"{(now - timedelta(days=7)).strftime('%-d %b')} to {now.strftime('%-d %b %Y')}"
    if args.usage_only:
        figures, footer = [], ""
        try:
            figures = key_figures(usage_report())
        except Exception as exc:  # noqa: BLE001 - say so in the email rather than send nothing
            footer = f"The usage report could not be run: {exc}"
        title = "Solutioning Agent weekly usage"
        body = "\n\n".join(p for p in [f"{title}, {period}", _figures_text(figures), footer] if p)
        print(body)
        _send(title, body, _html_email(title, period, "", [], [], figures, footer))
        return

    files = sorted(f for f in HISTORY.glob("*.json") if f.stat().st_mtime >= args.since) if HISTORY.exists() else []
    summary, passed, total = summarise(files)
    stamp = now.strftime("%Y-%m-%d_%H%M") + "_" + args.label.replace(" ", "-")
    where = _store(files, summary, stamp)
    footer = f"Full results: {where}" if where else ""
    evals_line = f"{passed} of {total} cases passed ({args.label})."
    figures = []
    if args.with_usage:
        try:
            figures = key_figures(usage_report())
        except Exception as exc:  # noqa: BLE001 - the eval summary still goes out
            footer = f"The usage report could not be run: {exc}\n{footer}".strip()
    title = "Solutioning Agent report" if args.with_usage else "Solutioning Agent evals"
    if not args.with_usage:
        period = now.strftime("%-d %b %Y")
    body = "\n\n".join(p for p in [f"{title}, {period}", _figures_text(figures),
                                    f"Evals: {evals_line}", summary, footer] if p)
    print(body)
    cases, misses = _results(files)
    _send(f"{title}: {passed} of {total} evals passed", body,
          _html_email(title, period, evals_line, cases, misses, figures, footer))


def _send(subject: str, body: str, html_body: str) -> None:
    to = os.environ.get("EVAL_SUMMARY_EMAIL", "")
    if not to:
        return
    try:
        _email(to, subject, body, html_body)
    except Exception as exc:  # noqa: BLE001 - a report that can't be emailed is still printed
        print(f"Could not email the summary: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
