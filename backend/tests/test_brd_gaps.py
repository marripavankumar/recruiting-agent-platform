"""Gaps found in the BRD review (docs/08_BRD_Gap_Review.md): Agent 6 candidate filters,
Agent 2's Manufacturing Manager, and Siemens PLC skills."""

import pytest
from fastapi.testclient import TestClient

from recruiting.agents.candidate_search import CandidateFilter, hourly_rate
from recruiting.agents.hiring_signals import CORE_ROLES, TARGET_ROLES, target_role
from recruiting.ai.skills import extract_skills
from recruiting.records.models import Candidate

PRIYA = Candidate(
    id="c1", full_name="Priya Raman", source="ats", linkedin_url="https://linkedin.com/in/pr",
    current_location="Detroit, MI", preferred_location="Remote", skills=["PFMEA", "APQP", "SolidWorks"],
    experience_years=8, education="BS Mechanical Engineering", industries=["Automotive"],
    work_history=[{"title": "Senior Quality Engineer"}], availability="2 weeks",
    visa_status="US citizen", expected_rate="$75/hr", resume_text="Priya Raman ...",
)  # fmt: skip
OMAR = Candidate(
    id="c2", full_name="Omar Haddad", source="linkedin", current_location="Toledo, OH",
    skills=["PLC", "Siemens PLC"], experience_years=3, industries=["Industrial Equipment"],
    work_history=[{"title": "Controls Engineer"}], notice_period="1 month",
    work_authorization="H-1B transfer", expected_rate="$140k per year",
)  # fmt: skip


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({}, {"c1", "c2"}),
        ({"text": "quality"}, {"c1"}),  # past job title
        ({"text": "toledo"}, {"c2"}),
        ({"title": "controls"}, {"c2"}),
        ({"skills": ("pfmea", "Solidworks")}, {"c1"}),  # canonical names, all required
        ({"skills": ("PFMEA", "PLC")}, set()),
        ({"industry": "auto"}, {"c1"}),
        ({"min_years": 5}, {"c1"}),
        ({"education": "mechanical"}, {"c1"}),
        ({"location": "remote"}, {"c1"}),  # preferred location counts
        ({"visa": "h-1b"}, {"c2"}),  # work authorisation counts
        ({"availability": "month"}, {"c2"}),  # notice period counts
        ({"max_hourly_rate": 70}, {"c2"}),  # $140k/yr is about $67/hr
        ({"max_hourly_rate": 80}, {"c1", "c2"}),
        ({"has_linkedin": True}, {"c1"}),
        ({"has_resume": False}, {"c2"}),
        ({"industry": "auto", "min_years": 10}, set()),  # every filter must match
    ],
)
def test_candidate_filters(filters, expected):
    wanted = CandidateFilter(**filters)
    assert {c.id for c in (PRIYA, OMAR) if wanted.matches(c)} == expected


def test_rates_are_compared_per_hour():
    assert hourly_rate("$75/hr") == 75
    assert hourly_rate("$120k") == hourly_rate("120,000 per year") == 57.69
    assert hourly_rate("90 USD hourly") == 90
    assert hourly_rate("negotiable") is None and hourly_rate(None) is None


def test_candidates_api_filters():
    from recruiting.api.app import create_app
    from recruiting.api.services import build_services
    from recruiting.config import Settings

    client = TestClient(create_app(build_services(Settings(demo_data=True, _env_file=None))))
    rec = {"X-Dev-User-Email": "recruiter@cbs.example"}
    everyone = client.get("/candidates", headers=rec).json()
    assert len(everyone) == 9
    linkedin_only = client.get("/candidates", headers=rec, params={"has_resume": "false"}).json()
    with_pfmea = client.get("/candidates", headers=rec, params={"skills": "PFMEA"}).json()
    assert 0 < len(with_pfmea) < len(everyone)
    assert all("PFMEA" in c["skills"] for c in with_pfmea)
    assert all("resume_text" not in c for c in with_pfmea + linkedin_only)
    none = client.get("/candidates", headers=rec, params={"skills": "PFMEA", "min_years": 59})
    assert none.status_code == 200 and none.json() == []
    bad = client.get("/candidates", headers=rec, params={"min_years": -1})
    assert bad.status_code == 422
    bd = {"X-Dev-User-Email": "bd.manager@cbs.example"}
    assert client.get("/candidates", headers=bd, params={"skills": "PFMEA"}).status_code == 403


def test_manufacturing_manager_is_a_target_role():
    # BRD Agent 2 searches for it; section 4 does not list it.
    assert len(TARGET_ROLES) == 28 and "Manufacturing Manager" in CORE_ROLES
    for title in ("Manufacturing Manager", "Production Manager", "Manager, Manufacturing",
                  "Sr. Manufacturing Manager - Assembly"):  # fmt: skip
        assert target_role(title) == "Manufacturing Manager", title
    assert target_role("Manufacturing Engineering Manager") == "Engineering Manager"
    assert target_role("Production Engineer") == "Production Engineer"
    assert target_role("Manufacturing Supervisor") == "Manufacturing Supervisor"


def test_siemens_plc_experience_is_recognised():
    def skills(text):
        return [m.skill for m in extract_skills(text)]

    assert "Siemens PLC" in skills("Programs Siemens PLCs and Allen-Bradley")
    assert "Siemens PLC" in skills("Siemens S7 PLC programming")
    assert "Siemens PLC" in skills("SIMATIC S7-300")
    assert "Siemens PLC" not in skills("Worked at Siemens on Teamcenter and NX")
