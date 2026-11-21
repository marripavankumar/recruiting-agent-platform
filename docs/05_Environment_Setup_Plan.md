# Environment Setup Plan (Phase 0, activity 7)

**Status:** Draft, 2026-11-22  
**Decision:** cloud-agnostic, plug-and-play hosting. The BRD names no hosting cloud, so the platform must run on Azure, AWS, Google Cloud or on-premises without code changes. Choosing or changing a cloud is a configuration and infrastructure-module choice.

## 1. How cloud plug-and-play works

The platform follows the same rule as the business integrations (plan Section 5.5): **application code depends on interfaces, never on a cloud SDK directly.**

1. **Everything runs in containers.** Backend API, agent workers, web app, Qdrant and n8n are container images. They run the same way on a laptop (Docker Compose), a Kubernetes cluster or a cloud container service.
2. **Cloud services sit behind adapters.** Each cloud-specific service has one interface in the code and one adapter per cloud. The active adapter is set by configuration (`RAP_*` environment variables).
3. **Infrastructure is code, one module per cloud.** Terraform modules share one set of inputs and outputs (`infra/terraform/modules/<cloud>`). An environment picks its cloud by choosing a module; the outputs (database URL, vault name, bucket) feed the same application settings.
4. **Only open standards cross the boundary:** PostgreSQL wire protocol, S3-compatible or native object-storage adapters, OpenID Connect for sign-in, OpenTelemetry for logs, metrics and traces, and OCI container images.

### Service mapping per cloud

| Platform need | Interface / standard | Azure | AWS | Google Cloud | On-premises / local |
| --- | --- | --- | --- | --- | --- |
| Container runtime | OCI images | Azure Container Apps or AKS | ECS Fargate or EKS | Cloud Run or GKE | Docker Compose or Kubernetes |
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
| Local | Developer laptops | Synthetic only | `docker compose up` | Developers |
| Dev | Shared integration | Synthetic only | Automatically on merge to `main` | Delivery team |
| Test | System and UAT testing (Phase 8) | Synthetic plus anonymised samples approved by legal | On release tag | Delivery team, CBS UAT users |
| Prod | Live operation | Real company, job and candidate data | Manual approval after test sign-off | CBS users by role; ops on-call |

- Real candidate or contact data never leaves prod (compliance checklist P6, P8).
- Outbound email and LinkedIn tasks are disabled in local and dev. In test, outreach can go only to an allow-list of internal addresses.
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
| 7 | OpenID Connect sign-in and the 6 roles | Not started (Phase 1) |
| 8 | Sending subdomain, SPF / DKIM / DMARC, warm-up | Not started |
| 9 | Backups and restore test for PostgreSQL and Qdrant | Not started |
| 10 | Observability (OpenTelemetry export) | Not started |

## 6. Running the scaffold locally

Prerequisites: Python 3.12+, Docker Desktop.

```
cp .env.example .env
docker compose -f infra/docker-compose.yml up -d     # PostgreSQL, Qdrant, n8n
cd backend
python -m venv .venv && .venv/Scripts/activate        # Windows; use .venv/bin/activate elsewhere
pip install -e ".[dev]"
pytest
uvicorn recruiting.api.main:app --reload             # http://localhost:8000/health
```
