# Compliance Checklist (Phase 0, activity 6)

**Status:** Draft for legal review, 2026-11-22  
**Scope:** USA and Canada at launch (Q3). Mexico is covered later, after go-live.  
**Purpose:** list every legal and platform-policy obligation the BRD workflow touches, the platform control that meets it, and who must confirm it. Legal sign-off on this checklist is a Phase 0 exit criterion.

> This is a working checklist prepared by the delivery team, not legal advice. Every row needs confirmation by CBS LLC's counsel. Items marked **Legal to confirm** depend on facts about CBS LLC (size, revenue, where it hires) that the team does not have.

**Status values:** `Not reviewed` · `Under review` · `Confirmed` · `Action needed` · `N/A`

## 1. Summary of what needs a decision

These items change what the product does. They need answers before the phase in brackets starts.

| # | Issue | Why it matters | Needed by |
| --- | --- | --- | --- |
| C1 | **LinkedIn connection requests (Agent 8).** LinkedIn has no public API for sending connection requests or InMail. Its User Agreement bans bots, scraping and browser-automation tools. | Agent 8 cannot send requests automatically without breaking LinkedIn terms. Compliant options: (a) the platform drafts the message, the recruiter sends it by hand in LinkedIn or LinkedIn Recruiter, and the platform logs it; (b) integrate through a LinkedIn Talent Solutions partner programme (for example Recruiter System Connect), which requires partner approval. Recommended: (a) at launch, (b) investigated in parallel. | Phase 5 |
| C2 | **LinkedIn as a data source (Agents 1, 2, 6).** The same terms ban automated collection of profile and company data. | LinkedIn adapters must be limited to sanctioned tools (LinkedIn Recruiter or Sales Navigator used by a person, or approved partner APIs). Company and candidate discovery runs mainly on the other licensed providers and job boards. | Phase 2 |
| C3 | **Cold email to Canada (Agent 3).** CASL is opt-in. Unsolicited commercial email needs express or implied consent. | For Canadian contacts, the platform must record the basis for implied consent (for example, the address was published openly with the person's business role and no "no solicitation" notice) or skip the contact. | Phase 2 |
| C4 | **Automated candidate ranking (Agent 7).** AI hiring rules apply in some places, for example NYC Local Law 144 (bias audit and candidate notice), Illinois' Human Rights Act amendment on AI in employment (in force 2026-01-01) and the Colorado AI Act. | If CBS fills roles in those places, ranking may need a yearly independent bias audit, candidate notice and published results. | Phase 4 |
| C5 | **CCPA / CPRA applicability.** It applies to businesses over the revenue or data-volume thresholds, and it covers job applicants and B2B contacts. | If CBS LLC is covered, California candidates and contacts get a notice when their data is collected, plus rights to access, delete and correct it. **Legal to confirm.** | Phase 1 |
| C6 | **Licence terms of data providers and job boards.** ZoomInfo, Apollo, Crunchbase, D&B, Dice, Monster, CareerBuilder, Indeed and ZipRecruiter restrict automated access, bulk export, storage and reuse after a contract ends. | Each adapter can only do what its licence allows. Some data may have to be deleted when a contract ends. Review each contract before its adapter is switched on (Q4, Q5). | Before each adapter is enabled |

## 2. Email outreach (Agents 3, 9, 12 via the Outreach Service)

| # | Requirement | Source | Platform control | Phase | Owner | Status |
| --- | --- | --- | --- | --- | --- | --- |
| E1 | No false or misleading header information (From, To, Reply-To, routing) | CAN-SPAM | Sending identity is fixed per connected mailbox; no spoofed senders | 2 | Integration engineer | Not reviewed |
| E2 | No deceptive subject lines | CAN-SPAM | AI-generated subjects reviewed at the first-email approval gate (§10); rule check against misleading terms | 2 | BD Manager | Not reviewed |
| E3 | Identify the message as a commercial solicitation where required | CAN-SPAM | Template footer wording approved by legal | 2 | Legal | Not reviewed |
| E4 | Include a valid physical postal address of CBS LLC | CAN-SPAM, CASL | Mandatory footer field; the send fails if it is missing | 2 | Admin | Not reviewed |
| E5 | Working opt-out link, kept working for at least 30 days (US) / 60 days (Canada) after sending | CAN-SPAM, CASL | Unsubscribe link plus one-click unsubscribe header (RFC 8058) on every outreach email | 2 | Backend engineer | Not reviewed |
| E6 | Honour opt-outs within 10 business days | CAN-SPAM, CASL | Opt-out registry applied across all agents in under 24 hours (plan Phase 2 exit criterion) | 1, 2 | Backend engineer | Not reviewed |
| E7 | Never sell or transfer opted-out addresses | CAN-SPAM | Opt-out flag blocks export and sync of the address | 1 | Backend engineer | Not reviewed |
| E8 | Consent basis for Canadian recipients (express, or implied with a recorded reason) | CASL | Consent-basis field on Company contacts; send is blocked for CA recipients without one (see C3) | 2 | BD Manager | Not reviewed |
| E9 | Keep records that prove consent (the sender carries the burden of proof) | CASL | Consent source, date and evidence stored with the contact; included in the audit trail | 1 | Backend engineer | Not reviewed |
| E10 | Identify the sender and give contact details in the message | CASL | Footer: CBS LLC name, postal address, plus email or phone or web contact | 2 | Admin | Not reviewed |
| E11 | Email authentication: SPF, DKIM, DMARC on sending domains | Gmail and Outlook.com sender requirements; deliverability | Set up during domain warm-up (activity 7); checked before an adapter goes live | 0 | Integration engineer | Not reviewed |
| E12 | Stay within provider sending limits and acceptable-use terms | Microsoft 365 and Google Workspace terms | Daily send caps per mailbox; BRD volume is 200–500 per week | 2 | Integration engineer | Not reviewed |
| E13 | Disclose open and click tracking | PIPEDA transparency; CCPA notice if applicable | Mention tracking in the privacy notice linked from the footer | 2 | Legal | Not reviewed |
| E14 | Ongoing client and candidate follow-ups count as commercial messages too | CAN-SPAM, CASL | Agent 12 sends only through the Outreach Service, so every rule above applies | 7 | Backend engineer | Not reviewed |

## 3. LinkedIn (Agents 1, 2, 6, 8)

| # | Requirement | Source | Platform control | Phase | Owner | Status |
| --- | --- | --- | --- | --- | --- | --- |
| L1 | No scraping, crawling or automated collection of LinkedIn data | LinkedIn User Agreement | LinkedIn adapters use only sanctioned tools or partner APIs; no headless browsers or extensions | 2, 4 | Solution architect | Not reviewed |
| L2 | No bots or automation that send connection requests or messages | LinkedIn User Agreement | Agent 8 drafts; the recruiter sends by hand or through an approved partner integration (see C1) | 5 | Solution architect | Not reviewed |
| L3 | Use LinkedIn Recruiter or Sales Navigator within each seat's licence terms | LinkedIn licence terms | Only licensed users act; data copied into the platform stays within licence limits | 0 | Legal | Not reviewed |
| L4 | Respect LinkedIn weekly invitation limits | LinkedIn product limits | Approval queue shows each recruiter's weekly count | 5 | Recruiter | Not reviewed |
| L5 | Recruiter approval before any connection request | BRD §10 | Approval-gate engine; no gate approval, no draft released | 1, 5 | Recruiter | Not reviewed |

## 4. Candidate and contact personal data

| # | Requirement | Source | Platform control | Phase | Owner | Status |
| --- | --- | --- | --- | --- | --- | --- |
| P1 | Meaningful consent to collect, use and disclose candidate data, for stated purposes | PIPEDA (Canada); Alberta and BC PIPA | Agent 9 collects consent to store the candidate's data and submit it to named clients (plan Phase 5) | 5 | Recruiter | Not reviewed |
| P2 | Consent per client submission: candidate agrees before their resume goes to a specific client | Staffing practice; PIPEDA purpose limits | Submission gate checks candidate consent for that client | 6 | Recruiter | Not reviewed |
| P3 | Quebec residents: privacy officer, privacy impact assessment before transferring data outside Quebec, stricter consent | Quebec Law 25 | PIA covers US-hosted storage and LLM processing; privacy officer named | 0, 1 | Legal | Not reviewed |
| P4 | Tell people when their data is stored or processed in another country | PIPEDA transparency | Privacy notice lists hosting regions and processors (LLM, resume parser, storage) | 1 | Legal | Not reviewed |
| P5 | Notice at collection and rights to access, delete and correct for California residents, including job applicants and B2B contacts | CCPA / CPRA (if CBS LLC is covered, see C5) | Data-subject request workflow; delete propagates to vector DB, document store and backups | 1 | Legal | Not reviewed |
| P6 | Data minimisation: collect only what the BRD needs | PIPEDA; CCPA | Fields limited to BRD §8 and Agent 9 lists; protected attributes are not collected | 1 | Solution architect | Not reviewed |
| P7 | Retention periods and deletion | PIPEDA; CCPA; EEOC recordkeeping (applications kept at least 1 year) | Retention date on Candidate and Company records; scheduled purge; legal to set periods | 1 | Legal | Not reviewed |
| P8 | Security safeguards suitable for the data's sensitivity (resumes, rates, visa status) | PIPEDA; CCPA; state breach-notification laws | Encryption at rest and in transit, role-based access (6 roles), secrets vault, audit log | 1 | Solution architect | Not reviewed |
| P9 | Breach notification process | PIPEDA (breach of security safeguards); US state laws | Incident-response runbook with notification steps | 1 | Admin | Not reviewed |
| P10 | Processor contracts with sub-processors (Anthropic, OpenRouter and the model providers it routes to, Affinda, cloud and storage providers) | PIPEDA accountability; CCPA service-provider terms | DPAs signed; providers that train on customer data are not used | 0 | Legal | Not reviewed |
| P11 | Client portal shows each client only their own data | BRD §9, Q8 | Tenant-scoped access control tested in Phase 8 | 1, 8 | QA engineer | Not reviewed |
| P12 | GDPR | GDPR | N/A at launch (USA and Canada only); revisit if EU residents are sourced | — | Legal | N/A |

## 5. Hiring fairness and AI use

| # | Requirement | Source | Platform control | Phase | Owner | Status |
| --- | --- | --- | --- | --- | --- | --- |
| F1 | No discrimination on protected characteristics, including disparate impact from scoring | Title VII and EEOC guidance; Canadian Human Rights Act and provincial codes | Protected attributes excluded from ranking inputs; periodic bias audit (plan risk table) | 4, 8 | AI/ML engineer | Not reviewed |
| F2 | Work-authorisation questions worded lawfully (avoid citizenship-status discrimination) | US INA §274B (IER) | Agent 9 asks "Are you legally authorised to work in [country]?" and "Will you need sponsorship?" rather than nationality or citizenship | 5 | Legal | Not reviewed |
| F3 | Bias audit and candidate notice for automated employment decision tools | NYC Local Law 144 | Required only if CBS fills NYC-based roles using Agent 7 scores; legal to confirm | 4 | Legal | Not reviewed |
| F4 | Notice when AI is used in employment decisions, and no discriminatory effect | Illinois Human Rights Act (HB 3773); Colorado AI Act | Applicability depends on where roles and candidates are; legal to confirm current effective dates | 4 | Legal | Not reviewed |
| F5 | A person makes every submission decision | BRD §10 | Agent 7 and Agent 10 only recommend; the recruiter approves each submission | 6 | Recruiter | Not reviewed |
| F6 | Explainable scores | Fairness practice; supports F1 and F3 | Ranking output includes reasons per component (plan Phase 4) | 4 | AI/ML engineer | Not reviewed |

## 6. Third-party licences

| # | Requirement | Platform control | Phase | Owner | Status |
| --- | --- | --- | --- | --- | --- |
| T1 | Review each data provider's terms (ZoomInfo, Apollo, Crunchbase, D&B): API access, storage, export, deletion at contract end | Adapter stays off until its terms are reviewed; records keep their source for later deletion | Before enablement | Legal | Not reviewed |
| T2 | Review each job board's resume-database terms (Dice, CareerBuilder, Monster, Indeed, ZipRecruiter): seats, automated access, storage | Same as T1 | Before enablement | Legal | Not reviewed |
| T3 | Google Custom Search and company-website crawling: API terms, robots.txt, rate limits | Crawler honours robots.txt; Search API used within its terms | 2 | Integration engineer | Not reviewed |
| T4 | Open-source licences: LangGraph (MIT), Qdrant (Apache 2.0), PostgreSQL; n8n Sustainable Use License allows internal use only | n8n used internally, not resold or offered as a hosted service to clients | 0 | Solution architect | Not reviewed |
| T5 | LLM and resume-parser terms (Anthropic, OpenRouter, Affinda): data use, retention, region | Business or enterprise terms under which inputs are not used for training; DPA signed (P10). OpenRouter: the chosen model's provider policy applies too; free models may log prompts, so real candidate data stays on paid models with acceptable terms | 0 | Legal | Not reviewed |

## 7. Questions for counsel

1. Does CBS LLC meet any CCPA / CPRA threshold (C5)?
2. Which US states and Canadian provinces will CBS place candidates in? This decides F3, F4 and P3.
3. For cold B2B email to Canada, is the "conspicuous publication" implied-consent basis acceptable to rely on, and what evidence must be kept (C3, E8, E9)?
4. What retention period applies to candidate records that were never submitted, and to records that were submitted (P7)?
5. Is the manual-send model for LinkedIn (C1 option a) acceptable, or should CBS apply to a LinkedIn partner programme?
6. What wording should the commercial-message and privacy-notice footers use (E3, E13)?

## 8. Sign-off

| Role | Name | Date | Decision |
| --- | --- | --- | --- |
| Legal / compliance advisor | | | |
| Administrator (CBS LLC) | | | |
| Product owner (CBS LLC) | | | |
