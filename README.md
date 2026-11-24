# AI Agent Platform for Engineering Staffing & Client Acquisition

An AI-powered multi-agent platform for CBS LLC. It automates the end-to-end recruiting and business-development process: it finds prospective manufacturing clients, acquires open job requirements, sources and ranks engineering candidates, engages them, collects resumes and submits approved candidates to clients. Human approval gates and LinkedIn / privacy compliance are built in.

## Documents

| Document | Description |
| --- | --- |
| [docs/01_Business_Requirements_Document.md](docs/01_Business_Requirements_Document.md) | Business Requirements Document (BRD), Markdown version |
| [docs/02_Implementation_Plan.md](docs/02_Implementation_Plan.md) | Phased implementation plan with status, scenarios, traceability and risks |
| [docs/03_Compliance_Checklist.md](docs/03_Compliance_Checklist.md) | Compliance checklist (draft for legal review): email, LinkedIn, privacy, hiring fairness, licences |
| [docs/04_KPI_Baseline_Timing_Study.md](docs/04_KPI_Baseline_Timing_Study.md) | Timing-study and KPI baseline template for the 80% effort goal (Q12) |
| [docs/05_Environment_Setup_Plan.md](docs/05_Environment_Setup_Plan.md) | Cloud-agnostic hosting, environments, CI/CD, sending domains, local setup |
| [docs/source/Business_Requirements_Document.docx](docs/source/Business_Requirements_Document.docx) | Original BRD (Word) |

## Implementation status (as of 2026-11-24)

| Phase | Name | Agents / BRD scope | Weeks | Status | % Done |
| --- | --- | --- | --- | --- | --- |
| 0 | Discovery and setup | §1, §2, §11, §12 | 1–3 | In progress | 80% |
| 1 | Core platform and data foundation | §7, §8, §9, §10 | 3–8 | In progress | 75% |
| 2 | Client acquisition | Agents 1–3 | 7–12 | In progress | 60% |
| 3 | JD intake and analysis | Agents 4–5 | 11–15 | In progress | 70% |
| 4 | Candidate search and ranking | Agents 6–7 | 14–19 | In progress | 60% |
| 5 | Candidate engagement | Agents 8–9 | 18–22 | In progress | 60% |
| 6 | Matching and client submission | Agents 10–11 | 21–25 | In progress | 70% |
| 7 | Follow-up | Agent 12 | 24–27 | In progress | 70% |
| 8 | E2E testing, KPI dashboard, go-live | §12 | 26–32 | In progress | 45% |
| 9 | Future enhancements | Future Enhancements | After go-live | Not started | 0% |

Full details, exit criteria and status notes are in the [implementation plan](docs/02_Implementation_Plan.md#implementation-phases-and-current-status).

## Decisions log

| Date | Question | Decision |
| --- | --- | --- |
| 2026-11-22 | Q1 Target industries | Union of BRD §3 and Agent 1 lists (14 industries) |
| 2026-11-22 | Q2 Hiring discovery roles | All 27 §4 positions |
| 2026-11-22 | Q3 Mexico | USA and Canada at launch; Mexico after go-live |
| 2026-11-22 | Q6 ATS/CRM | None; platform DBs are the system of record; optional sync adapters for Bullhorn, RecruitCRM, Zoho Recruit, Salesforce |
| 2026-11-22 | Q4 Data providers | Pluggable connectors with adapters for all BRD providers (LinkedIn, websites, industry DBs, ZoomInfo, Apollo, Crunchbase, D&B) |
| 2026-11-22 | Q5 Job boards | Pluggable connectors with adapters for all BRD sources (LinkedIn, Internal ATS, Company DB, Resume DB, Dice, CareerBuilder, Monster, Indeed Resume, ZipRecruiter, Recruiting CRM) |
| 2026-11-22 | Q11 Email platform | Both Microsoft 365 and Gmail API adapters |
| 2026-11-22 | Q8 Client role | Full client portal: submissions, feedback and interview / offer decisions, JD upload, job and candidate status tracking |
| 2026-11-22 | Q9 Communication Score | Configurable weighted rules: reply speed, completeness, recruiter rating |
| 2026-11-22 | Q10 Priority Score | Configurable weighted rules: posting age, open roles, role fit |
| 2026-11-22 | Q13 Follow-up cadence | Configurable; default 3 follow-ups at 3, 7, 14 days |
| 2026-11-22 | Q14 Approvers | Configurable per gate with optional second approver |
| 2026-11-22 | Orchestration and open-source defaults | LangGraph (MIT) for agent orchestration and approval-gate interrupts; PostgreSQL; Qdrant (Apache 2.0, self-hosted); n8n (self-hosted). Commercial-only layers (LLM, resume parsing, data providers) stay as pluggable adapters |
| 2026-11-22 | Q7 Candidate email owner | Shared channel-agnostic Outreach Service behind the approval gate, used by Agents 8, 9, 12 |
| 2026-11-22 | Default LLM and resume parser | Claude is the LLM gateway default; Affinda is the resume-parsing default. Other BRD options stay as adapters |
| 2026-11-22 | Email and document storage | No fixed default: adapters for Microsoft 365, Gmail, SharePoint, OneDrive and AWS S3; active ones set by configuration |
| 2026-11-22 | Q15 LinkedIn connection requests | Agent 8 drafts, recruiter approves and sends by hand, platform logs it; partner integration explored. Pending legal confirmation |
| 2026-11-22 | Hosting | Cloud-agnostic, plug-and-play: containers on Azure, AWS, Google Cloud or on-premises; cloud services behind adapters and per-cloud Terraform modules |
| 2026-11-22 | Application stack | Python 3.12 + FastAPI (backend, LangGraph agents); Next.js (web app, client portal) |

## Repository layout

| Path | Contents |
| --- | --- |
| [backend/](backend/) | Python 3.12, FastAPI, LangGraph: the 12 agents and the deep-agent layer, approval-gate engine, Outreach Service, opt-out registry, connectors, scheduler, mailbox reader |
| [web/](web/) | Next.js web app and client portal: dashboard, approval queue, jobs and candidates, follow-ups, AI agents, client portal |
| [mock-ui/](mock-ui/) | Static clickable mock-up: the reference for screens not built in the web app yet |
| [db/migrations/](db/migrations/) | PostgreSQL schema |
| [infra/](infra/) | Local Docker Compose stack (PostgreSQL, Qdrant, n8n); per-cloud Terraform modules to come |
| [docs/](docs/) | BRD, plan, checklists |

To run locally, see [section 6 of the environment plan](docs/05_Environment_Setup_Plan.md#6-running-the-scaffold-locally).

## Next step

Connect the web app to CBS LLC's identity provider and run UAT with every role; run a supervised trial of the deep agents with the real model; and close the Phase 0 items that need CBS LLC: legal review of the [compliance checklist](docs/03_Compliance_Checklist.md) (including Q15), the Q12 [timing study](docs/04_KPI_Baseline_Timing_Study.md), licences and the first cloud environment.
