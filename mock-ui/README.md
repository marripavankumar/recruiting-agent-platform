# Mock UI

Clickable static mock-up of the Recruiting Agent Platform. It shows how the screens look and how the six roles and the four approval gates work. There is no build step and no backend: all data is fictional sample data, and nothing is sent anywhere.

## Open it

Double-click `index.html`, or serve the folder:

```
cd mock-ui
python -m http.server 8080      # then open http://localhost:8080
```

Use **Viewing as** at the top right to switch role. The sidebar, the approval queue and the screen access change to match that role. The sun, moon and screen buttons next to it switch between light, dark and the device's theme; the choice is remembered in this browser.

## Screens

| Screen | File | BRD scope | Main roles |
| --- | --- | --- | --- |
| Dashboard: the nine BRD KPIs with target and status, pipeline funnel, operations alerts | `index.html` | §12 KPIs | All staff |
| Approval queue: approve, edit and approve, reject with reason; second approver | `approvals.html` | §10 gates | All staff |
| Companies & outreach: list import, hiring signals and priority factors, contacts with CASL consent, drafting with skip reasons, email tracking and daily cap | `companies.html` | Agents 1–3 | BD, Sales, Account, Admin |
| Client replies: reply categories, JD to job records, unsubscribes, unknown senders | `replies.html` | Agent 4 | BD, Account, Sales, Admin |
| Job detail: extracted fields with confirm-to-review, search profile, semantic search, all jobs | `jobs.html` | Agents 4–5 | All staff |
| Candidate ranking: Top 20, 0–100 score, strengths and gaps, hard-filter exclusions, recruiter verdicts, add resumes | `candidates.html` | Agents 6–7 | Recruiter, Account, Admin |
| Engagement & LinkedIn: approved notes to send by hand (Q15), outcomes, resume and consent collection, completion rate | `linkedin-tasks.html` | Agents 8–9 | Recruiter, Account, Admin |
| Candidate secure link: resume, details and two consents (public, expiring) | `collect.html` | Agent 9, checklist P1–P2 | Candidate |
| Match report (score, flags, interview probability) and the nine-item client package; right to represent, then the submission gate | `match.html` | Agents 10–11 | Recruiter, Account, Admin |
| Follow-ups: reminders for clients, candidates, interviews, offers, start dates; drafted follow-ups for approval; cadence (Q13) | `followups.html` | Agent 12 | All staff |
| AI agents: the four deep agents, a recruiting run waiting for the recruiter's decision (widen search, S12), a run's report and tool steps, recent runs per role, run count and AI cost | `agents.html` | §7, §5.4a | All staff |
| Users & approvers, approval-gate policy, connectors, LLM cost per prompt, ranking weights, audit log | `admin.html` | §9 roles, Q14, §5.4 | Administrator |
| Client portal: own company's roles and submissions, feedback | `client-portal.html` | Q8 | Client |
| Unsubscribe page behind the link in every email (public) | `unsubscribe.html` | Compliance E6–E7 | Anyone |
| Build status: which screens are backed by working code | `build-status.html` | — | All staff |

## Matches the backend

Screens marked **Built in backend** use the same fields, statuses and rules as the implemented API (see `build-status.html`); the rest show planned phases.

- Approval queue and decisions follow the `/approvals` API: only the first approver can edit, a rejection needs a reason, and a second approver sees the final version.
- Users and approvers follow the `/admin` API: client users must have a client company, an Administrator cannot deactivate or demote themselves, and gate changes apply without a restart.
- LinkedIn requests are queued for the recruiter to send by hand, never automated.

The final UI will be built in Next.js (stack decision record); this mock is the reference for layout and flow.
