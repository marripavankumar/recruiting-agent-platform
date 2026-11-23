"""The records, AI services and agents behind the business APIs, built from settings."""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from recruiting.agents.candidate_search import CandidateSearchAgent, CandidateSource
from recruiting.agents.client_outreach import ClientOutreachAgent, SenderProfile, WorkflowStarter
from recruiting.agents.discovery import CompanyDiscoveryAgent, CompanySource
from recruiting.agents.hiring_signals import (
    CareerPageFetcher,
    HiringSignalsAgent,
    HttpCareerPageFetcher,
)
from recruiting.agents.jd_analysis import JDAnalysisAgent
from recruiting.agents.jd_intake import JDIntakeAgent
from recruiting.agents.ranking import CandidateRankingAgent
from recruiting.agents.resume_intake import ResumeIntake
from recruiting.ai.llm import ClaudeProvider, LLMGateway, UsageLedger
from recruiting.ai.prompts import default_registry
from recruiting.ai.search import HashingEmbedder, InMemoryIndex, QdrantIndex, SemanticSearch
from recruiting.compliance import OptOutRegistry
from recruiting.compliance.unsubscribe import UnsubscribeLinks
from recruiting.config import Settings
from recruiting.domain import Role
from recruiting.identity import UserDirectory
from recruiting.outreach import DeliveryLog
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
) -> Platform:
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
        inbound_webhook_secret=settings.inbound_webhook_secret,
    )
