"""Phase 4: resume intake, Agent 6 search, Agent 7 ranking, recruiter review, API."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from recruiting.agents.candidate_search import CandidateSearchAgent, ExternalResume
from recruiting.agents.ranking import CandidateRankingAgent, RankingWeights, needs_sponsorship
from recruiting.agents.resume_intake import ResumeIntake, parse_by_rules
from recruiting.ai.llm import LLMGateway, ScriptedProvider
from recruiting.ai.prompts import default_registry
from recruiting.ai.search import HashingEmbedder, InMemoryIndex, SemanticSearch
from recruiting.records.memory import InMemoryCandidates
from recruiting.records.models import Candidate, Job, Verdict
from recruiting.records.rankings import InMemoryRankings

NOW = datetime(2026, 11, 23, tzinfo=UTC)

JOB = Job(
    id="job-1",
    job_code="JOB-2026-0142",
    client_company_id="c1",
    title="CAE Engineer - Crash",
    required_skills=["LS-DYNA", "ANSA", "FMVSS"],
    preferred_skills=["Python", "Primer"],
    location="Troy, MI",
    industry="Automotive",
    education="BS Mechanical Engineering",
    visa_requirements="US citizen or green card holder",
    responsibilities="Full-vehicle crash simulation with LS-DYNA and test correlation",
    analysis={"must_have_skills": ["LS-DYNA", "ANSA", "FMVSS"], "nice_to_have_skills": ["Python"],
              "years_experience_min": 5},
)  # fmt: skip


def strong(cid="strong", name="Priya Natarajan"):
    return Candidate(
        id=cid, full_name=name, source="ats", email=f"{cid}@mail.example", phone="+1 313 555 0101",
        current_location="Troy, MI", skills=["LS-DYNA", "ANSA", "FMVSS", "Python", "HyperMesh"],
        experience_years=8, education="MS Mechanical Engineering", industries=["Automotive"],
        work_history=[{"title": "Senior CAE Engineer", "company": "OEM", "start_year": 2019, "end_year": None}],
        projects=[{"title": "Frontal crash correlation", "description": "LS-DYNA full-vehicle crash simulation and test correlation"}],
        availability="2 weeks", work_authorization="US citizen",
        communication={"reply_hours": 6, "completeness": 1.0, "recruiter_rating": 5},
    )  # fmt: skip


def agent(weights=None):
    w = weights or RankingWeights()
    return CandidateRankingAgent(lambda: w, embedder=HashingEmbedder(), clock=lambda: NOW)


# ----- resume intake ---------------------------------------------------------------------------

RESUME = """Elena Vasquez
Toledo, OH | elena.vasquez@mail.example | (419) 555-0188 | linkedin.com/in/elena-vasquez-fea
FEA / Crash Engineer with 6 years of experience in LS-DYNA, Primer and ANSA.
B.S. Mechanical Engineering, University of Toledo
"""


def test_rule_parser_reads_contact_details_skills_years_and_degree():
    parsed = parse_by_rules(RESUME)
    assert parsed.full_name == "Elena Vasquez"
    assert parsed.email == "elena.vasquez@mail.example" and parsed.phone == "(419) 555-0188"
    assert parse_by_rules("A B\n+1 313 555 0108\n").phone == "+1 313 555 0108"
    assert parsed.linkedin_url.endswith("elena-vasquez-fea")
    assert {"LS-DYNA", "ANSA"} <= set(parsed.skills) and parsed.experience_years == 6
    assert parsed.education.startswith("B.S. Mechanical")


def test_intake_deduplicates_and_never_clears_known_values():
    candidates = InMemoryCandidates()
    search = SemanticSearch(HashingEmbedder(), InMemoryIndex())
    intake = ResumeIntake(candidates, search)
    first = intake.add(RESUME, source="dice")
    assert first.created and first.parsed_by == "rules"
    # Same person, different email, same LinkedIn profile: merged, not duplicated.
    second = intake.add(
        "Elena Vasquez\nhttps://www.linkedin.com/in/Elena-Vasquez-FEA/\nAlso knows Python.",
        source="ats",
    )
    assert not second.created and second.candidate.id == first.candidate.id
    merged = candidates.get(first.candidate.id)
    assert (
        merged.email == "elena.vasquez@mail.example"
        and "Python" in merged.skills
        and "LS-DYNA" in merged.skills
    )
    assert search.search("candidate", "crash LS-DYNA")[0].id == merged.id


def test_intake_uses_the_llm_parser_with_canonical_skills():
    provider = ScriptedProvider({"resume_parse": lambda req: {
        "full_name": "Raj Iyer", "email": "raj@mail.example", "phone": None, "linkedin_url": None,
        "current_location": "Grand Rapids, MI", "skills": ["allen bradley", "PLC", "FactoryTalk"],
        "experience_years": 7, "education": "BS Electrical Engineering", "industries": ["Packaging"],
        "work_history": [{"title": "Controls Engineer", "company": "X", "start_year": 2020, "end_year": None}],
        "projects": [], "work_authorization": "US citizen", "availability": "2 weeks"}})  # fmt: skip
    candidates = InMemoryCandidates()
    outcome = ResumeIntake(candidates, gateway=LLMGateway(provider, default_registry())).add(
        "Raj Iyer resume text", "ats"
    )
    assert outcome.parsed_by == "llm"
    assert "Rockwell" in outcome.candidate.skills and "PLC" in outcome.candidate.skills
    assert outcome.candidate.work_history[0]["title"] == "Controls Engineer"


# ----- Agent 7 ---------------------------------------------------------------------------------


def test_strong_candidate_scores_high_with_reasons():
    result = agent().score(JOB, strong())
    assert result.excluded_reason is None and result.score >= 85
    assert result.components["skill_match"] == pytest.approx(0.75 + 0.25)
    assert any("3 of 3 must-have" in s for s in result.strengths)
    assert any("Based in Troy, MI" in s for s in result.strengths)
    assert set(result.components) == set(RankingWeights().components())


def test_names_do_not_change_the_score():
    a = agent().score(JOB, strong("a", "Priya Natarajan"))
    b = agent().score(JOB, strong("b", "John Smith"))
    assert a.score == b.score and a.components == b.components


def test_hard_filters_must_have_skills_and_work_authorisation():
    weak = replace(strong("weak"), skills=["SolidWorks"])
    assert "must-have" in agent().score(JOB, weak).excluded_reason
    sponsored = replace(strong("h1b"), work_authorization="H-1B, needs sponsorship")
    assert "sponsorship" in agent().score(JOB, sponsored).excluded_reason
    unknown = replace(strong("unknown"), work_authorization=None)
    result = agent().score(JOB, unknown)
    assert result.excluded_reason is None and "Confirm work authorisation" in result.gaps
    assert needs_sponsorship(strong()) is False


def test_unknown_facts_are_neutral_gaps_not_penalties():
    thin = Candidate("thin", "Thin Profile", "ats", skills=["LS-DYNA", "ANSA", "FMVSS"])
    result = agent().score(JOB, thin)
    for key in ("years_experience", "location", "availability", "communication", "industry_match"):
        assert result.components[key] == 0.5, key
    assert "Availability unknown" in result.gaps


def test_ranking_orders_limits_and_lists_exclusions_last():
    near = replace(
        strong("near"), current_location="Detroit, MI", availability="6 weeks", experience_years=5
    )
    far = replace(
        strong("far"),
        current_location="Austin, TX",
        availability="3 months",
        industries=["Aerospace"],
    )
    weak = replace(strong("weak"), skills=["SolidWorks"])
    ranking = agent().rank(JOB, [far, weak, strong(), near], limit=2)
    assert [c.candidate_id for c in ranking.top] == ["strong", "near"]
    assert ranking.candidates[-1].candidate_id == "weak" and ranking.candidates[-1].rank is None
    assert ranking.searched == 4


def test_weights_change_the_order_and_are_validated():
    local_junior = replace(strong("local"), experience_years=2, skills=["LS-DYNA", "ANSA", "FMVSS"])
    remote_senior = replace(strong("remote"), current_location="Austin, TX", experience_years=15)
    location_heavy = RankingWeights(location=5.0)
    assert (
        agent(location_heavy).rank(JOB, [remote_senior, local_junior]).top[0].candidate_id
        == "local"
    )
    with pytest.raises(ValueError):
        RankingWeights(skill_match=-1).validate()


# ----- Agent 6 and review store ----------------------------------------------------------------


class FakeBoard:
    name = "dice"

    def search(self, query, skills, location, limit):
        return [
            ExternalResume(
                "dice",
                "Wei Zhang\nwei@mail.example\nCrash analyst, 9 years LS-DYNA, ANSA, FMVSS, Python",
            )
        ]


def test_search_merges_internal_and_external_and_writes_back():
    candidates = InMemoryCandidates([strong(), replace(strong("plc"), skills=["PLC", "Rockwell"])])
    search = SemanticSearch(HashingEmbedder(), InMemoryIndex())
    intake = ResumeIntake(candidates, search)
    for c in candidates.list():
        search.index("candidate", c.id, " ".join(c.skills))
    finder = CandidateSearchAgent(candidates, search, intake, agent(), [FakeBoard()])
    ranking, report = finder.run(JOB)
    assert report.external_found == {"dice": 1} and report.external_new == 1
    assert len(candidates.list()) == 3  # the job-board resume is now a database record
    ids = [c.candidate_id for c in ranking.top]
    assert ids[0] == "strong" and "plc" not in ids


def test_reviews_survive_reranking_and_count_towards_the_exit_criterion():
    store = InMemoryRankings()
    ranking = store.save(agent().rank(JOB, [strong("a"), strong("b"), strong("c")]))
    store.review(JOB.id, "a", Verdict.QUALIFIED, "great", "u-rec")
    store.review(JOB.id, "b", Verdict.NOT_QUALIFIED, None, "u-rec")
    assert store.get(JOB.id).qualified_in_top_20 == 1
    rerun = store.save(agent().rank(JOB, [strong("a"), strong("b"), strong("c")]))
    assert {c.candidate_id: c.verdict for c in rerun.candidates}["a"] is Verdict.QUALIFIED
    moved = store.move(JOB.id, "c", 1)
    assert [c.candidate_id for c in moved.top][0] == "c"
    assert ranking.ranked_at == NOW


# ----- API -------------------------------------------------------------------------------------


@pytest.fixture
def api():
    from recruiting.api.app import create_app
    from recruiting.api.platform import build_platform
    from recruiting.api.services import Services
    from recruiting.approvals import ApprovalEngine
    from recruiting.compliance import OptOutRegistry
    from recruiting.config import Settings
    from recruiting.domain import Role
    from recruiting.identity import CurrentUser, DevHeaderAuthenticator, InMemoryUserDirectory
    from recruiting.outreach import InMemoryDeliveryLog

    users = InMemoryUserDirectory([
        CurrentUser("u-rec", "rec@cbs.example", "Recruiter", Role.RECRUITER),
        CurrentUser("u-admin", "admin@cbs.example", "Admin", Role.ADMINISTRATOR),
        CurrentUser("u-bd", "bd@cbs.example", "BD", Role.BD_MANAGER),
        CurrentUser("u-cl", "cl@client.example", "Client", Role.CLIENT, "c1"),
    ])  # fmt: skip
    platform = build_platform(
        Settings(llm_provider="none", _env_file=None), users=users, opt_outs=OptOutRegistry(),
        deliveries=InMemoryDeliveryLog(), workflows=None,
    )  # fmt: skip
    platform.jobs.add(JOB)
    services = Services(
        ApprovalEngine(), OptOutRegistry(), users, DevHeaderAuthenticator(), platform=platform
    )
    return TestClient(create_app(services))


def h(email):
    return {"X-Dev-User-Email": email}


def test_api_add_rank_review_and_weights(api):
    resumes = [
        {"resume_text": "Priya Natarajan\nNovi, MI | priya@mail.example\n8 years crash CAE: LS-DYNA, ANSA, FMVSS, Python. US citizen.", "source": "ats"},
        {"resume_text": "Tom Baker\ntom@mail.example\nSolidWorks designer, 4 years experience", "source": "ats"},
    ]  # fmt: skip
    added = api.post("/candidates", json={"resumes": resumes}, headers=h("rec@cbs.example"))
    assert added.status_code == 201 and added.json()["created"] == 2
    assert (
        api.post("/candidates", json={"resumes": resumes}, headers=h("bd@cbs.example")).status_code
        == 403
    )
    assert api.get("/candidates", headers=h("cl@client.example")).status_code == 403

    ranked = api.post(f"/jobs/{JOB.id}/ranking", headers=h("rec@cbs.example")).json()
    top = [c for c in ranked["candidates"] if c["rank"]]
    assert top[0]["full_name"] == "Priya Natarajan" and top[0]["strengths"]
    excluded = [c for c in ranked["candidates"] if c["rank"] is None]
    assert excluded and excluded[0]["full_name"] == "Tom Baker"
    assert ranked["search"]["pool"] == 2

    cid = top[0]["candidate_id"]
    review = api.post(
        f"/jobs/{JOB.id}/ranking/{cid}/review",
        json={"verdict": "qualified"},
        headers=h("rec@cbs.example"),
    )
    assert review.status_code == 200
    assert (
        api.get(f"/jobs/{JOB.id}/ranking", headers=h("rec@cbs.example")).json()[
            "qualified_in_top_20"
        ]
        == 1
    )
    assert (
        api.post(
            f"/jobs/{JOB.id}/ranking/nobody/review",
            json={"verdict": "qualified"},
            headers=h("rec@cbs.example"),
        ).status_code
        == 404
    )

    weights = api.get("/admin/ranking-weights", headers=h("admin@cbs.example")).json()
    assert weights["skill_match"] == 0.25
    weights["location"] = 0.2
    assert (
        api.put("/admin/ranking-weights", json=weights, headers=h("admin@cbs.example")).json()[
            "location"
        ]
        == 0.2
    )
    assert (
        api.put("/admin/ranking-weights", json=weights, headers=h("rec@cbs.example")).status_code
        == 403
    )
    zero = {k: 0 for k in weights} | {"min_must_have_coverage": 0.5}
    assert (
        api.put("/admin/ranking-weights", json=zero, headers=h("admin@cbs.example")).status_code
        == 422
    )
