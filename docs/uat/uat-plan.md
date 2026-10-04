# UAT plan

User acceptance testing (UAT) is a one-week period in which HT's own people
use the Solutioning Agent on their real work, so that HT can decide whether
it meets the pilot's acceptance criteria and sign it off.

## At a glance

| | |
|---|---|
| Who | Solutioning team members |
| How long | One working week |
| Support | A 30-minute call every day of the week |
| Testers read | [user-guide.md](user-guide.md) |
| Sign-off | HT's Project Acceptance Form, within 5 business days of the closing meeting |

## Before day 1

- [ ] Deploy the current version (see [../operations/deploy.md](../operations/deploy.md)) and
      confirm the after-deploy evals passed.
- [ ] Confirm every tester has an HT account on hindustantimes.com or
      htdigital.in, and a Gemini Enterprise licence if they'll use chat.
- [ ] Resume the inbox check (the `solutioning-agent-sweep` scheduler job;
      see [../operations/troubleshooting.md](../operations/troubleshooting.md)).
- [ ] Add a **Taken forward** column to the briefs sheet (the agent writes one
      row per brief; this column is filled in by hand on the daily call).
- [ ] Share the user guide with testers.

## Day 1: onboarding session (about 45 minutes)

1. What the agent does and doesn't do (user guide, "What it can't do").
2. Everyone connects their inbox using the onboarding link, during the call.
3. A live example: forward a real brief to a tester's inbox, wait for the
   "Solution deck drafted" email, open the deck together.
4. A chat example: find that deck in Gemini Enterprise and make two changes.
5. How feedback is collected (below).

## Days 1 to 5: use it on real work

Testers keep working as normal. Briefs that arrive in their inboxes are
drafted automatically; they can also paste briefs into chat.

**Daily 30-minute call**, with the solutioning team and the delivery team:

- go through every deck drafted since the last call;
- for each, record in the briefs sheet whether it was **taken forward**: used
  as the basis of the real proposal, with material reuse or refinement (yes
  or no, and a line on why);
- note problems: wrong facts, missed briefs, unhelpful slides, anything
  confusing. The delivery team triages them the same day.

## How acceptance is measured

The pilot's acceptance criteria for this agent, and where each figure comes
from:

| Criterion | Target | Measured from |
|---|---|---|
| Time to first draft | Draft delivered within 4 working hours of the brief arriving, for 70% of briefs | The weekly report (query 3) |
| First-draft usefulness | At least 20% of drafts taken forward | The "taken forward" record kept on the daily call |
| Evidentiary grounding | 100% of drafts follow the HT template and cite at least one prior-campaign or competitor source | The weekly report (query 6); the template is enforced on every deck |
| Retrieval transparency | 100% of drafts state which sources returned results and which didn't | The weekly report (query 6); every email carries a sources section |

The weekly report is [bigquery/weekly_report.sql](../../bigquery/weekly_report.sql),
explained in [../operations/weekly-report.md](../operations/weekly-report.md).
Run it at the end of the week for the UAT period.

## Closing

1. Run the weekly report for the UAT week.
2. Summarise: the four figures above, the issues raised and how each was
   resolved, and any open items.
3. Closing meeting with HT, then the Project Acceptance Form.
