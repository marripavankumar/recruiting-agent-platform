"""Company, Job and Candidate records (BRD section 8, plan section 5.1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class OutreachStatus(StrEnum):
    NOT_CONTACTED = "not_contacted"
    CONTACTED = "contacted"
    REPLIED = "replied"
    CLIENT = "client"
    DO_NOT_CONTACT = "do_not_contact"


class ContactRole(StrEnum):
    """Agent 1 output: the contact roles the BRD asks for."""

    HIRING_MANAGER = "hiring_manager"
    ENGINEERING_MANAGER = "engineering_manager"
    HR_MANAGER = "hr_manager"
    TALENT_ACQUISITION = "talent_acquisition"
    DIRECTOR_OF_ENGINEERING = "director_of_engineering"
    RECRUITER = "recruiter"
    OTHER = "other"


class ConsentBasis(StrEnum):
    """CASL consent basis for commercial email (compliance checklist E8, E9)."""

    NONE = "none"
    EXPRESS = "express"
    IMPLIED_CONSPICUOUS_PUBLICATION = "implied_conspicuous_publication"
    IMPLIED_EXISTING_RELATIONSHIP = "implied_existing_relationship"
    NOT_REQUIRED = "not_required"


@dataclass
class Contact:
    id: str
    company_id: str
    full_name: str
    title: str | None
    role: ContactRole
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    country: str | None = None  # ISO 3166 alpha-2
    consent_basis: ConsentBasis = ConsentBasis.NONE
    source: str = "manual"
    email_valid: bool = True  # False after a hard bounce (scenario S6); Agent 3 skips it


@dataclass
class Company:
    id: str
    name: str
    domain: str  # de-duplication key, e.g. "northfield-axle.com"
    source: str
    industry: str | None = None
    revenue_usd: float | None = None
    employees: int | None = None
    country: str | None = None
    locations: list[str] = field(default_factory=list)
    website: str | None = None
    linkedin_url: str | None = None
    career_page_url: str | None = None
    is_client: bool = False
    outreach_status: OutreachStatus = OutreachStatus.NOT_CONTACTED
    priority_score: int = 0
    hiring_history: list[dict[str, Any]] = field(default_factory=list)
    last_verified_at: datetime | None = None
    created_at: datetime | None = None  # first stored (KPI: companies identified)


@dataclass
class JobPosting:
    """Agent 2 output: a public posting that signals the company is hiring engineers."""

    id: str
    company_id: str
    title: str
    target_role: str | None  # one of the BRD section 4 positions, or None if not a target
    url: str
    location: str | None = None
    hiring_manager: str | None = None
    date_posted: date | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    active: bool = True


class JobStatus(StrEnum):
    OPEN = "open"
    ON_HOLD = "on_hold"
    FILLED = "filled"
    CLOSED = "closed"


class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


@dataclass
class Job:
    """Job Database record (BRD section 8) with the Agent 4 fields and Agent 5 analysis."""

    id: str
    job_code: str  # the BRD "Job ID", e.g. JOB-2026-0142
    client_company_id: str
    title: str
    status: JobStatus = JobStatus.OPEN
    priority: Priority = Priority.MEDIUM
    recruiter_id: str | None = None
    responsibilities: str | None = None
    required_skills: list[str] = field(default_factory=list)
    preferred_skills: list[str] = field(default_factory=list)
    location: str | None = None
    experience: str | None = None
    visa_requirements: str | None = None
    education: str | None = None
    industry: str | None = None
    travel: str | None = None
    work_mode: str | None = None  # onsite | remote | hybrid
    salary: str | None = None
    contract_type: str | None = None
    source_email_ref: str | None = None
    # Fields Agent 4 was unsure about, for the Account Manager to confirm.
    review_fields: list[str] = field(default_factory=list)
    analysis: dict[str, Any] = field(default_factory=dict)  # Agent 5 search profile
    created_at: datetime | None = None


class ConsentStatus(StrEnum):
    NONE = "none"
    GRANTED = "granted"
    WITHDRAWN = "withdrawn"


@dataclass
class Candidate:
    """Candidate Database record (BRD section 8)."""

    id: str
    full_name: str
    source: str
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    current_location: str | None = None
    preferred_location: str | None = None
    skills: list[str] = field(default_factory=list)
    projects: list[dict[str, Any]] = field(default_factory=list)
    experience_years: float | None = None
    availability: str | None = None
    notice_period: str | None = None
    visa_status: str | None = None
    work_authorization: str | None = None
    expected_rate: str | None = None
    resume_text: str | None = None
    consent_status: ConsentStatus = ConsentStatus.NONE
    # Added in Phase 4 (migration 0005).
    education: str | None = None  # highest degree and field, e.g. "MS Mechanical Engineering"
    industries: list[str] = field(default_factory=list)
    # Most recent first: {"title", "company", "start_year", "end_year" (None = present)}
    work_history: list[dict[str, Any]] = field(default_factory=list)
    # Q9 inputs: {"reply_hours": float, "completeness": 0..1, "recruiter_rating": 1..5}
    communication: dict[str, Any] = field(default_factory=dict)


class Verdict(StrEnum):
    """Recruiter review of a ranked candidate (Phase 4 exit criterion: >= 10 of 20 qualified)."""

    QUALIFIED = "qualified"
    NOT_QUALIFIED = "not_qualified"
    EXCLUDED = "excluded"


@dataclass
class RankedCandidate:
    candidate_id: str
    score: int  # 0-100
    components: dict[str, float]  # each BRD component, 0..1
    strengths: list[str]
    gaps: list[str]
    rank: int | None = None  # None: excluded by a hard filter
    excluded_reason: str | None = None
    verdict: Verdict | None = None
    note: str | None = None


@dataclass
class Ranking:
    job_id: str
    ranked_at: datetime
    weights: dict[str, float]
    candidates: list[RankedCandidate] = field(default_factory=list)  # ranked first, then excluded
    searched: int = 0  # candidates considered by Agent 6
    widened: dict[str, Any] | None = None  # S12: who widened the search, when and how

    @property
    def top(self) -> list[RankedCandidate]:
        return [
            c for c in self.candidates if c.rank is not None and c.verdict is not Verdict.EXCLUDED
        ]

    @property
    def qualified_in_top_20(self) -> int:
        return sum(1 for c in self.top[:20] if c.verdict is Verdict.QUALIFIED)


class EngagementStatus(StrEnum):
    """Agents 8-9 tracking per job and candidate (plan section 9)."""

    DRAFTED = "drafted"  # outreach waiting in the recruiter's approval queue
    DRAFT_REJECTED = "draft_rejected"  # the recruiter rejected the draft; nothing was sent
    CONTACTED = "contacted"  # email sent or LinkedIn note queued for the recruiter
    ACCEPTED = "accepted"  # LinkedIn connection accepted
    REPLIED = "replied"
    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    DO_NOT_CONTACT = "do_not_contact"
    DETAILS_REQUESTED = "details_requested"  # Agent 9 request sent, items outstanding
    COMPLETE = "complete"  # resume, the eight details and consent received: ready for Agent 10
    SUBMITTED = "submitted"  # Agent 11 sent the approved package to the client


# Agent 9: what a candidate supplies before matching (BRD Agent 9).
DETAIL_ITEMS = (
    "availability", "visa_status", "current_location", "expected_rate", "notice_period",
    "work_authorization", "preferred_location",
)  # fmt: skip
REQUIRED_ITEMS = ("resume", *DETAIL_ITEMS, "consent")


@dataclass
class Engagement:
    id: str
    job_id: str
    candidate_id: str
    status: EngagementStatus = EngagementStatus.DRAFTED
    # kind -> approval request id: "linkedin", "email", "details_request"
    approvals: dict[str, str] = field(default_factory=dict)
    token_hash: str | None = None  # Agent 9 upload link (only the hash is stored)
    token_expires_at: datetime | None = None
    requested_at: datetime | None = None
    consent_store_at: datetime | None = None
    consent_submit_at: datetime | None = None  # consent to be submitted for this job's client
    consent_evidence: str | None = None
    missing: list[str] = field(default_factory=list)
    next_reminder_at: datetime | None = None  # picked up by Agent 12
    history: list[dict[str, Any]] = field(default_factory=list)
    updated_at: datetime | None = None

    def log(self, at: datetime, event: str, **details: Any) -> None:
        self.history.append({"at": at.isoformat(), "event": event, **details})
        self.updated_at = at


class SubmissionStatus(StrEnum):
    """Agents 10-11 and the recruiter approval step (plan section 10)."""

    MATCHED = "matched"  # Agent 10 report ready; the recruiter reviews and edits the notes
    PENDING_APPROVAL = "pending_approval"  # package waiting at the resume-submission gate
    REJECTED = "rejected"  # the approver rejected it; nothing was sent, reason kept for tuning
    SUBMITTED = "submitted"  # sent to the client; the sent version is kept for audit
    FAILED = "failed"  # approved but the email could not be delivered
    WITHDRAWN = "withdrawn"  # the recruiter withdrew it before approval


class PlacementStage(StrEnum):
    """Pipeline after a submission is sent (plan section 11): Submitted -> Interview -> Offer ->
    Started, or rejected / withdrawn at any step."""

    SUBMITTED = "submitted"
    INTERVIEW_REQUESTED = "interview_requested"
    INTERVIEW_SCHEDULED = "interview_scheduled"
    INTERVIEWED = "interviewed"
    OFFER_EXTENDED = "offer_extended"
    OFFER_ACCEPTED = "offer_accepted"
    STARTED = "started"
    CLIENT_REJECTED = "client_rejected"
    OFFER_DECLINED = "offer_declined"
    CANDIDATE_WITHDRAWN = "candidate_withdrawn"


# Which stage may follow which (recorded by staff; the client portal sets the first steps).
STAGE_NEXT: dict[PlacementStage, set[PlacementStage]] = {
    PlacementStage.SUBMITTED: {
        PlacementStage.INTERVIEW_REQUESTED, PlacementStage.INTERVIEW_SCHEDULED,
        PlacementStage.CLIENT_REJECTED,
    },
    PlacementStage.INTERVIEW_REQUESTED: {PlacementStage.INTERVIEW_SCHEDULED},
    PlacementStage.INTERVIEW_SCHEDULED: {
        PlacementStage.INTERVIEW_SCHEDULED, PlacementStage.INTERVIEWED,
    },
    PlacementStage.INTERVIEWED: {
        PlacementStage.INTERVIEW_SCHEDULED, PlacementStage.OFFER_EXTENDED,
        PlacementStage.CLIENT_REJECTED,
    },
    PlacementStage.OFFER_EXTENDED: {PlacementStage.OFFER_ACCEPTED, PlacementStage.OFFER_DECLINED},
    PlacementStage.OFFER_ACCEPTED: {PlacementStage.STARTED},
    PlacementStage.STARTED: set(),
    PlacementStage.CLIENT_REJECTED: set(),
    PlacementStage.OFFER_DECLINED: set(),
    PlacementStage.CANDIDATE_WITHDRAWN: set(),
}  # fmt: skip
CLOSED_STAGES = {
    PlacementStage.STARTED, PlacementStage.CLIENT_REJECTED, PlacementStage.OFFER_DECLINED,
    PlacementStage.CANDIDATE_WITHDRAWN,
}  # fmt: skip


class ClientDecision(StrEnum):
    """Client portal feedback on a submission (Q8)."""

    INTERVIEW = "interview"
    HOLD = "hold"
    REJECTED = "rejected"


@dataclass
class Submission:
    id: str
    job_id: str
    candidate_id: str
    client_company_id: str
    status: SubmissionStatus = SubmissionStatus.MATCHED
    # Agent 10: score, strengths, missing_skills, interview_probability, notes, flags ...
    match: dict[str, Any] = field(default_factory=dict)
    package: dict[str, Any] = field(default_factory=dict)  # Agent 11: the nine BRD items
    recipient: str | None = None  # client hiring manager's email
    approval_request_id: str | None = None
    right_to_represent_at: datetime | None = None
    right_to_represent_by: str | None = None
    warnings: list[str] = field(default_factory=list)
    rejection_reason: str | None = None
    sent_at: datetime | None = None
    sent_content: dict[str, str] = field(default_factory=dict)  # exactly what was sent
    follow_up_at: datetime | None = None  # client follow-up, picked up by Agent 12
    client_decision: ClientDecision | None = None
    client_feedback: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Phase 7 pipeline (migration 0008).
    stage: PlacementStage | None = None  # set once the package is sent
    stage_at: datetime | None = None
    interview_at: datetime | None = None
    offer_at: datetime | None = None
    start_date: date | None = None
    stage_reason: str | None = None

    def log(self, at: datetime, event: str, **details: Any) -> None:
        self.history.append({"at": at.isoformat(), "event": event, **details})
        self.updated_at = at


class FollowUpKind(StrEnum):
    """Agent 12 follow-up types (BRD Agent 12, plan section 11)."""

    CLIENT_OUTREACH = "client_outreach"  # first client email with no reply
    SUBMISSION_FEEDBACK = "submission_feedback"  # submission with no client decision
    CANDIDATE_OUTREACH = "candidate_outreach"  # candidate contacted, no reply
    CANDIDATE_DETAILS = "candidate_details"  # resume, details or consent still missing
    INTERVIEW_SCHEDULING = "interview_scheduling"  # client asked for an interview
    INTERVIEW_PREP = "interview_prep"  # confirm the time and prepare the candidate
    INTERVIEW_FEEDBACK = "interview_feedback"  # interview held, no outcome logged
    OFFER = "offer"  # offer extended: track the decision and terms
    START_DATE = "start_date"  # offer accepted: documents and start-date check-ins
    CANDIDATE_SHORTFALL = "candidate_shortfall"  # S12: fewer than 10 qualified for a job


# Kinds where a follow-up is a message to the client or candidate (approval gate applies).
MESSAGE_KINDS = {
    FollowUpKind.CLIENT_OUTREACH, FollowUpKind.SUBMISSION_FEEDBACK,
    FollowUpKind.CANDIDATE_OUTREACH, FollowUpKind.CANDIDATE_DETAILS,
    FollowUpKind.INTERVIEW_FEEDBACK,
}  # fmt: skip


class FollowUpStatus(StrEnum):
    OPEN = "open"  # has a due date
    DONE = "done"  # resolved: the record moved on, or every step was completed
    STOPPED = "stopped"  # the cadence limit was reached with no response (scenario S8)
    CANCELLED = "cancelled"  # job closed, opted out or withdrawn


@dataclass
class FollowUp:
    id: str
    kind: FollowUpKind
    subject_id: str  # company, engagement or submission id
    owner_role: str
    anchor_at: datetime  # the event the cadence counts from
    due_at: datetime | None
    status: FollowUpStatus = FollowUpStatus.OPEN
    attempt: int = 0  # follow-ups done or drafted so far
    job_id: str | None = None
    candidate_id: str | None = None
    company_id: str | None = None
    owner_id: str | None = None  # a named user, e.g. the job's recruiter
    note: str = ""
    approvals: list[str] = field(default_factory=list)  # drafted follow-up messages
    context: dict[str, Any] = field(default_factory=dict)  # recipient, subject, names
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def log(self, at: datetime, event: str, **details: Any) -> None:
        self.history.append({"at": at.isoformat(), "event": event, **details})
        self.updated_at = at
