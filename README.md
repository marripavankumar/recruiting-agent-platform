# AI Agent Platform for Engineering Staffing & Client Acquisition

An AI-powered multi-agent platform for CBS LLC. It automates the end-to-end recruiting and business-development process: it finds prospective manufacturing clients, acquires open job requirements, sources and ranks engineering candidates, engages them, collects resumes and submits approved candidates to clients. Human approval gates and LinkedIn / privacy compliance are built in.

## Documents

| Document | Description |
| --- | --- |
| [docs/01_Business_Requirements_Document.md](docs/01_Business_Requirements_Document.md) | Business Requirements Document (BRD), Markdown version |
| [docs/02_Implementation_Plan.md](docs/02_Implementation_Plan.md) | Phased implementation plan with status, scenarios, traceability and risks |
| [docs/source/Business_Requirements_Document.docx](docs/source/Business_Requirements_Document.docx) | Original BRD (Word) |

## Implementation status (as of 2026-11-22)

| Phase | Name | Agents / BRD scope | Weeks | Status | % Done |
| --- | --- | --- | --- | --- | --- |
| 0 | Discovery and setup | §1, §2, §11, §12 | 1–3 | In progress | 45% |
| 1 | Core platform and data foundation | §7, §8, §9, §10 | 3–8 | Not started | 0% |
| 2 | Client acquisition | Agents 1–3 | 7–12 | Not started | 0% |
| 3 | JD intake and analysis | Agents 4–5 | 11–15 | Not started | 0% |
| 4 | Candidate search and ranking | Agents 6–7 | 14–19 | Not started | 0% |
| 5 | Candidate engagement | Agents 8–9 | 18–22 | Not started | 0% |
| 6 | Matching and client submission | Agents 10–11 | 21–25 | Not started | 0% |
| 7 | Follow-up | Agent 12 | 24–27 | Not started | 0% |
| 8 | E2E testing, KPI dashboard, go-live | §12 | 26–32 | Not started | 0% |
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
| 2026-11-22 | Q7 Candidate email owner | Shared channel-agnostic Outreach Service behind the approval gate, used by Agents 8, 9, 12 |

## Next step

Answer the 6 remaining open questions (Q8, Q9, Q10, Q12, Q13, Q14) in [Section 2 of the plan](docs/02_Implementation_Plan.md#2-assumptions-and-open-questions) to complete Phase 0.
