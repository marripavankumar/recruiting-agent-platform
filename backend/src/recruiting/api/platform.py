"""The records, AI services and agents behind the business APIs, built from settings."""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from recruiting.agents.candidate_outreach import CandidateOutreachAgent
from recruiting.agents.candidate_search import CandidateSearchAgent, CandidateSource
from recruiting.agents.client_outreach import ClientOutreachAgent, SenderProfile, WorkflowStarter
from recruiting.agents.client_submission import ClientSubmissionAgent
from recruiting.agents.discovery import CompanyDiscoveryAgent, CompanySource
from recruiting.agents.follow_up import FollowUpAgent
from recruiting.agents.hiring_signals import (
    CareerPageFetcher,
    HiringSignalsAgent,
    HttpCareerPageFetcher,
)
from recruiting.agents.jd_analysis import JDAnalysisAgent
from recruiting.agents.jd_intake import JDIntakeAgent
from recruiting.agents.matching import ResumeMatchingAgent
from recruiting.agents.ranking import CandidateRankingAgent
from recruiting.agents.resume_collection import ResumeCollectionAgent
from recruiting.agents.resume_intake import ResumeIntake
from recruiting.ai.llm import ClaudeProvider, LLMGateway, UsageLedger
from recruiting.ai.prompts import default_registry
from recruiting.ai.search import HashingEmbedder, InMemoryIndex, QdrantIndex, SemanticSearch
from recruiting.approvals import ApprovalEngine
from recruiting.compliance import OptOutRegistry
from recruiting.compliance.erasure import CandidateErasure
from recruiting.compliance.unsubscribe import UnsubscribeLinks
from recruiting.config import Settings
from recruiting.domain import Role
from recruiting.identity import UserDirectory
from recruiting.outreach import DeliveryLog
from recruiting.records.engagements import EngagementStore, InMemoryEngagements
from recruiting.records.followups import (
    CadenceStore,
    FollowUpStore,
    InMemoryCadence,
    InMemoryFollowUps,
)
from recruiting.records.memory import (
    CandidateRepository,
    CompanyRepository,
    InMemoryCandidates,
    InMemoryCompanies,
    InMemoryJobs,
    JobRepository,
)
from recruiting.records.rankings import (
    InMemoryRankings,
    InMemoryWeights,
    RankingStore,
    WeightsStore,
)
from recruiting.records.submissions import InMemorySubmissions, SubmissionStore
from recruiting.reporting import Reporting

logger = logging.getLogger(__name__)


@dataclass
class Platform:
    companies: CompanyRepository
    jobs: JobRepository
    candidates: CandidateRepository
    search: SemanticSearch
    gateway: LLMGateway | None
    unsubscribe: UnsubscribeLinks
    deliveries: DeliveryLog
    discovery: CompanyDiscoveryAgent
    signals: HiringSignalsAgent
    client_outreach: ClientOutreachAgent | None  # needs workflows
    jd_intake: JDIntakeAgent
    jd_analysis: JDAnalysisAgent
    resume_intake: ResumeIntake
    candidate_search: CandidateSearchAgent
    rankings: RankingStore
    ranking_weights: WeightsStore
    engagements: EngagementStore
    candidate_outreach: CandidateOutreachAgent | None  # needs approvals and workflows
    resume_collection: ResumeCollectionAgent
    submissions: ClientSubmissionAgent
    follow_ups: FollowUpAgent
    follow_up_cadence: CadenceStore
    reporting: Reporting
    erasure: CandidateErasure
    inbound_webhook_secret: str | None = None


def round_robin_recruiter(users: UserDirectory) -> Callable[[], str | None]:
    """Assign new jobs to active recruiters in turn (BRD section 8: Job Database "Recruiter")."""
    counter = itertools.count()

    def assign() -> str | None:
        recruiters = sorted(
            (u for u in users.list_users() if u.role is Role.RECRUITER and u.active),
            key=lambda u: u.email.lower(),
        )
        return recruiters[next(counter) % len(recruiters)].user_id if recruiters else None

    return assign


def _log_llm_error(key: str, exc: Exception) -> None:
    logger.warning("LLM call for %s failed; used the rule-based fallback: %s", key, exc)


def build_platform(
    settings: Settings,
    *,
    users: UserDirectory,
    opt_outs: OptOutRegistry,
    deliveries: DeliveryLog,
    workflows: WorkflowStarter | None,
    companies: CompanyRepository | None = None,
    jobs: JobRepository | None = None,
    candidates: CandidateRepository | None = None,
    ledger: UsageLedger | None = None,
    llm_provider: Any = None,
    sources: list[CompanySource] | None = None,
    fetcher: CareerPageFetcher | None = None,
    rankings: RankingStore | None = None,
    ranking_weights: WeightsStore | None = None,
    candidate_sources: list[CandidateSource] | None = None,
    engagements: EngagementStore | None = None,
    approvals: ApprovalEngine | None = None,
    submissions: SubmissionStore | None = None,
    follow_ups: FollowUpStore | None = None,
    follow_up_cadence: CadenceStore | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Platform:
    timing: dict[str, Any] = {"clock": clock} if clock is not None else {}
    companies = companies or InMemoryCompanies()
    jobs = jobs or InMemoryJobs()
    candidates = candidates or InMemoryCandidates()

    if settings.vector_store == "qdrant":
        index: Any = QdrantIndex(settings.qdrant_url)
    else:
        index = InMemoryIndex()
    search = SemanticSearch(HashingEmbedder(), index, prefix=f"rap_{settings.environment}")

    gateway = None
    if llm_provider is not None or settings.llm_provider == "claude":
        provider = llm_provider or ClaudeProvider(model=settings.llm_model)
        gateway = LLMGateway(provider, default_registry(), ledger)

    if settings.environment != "local" and settings.unsubscribe_secret.startswith("local-dev"):
        raise RuntimeError("Set RAP_UNSUBSCRIBE_SECRET outside local")
    unsubscribe = UnsubscribeLinks(settings.unsubscribe_secret, settings.public_base_url)
    analysis = JDAnalysisAgent(search, gateway, _log_llm_error)
    rankings = rankings or InMemoryRankings()
    ranking_weights = ranking_weights or InMemoryWeights()
    resume_intake = ResumeIntake(candidates, search, gateway, _log_llm_error)
    ranking_agent = CandidateRankingAgent(ranking_weights.get, embedder=HashingEmbedder())
    candidate_search = CandidateSearchAgent(
        candidates, search, resume_intake, ranking_agent, candidate_sources or []
    )

    engagements = engagements or InMemoryEngagements()
    candidate_outreach = None
    if workflows is not None and approvals is not None:
        candidate_outreach = CandidateOutreachAgent(
            candidates, engagements, approvals, deliveries, opt_outs, workflows, unsubscribe,
            settings.outreach_postal_address, settings.linkedin_weekly_limit, **timing,
        )  # fmt: skip
    resume_collection = ResumeCollectionAgent(
        candidates, jobs, engagements, resume_intake, workflows, opt_outs, deliveries, unsubscribe,
        settings.public_base_url, settings.outreach_postal_address, **timing,
    )  # fmt: skip

    submissions = submissions or InMemorySubmissions()
    follow_ups = follow_ups or InMemoryFollowUps()
    submission_agent = ClientSubmissionAgent(
        companies, jobs, candidates, engagements, submissions,
        ResumeMatchingAgent(ranking_agent, gateway, HashingEmbedder(), _log_llm_error),
        approvals, deliveries, workflows, rankings=rankings, **timing,
    )  # fmt: skip

    follow_up_cadence = follow_up_cadence or InMemoryCadence()

    def user_name(user_id: str | None) -> str | None:
        return next((u.display_name for u in users.list_users() if u.user_id == user_id), None)

    follow_up_agent = FollowUpAgent(
        follow_ups, follow_up_cadence, companies, jobs, candidates,
        engagements, submission_agent, resume_collection, approvals, deliveries, workflows,
        opt_outs, unsubscribe, settings.outreach_sender_name, settings.outreach_postal_address,
        user_name, **timing,
    )  # fmt: skip

    outreach_agent = None
    if workflows is not None:
        outreach_agent = ClientOutreachAgent(
            companies,
            opt_outs,
            workflows,
            SenderProfile(settings.outreach_sender_name, settings.outreach_postal_address),
            unsubscribe,
            gateway,
            min_priority=settings.client_outreach_min_priority,
            on_error=_log_llm_error,
        )

    return Platform(
        companies=companies,
        jobs=jobs,
        candidates=candidates,
        search=search,
        gateway=gateway,
        unsubscribe=unsubscribe,
        deliveries=deliveries,
        discovery=CompanyDiscoveryAgent(companies, sources or []),
        signals=HiringSignalsAgent(companies, fetcher or HttpCareerPageFetcher()),
        client_outreach=outreach_agent,
        jd_intake=JDIntakeAgent(
            companies,
            jobs,
            opt_outs,
            deliveries,
            analysis,
            round_robin_recruiter(users),
            gateway,
            _log_llm_error,
        ),
        jd_analysis=analysis,
        resume_intake=resume_intake,
        candidate_search=candidate_search,
        rankings=rankings,
        ranking_weights=ranking_weights,
        engagements=engagements,
        candidate_outreach=candidate_outreach,
        resume_collection=resume_collection,
        submissions=submission_agent,
        follow_ups=follow_up_agent,
        follow_up_cadence=follow_up_cadence,
        reporting=Reporting(
            companies,
            jobs,
            engagements,
            submission_agent,
            rankings,
            deliveries,
            approvals,
            **timing,
        ),  # fmt: skip
        erasure=CandidateErasure(
            candidates, engagements, submissions, follow_ups, search, opt_outs, **timing
        ),  # fmt: skip
        inbound_webhook_secret=settings.inbound_webhook_secret,
    )
