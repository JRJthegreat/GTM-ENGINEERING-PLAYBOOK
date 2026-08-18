# EMaaS: CC Sherif on an interested reply

Import `emaas_intro_on_interested.json` into n8n.

## What it does

When a lead on the EMaaS campaign is marked **Interested** in Instantly, this
replies in that same thread with Sherif copied in. That reply *is* the
introduction the campaign promised, so the prospect never has to be handed off
twice and Sherif lands in a warm thread rather than a forwarded one.

```
Instantly (lead_interested)
      -> normalise payload
      -> guard: right campaign, real thread, not already introduced
      -> reply in-thread with cc_address_email_list = Sherif
      -> Slack ping to Jude
```

## Why CC on the reply and not on the send

CCing Sherif on the outbound would put him on 263 cold emails and would sit
badly against copy that says "I know an energy engineer" while that engineer is
visibly in the CC line. The introduction only makes sense once someone has said
yes.

## Setup

1. **n8n variables** (Settings > Variables):
   - `EMAAS_CAMPAIGN_ID` — the Instantly campaign id
   - `SHERIF_EMAIL` — `sherif.hani@energygreenprint.net.au`
   - `SLACK_WEBHOOK_URL` — incoming webhook for the client channel
2. **Credential** `Instantly API`, type *Header Auth*:
   name `Authorization`, value `Bearer <INSTANTLY_API_KEY>`
3. Activate the workflow, copy the production webhook URL.
4. In Instantly: Settings > Webhooks, add that URL for event
   **`lead_interested`**, scoped to the EMaaS campaign.

## Guards

- **Fires once per lead.** Instantly retries a failed delivery three times, and
  a lead can be re-marked interested by hand. The workflow keeps a set of
  already-introduced addresses in workflow static data and drops repeats, so
  nobody gets introduced twice.
- **Campaign-scoped.** Replies from any other campaign are ignored and reported
  to Slack rather than silently dropped.
- **Needs a real thread id.** Without `reply_to_uuid` the reply would start a
  new thread instead of continuing the conversation, so those are skipped.

## Things worth knowing

- Webhooks need Instantly's Hypergrowth plan or above.
- `lead_interested` can be set by Instantly's AI categorisation as well as by
  hand. If you would rather approve each one, split the connection after
  **Build intro (fire once)** and put a Slack approval step in front of
  **Reply + CC Sherif**. Everything upstream stays the same.
- The intro copy lives in the **Build intro (fire once)** node. It follows the
  connector framework: Jude introduces, Sherif's credentials are attributed to
  Sherif, and there is no sign-off because the sending account signature
  carries identity.
