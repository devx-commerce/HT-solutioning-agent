# Weekly report

The full eval run's email (sent to `settings.eval_summary_email`) includes
this report for the last 7 days, as one table of key figures.

[`bigquery/weekly_report.sql`](../../bigquery/weekly_report.sql) reports
what the agent did over a period, from data it already logs. Open the
BigQuery console in project `academic-diode-477405-m3`, paste the file, and
run it. Each query shows as its own result. It covers the last 7 days; change
`period_start` and `period_end` at the top for another period.

| Query | Shows |
|---|---|
| 1. Emails checked | Every email the inbox check looked at, by decision: a brief, not a brief, already handled, or labelled by hand |
| 2. Briefs | Briefs from email by outcome (built or failed) and by how they were picked up (automatically, or by the `generate-deck` label). Each built brief sent one "deck drafted" email. |
| 3. Time to first draft | Median and 70th-percentile hours from an email arriving to its deck being built, and the share within 4 hours |
| 4. Decks | Decks built, and decks revised afterwards |
| 5. Research sources | For each source (past decks, web, YouTube, web pages): how often it returned results, returned nothing, or failed |
| 6. Grounding and transparency | Share of decks with at least one cited source, and share with their sources logged (the SOW targets are 100%) |
| 7. Inboxes | Connected inboxes, and any needing reconnection |

Notes:

- Time to first draft counts wall-clock hours, so a brief arriving at night
  counts from arrival. Briefs before 5 Oct 2026 have no arrival time and are
  left out.
- "First-draft usefulness" (decks taken forward) isn't in BigQuery; it is
  recorded by hand in the briefs sheet.
- To share the results, use **Save results > Google Sheets** in the console.
