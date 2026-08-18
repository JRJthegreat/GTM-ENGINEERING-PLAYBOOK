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

1. Open the **Config** node and set three values. They live in a node rather
   than in n8n Variables on purpose: Variables are a licensed feature, and on
   community n8n `$vars` resolves to undefined silently, which would send the
   introduction with an empty CC and still report success.
   - `SHERIF_EMAIL` — `sherif.hani@energygreenprint.net.au`
   - `EMAAS_CAMPAIGN_ID` — the Instantly campaign id
   - `SLACK_WEBHOOK_URL` — incoming webhook for the client channel
2. **Credential** `Instantly API`, type *Header Auth*:
   name `Authorization`, value `Bearer <INSTANTLY_API_KEY>`
3. Activate the workflow, copy the production webhook URL.
4. In Instantly: Settings > Webhooks, add that URL for event
   **`lead_interested`**, scoped to the EMaaS campaign.

## What has actually been tested

The payload contract is **not guessed**. Instantly sends a flat object and the
repo already has a production handler running on it
(`instantly-autoreply/scripts/instantly_autoreply.py`), which reads exactly:

    campaign_id, campaign_name, email_account, email_id,
    lead_email, reply_subject, reply_text, reply_html

It carries **no first name and no company**, which is why **Look up lead**
exists. That call was tested live against the API: `POST /v2/leads/list` with
`{search: <email>, limit: 1}` returns HTTP 200 and an item exposing
`first_name`, `company_name`, `job_title` and a `payload` object holding the
custom variables set at push time.

The full chain was then simulated end to end against the live API with a real
lead. It resolved `Suzanne` / `Powers Health` and produced a correctly formed
`/emails/reply` body with Sherif in `cc_address_email_list`. Nothing was sent,
because that step needs a genuine `reply_to_uuid` from a real inbound reply.

Still unproven, and only provable once a real reply exists:
  * that Instantly accepts the reply and honours the CC on a live thread
  * the exact `lead_interested` event envelope (whether the fields above sit at
    the top level or under a wrapper). The **Slack: raw payload (first run)**
    branch prints whatever arrives, so the first firing settles it.

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
