# 10 — Risks and things to verify first

Ordered by how much rework a bad answer causes. The first two are worth a
morning before writing any deck code.

### 1. `drive.file` scope vs. copying the template — **verify first**

`oauth_creds.py` holds `drive.file`, which grants access only to files the
app itself created or that were explicitly opened with it. The template deck
(README step 5) is created by hand in the Slides UI. **`files().copy()` on it
may return 404 even though `sales.agent@` owns both.**

The skeleton may not have exercised this yet. Test it directly:

```bash
# as the shared identity's credentials
python -c "from tools.deck import build_hello_deck; print(build_hello_deck('Scope Test','x'))"
```

If it 404s, two fixes, in order of preference:

1. **Have the app create the template.** A one-off script creates the deck
   via the API under `drive.file`, a designer then styles it in the UI —
   ownership of *creation* is what the scope tracks, and styling does not
   change it. Keeps the narrow scope.
2. **Widen to `https://www.googleapis.com/auth/drive`.** Works, but it is a
   full-Drive grant on a shared identity, and it means redoing consent
   (`scripts/get_refresh_token.py`) and the scope list in README step 2.

Everything else we need — `permissions.create`, `files.update`,
`contentRestrictions` — operates on the deck *we* created, so `drive.file`
covers it.

### 2. GCS signed URLs need a signing identity

`createImage` needs a public URL ([02](02-constraints.md) §3), so generated
images are served from GCS by signed URL. Cloud Run's runtime service account
cannot sign without `roles/iam.serviceAccountTokenCreator` **on itself**, and
`iamcredentials.googleapis.com` enabled.

Given README step 0 already found `roles/editor` missing
`aiplatform.reasoningEngines.setIamPolicy`, assume this is missing too and
ask for it in the same conversation rather than discovering it at stage 6.

```bash
gcloud services enable iamcredentials.googleapis.com storage.googleapis.com
gcloud iam service-accounts add-iam-policy-binding SA_EMAIL \
  --member="serviceAccount:SA_EMAIL" \
  --role="roles/iam.serviceAccountTokenCreator"
```

Fallback if signing stays blocked: a small authenticated image-proxy endpoint
on the existing Cloud Run service. Uglier, but it removes the IAM dependency.

### 3. Comment anchors on Slides — one-hour spike

Drive-API comments are unanchored in Google editors. The Slides API has
developer-preview comment requests (`InsertComment`, `AddCommentReply`,
`UpdateCommentPost`) that may carry proper slide-level anchors.

- If they do: we get the refinement target for free **and** can reply
  in-thread when a change lands — materially better UX.
- If they do not: `quotedFileContent` text-matching against the IR
  ([08](08-lifecycle.md) §3), which is robust and needs no preview features.

Do not build on the preview API until the spike says it works.

### 4. Image-model availability in this project and region

README step 9 notes that Agent Engine deploy is the first real test of Gemini
model quota in `academic-diode-477405-m3` / `us-central1`. The image model is
a *separate* entitlement. Check before stage 6, not during it.

### 5. Font metrics must match Google's rendering

Our overflow prediction is only as good as the font file we measure against.
If the template uses a font whose metrics we cannot obtain, predictions drift
and Tier A gives false confidence.

Mitigation: restrict the template to fonts available as real files (the
Google Fonts set), pin the exact files in the repo, and calibrate once —
render a known string via `getThumbnail`, measure the pixels, compare to the
prediction, store the correction factor. Do this at stage 1, not stage 4.

### 6. `contentRestrictions` on a Slides file — confirm before relying on it

Documented as supported for Slides, but only used at `FINAL`, so a failure is
low-blast-radius. Confirm during stage 7; if it misbehaves, `FINAL` degrades
to revoking the commenter permission, which is weaker but adequate.

### 7. GE entitlement for custom agents

Unchanged from README step 0 and still unconfirmed. It gates delivery, not
design — the deck subagent is callable from Cloud Run and from ADK directly
regardless.

### 8. Quota under load

60 writes/min/user against **one** shared identity ([02](02-constraints.md)
§4) is the ceiling for the whole system, not per user. A 12-slide deck should
be 2–4 batches. If concurrent builds ever approach the limit, the fix is a
work queue on the existing Pub/Sub topic (README step 7), not more identities.

`getThumbnail` is an expensive read and the VLM loop is the only thing that
calls it — another reason the two-pass cap is structural.
