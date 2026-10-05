# Troubleshooting

## Reading the logs

Every step of the email loop writes one structured log line, so the logs read
as a story: an email was found, judged, queued, built and announced. Open a
service in Cloud Run > **Logs**, or use Logs Explorer with these filters:

| To see | Filter |
|---|---|
| The email pipeline | `resource.labels.service_name="solutioning-agent"` |
| Inbox connections | `resource.labels.service_name="solutioning-agent-onboarding"` |
| The renderer | `resource.labels.service_name="solutioning-agent-renderer"` |
| The agent | `resource.type="aiplatform.googleapis.com/ReasoningEngine"` |
| One kind of event | `jsonPayload.event="build.done"` |
| Everything about one brief | `jsonPayload.thread_id="<thread id>"` |
| Only problems | `severity>=WARNING` |

| Event | Where | Means |
|---|---|---|
| `sweep.found` | pipeline | A check found new email in an inbox: how many, and what it did with them. Checks that find nothing write no line. |
| `email.not_a_brief` | pipeline | An email was judged not a brief, with the reason |
| `email.skipped` | pipeline | An email's thread already has a deck or is being built |
| `email.label_without_brief` | pipeline | Labelled `generate-deck`, but no brief could be read from it |
| `brief.queued` | pipeline | A brief was found and queued: client, sender, subject, how it was picked up |
| `build.started` / `build.done` | pipeline | The agent started a deck; the deck is built, with its link, who was notified and how long it took |
| `build.failed` / `build.error` | pipeline | The agent finished without a deck / the build raised an error and will be retried |
| `inbox.needs_reconnect` | pipeline | An inbox's access stopped working; the person was emailed a reconnect link |
| `sweep.inbox_failed` | pipeline | Checking one inbox failed; it is retried on the next check |
| `inbox.connect_started` / `inbox.connected` / `inbox.connect_failed` | onboarding | Someone opened the onboarding link / connected / was refused, with why |
| `deck.published` / `deck.revised` | agent | A deck was published or a revision saved, with its link |
| `deck.rejected` / `deck.revision_rejected` | agent | The deck broke the HT master deck rules; the agent fixes it and tries again |
| `deck.renderer_unavailable` | agent | The renderer couldn't be reached |
| `render.done` / `render.rejected` / `render.failed` | renderer | A deck was rendered / was invalid / the renderer failed |

## Pausing and resuming the inbox check

```bash
gcloud scheduler jobs pause  solutioning-agent-sweep --location=us-central1 --project=academic-diode-477405-m3
gcloud scheduler jobs resume solutioning-agent-sweep --location=us-central1 --project=academic-diode-477405-m3
```

Or Cloud Scheduler in the console. Deploys never change whether it is paused.

## Nobody is getting decks from email

1. **Is the inbox check running?** Cloud Scheduler > `solutioning-agent-sweep`
   should be Enabled, with a recent successful run.
2. **Are inboxes connected?** `SELECT email, status FROM solutioning_agent.users`.
   Anyone `reauthorization_required` has been emailed a reconnect link.
3. **Was the email judged a brief?**
   `SELECT * FROM solutioning_agent.decisions ORDER BY recorded_at DESC LIMIT 20`.
   `not_a_request` with its reason means the classifier decided it wasn't a
   brief; the person can apply the `generate-deck` label to force a build.
4. **Did the build fail?**
   `SELECT * FROM solutioning_agent.ingestion_threads WHERE status = 'failed'`.
   Briefs that failed five times sit in the Pub/Sub topic
   `solutioning-agent-build-work-dead`.

## Builds fail

Look in the pipeline's logs for `build.failed` or `build.error`, then in the
agent's logs at the same time.

| Log says | Cause | Fix |
|---|---|---|
| `invalid_grant` | The agent account's token was revoked | Renew it ([access-and-credentials.md](access-and-credentials.md)) |
| `404 NOT_FOUND ... models/<name>` | A model in `config.yaml` isn't enabled in the project | Put back a model that is, and deploy |
| `429 RESOURCE_EXHAUSTED` | Vertex AI quota | Retried automatically; if persistent, request more quota |
| "rendering service is unavailable" | The renderer is down or not callable | Check `solutioning-agent-renderer` in Cloud Run; redeploy |

## Drafts say "no prior HT work found" for everything

- The agent account lost its **Gemini Enterprise licence**: the logs show
  "User must be assigned a license". Reassign it.
- The **Past Pitch Decks** folder was moved or unshared from
  sales.agent@hindustantimes.com. Share it again, or update
  `past_decks_folder_ids`.

## Decks look wrong

| Symptom | Why | What to do |
|---|---|---|
| Pictures are grey placeholders | Image generation failed or was filtered | Usually transient; ask in chat for a new picture on that slide. If it's every deck, check `models.images` is enabled. |
| Client logo is a placeholder | The client's website offers no usable PNG or JPEG logo, or the agent couldn't establish the website | Attach the logo in chat: "use this as the client logo" |
| HT logo is missing | The HT brand assets folder has no image with "logo" in its name, or isn't shared with the agent account | Fix the folder |
| People can't open the deck | Their domain isn't in `deck_reader_domains` | Add it and deploy (applies to new decks) |

## A chat request didn't work

The agent always says what it changed or why it couldn't. Common reasons:
the request asked for something outside what chat can do (see
[../how-it-works/chat-and-revisions.md](../how-it-works/chat-and-revisions.md)),
an attached image didn't fit its slot, or the change would break the deck
rules (for example a 21st slide when the cap is 20).

## The eval summary email didn't arrive

Check Cloud Build > History for the run. The summary is also always printed in
the build log and stored in the eval bucket, even when the email can't be sent.
