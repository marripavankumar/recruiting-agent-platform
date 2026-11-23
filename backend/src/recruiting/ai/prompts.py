"""Prompt templates used by the agents. Bump `version` for any wording change; the gateway
records which version produced each output."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from recruiting.ai.llm import PromptRegistry, PromptTemplate

# ----- Agent 3: first client email -----------------------------------------------------------


class ClientEmailDraft(BaseModel):
    subject: str = Field(description="Specific subject line, at most 80 characters")
    body: str = Field(description="Plain-text email body ending with the sender's sign-off")


FIRST_CLIENT_EMAIL = PromptTemplate(
    name="first_client_email",
    version=1,
    effort="medium",
    max_tokens=4000,
    output=ClientEmailDraft,
    system=(
        "You draft first-contact business emails for CBS LLC, an engineering staffing company. "
        "A BD Manager reviews every draft before it is sent, so write something they can approve "
        "as is.\n\n"
        "Follow the structure of this approved sample, personalised with the facts given:\n"
        "---\n"
        "Dear <Hiring Manager>,\n\n"
        "I noticed your organization is actively hiring engineering professionals.\n\n"
        "CBS LLC specializes in providing highly qualified engineering consultants across "
        "Manufacturing Engineering, Mechanical Design, CAE, Quality, Controls, Electrical, "
        "Automation, Product Development, and Industrial Engineering.\n\n"
        "If you have any open engineering positions, we would appreciate the opportunity to "
        "support your hiring efforts.\n\n"
        "Please share the detailed Job Description(s), and we will promptly identify highly "
        "qualified candidates.\n\n"
        "Thank you.\n\nRegards,\n<Sender>\nCBS LLC\n"
        "---\n"
        "Rules: mention only the open roles and facts provided; never invent numbers, clients or "
        "results; keep the body under 170 words; plain text, no markdown; address the contact "
        "by title and surname when the name is known. Do not add an unsubscribe line or postal "
        "address: the platform appends the compliance footer."
    ),
    user=(
        "Company: $company\nIndustry: $industry\nContact: $contact_name ($contact_title)\n"
        "Open engineering roles seen recently: $roles\nLocation: $location\nSender: $sender_name"
    ),
)

# ----- Agent 4: reply classification and JD extraction --------------------------------------

ReplyCategory = Literal[
    "jd_received", "interested", "jd_to_follow", "not_now", "not_interested", "unsubscribe",
    "out_of_office", "other",
]  # fmt: skip


class ReplyClassification(BaseModel):
    category: ReplyCategory
    summary: str = Field(description="One sentence for the BD Manager")


REPLY_CLASSIFICATION = PromptTemplate(
    name="reply_classification",
    version=1,
    effort="low",
    max_tokens=1000,
    output=ReplyClassification,
    system=(
        "Classify a reply to a staffing company's outreach email. Categories: jd_received (the "
        "reply or its attachments contain at least one job description), interested (wants to "
        "talk, no JD yet), jd_to_follow (promises to send a JD), not_now (maybe later), "
        "not_interested, unsubscribe (asks to stop emails), out_of_office (automatic reply), "
        "other. Judge from the content only."
    ),
    user="Subject: $subject\n\nBody:\n$body\n\nAttachment text:\n$attachments",
)


class ExtractedJob(BaseModel):
    title: str
    responsibilities: str | None
    required_skills: list[str]
    preferred_skills: list[str]
    location: str | None
    experience: str | None
    visa_status: str | None
    education: str | None
    industry: str | None
    travel: str | None
    work_mode: Literal["onsite", "remote", "hybrid"] | None
    salary: str | None
    contract_type: str | None
    priority: Literal["low", "medium", "high", "urgent"] | None
    low_confidence_fields: list[str] = Field(
        description="Names of fields above that are guessed or ambiguous in the source"
    )


class JDExtraction(BaseModel):
    jobs: list[ExtractedJob] = Field(description="One entry per distinct job description")


JD_EXTRACTION = PromptTemplate(
    name="jd_extraction",
    version=1,
    effort="medium",
    max_tokens=16000,
    output=JDExtraction,
    system=(
        "Extract job descriptions from a client email for an engineering staffing company. "
        "Create one entry per distinct position. Copy facts as written; use null when the source "
        "does not say. Put a field name in low_confidence_fields when you had to infer it or the "
        "source is ambiguous (for example visa wording like 'citizens preferred'). Priority is "
        "urgent or high only when the client says so (ASAP, urgent, immediate start)."
    ),
    user="Subject: $subject\n\nBody:\n$body\n\nAttachment text:\n$attachments",
)

# ----- Agent 5: JD analysis -------------------------------------------------------------------


class JDAnalysis(BaseModel):
    must_have_skills: list[str]
    nice_to_have_skills: list[str]
    years_experience_min: int | None
    industry: str | None
    software: list[str]
    cad_tools: list[str]
    manufacturing_methods: list[str]
    erp: list[str]
    plm: list[str]
    lean: bool
    six_sigma: bool
    automation: list[str]
    programming: list[str]
    search_summary: str = Field(description="Two sentences describing the ideal candidate")


JD_ANALYSIS = PromptTemplate(
    name="jd_analysis",
    version=1,
    effort="medium",
    max_tokens=8000,
    output=JDAnalysis,
    system=(
        "Analyse an engineering job description for candidate search. Separate must-have from "
        "nice-to-have skills using the JD's own wording (required/must vs preferred/plus). Use "
        "standard tool names (SolidWorks, CATIA, Creo, NX, ANSYS, HyperMesh, Abaqus, LS-DYNA, "
        "MATLAB, Python, SAP, Teamcenter, Windchill, Enovia, PLC, Rockwell, Siemens, ABB, Fanuc, "
        "GD&T, PFMEA, APQP, PPAP, Control Plan, 8D, DOE, Lean Manufacturing, Six Sigma). List "
        "only what the JD mentions."
    ),
    user="Job title: $title\nClient industry: $industry\n\nJob description:\n$text",
)


# ----- Phase 4: resume parsing (feeds Agents 6, 7 and 10) ------------------------------------


class WorkRole(BaseModel):
    title: str
    company: str | None
    start_year: int | None
    end_year: int | None = Field(description="null when the role is current")


class ResumeProject(BaseModel):
    title: str
    description: str


class ParsedResume(BaseModel):
    full_name: str
    email: str | None
    phone: str | None
    linkedin_url: str | None
    current_location: str | None = Field(description="City, State/Province as written")
    skills: list[str] = Field(description="Tools, software and methods, using standard names")
    experience_years: float | None = Field(description="Total professional engineering years")
    education: str | None = Field(
        description="Highest degree and field, e.g. 'MS Mechanical Engineering'"
    )
    industries: list[str]
    work_history: list[WorkRole] = Field(description="Most recent first")
    projects: list[ResumeProject]
    work_authorization: str | None = Field(description="Only if the resume states it")
    availability: str | None = Field(description="Only if the resume states it")


RESUME_PARSE = PromptTemplate(
    name="resume_parse",
    version=1,
    effort="low",
    max_tokens=8000,
    output=ParsedResume,
    system=(
        "Extract structured data from an engineering resume for a staffing company's candidate "
        "database. Copy facts as written; use null when the resume does not say. Use standard "
        "tool names (LS-DYNA, ANSA, HyperMesh, SolidWorks, CATIA, Creo, NX, PLC, Rockwell, GD&T, "
        "PFMEA, Six Sigma ...). Do not record age, date of birth, gender, marital status, "
        "religion, nationality or photos: they must not influence candidate selection."
    ),
    user="Resume:\n$resume",
)


# ----- Agent 10: resume matching review ------------------------------------------------------


class MatchReview(BaseModel):
    strengths: list[str] = Field(description="Up to 5 evidence-based strengths for this JD")
    concerns: list[str] = Field(description="Up to 5 gaps or risks the recruiter should check")
    key_projects: list[str] = Field(description="Up to 3 resume projects most relevant to the JD")
    summary: str = Field(description="Candidate summary for the client, 2-3 sentences")
    submission_notes: str = Field(
        description="Notes to the hiring manager, under 90 words, facts from the resume only"
    )


MATCH_REVIEW = PromptTemplate(
    name="match_review",
    version=1,
    effort="medium",
    max_tokens=6000,
    output=MatchReview,
    system=(
        "You review how well an engineering candidate fits a job for CBS LLC, a staffing "
        "company. A recruiter edits and approves everything before a client sees it. Compare "
        "the resume with the job description and the structured match provided. Use only facts "
        "in the resume and JD; never invent employers, years, certifications or results. Name "
        "missing must-have skills plainly in the concerns rather than hiding them. Do not "
        "mention or infer age, gender, nationality, ethnicity, religion, marital status or "
        "health. Write the summary and notes in a neutral, professional tone for a hiring "
        "manager; plain text, no markdown. Do not state a match percentage: the platform adds it."
    ),
    user=(
        "Job: $title ($job_code)\nClient industry: $industry\nLocation: $location\n"
        "Must-have skills: $must\nNice-to-have skills: $nice\n\nJob description:\n$jd\n\n"
        "Structured match (platform): $structured\n\nResume:\n$resume"
    ),
)


def default_registry() -> PromptRegistry:
    return PromptRegistry(
        [
            FIRST_CLIENT_EMAIL, REPLY_CLASSIFICATION, JD_EXTRACTION, JD_ANALYSIS, RESUME_PARSE,
            MATCH_REVIEW,
        ]
    )  # fmt: skip
