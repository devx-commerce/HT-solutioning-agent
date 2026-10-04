"""The weekly email's key figures (evals/report.py), from the usage report's queries."""

from __future__ import annotations

import re
from pathlib import Path

from evals import report

USAGE = [
    ("Emails", ["decision", "emails"], [("A brief", 5), ("Not a brief", 2)]),
    ("Briefs", ["outcome", "picked_up", "briefs"],
     [("Built", "Automatically", 4), ("Built", "generate-deck label", 1), ("Failed", "Automatically", 1)]),
    ("Timing", ["briefs_built", "median_hours", "hours_for_70_percent", "percent_within_4_hours"], [(5, 0.4, 0.6, 100.0)]),
    ("Decks", ["decks_built", "decks_revised"], [(6, 2)]),
    ("Sources", ["source", "returned_results", "returned_nothing", "failed", "calls"], [("Web pages", 3, 0, 5, 8)]),
    ("Grounding", ["drafts", "percent_with_a_cited_source", "percent_with_sources_logged"], [(6, 100.0, 83.3)]),
    ("Inboxes", ["status", "inboxes"], [("Connected", 3), ("Needs reconnecting", 1)]),
]


def test_key_figures_read_every_query():
    figures = dict((label, value) for _, items in report.key_figures(USAGE) for label, value in items)
    assert figures["Emails checked"] == "7, of which 5 were briefs"
    assert figures["Decks built from email"] == "5 (1 from the generate-deck label)"
    assert figures["Briefs that failed"] == "1"
    assert figures["Time to first draft"] == "Median 0.4 hours; 100% within 4 hours (target 70%)"
    assert figures["With their sources logged"] == "83.3% (target 100%)"
    assert figures["Web pages"] == "8 calls: 3 with results, 0 empty, 5 failed"
    assert figures["Connected"] == "3; 1 need reconnecting"


def test_key_figures_match_the_report_queries():
    sql = (Path(report.ROOT) / "bigquery" / "weekly_report.sql").read_text()
    assert len(re.findall(r"^-- \d+\. ", sql, flags=re.M)) == len(USAGE)


def test_html_email_has_no_preformatted_text():
    cases = [("uber", True, {"response": 1.0, "grounding": 0.98}), ("air-india", False, {"response": 0.5})]
    body = report._html_email("Weekly", "1 Oct to 8 Oct 2026", "1 of 2 cases passed.", cases,
                              [("air-india", "g-gaps-reported", "Missed a failed call.")],
                              report.key_figures(USAGE), "")
    assert "<pre" not in body and "Fail" in body and "0.98" in body and "g-gaps-reported" in body
