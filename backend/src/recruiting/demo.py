"""Local demo data (RAP_DEMO_DATA=true): a realistic pipeline to click through in the web app.

Allowed only with RAP_ENVIRONMENT=local and RAP_STORAGE=memory. Nothing leaves the machine:
emails go to an in-memory outbox (DemoOutbox) and career pages come from canned HTML
(DemoCareerPages). The seed drives the real HTTP API in process, so the demo follows the same
rules, gates and LangGraph workflows as production, and leaves work waiting for every role:
first client emails and candidate outreach to approve, a job for the Account Manager to
confirm, a submission waiting for the recruiter, follow-ups and a client portal with feedback.

All companies, people and addresses are fictional (.example domains).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from recruiting.domain import Channel, Role
from recruiting.outreach import DeliveryResult, DeliveryStatus

if TYPE_CHECKING:
    from recruiting.api.services import Services

WEBHOOK_SECRET = "local-demo-webhook-secret"

CLIENT_EMAIL = "r.lee@northfield-axle.example"


class DemoOutbox:
    """Email channel that records each approved message instead of sending it."""

    channel = Channel.EMAIL

    def __init__(self) -> None:
        self.sent: list = []

    def deliver(self, message) -> DeliveryResult:
        self.sent.append(message)
        return DeliveryResult(DeliveryStatus.SENT, f"demo-{len(self.sent)}")


def _postings(domain: str, *items: tuple[str, str]) -> str:
    rows = ",".join(
        f'{{"@type":"JobPosting","title":"{title}","url":"https://{domain}.example/j/{i}",'
        f'"datePosted":"{day}"}}'
        for i, (title, day) in enumerate(items)
    )
    return f'<script type="application/ld+json">[{rows}]</script>'


CAREER_PAGES = {
    "northfield-axle": _postings(
        "northfield-axle",
        ("Quality Engineer", "2026-11-20"),
        ("Quality Engineer II", "2026-11-19"),
        ("Supplier Quality Engineer", "2026-11-18"),
    ),
    "great-lakes-controls": _postings(
        "great-lakes-controls",
        ("Controls Engineer", "2026-11-21"),
        ("PLC Programmer", "2026-11-17"),
        ("Automation Engineer", "2026-11-16"),
    ),
    "prairie-castings": _postings(
        "prairie-castings",
        ("Manufacturing Engineer", "2026-11-22"),
        ("Process Engineer", "2026-11-15"),
    ),
}


class DemoCareerPages:
    def fetch(self, url: str) -> str | None:
        return next((html for key, html in CAREER_PAGES.items() if key in url), None)


COMPANIES = """company,domain,industry,revenue_usd,country,location,career_page_url,contact_name,contact_title,contact_email
Northfield Axle Systems,northfield-axle.example,Automotive,1.2B,US,"Troy, MI",https://northfield-axle.example/careers,Rachel Lee,Engineering Manager,r.lee@northfield-axle.example
Great Lakes Controls,great-lakes-controls.example,Industrial Automation,640M,US,"Grand Rapids, MI",https://great-lakes-controls.example/careers,Omar Haddad,Director of Engineering,o.haddad@great-lakes-controls.example
Prairie Castings,prairie-castings.example,Manufacturing,310M,US,"Peoria, IL",https://prairie-castings.example/careers,Julie Tremblay,Plant Engineering Manager,j.tremblay@prairie-castings.example
Summit Aero Components,summit-aero.example,Aerospace,2.4B,US,"Wichita, KS",,Ken Morris,HR Manager,k.morris@summit-aero.example
"""

QUALITY_JD = (
    "Job description: Quality Engineer. Required: PFMEA, PPAP, APQP. 5+ years of automotive "
    "quality experience. Location: Troy, MI. Contract, 12 months."
)
CONTROLS_JD = (
    "Job description: Controls Engineer. Required: PLC programming (Allen-Bradley, Siemens), "
    "HMI, robotics integration. 5+ years of experience. Location: Grand Rapids, MI."
)

PEOPLE = [
    ("Priya Natarajan", "priya@mail.example", 8, "PFMEA, PPAP, APQP and 8D", "Troy, MI"),
    ("Wei Zhang", "wei@mail.example", 6, "PFMEA, PPAP, APQP and SPC", "Detroit, MI"),
    ("Raj Iyer", "raj@mail.example", 5, "PFMEA, PPAP, APQP and MSA", "Warren, MI"),
    ("Maria Gomez", "maria@mail.example", 9, "PFMEA, PPAP, APQP, IATF 16949", "Novi, MI"),
    ("Daniel Brooks", "dan@mail.example", 7, "PPAP, APQP and supplier audits", "Troy, MI"),
    ("Aisha Bello", "aisha@mail.example", 4, "PFMEA, PPAP, APQP and Six Sigma", "Southfield, MI"),
    ("Tom Fischer", "tom@mail.example", 3, "CNC programming and fixture design", "Toledo, OH"),
    ("Grace Kim", "grace@mail.example", 10, "PFMEA, PPAP, APQP, 8D and GD&T", "Livonia, MI"),
]


LINKEDIN_ONLY = (
    "Elena Ruiz\nDearborn, MI | linkedin.com/in/elena-ruiz-quality\n"
    "Supplier Quality Engineer with 9 years of experience in PFMEA, PPAP, APQP and 8D.\n"
    "B.S. Industrial Engineering"
)


def _resume(name: str, email: str, years: int, skills: str, city: str) -> str:
    return (
        f"{name}\n{city} | {email} | (313) 555-01{years:02d}\n"
        f"Quality Engineer with {years} years of experience in {skills}.\n"
        "B.S. Mechanical Engineering"
    )


def seed(services: Services, outbox: DemoOutbox) -> None:
    """Run the demo pipeline through the API. Called once at start-up."""
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from recruiting.api.app import create_app
    from recruiting.api.services import demo_users
    from recruiting.identity import DevHeaderAuthenticator

    # The seed signs in as the demo users through an in-process app of its own, so it works
    # whatever sign-in the deployment uses (dev headers or OIDC). Same stores, same rules.
    client = TestClient(create_app(replace(services, authenticator=DevHeaderAuthenticator())))
    by_role = {u.role: u.email for u in demo_users()}

    def call(method: str, path: str, role: Role | None, **kwargs):
        headers = {"X-Dev-User-Email": by_role[role]} if role else {}
        if path.startswith("/inbound/"):
            headers["X-RAP-Webhook-Secret"] = WEBHOOK_SECRET
        response = client.request(method, path, headers=headers, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Demo seed: {method} {path} -> {response.status_code} {response.text}"
            )
        return response.json() if "json" in response.headers.get("content-type", "") else None

    def approve(approval_id: str, role: Role) -> None:
        call("POST", f"/approvals/{approval_id}/decision", role, json={"action": "approve"})

    bd, rec, am, admin = Role.BD_MANAGER, Role.RECRUITER, Role.ACCOUNT_MANAGER, Role.ADMINISTRATOR

    # Agents 1-3: companies, hiring signals, first emails. Two approved and sent, the rest wait.
    call("POST", "/companies/import", bd, json={"source_name": "demo list", "csv": COMPANIES})
    call("POST", "/companies/hiring-signals", bd, json={})
    drafted = call("POST", "/companies/outreach-drafts", bd, json={})["drafted"]
    names = {c["id"]: c["name"] for c in call("GET", "/companies", bd)}
    for draft in drafted:
        if names[draft["company_id"]] != "Prairie Castings":
            approve(draft["approval_request_id"], bd)
    northfield = next(i for i, name in names.items() if name == "Northfield Axle Systems")

    # Agents 4-5: two clients reply with JDs. The Quality Engineer job is confirmed; the
    # Controls Engineer job waits for the Account Manager.
    first = next(m for m in outbox.sent if m.recipient == CLIENT_EMAIL)
    reply = {"from_address": CLIENT_EMAIL, "subject": "Re: " + first.content["subject"],
             "body": "Thanks, JD attached. We need someone soon.", "attachments_text": QUALITY_JD,
             "message_id": "<demo-jd-1@mail>"}  # fmt: skip
    [job_id] = call("POST", "/inbound/email", None, json=reply)["job_ids"]
    call("PATCH", f"/jobs/{job_id}", am, json={"title": "Quality Engineer",
         "confirm_fields": ["title", "location", "employment_type", "rate"]})  # fmt: skip
    controls = next(
        (m for m in outbox.sent if m.recipient == "o.haddad@great-lakes-controls.example"), None
    )
    if controls is not None:
        call("POST", "/inbound/email", None, json={
            "from_address": controls.recipient, "subject": "Re: " + controls.content["subject"],
            "body": "Good timing. JD attached for our Controls Engineer opening.",
            "attachments_text": CONTROLS_JD, "message_id": "<demo-jd-2@mail>"})  # fmt: skip

    # Agents 6-7: candidates added and ranked; the recruiter marks the top five as qualified.
    resumes = [{"resume_text": _resume(*p), "source": "ats"} for p in PEOPLE]
    resumes.append({"resume_text": LINKEDIN_ONLY, "source": "linkedin"})
    call("POST", "/candidates", rec, json={"resumes": resumes})
    ranking = call("POST", f"/jobs/{job_id}/ranking", rec)
    ranked = [c for c in ranking["candidates"] if c["rank"] is not None]
    by_email = [c for c in ranked if c["full_name"] != "Elena Ruiz"]
    for c in by_email[:5]:
        call("POST", f"/jobs/{job_id}/ranking/{c['candidate_id']}/review", rec,
             json={"verdict": "qualified"})  # fmt: skip

    # Agent 8: outreach to the top five. Three approved and sent, two wait for the recruiter.
    ids = [c["candidate_id"] for c in by_email[:5]]
    out = call("POST", f"/jobs/{job_id}/outreach", rec,
               json={"candidate_ids": ids, "channels": ["email"]})["drafted"]  # fmt: skip
    for d in out[:3]:
        approve(d["approval_request_id"], rec)

    # Q15: a LinkedIn-only candidate. The approved note waits for the recruiter to send by hand.
    elena = next((c["candidate_id"] for c in ranked if c["full_name"] == "Elena Ruiz"), None)
    if elena is not None:
        note = call("POST", f"/jobs/{job_id}/outreach", rec,
                    json={"candidate_ids": [elena], "channels": ["linkedin"]})["drafted"]  # fmt: skip
        for d in note:
            approve(d["approval_request_id"], rec)

    # Agent 9: the first contacted candidate replies; details request approved and completed.
    contacted = {m.recipient for m in outbox.sent}
    person = next(p for p in PEOPLE if p[1] in contacted)
    answer = {"from_address": person[1], "subject": "Re: Quality Engineer opportunity",
              "body": "Yes, I'm interested. Happy to share details.", "attachments_text": "",
              "message_id": "<demo-c-1@mail>"}  # fmt: skip
    result = call("POST", "/inbound/email", None, json=answer)
    approve(result["details_request_id"], rec)
    link = next(m for m in reversed(outbox.sent) if m.recipient == person[1])
    token = re.search(r"/collect/([\w-]+)", link.content["body"]).group(1)
    form = {"availability": "2 weeks", "visa_status": "US citizen", "current_location": person[4],
            "expected_rate": "$75/hr", "notice_period": "2 weeks",
            "work_authorization": "US citizen", "preferred_location": "Detroit area",
            "consent_store": "yes", "consent_submit": "yes"}  # fmt: skip
    client.post(f"/collect/{token}", data=form)

    # Agents 10-11: match report and package; the submission waits for the recruiter's approval.
    sub = call(
        "POST", f"/jobs/{job_id}/submissions", rec, json={"candidate_id": result["candidate_id"]}
    )
    call("POST", f"/submissions/{sub['id']}/request-approval", rec,
         json={"right_to_represent": True, "acknowledge_flags": True})  # fmt: skip

    # The client's portal user, and Agent 12's follow-up sweep.
    call("POST", "/admin/users", admin, json={"email": CLIENT_EMAIL, "display_name": "Rachel Lee",
         "role": "client", "client_company_id": northfield})  # fmt: skip
    call("POST", "/follow-ups/run", admin)
