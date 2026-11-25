"""Affinda resume parser adapter: the request Affinda gets, how its answer maps onto the
platform's candidate fields, and that any failure falls back so no resume is lost."""

import httpx
import pytest

from recruiting.agents.resume_intake import ResumeIntake
from recruiting.connectors.affinda import AffindaResumeParser, ResumeParserError
from recruiting.records.memory import InMemoryCandidates

RESUME = """Priya Raman
Detroit, MI | priya.raman@mail.example | (313) 555-0142
linkedin.com/in/priya-raman
Quality Engineer, 8 years: PFMEA, APQP, PPAP, GD&T
"""

# Compact response, shaped like Affinda's "data extracted" example (parsed values only).
COMPACT = {
    "data": {
        "candidateName": {"candidateNameFirst": "Priya", "candidateNameFamily": "Raman"},
        "email": ["priya.raman@mail.example"],
        "phoneNumber": ["(313) 555-0142"],
        "website": ["https://www.linkedin.com/in/priya-raman"],
        "location": {"formatted": "Detroit, MI, USA", "city": "Detroit", "state": "MI"},
        "skill": ["PFMEA", "APQP", "PPAP", "GD&T", "PFMEA"],
        "totalYearsExperience": 8,
        "education": [{
            "educationAccreditation": "Bachelor of Science",
            "educationMajor": ["Mechanical Engineering"],
            "educationOrganization": "Wayne State University",
        }],
        "workExperience": [
            {"jobTitle": "Senior Quality Engineer", "workExperienceOrganization": "Northfield Axle",
             "workExperienceDateRange": {"start": {"date": "2021-03-01"}, "isCurrent": True}},
            {"jobTitle": "Quality Engineer", "workExperienceOrganization": "Great Lakes Stamping",
             "workExperienceDateRange": {"start": {"date": "2017-06-01"},
                                         "end": {"date": "2021-02-01"}, "isCurrent": False}},
        ],
        "rightToWork": "US citizen",
        "availability": None,
        "rawText": RESUME,
    },
    "meta": {"identifier": "abc123", "ready": True, "failed": False},
}  # fmt: skip

# The same resume in the full form: each field carries raw and parsed values.
FULL = {
    "data": {
        "candidateName": {"raw": "Priya Raman", "parsed": {
            "candidateNameFirst": {"raw": "Priya", "parsed": "Priya"},
            "candidateNameFamily": {"raw": "Raman", "parsed": "Raman"}}},
        "email": [{"raw": "priya.raman@mail.example", "parsed": "priya.raman@mail.example"}],
        "phoneNumber": [{"raw": "(313) 555-0142", "parsed": {"formattedNumber": "+1 313-555-0142",
                                                             "rawText": "(313) 555-0142"}}],
        "skill": [{"raw": "PFMEA", "parsed": {"name": "PFMEA"}}, {"parsed": {"name": "APQP"}}],
        "location": {"raw": "Detroit, MI", "parsed": {"city": "Detroit", "state": "MI"}},
        "totalYearsExperience": {"raw": "8", "parsed": 8},
    },
    "meta": {"identifier": "abc124", "ready": True, "failed": False},
}  # fmt: skip


def parser(handler, **kw):
    client = httpx.Client(base_url="https://api.us1.affinda.test",
                          transport=httpx.MockTransport(handler))  # fmt: skip
    return AffindaResumeParser(lambda: "secret-key", "ws-1", client=client, **kw)


def answering(status, body):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, json=body) if not isinstance(body, str) else httpx.Response(
            status, text=body)  # fmt: skip

    return handler, seen


def test_request_sends_the_text_as_a_file_and_asks_affinda_to_keep_nothing():
    handler, seen = answering(200, COMPACT)
    parser(handler, document_type="resume-type").parse(RESUME)
    [request] = seen
    assert request.method == "POST" and request.url.path == "/v3/documents"
    assert request.headers["authorization"] == "Bearer secret-key"
    body = request.content.decode("utf-8")
    for name, value in (("workspace", "ws-1"), ("wait", "true"), ("deleteAfterParse", "true"),
                        ("compact", "true"), ("documentType", "resume-type")):  # fmt: skip
        assert f'name="{name}"\r\n\r\n{value}\r\n' in body
    assert 'filename="resume.txt"' in body and "Content-Type: text/plain" in body
    assert "Priya Raman" in body  # the text itself, as the file
    assert "Priya" not in body.split("Content-Type: text/plain")[0]  # no name in the metadata


def test_compact_answer_maps_onto_candidate_fields():
    handler, _ = answering(200, COMPACT)
    parsed = parser(handler).parse(RESUME)
    assert parsed.full_name == "Priya Raman"
    assert parsed.email == "priya.raman@mail.example"
    assert parsed.phone == "(313) 555-0142"
    assert parsed.linkedin_url == "https://www.linkedin.com/in/priya-raman"
    assert parsed.current_location == "Detroit, MI"
    assert parsed.skills == ["PFMEA", "APQP", "PPAP", "GD&T"]  # duplicates dropped
    assert parsed.experience_years == 8
    assert parsed.education == "Bachelor of Science (Mechanical Engineering)"
    current, earlier = parsed.work_history
    assert (current.title, current.company, current.start_year, current.end_year) == (
        "Senior Quality Engineer", "Northfield Axle", 2021, None)  # fmt: skip
    assert (earlier.start_year, earlier.end_year) == (2017, 2021)
    assert parsed.work_authorization == "US citizen" and parsed.availability is None


def test_full_answer_with_raw_and_parsed_parts_maps_the_same():
    handler, _ = answering(200, FULL)
    parsed = parser(handler).parse(RESUME)
    assert parsed.full_name == "Priya Raman"
    assert parsed.email == "priya.raman@mail.example"
    assert parsed.phone == "(313) 555-0142"
    assert parsed.skills == ["PFMEA", "APQP"]
    assert parsed.current_location == "Detroit, MI"
    assert parsed.experience_years == 8


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (429, {"detail": "slow down"}, "rate limit"),
        (401, {"detail": "Priya Raman bad key"}, "HTTP 401"),
        (500, "oops", "HTTP 500"),
        (
            200,
            {"data": {}, "meta": {"failed": True, "errorCode": "no_text_found"}},
            "no_text_found",
        ),
        (200, {"data": None, "meta": {"ready": False}}, "no resume data"),
        (200, "not json", "other than JSON"),
    ],
)
def test_failures_raise_without_echoing_the_answer(status, body, message):
    handler, _ = answering(status, body)
    with pytest.raises(ResumeParserError, match=message) as error:
        parser(handler).parse(RESUME)
    assert "Priya" not in str(error.value)  # error text never quotes the resume


def test_unreachable_affinda_raises():
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(ResumeParserError, match="not reachable"):
        parser(handler).parse(RESUME)


def test_intake_uses_affinda_and_falls_back_when_it_fails():
    handler, _ = answering(200, COMPACT)
    errors = []
    intake = ResumeIntake(InMemoryCandidates(), on_error=lambda k, e: errors.append(k),
                          parser=parser(handler))  # fmt: skip
    outcome = intake.add(RESUME, source="manual")
    assert outcome.parsed_by == "affinda"
    assert outcome.candidate.full_name == "Priya Raman"
    assert outcome.candidate.work_history[0]["title"] == "Senior Quality Engineer"

    down, _ = answering(503, {"detail": "maintenance"})
    intake = ResumeIntake(InMemoryCandidates(), on_error=lambda k, e: errors.append(k),
                          parser=parser(down))  # fmt: skip
    outcome = intake.add(RESUME, source="manual")
    assert outcome.parsed_by == "rules"  # no LLM configured here: straight to the rules
    assert outcome.candidate.email == "priya.raman@mail.example"
    assert errors == ["resume_parser"]


def test_affinda_is_used_only_when_configured(monkeypatch):
    from recruiting.api.platform import build_resume_parser
    from recruiting.config import Settings

    assert build_resume_parser(Settings(_env_file=None)) is None  # default: no workspace
    assert build_resume_parser(
        Settings(resume_parser="none", affinda_workspace="ws-1", _env_file=None)
    ) is None  # fmt: skip
    built = build_resume_parser(Settings(affinda_workspace="ws-1", _env_file=None))
    assert built is not None and built.name == "affinda"

    # A missing key is a parse failure that falls back, not a crash at start-up.
    monkeypatch.delenv("RAP_SECRET_AFFINDA_API_KEY", raising=False)
    intake = ResumeIntake(InMemoryCandidates(), parser=built)
    assert intake.add(RESUME, source="manual").parsed_by == "rules"
