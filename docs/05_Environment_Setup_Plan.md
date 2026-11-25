# Environment Setup Plan (Phase 0, activity 7)

**Status:** Draft, 2026-11-22  
**Decision:** cloud-agnostic, plug-and-play hosting. The BRD names no hosting cloud, so the platform must run on Azure, AWS, Google Cloud or on-premises without code changes. Choosing or changing a cloud is a configuration and infrastructure-module choice.
**Current hosting (2026-11-25):** Podman. Until a cloud is chosen, every environment runs the same images with Podman (`podman compose`). The cloud modules below stay the target; nothing in the code changes when one is picked.

## 1. How cloud plug-and-play works

The platform follows the same rule as the business integrations (plan Section 5.5): **application code depends on interfaces, never on a cloud SDK directly.**

1. **Everything runs in containers.** Backend API, agent workers, web app, Qdrant and n8n are container images. They run the same way on a laptop (Podman or Docker Compose), a Kubernetes cluster or a cloud container service.
2. **Cloud services sit behind adapters.** Each cloud-specific service has one interface in the code and one adapter per cloud. The active adapter is set by configuration (`RAP_*` environment variables).
3. **Infrastructure is code, one module per cloud.** Terraform modules share one set of inputs and outputs (`infra/terraform/modules/<cloud>`). An environment picks its cloud by choosing a module; the outputs (database URL, vault name, bucket) feed the same application settings.
4. **Only open standards cross the boundary:** PostgreSQL wire protocol, S3-compatible or native object-storage adapters, OpenID Connect for sign-in, OpenTelemetry for logs, metrics and traces, and OCI container images.

### Service mapping per cloud

| Platform need | Interface / standard | Azure | AWS | Google Cloud | On-premises / local |
| --- | --- | --- | --- | --- | --- |
| Container runtime | OCI images | Azure Container Apps or AKS | ECS Fargate or EKS | Cloud Run or GKE | Podman (current), Docker Compose or Kubernetes |
| PostgreSQL (BRD §11) | PostgreSQL protocol | Azure Database for PostgreSQL | Amazon RDS / Aurora PostgreSQL | Cloud SQL for PostgreSQL | PostgreSQL container |
| Vector DB | Qdrant API | Qdrant container with persistent disk | Same | Same | Same |
| Workflow automation | n8n | n8n container | Same | Same | Same |
| Secrets vault | `SecretsProvider` adapter | Key Vault | Secrets Manager | Secret Manager | Environment file (dev only) or HashiCorp Vault |
| Document storage (BRD §11) | `DocumentStore` connector | SharePoint, OneDrive or Blob Storage | AWS S3 | Cloud Storage | MinIO (S3-compatible) |
| Sign-in for the 6 roles | OpenID Connect | Microsoft Entra ID | Amazon Cognito | Identity Platform | Keycloak |
| Observability | OpenTelemetry | Azure Monitor | CloudWatch | Cloud Operations | Grafana stack |
| Container registry | OCI registry | Azure Container Registry | ECR | Artifact Registry | Any OCI registry |

SharePoint, OneDrive and S3 are the BRD's document-storage options and are built regardless of the hosting cloud. For example, the platform can run on AWS and store resumes in SharePoint.

### Rules that keep it portable

- No cloud SDK imported outside an adapter module.
- No cloud-only database features (stay on standard PostgreSQL 16 plus common extensions).
- Configuration comes only from environment variables and the secrets provider.
- Each cloud module ships with an automated smoke test (deploy to an empty account or subscription, run the health check, destroy). A cloud counts as supported only when its smoke test passes.

## 2. Environments

| Environment | Purpose | Data | Deploys | Who has access |
| --- | --- | --- | --- | --- |
| Local | Developer laptops | Synthetic only | `podman compose up` | Developers |
| Dev | Shared integration | Synthetic only | Automatically on merge to `main` | Delivery team |
| Test | System and UAT testing (Phase 8) | Synthetic plus anonymised samples approved by legal | On release tag | Delivery team, CBS UAT users |
| Prod | Live operation | Real company, job and candidate data | Manual approval after test sign-off | CBS users by role; ops on-call |

- Real candidate or contact data never leaves prod (compliance checklist P6, P8).
- Outbound email and LinkedIn tasks are disabled in local and dev (`RAP_OUTREACH_ENABLED=false`). In test, outreach can go only to an allow-list of internal addresses (`RAP_OUTREACH_ALLOW_LIST`). The Outreach Service enforces both before any channel adapter runs.
- Each environment has its own database, vault, sending identity and API keys.

## 3. Repository and CI/CD

**Repository layout**

```
backend/          Python 3.12, FastAPI, LangGraph agents (src/recruiting)
web/              Next.js app: review queues, dashboards, client portal (Phase 1)
db/migrations/    PostgreSQL schema, applied in order
infra/            docker-compose.yml for local; terraform/ modules per cloud
docs/             BRD, plan, checklists
```

**Pipeline**

| Stage | Runs on | Steps |
| --- | --- | --- |
| Pull request | Every PR | Lint (ruff), unit tests (pytest), type check, dependency and secret scanning |
| Merge to `main` | Main branch | Build container images, push to registry, deploy to dev, run smoke test |
| Release tag | `v*` tags | Deploy the same images to test, run integration and compliance tests |
| Production | Manual approval | Promote the tested images to prod, run migrations, smoke test, roll back on failure |

The pipeline definition is CI-vendor neutral in intent. A GitHub Actions workflow is included as the starting point because it is the most common choice; the same steps port to Azure DevOps, GitLab CI or AWS CodePipeline.

**Secrets:** never in the repository. Local development uses `.env` (git-ignored, see `.env.example`). Shared environments read secrets from the active vault adapter at start-up.

## 4. Email sending domains

Required for compliance items E11 and E12 before Phase 2 sends anything.

1. Use a dedicated outreach subdomain per brand (for example `mail.<cbs-domain>`), separate from staff mail.
2. Publish SPF, DKIM and DMARC records (start DMARC at `p=none` with reports, move to `quarantine` once clean).
3. Connect the mailbox through the Microsoft 365 or Gmail adapter, whichever CBS activates (Q11).
4. Warm up over 4–6 weeks: start at about 20 emails a day per mailbox and ramp towards the BRD volume of 200–500 a week, watching bounce and spam-complaint rates.
5. Set the platform's daily send cap per mailbox to match the warm-up stage.

## 5. Setup checklist

| # | Item | Status |
| --- | --- | --- |
| 1 | Repository structure and local Docker Compose stack | Done (scaffold) |
| 2 | Initial database schema (Company, Job, Candidate, approvals, consent, opt-out, audit) | Done (scaffold) |
| 3 | CI workflow: lint and tests | Done (scaffold) |
| 4 | Choose the first cloud for dev, test and prod (any of the mapping above) | Not started |
| 5 | Terraform module for that cloud, with smoke test | Not started |
| 6 | Secrets vault adapter for that cloud | Not started |
| 7 | OpenID Connect sign-in and the 6 roles | Done (scaffold): OIDC adapter for any provider; roles from app_user |
| 8 | Sending subdomain, SPF / DKIM / DMARC, warm-up | Not started |
| 9 | Backups and restore test for PostgreSQL and Qdrant | Not started |
| 10 | Observability (OpenTelemetry export) | Not started |

## 6. Running the scaffold locally

Prerequisites: Python 3.12+ (or [uv](https://docs.astral.sh/uv/)), Podman with a compose provider (or Docker Desktop; replace `podman` with `docker` below).

Podman on Windows runs in a WSL 2 machine. One-time setup: `wsl --install --no-distribution` (admin; reboot if it asks), `podman machine init --cpus 4 --memory 6144`, `podman machine start`, and a compose provider: `uv tool install podman-compose`. If Windows Smart App Control blocks `podman-compose.exe`, run it as a module instead: `uv run --no-project --with podman-compose python -m podman_compose -f infra/docker-compose.yml ...` in place of `podman compose -f infra/docker-compose.yml ...`.

```
cp .env.example .env
podman compose -f infra/docker-compose.yml up -d     # PostgreSQL, Qdrant, n8n
cd backend
python -m venv .venv && .venv/Scripts/activate        # Windows; use .venv/bin/activate elsewhere
pip install -e ".[dev]"                               # or: uv sync --extra dev, then prefix commands with "uv run"
pytest                                                # PostgreSQL tests need RAP_TEST_DATABASE_URL
uvicorn recruiting.api.main:app --reload             # http://localhost:8000/health
python -m recruiting.scheduler                        # second process: follow-ups, retries, re-checks

# Quickest try-out, no database or API key: in-memory storage, rule-based agents, demo users
RAP_STORAGE=memory RAP_LLM_PROVIDER=none RAP_DEMO_USERS=true RAP_RUN_SCHEDULER=true uvicorn recruiting.api.main:app --reload
# Open http://localhost:8000/docs. Sign in by sending a header, e.g.
curl -H "X-Dev-User-Email: bd.manager@cbs.example" http://localhost:8000/approvals/pending

# The web app (Node.js 24+), against a fictional pipeline with work waiting for every role.
# RAP_DEMO_DATA=true implies demo users, rule-based agents and an in-memory outbox (nothing is sent).
RAP_DEMO_DATA=true uvicorn recruiting.api.main:app --port 8000
cd ../web && npm install && npm run dev                # http://localhost:3000, see web/README.md
# Company sign-in instead of demo users: RAP_WEB_AUTH=oidc with RAP_OIDC_ISSUER, RAP_OIDC_CLIENT_ID and
# RAP_WEB_BASE_URL on the web app, and RAP_AUTH_PROVIDER=oidc with RAP_OIDC_AUDIENCE = that client id on the API.

# With PostgreSQL (RAP_STORAGE=postgres), add users first:
#   psql postgresql://rap:rap@localhost:5432/rap -c "INSERT INTO app_user (email, display_name, role) VALUES ('bd@cbs.example', 'BD Manager', 'bd_manager')"

# Everything in containers (API, scheduler and web app too), the same images the cloud runs.
# Easiest: scripts/start.sh [--build] and scripts/stop.sh [--machine] (bash), or
# scripts\start.ps1 [-Build] and scripts\stop.ps1 [-Machine] (PowerShell). They start the Podman
# machine if needed, create .env if missing, wait until healthy and add the demo users to an empty
# database. By hand:
# Reads ../.env; the compose file points the services at each other. Add users as above first.
podman compose -f infra/docker-compose.yml --profile app up --build -d   # web :3000, API :8000
# The web app's demo sign-in lists these five users; with PostgreSQL they must exist in app_user:
podman exec recruiting-agent-platform_postgres_1 psql -U rap -d rap -c "INSERT INTO app_user (email, display_name, role) VALUES ('administrator@cbs.example','Demo Administrator','administrator'), ('bd.manager@cbs.example','Demo BD Manager','bd_manager'), ('recruiter@cbs.example','Demo Recruiter','recruiter'), ('account.manager@cbs.example','Demo Account Manager','account_manager'), ('sales.manager@cbs.example','Demo Sales Manager','sales_manager') ON CONFLICT (email) DO NOTHING"
```

The images are [backend/Dockerfile](../backend/Dockerfile) (API by default; the scheduler is the same image with `python -m recruiting.scheduler`) and [web/Dockerfile](../web/Dockerfile) (Next.js standalone). Image names are fully qualified (`docker.io/...`) because Podman does not assume Docker Hub, and the compose file repeats the health checks because Podman's default OCI image format drops `HEALTHCHECK` (or build with `--format docker`). Neither holds secrets or environment-specific settings: everything is read from `RAP_*` variables at runtime, so one build is promoted from dev to test to prod.

Main endpoints (all need sign-in except the last two):

| Area | Endpoints | Roles |
| --- | --- | --- |
| Approval queue | `GET /approvals/pending`, `GET /approvals/{id}`, `POST /approvals/{id}/decision` | Staff, per gate policy |
| Companies (Agents 1-3) | `GET /companies`, `GET /companies/{id}`, `POST /companies/import`, `POST /companies/hiring-signals`, `POST /companies/outreach-drafts` | View: Admin, BD, Sales, Account; run: Admin, BD |
| Jobs (Agents 4-5) | `GET /jobs`, `GET /jobs/{id}`, `PATCH /jobs/{id}`, `GET /jobs/search?q=` | Staff; Clients see own jobs; edit: Admin, Account, Recruiter |
| Candidates (Agents 6-7) | `POST /candidates` (text), `POST /candidates/upload` (PDF, .docx or text files, multipart), `GET /candidates`, `GET /candidates/{id}`, `POST` / `GET /jobs/{id}/ranking`, `POST /jobs/{id}/ranking/{candidate_id}/review`, `POST /jobs/{id}/ranking/{candidate_id}/move` | Admin, Recruiter, Account |
| Engagement (Agents 8-9) | `POST /jobs/{id}/outreach`, `GET /jobs/{id}/engagements`, `POST /engagements/{id}/outcome`, `POST /engagements/{id}/request-details`, `GET /linkedin-tasks`, `POST /linkedin-tasks/{approval_id}/sent` | Admin, Recruiter (Account: view) |
| Matching and submission (Agents 10-11) | `POST /jobs/{id}/submissions`, `GET /jobs/{id}/submissions`, `GET` / `PATCH /submissions/{id}`, `POST /submissions/{id}/request-approval`, `POST /submissions/{id}/withdraw`, `GET /submissions/{id}/package?format=pdf|docx` (branded package preview; set `RAP_SUBMISSION_ATTACHMENT`, `RAP_BRAND_NAME`, `RAP_BRAND_COLOR`, `RAP_BRAND_LOGO_PATH`) | Admin, Recruiter, Account Manager (request approval: Admin, Recruiter) |
| Client portal submissions | `GET /client/submissions`, `POST /client/submissions/{id}/feedback` | Client (own company) |
| Follow-ups (Agent 12) | `GET /follow-ups`, `GET /follow-ups/summary`, `POST /follow-ups/run`, `POST /follow-ups/{id}/complete`, `.../snooze`, `.../draft`, `POST /submissions/{id}/stage` | All staff; `scope=all`: Admin |
| Follow-up cadence (Q13) | `GET /admin/follow-up-cadence`, `PUT /admin/follow-up-cadence/{kind}` | Admin |
| KPIs and operations (Phase 8) | `GET /kpis?days=30` (staff), `GET /admin/operations`, `GET /admin/access-denied` (Admin) | Staff / Admin |
| Scheduled sweeps | Run by the built-in scheduler (`python -m recruiting.scheduler`, any number of replicas; each run is claimed in the `scheduler_job` table): follow-ups hourly, delivery retries every 5 minutes (S24), career-page re-check daily (S4). The same sweeps can be triggered by hand: `POST /follow-ups/run`, `POST /admin/outreach/retry`, `POST /companies/hiring-signals/run-due`. Last runs and alerts: `GET /admin/operations` | Scheduler process / Admin |
| Candidate erasure (S23) | `POST /candidates/{id}/erase` | Admin |
| Candidate secure link | `GET` / `POST /collect/{token}` | Public (expiring token) |
| Administration | `/admin/users`, `/admin/gate-policies`, `/admin/ranking-weights`, `GET /admin/llm-usage` | Administrator |
| Deep agents | `RAP_AGENTS_ENABLED=true` with `RAP_LLM_PROVIDER=claude` (`RAP_AGENT_EFFORT`, default `high`; `RAP_AGENT_DAILY_RUN_LIMIT`, default 200). `GET /agents`, `POST /agents/{agent}/runs` (recruiting: `subject_id` = job id; business_development; follow_up), `GET /agents/runs`, `GET /agents/runs/{id}`, `POST /agents/runs/{id}/decisions` (approve / edit / reject what a run waits for) | Roles per agent: recruiting = Admin, Recruiter, Account Manager; business_development = Admin, BD Manager; follow_up = all staff |
| Mailbox reader | Built in: `RAP_READ_MAILBOX=true` reads the outreach mailbox (first of `RAP_EMAIL_PROVIDERS`; `RAP_INBOUND_MAILBOX`, default `RAP_EMAIL_SENDER`) every 2 minutes through the scheduler. Token scope: Gmail `gmail.modify`; Microsoft 365 `Mail.ReadWrite` | Scheduler process |
| Inbound replies (alternative to the reader) | `POST /inbound/email` with header `X-RAP-Webhook-Secret`; attachments as `attachments: [{filename, content_base64}]` (PDF, .docx, text, up to 10 MB each, 10 per email) or pre-extracted `attachments_text`; unreadable files come back in `attachment_errors` | Mail-routing workflow |
| Bounce notices (S6, alternative to the reader) | `POST /inbound/bounce` with header `X-RAP-Webhook-Secret`; body `recipient`, `reason`, `hard`, optional `approval_request_id` | Mail-routing workflow |
| Unsubscribe | `GET` / `POST /unsubscribe?token=` | Public (signed link) |

Windows note: Smart App Control can block newly downloaded native Python modules (for example `psycopg_binary`) with "An Application Control policy has blocked this file". It may allow a module after a while, as it did for `ormsgpack`. While `psycopg` is blocked, run with `RAP_STORAGE=memory`; the PostgreSQL tests then run only in CI or in a Linux container.
