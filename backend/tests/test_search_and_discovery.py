import json
from datetime import UTC, date, datetime, timedelta

import httpx

from recruiting.agents.discovery import (
    CompanyDiscoveryAgent,
    CsvCompanySource,
    DiscoveryCriteria,
    contact_role,
)
from recruiting.agents.hiring_signals import (
    HiringSignalsAgent,
    HttpCareerPageFetcher,
    PriorityWeights,
    extract_postings,
    priority_score,
    target_role,
)
from recruiting.ai.search import HashingEmbedder, InMemoryIndex, QdrantIndex, SemanticSearch
from recruiting.ai.skills import canonical, extract_skills
from recruiting.records.memory import InMemoryCompanies, normalize_domain
from recruiting.records.models import ContactRole, JobPosting

# ----- skills and search -----------------------------------------------------------------------


def test_skill_dictionary_normalises_aliases_and_avoids_acronym_false_positives():
    text = (
        "Allen-Bradley ControlLogix, GD&T, PFMEA, Six Sigma Green Belt, C++, SAP and DOE. John Doe."
    )
    skills = [m.skill for m in extract_skills(text)]
    assert {"Rockwell", "GD&T", "PFMEA", "Six Sigma", "C++", "SAP", "DOE"} <= set(skills)
    assert [m.skill for m in extract_skills("john doe will sap your energy")] == []
    assert canonical("allen bradley") == "Rockwell"
    assert canonical("Underwater basket weaving ") == "Underwater basket weaving"


def test_semantic_search_ranks_the_closest_jd_first():
    search = SemanticSearch(HashingEmbedder(), InMemoryIndex())
    search.index(
        "job", "cae", "CAE Engineer crash LS-DYNA ANSA FMVSS correlation", {"status": "open"}
    )
    search.index(
        "job", "plc", "Controls Engineer Rockwell PLC ControlLogix FactoryTalk", {"status": "open"}
    )
    search.index("job", "old", "Controls Engineer Allen-Bradley PLC", {"status": "closed"})
    assert search.search("job", "crash simulation with LS-DYNA")[0].id == "cae"
    hits = search.search("job", "Allen-Bradley PLC programmer", where={"status": "open"})
    assert hits[0].id == "plc" and all(h.id != "old" for h in hits)


def test_qdrant_index_speaks_the_rest_api():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(404)
        if request.url.path.endswith("/points/search"):
            body = json.loads(request.content)
            assert body["filter"] == {"must": [{"key": "status", "match": {"value": "open"}}]}
            return httpx.Response(
                200,
                json={
                    "result": [
                        {"id": "x", "score": 0.9, "payload": {"_id": "job-1", "status": "open"}}
                    ]
                },
            )
        return httpx.Response(200, json={"result": True})

    client = httpx.Client(base_url="http://qdrant:6333", transport=httpx.MockTransport(handler))
    index = QdrantIndex("http://qdrant:6333", client=client)
    index.ensure_collection("rap_job", 4)
    index.upsert("rap_job", "job-1", [0.1, 0.2, 0.3, 0.4], {"status": "open"})
    [hit] = index.search("rap_job", [0.1, 0.2, 0.3, 0.4], 5, {"status": "open"})
    assert (hit.id, hit.payload) == ("job-1", {"status": "open"})
    assert ("PUT", "/collections/rap_job") in calls and (
        "PUT",
        "/collections/rap_job/points",
    ) in calls


# ----- Agent 1 ---------------------------------------------------------------------------------

CSV = """company,domain,industry,revenue_usd,employees,country,location,career_page_url,contact_name,contact_title,contact_email
Northfield Axle Systems,https://www.northfield-axle.example/,Automotive,$1.2B,5400,US,"Troy, MI",https://northfield-axle.example/careers,Rachel Lee,Engineering Manager,r.lee@northfield-axle.example
Northfield Axle Systems,northfield-axle.example,Automotive,$1.2B,5400,US,"Auburn Hills, MI",,Sam Ito,Talent Acquisition Partner,s.ito@northfield-axle.example
Northfield Axle Systems,northfield-axle.example,Automotive,$1.2B,5400,US,,,Bad Email,HR Manager,not-an-email
Tiny Widgets,tinywidgets.example,Manufacturing,40000000,80,US,"Dayton, OH",,Al Smith,Plant Manager,al@tinywidgets.example
Maple Robotics,maple-robotics.example,Industrial Automation,250M,900,CA,"Waterloo, ON",,Jo Roy,Director of Engineering,jo@maple-robotics.example
Lisbon Motors,lisbonmotors.example,Automotive,900M,3000,PT,Lisbon,,Ana Sousa,HR Manager,ana@lisbonmotors.example
"""


def test_contact_roles_follow_the_brd_list():
    assert contact_role("Director of Engineering") is ContactRole.DIRECTOR_OF_ENGINEERING
    assert contact_role("Engineering Manager") is ContactRole.ENGINEERING_MANAGER
    assert contact_role("Senior Talent Acquisition Partner") is ContactRole.TALENT_ACQUISITION
    assert contact_role("HR Manager") is ContactRole.HR_MANAGER
    assert contact_role("Plant Manager") is ContactRole.HIRING_MANAGER
    assert contact_role(None) is ContactRole.OTHER


def test_discovery_filters_merges_and_deduplicates():
    companies = InMemoryCompanies()
    agent = CompanyDiscoveryAgent(companies, [CsvCompanySource("list-1", CSV)])
    report = agent.run(DiscoveryCriteria())
    assert (report.seen, report.created) == (4, 2)
    assert report.skipped == {"revenue": 1, "country": 1}
    northfield = companies.by_domain("NORTHFIELD-AXLE.example")
    assert northfield.revenue_usd == 1.2e9
    assert northfield.locations == ["Auburn Hills, MI", "Troy, MI"]
    assert northfield.career_page_url == "https://northfield-axle.example/careers"
    contacts = {c.email: c for c in companies.contacts(northfield.id)}
    assert set(contacts) == {"r.lee@northfield-axle.example", "s.ito@northfield-axle.example"}
    assert contacts["s.ito@northfield-axle.example"].role is ContactRole.TALENT_ACQUISITION

    again = agent.run(DiscoveryCriteria())  # a second run updates, never duplicates
    assert (again.created, again.updated, again.contacts_added) == (0, 2, 0)
    assert normalize_domain("https://www.Example.com/jobs?x=1") == "example.com"


# ----- Agent 2 ---------------------------------------------------------------------------------

CAREERS_HTML = """<html><head>
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
 {"@type":"JobPosting","title":"Senior CAE Engineer - Crash","url":"/jobs/101","datePosted":"2026-11-15",
  "jobLocation":{"@type":"Place","address":{"addressLocality":"Troy","addressRegion":"MI"}}},
 {"@type":"JobPosting","title":"Supplier Quality Engineer","url":"/jobs/102","datePosted":"2026-11-01"},
 {"@type":"JobPosting","title":"Payroll Specialist","url":"/jobs/103","datePosted":"2026-11-10"}]}
</script>
<script type="application/ld+json">[{"@type":"JobPosting","title":"Controls Engineer (PLC)","datePosted":"2026-10-30"}]</script>
<script type="application/ld+json">{not json</script></head></html>"""


def test_titles_map_to_brd_target_roles():
    assert target_role("Senior CAE Engineer - Crash") == "CAE Engineer"
    assert target_role("Supplier Quality Engineer II") == "Supplier Quality Engineer"
    assert target_role("Manufacturing Engineering Manager") == "Engineering Manager"
    assert target_role("Controls Engineer (PLC)") == "PLC Engineer"
    assert target_role("Senior Software Engineer") is None
    assert target_role("Payroll Specialist") is None


def test_json_ld_postings_are_extracted():
    postings = extract_postings(CAREERS_HTML, "https://northfield-axle.example/careers")
    by_title = {p.title: p for p in postings}
    assert len(postings) == 4
    cae = by_title["Senior CAE Engineer - Crash"]
    assert (cae.url, cae.location, cae.date_posted) == (
        "https://northfield-axle.example/jobs/101",
        "Troy, MI",
        date(2026, 11, 15),
    )


def test_priority_score_rewards_recent_core_roles():
    today = date(2026, 11, 23)

    def posting(role, days):
        return JobPosting("p", "c", role, role, "u", date_posted=today - timedelta(days=days))

    hot = [
        posting("CAE Engineer", 3),
        posting("Quality Engineer", 5),
        posting("Mechanical Engineer", 9),
    ]
    cold = [posting("Tooling Engineer", 55)]
    weights = PriorityWeights()
    assert priority_score(hot, today, weights) > 70 > priority_score(cold, today, weights) > 0
    assert priority_score([], today, weights) == 0


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages

    def fetch(self, url):
        return self.pages.get(url)


def test_hiring_signals_agent_updates_postings_score_and_history():
    companies = InMemoryCompanies()
    CompanyDiscoveryAgent(companies, [CsvCompanySource("list-1", CSV)]).run()
    northfield = companies.by_domain("northfield-axle.example")
    now = datetime(2026, 11, 23, 9, tzinfo=UTC)
    fetcher = FakeFetcher({northfield.career_page_url: CAREERS_HTML})
    agent = HiringSignalsAgent(companies, fetcher, clock=lambda: now)
    report = agent.run()
    assert (report.target_postings, report.new_postings) == (3, 3)
    refreshed = companies.get(northfield.id)
    assert refreshed.priority_score == report.scores[northfield.id] > 0
    assert refreshed.hiring_history[-1]["roles"] == [
        "CAE Engineer",
        "PLC Engineer",
        "Supplier Quality Engineer",
    ]

    # A month later the page is gone: postings expire and the score drops to 0.
    later = HiringSignalsAgent(companies, FakeFetcher({}), clock=lambda: now + timedelta(days=30))
    assert later.run().expired == 3
    assert companies.get(northfield.id).priority_score == 0


def test_http_fetcher_respects_robots_txt():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
        return httpx.Response(200, text="<html>ok</html>")

    fetcher = HttpCareerPageFetcher(httpx.Client(transport=httpx.MockTransport(handler)))
    assert fetcher.fetch("https://acme.example/careers") == "<html>ok</html>"
    assert fetcher.fetch("https://acme.example/private/jobs") is None
