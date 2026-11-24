# Web app

The role-based web app and client portal for the Recruiting Agent Platform. It uses Next.js (App Router), React and TypeScript. The layout and styles come from the [mock UI](../mock-ui/).

## Run it locally

You need Node.js 24 or later and the backend running in demo mode:

```
# Terminal 1: the API with a fictional pipeline (in memory, nothing is sent anywhere)
cd backend
RAP_DEMO_DATA=true uvicorn recruiting.api.main:app --port 8000

# Terminal 2: the web app
cd web
npm install
npm run dev            # http://localhost:3000
```

Sign in as any demo role. The demo leaves work waiting for every role:
- The BD Manager has a first client email to approve.
- The Account Manager has a job whose fields still need confirming.
- The Recruiter has candidate outreach and a résumé submission to approve.
- The client (Northfield Axle Systems) sees each submission once it's approved.

## How it works

- **Server-side data:** pages are server components that call the API with the signed-in user's identity. The browser never holds an API credential, and CORS doesn't apply.
- **Forms:** every action is a plain HTML form backed by a server action in [app/actions.ts](app/actions.ts), so the screens also work without JavaScript. Each action returns to its page with a success or error message.
- **Permissions:** the API decides what each role may see and do. The sidebar only hides what the role can't use; it isn't the security boundary.
- **Package downloads:** the branded submission package is streamed from the API unchanged by [app/files/submissions/[id]/package/route.ts](app/files/submissions/[id]/package/route.ts).

| Setting | Default | Meaning |
| --- | --- | --- |
| `RAP_API_URL` | `http://localhost:8000` | Where the FastAPI backend runs |
| `RAP_WEB_AUTH` | `dev` outside production, `oidc` in production | `dev`: choose a demo user, which the API accepts only when `RAP_ENVIRONMENT=local`. `oidc`: company sign-in, which isn't built yet, so sign-in is refused |

## Screens

| Screen | Path | Roles |
| --- | --- | --- |
| Dashboard: the nine BRD KPIs, pipeline, operations alerts (alerts for the Administrator only) | `/` | Staff |
| Approval queue: approve; edit, then approve; reject with a reason; package preview for résumé submissions | `/approvals` | Staff, per the gate policy |
| Jobs; job details with field confirmation, ranked candidates with verdicts and outreach, engagement, submissions | `/jobs`, `/jobs/{id}` | Staff (actions per role) |
| Follow-ups: draft, snooze, done | `/follow-ups` | Staff |
| AI agents: start runs, decide on what a run is waiting for | `/agents` | Staff (per agent) |
| Client portal: submissions sent, interview, hold or reject | `/portal` | Client |

Next increment: companies and outreach, LinkedIn tasks, candidates and résumé upload, the Administrator screens (users, gate policies, ranking weights, cadence), and OIDC sign-in.

## Checks

```
npm run typecheck
npm run build
```

CI runs both on every push (`.github/workflows/ci.yml`).
