# Go-Live Runbook — CBS Talent Agents

**For:** the CBS LLC Administrator and whoever supports the platform day to day.
**Status:** draft for Phase 8 (plan section 12). Owner fills in the names and dates marked ____.

This runbook covers what must be true before go-live, how to watch the platform once it runs,
and what to do when something goes wrong. Endpoints are listed in
`05_Environment_Setup_Plan.md`; every setting below is an environment variable with the
`RAP_` prefix.

## 1. Before go-live: checklist

| # | Item | How it is checked | Owner | Done |
| --- | --- | --- | --- | --- |
| 1 | PostgreSQL migrations 0001–0008 applied and the PostgreSQL tests pass | CI run on the repository (needs a GitHub repo or WSL + Podman) | ____ | ☐ |
| 2 | Automated end-to-end scenarios pass | `uv run pytest` in `backend/`; 21 of 27 pass, gaps listed in plan section 14 | Engineering | ☑ (in memory) |
| 3 | S6 (bounces) and S12 (too few qualified candidates) decided: build before go-live or accept for the pilot | Decision recorded in plan section 14 | ____ | ☐ |
| 4 | Legal review of consent wording and outreach footers (compliance checklist P1, E-items) | Signed checklist | ____ | ☐ |
| 5 | Retention period for approval and delivery records agreed (they keep the sent text; see section 4.3) | Written decision | ____ | ☐ |
| 6 | Security test: role access matrix, penetration test, secrets in the vault | Test report, zero open critical findings | ____ | ☐ |
| 7 | Identity provider configured (`RAP_AUTH_PROVIDER=oidc`, issuer, audience, JWKS URL) and every user created with the right role | `/admin/users` review | Administrator | ☐ |
| 8 | Approvers per gate confirmed (Q14) | `/admin/gate-policies` | Administrator | ☐ |
| 9 | Follow-up cadence confirmed (Q13) | `/admin/follow-up-cadence` | Administrator | ☐ |
| 10 | Email provider connected (`RAP_EMAIL_PROVIDERS`, `RAP_EMAIL_SENDER`), postal address and unsubscribe secret set | Test email to an allow-listed address | ____ | ☐ |
| 11 | Hourly scheduler calls `POST /follow-ups/run` (n8n or cron, with a service user) | Scheduler log shows hourly runs | ____ | ☐ |
| 12 | UAT sign-off by each of the six roles | Signed UAT sheet | Role leads | ☐ |
| 13 | 4-week pilot with 2–3 recruiters and a few clients; KPI dashboard shows pilot data | `GET /kpis` | ____ | ☐ |
| 14 | Users trained; this runbook reviewed | Training record | ____ | ☐ |

## 2. Switching outreach on safely

Outreach is off by default in every environment.

1. Pilot: `RAP_OUTREACH_ENABLED=true` with `RAP_OUTREACH_ALLOW_LIST` set to the pilot clients'
   and candidates' addresses only.
2. Keep `RAP_OUTREACH_DAILY_CAP_EMAIL` low at first (default 100 a day) and raise it after a
   week with a failure rate under 5%.
3. Go-live: clear the allow-list. Every message still needs its approval; nothing is sent
   without one.

**To stop all sending immediately:** set `RAP_OUTREACH_ENABLED=false` and restart. Approvals
already given stay unused, so nothing is lost; LinkedIn tasks are unaffected (they are sent by
hand).

## 3. Daily monitoring

The Administrator opens **`GET /admin/operations`** (the Operations card on the dashboard)
each morning. It raises these alerts:

| Alert | Threshold | What to do |
| --- | --- | --- |
| Email failure rate | Above 5% of emails in 30 days | Check the provider's status and the sending domain (SPF, DKIM, DMARC); lower the daily cap; review failed recipients. A failed send is never retried automatically: create a new approval if it should go again. |
| Approvals waiting | Oldest pending request older than 48 hours | Remind the approver role for that gate, or change the approver in `/admin/gate-policies`. |
| Overdue follow-ups | Any | Owners work through `GET /follow-ups`; check the hourly scheduler is running. |

Also on the operations view:

- **LinkedIn tasks waiting:** approved notes the recruiter still has to send by hand.
- **AI cost (30 days):** compare it with the budget. Per-template detail is in
  `/admin/llm-usage`.
- **Access denied (S26):** the recent count. Review `/admin/access-denied` weekly. Repeated
  refusals for one user usually mean a wrong role. Refusals with no user point to a sign-in
  or integration problem.

The KPI dashboard (`GET /kpis`, any staff role) shows the nine BRD section 12 metrics with target,
status and basis. `no_data` means nothing happened in the period, not zero performance.

## 4. Common requests

### 4.1 A job is filled or cancelled (S22)

Set the job's status to **Filled** or **Closed** (`PATCH /jobs/{id}`). The platform cancels every
draft still waiting for approval for that job and stops its follow-ups. The response lists what
was stopped.

### 4.2 Someone asks to stop being contacted

A reply saying "unsubscribe" or "remove me" is handled automatically. For a request by phone
or another channel, the recruiter records the outcome **Do not contact** on the engagement. Both
put every address of that person on the opt-out list, which blocks all agents and channels.

### 4.3 A candidate asks for their data to be deleted (S23)

1. Confirm the person's identity through the recruiter who knows them.
2. The Administrator calls `POST /candidates/{id}/erase` with the reason.
3. The platform:
   - anonymises the candidate record
   - removes them from search
   - clears upload links and consent evidence
   - replaces submission packages with a placeholder
   - withdraws any active placement
   - cancels follow-ups
   - puts their addresses on the opt-out list so no agent contacts them again
4. The approval requests and delivery records still hold the text that was approved and sent:
   that is the audit trail BRD section 10 requires. The response reports how many were kept
   (`audit_records_kept`). They are purged under the retention period agreed in checklist item 5.
5. File the response with the request; it is also written to the audit log.

### 4.4 The AI service is unavailable

Every agent falls back to its rule-based version automatically and logs a warning. Drafts are
plainer but still go through approval. Nothing needs to be done unless it lasts more than a day.
To run without AI on purpose, set `RAP_LLM_PROVIDER=none`.

### 4.5 A user cannot do something

The roles are fixed by BRD section 9 and Q8 / Q14. Check the user's role in `/admin/users` and the
gate approvers in `/admin/gate-policies`. The access-denied log shows exactly which request was
refused.

## 5. Hypercare (first 30 days after go-live)

- Daily 15-minute check of the operations view and the KPI dashboard with the pilot lead.
- Weekly review of the following, with prompt and template changes recorded with a new template
  version:
  - approval rejections and their reasons (these show where drafts need better prompts)
  - recruiter ranking verdicts
  - stopped follow-ups
- Defects are logged with severity. Go-live exit needs zero open critical defects
  (plan section 12).
