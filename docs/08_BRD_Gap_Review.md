# BRD Gap Review

**Date:** 2026-11-25
**Method:** every requirement in the BRD (sections 1 to 12, all 12 agents) checked against the code and the running application, not against the plan's status notes. Where the plan said something was built, the code was checked for it.

## 1. Summary

Most of the BRD is built as written. Five gaps are fixed (three in the review, G6 and G5 since). Three remain, two of which need a decision or outside input. The plan overstated five integration claims; they are corrected.

| # | Gap | BRD | Status |
| --- | --- | --- | --- |
| G1 | Postings titled "Manufacturing Manager" or "Production Manager" were ignored: Agent 2 searches for this role, section 4 does not list it, and only the 27 section 4 roles were matched | Agent 2 | **Fixed:** 28th target role, one of the 10 strongest-fit roles |
| G2 | "Siemens PLCs", "Siemens S7" and "SIMATIC" were read only as generic PLC experience (Controls and PLC Engineer roles) | Agent 5 example skills | **Fixed:** synonyms added; "Siemens" alone still means nothing, since it is also an employer and makes NX and Teamcenter |
| G3 | The Candidate Database could be searched by one text box only; none of the Agent 6 candidate filters existed for recruiters | Agent 6 | **Fixed:** all eleven filters in the API (`GET /candidates`) and on the Candidates page |
| G4 | Open tracking is not wired: the delivery log can record an open, but nothing does (emails are plain text, no tracking pixel) | Agent 3 | **Open, decision needed** (section 3) |
| G5 | Semantic search used a hashing embedder, which matches words, not meaning | §7 | **Fixed (option b):** open-source BGE-small model on the CPU in the container, mixed with the skill dictionary |
| G6 | Original resume files were not stored, only their extracted text; no document store adapter existed | §11, Agents 9 and 11 | **Fixed (S3-compatible):** originals kept, downloadable, attached to the client email as approved, erased with the candidate. SharePoint and OneDrive still open |
| G7 | CRM/ATS sync adapters (Bullhorn, RecruitCRM, Zoho Recruit, Salesforce) do not exist, although the plan said they were included | §11 | **Open, low priority:** sync is optional (Q6, the platform is the system of record) |
| G8 | Licensed data and source adapters do not exist: company data providers (Apollo, ZoomInfo, Crunchbase, D&B), job boards (Dice, CareerBuilder, Monster, Indeed, ZipRecruiter) and Google Custom Search for Agent 2 | Agents 1, 2, 6; §11 | **Open, needs contracts and API access.** The plan already listed the providers and job boards as open; industry-database lists import as CSV today |

## 2. Checked and found as the BRD asks

| BRD | Checked |
| --- | --- |
| §3, Agent 1 industries | All 14 (the union of both lists, Q1) |
| §4 positions | All 27, plus Manufacturing Manager (G1) |
| Agent 1 | Revenue over $100 million, USA and Canada (Mexico after go-live, Q3); every output field, including the six contact roles; LinkedIn used only through sanctioned tools (compliance C2) |
| Agent 2 | Every output field (title, location, hiring manager, link, date posted, priority score); stale postings expire |
| Agent 3 | Personalised drafts from the BRD template, subject, follow-up reminders (Agent 12), delivery and reply tracking; opens not tracked (G4) |
| Agent 4 | All 14 fields extracted into the Job Database; unsure fields go to the Account Manager |
| Agent 5 | Every analysis category (must-have, nice-to-have, years, industry, software, CAD, manufacturing methods, ERP, PLM, Lean, Six Sigma, automation, programming); 27 of 28 example skills recognised, the 28th ("Siemens") deliberately not on its own |
| Agent 6 | Internal database and licensed-source interface; all eleven filters (G3) |
| Agent 7 | All ten score components with Administrator weights, 0-100 score, Top 20, reasons |
| Agent 8 | BRD message, recruiter approval; sent by hand in LinkedIn and logged (Q15, LinkedIn terms) |
| Agent 9 | Resume and all seven details requested, with consent |
| Agent 10 | Match score, strengths, missing skills, interview probability, submission notes |
| Agent 11 | All nine package items, branded PDF or Word |
| Agent 12 | All six follow-up types, plus the daily staff digest |
| §7 AI components | NLP, resume and JD parsing, LLM, vector database, embedding search with a sentence-embedding model (G5), recommendations (ranking and next steps); RAG as retrieval feeding the LLM in matching |
| §8 databases | Every listed field of the Company, Job and Candidate databases |
| §9 roles | All six, enforced by the API on every route |
| §10 gates | All four; nothing is sent without approval |
| §12 KPIs | All nine, with target and status (`GET /kpis`, dashboard) |

## 3. Decisions needed

**G4. Open tracking.** Tracking opens needs HTML email with a tracking image. Recommended: build it, but report opens as indicative only. Apple Mail and some corporate filters load images automatically, so open rates run high, and replies (already tracked) are the reliable signal. The privacy notice should mention it (compliance checklist P4). Cost: HTML version of each approved email, a signed image link, one endpoint.

**G5. Embedding model (done, option b, 2026-11-25).** BAAI/bge-small-en-v1.5 (MIT licence, quantised ONNX, about 70 MB) runs on the CPU inside the API and scheduler containers through fastembed; the model is in the image, so nothing is downloaded at run time and no text leaves the machine (no new processor, P10). Its vector is mixed 70/30 with the skill-dictionary vector: measured on engineering JD and resume pairs, the model alone barely separated matching pairs from other engineering pairs (0.71 vs up to 0.72), the mix separates them clearly (0.70-0.84 vs at most 0.51). Search, ranking (project similarity) and matching (resume-to-JD similarity) use the same embedder on one calibrated 0-1 scale. Each embedder has its own Qdrant collections; `python -m recruiting.reindex` and an hourly scheduler check fill them after a change. Embedding 50 long resumes takes about 2 seconds. Still to do: check search quality on real CBS JDs and resumes in the pilot. An embedding API (option a, e.g. Voyage AI) fits the same adapter if quality calls for it, with a DPA.

**G6. Document storage (done for S3-compatible stores, 2026-11-25).** Original resume files from recruiter uploads and the candidate's secure link are kept in an S3-compatible store (SeaweedFS in the Podman stack, since MinIO no longer publishes images; AWS S3 or a cloud equivalent later). Recruiters download the original; the submission email attaches it next to the package, and the approval covers that exact file (SHA-256); erasure and test-data cleanup delete the files. Still open: SharePoint and OneDrive adapters if CBS wants files in Microsoft 365, and keeping originals from resumes attached to candidate email replies (their text is read today).

## 4. Interpretations to confirm

These follow decisions already recorded in the plan; listed so the product owner can confirm them in one place.

- Agent 8 "send LinkedIn connection requests": the platform drafts and the recruiter sends by hand (Q15), since LinkedIn has no public API for it and bans automation.
- "Quality Manager" postings count as Quality Engineer hiring signals: they show engineering hiring at that company.
- Mexico is out of scope until after go-live (Q3).
