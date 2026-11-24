import base64
import email
import json
from datetime import UTC, datetime

import httpx
import pytest

from recruiting.agents.client_outreach import ClientOutreachAgent, SenderProfile
from recruiting.agents.jd_analysis import JDAnalysisAgent
from recruiting.agents.jd_intake import InboundEmail, JDIntakeAgent, classify_by_rules
from recruiting.ai.llm import LLMError, LLMGateway, ScriptedProvider
from recruiting.ai.prompts import default_registry
from recruiting.ai.search import HashingEmbedder, InMemoryIndex, SemanticSearch
from recruiting.approvals import ApprovalEngine, DecisionAction, Reviewer
from recruiting.compliance import OptOutRegistry
from recruiting.compliance.unsubscribe import UnsubscribeLinks
from recruiting.domain import Channel, GateType, Role
from recruiting.outreach import (
    DailyCapReached,
    DeliveryResult,
    DeliveryStatus,
    GmailEmailChannel,
    InMemoryDeliveryLog,
    Microsoft365EmailChannel,
    OutboundMessage,
    OutreachService,
)
from recruiting.records.memory import InMemoryCompanies, InMemoryJobs
from recruiting.records.models import (
    Company,
    ConsentBasis,
    Contact,
    ContactRole,
    JobPosting,
    OutreachStatus,
)

LINKS = UnsubscribeLinks("test-secret-0123456789", "https://rap.example")
SENDER = SenderProfile("Marcus Hale", "CBS LLC, 100 Main St, Detroit, MI 48226")


class RecordingWorkflows:
    def __init__(self):
        self.started = []

    def start(self, **kwargs):
        self.started.append(kwargs)
        return f"req-{len(self.started)}"


def seed(companies, country="US", consent=ConsentBasis.NONE, priority=85):
    company = companies.save(
        Company("c1", "Northfield Axle Systems", "northfield-axle.example", "list", industry="Automotive",
                country=country, locations=["Troy, MI"], priority_score=priority)
    )  # fmt: skip
    companies.save_contact(Contact("hr", "c1", "Pat Doe", "HR Manager", ContactRole.HR_MANAGER,
                                   email="pat@northfield-axle.example", country=country, consent_basis=consent))  # fmt: skip
    companies.save_contact(Contact("hm", "c1", "Rachel Lee", "Engineering Manager", ContactRole.HIRING_MANAGER,
                                   email="r.lee@northfield-axle.example", country=country, consent_basis=consent))  # fmt: skip
    companies.save_posting(JobPosting("p1", "c1", "CAE Engineer", "CAE Engineer", "https://x/1"))
    return company


def agent(companies, opt_outs=None, gateway=None):
    workflows = RecordingWorkflows()
    return (
        ClientOutreachAgent(
            companies, opt_outs or OptOutRegistry(), workflows, SENDER, LINKS, gateway
        ),
        workflows,
    )


# ----- unsubscribe links -----------------------------------------------------------------------


def test_unsubscribe_tokens_cannot_be_forged():
    token = LINKS.token("Lee@Example.com")
    assert LINKS.verify(token) == "lee@example.com"
    encoded, _ = token.split(".")
    other = base64.urlsafe_b64encode(b"ceo@example.com").decode().rstrip("=")
    assert LINKS.verify(f"{other}.{token.split('.')[1]}") is None
    assert LINKS.verify("garbage") is None
    assert LINKS.url("a@b.co").startswith("https://rap.example/unsubscribe?token=")


# ----- Agent 3 ---------------------------------------------------------------------------------


def test_agent3_drafts_to_the_hiring_manager_with_compliance_footer():
    companies = InMemoryCompanies()
    seed(companies)
    outreach, workflows = agent(companies)
    report = outreach.run()
    [started] = workflows.started
    assert started["gate"] is GateType.FIRST_CLIENT_EMAIL and started["channel"] is Channel.EMAIL
    assert started["recipient"] == "r.lee@northfield-axle.example"
    body = started["content"]["body"]
    assert "Dear Rachel Lee," in body and "CAE Engineer" in body  # BRD sample template
    assert SENDER.postal_address in body and "unsubscribe" in body.lower()
    assert LINKS.url("r.lee@northfield-axle.example") in body
    assert report.drafted[0]["approval_request_id"] == "req-1"
    assert companies.get("c1").outreach_status is OutreachStatus.CONTACTED
    assert outreach.run().drafted == []  # never drafts twice


def test_agent3_uses_the_llm_draft_and_falls_back_on_errors():
    companies = InMemoryCompanies()
    seed(companies)
    provider = ScriptedProvider(
        {
            "first_client_email": lambda req: {
                "subject": "CAE support for Northfield",
                "body": "Dear Ms Lee,\n...",
            }
        }
    )
    outreach, workflows = agent(companies, gateway=LLMGateway(provider, default_registry()))
    outreach.run()
    assert workflows.started[0]["content"]["subject"] == "CAE support for Northfield"
    assert "Rachel Lee (Engineering Manager)" in provider.requests[0].user

    def broken(req):
        raise LLMError("down")

    companies2 = InMemoryCompanies()
    seed(companies2)
    fallback, wf2 = agent(
        companies2,
        gateway=LLMGateway(ScriptedProvider({"first_client_email": broken}), default_registry()),
    )
    fallback.run()
    assert (
        wf2.started[0]["content"]["subject"] == "Engineering Talent Support for Your Hiring Needs"
    )


def test_agent3_skips_opted_out_low_priority_and_canadian_contacts_without_consent():
    companies = InMemoryCompanies()
    seed(companies)
    opt_outs = OptOutRegistry()
    opt_outs.record("R.Lee@northfield-axle.example", "reply")
    outreach, workflows = agent(companies, opt_outs)
    outreach.run()
    assert workflows.started[0]["recipient"] == "pat@northfield-axle.example"  # next best contact

    canada = InMemoryCompanies()
    seed(canada, country="CA")
    report = agent(canada)[0].run()
    assert report.skipped == {"casl_consent_missing": ["Northfield Axle Systems"]}
    consented = InMemoryCompanies()
    seed(consented, country="CA", consent=ConsentBasis.IMPLIED_CONSPICUOUS_PUBLICATION)
    assert len(agent(consented)[0].run().drafted) == 1

    low = InMemoryCompanies()
    seed(low, priority=40)
    assert agent(low)[0].run(low.list()).skipped == {"low_priority": ["Northfield Axle Systems"]}


# ----- Agents 4 and 5 --------------------------------------------------------------------------

NOW = datetime(2026, 11, 23, 9, tzinfo=UTC)
JD_EMAIL = InboundEmail(
    from_address="R.Lee@northfield-axle.example",
    subject="Re: Engineering talent support",
    body="Hi, please see two openings below.",
    attachments_text=(
        "CAE Engineer - Crash. Required: LS-DYNA, ANSA, FMVSS 208. Preferred: Python. 5+ years. Troy, MI.\n"
        "Controls Engineer. Required: Rockwell ControlLogix PLC."
    ),
    received_at=NOW,
    message_id="<abc@mail>",
)


def extraction(req):
    base = dict(responsibilities=None, experience=None, education=None, industry=None, travel=None,
                work_mode=None, salary=None, contract_type=None, preferred_skills=[])  # fmt: skip
    return {"jobs": [
        {**base, "title": "CAE Engineer - Crash", "required_skills": ["LS-DYNA", "ansa", "FMVSS 208"],
         "preferred_skills": ["python"], "location": "Troy, MI", "visa_status": "US citizens preferred",
         "priority": "urgent", "experience": "5+ years", "low_confidence_fields": ["visa_status"]},
        {**base, "title": "Controls Engineer", "required_skills": ["Allen-Bradley PLC"], "location": None,
         "visa_status": None, "priority": None, "low_confidence_fields": []},
    ]}  # fmt: skip


def intake_setup(provider=None):
    companies, jobs = InMemoryCompanies(), InMemoryJobs()
    seed(companies)
    opt_outs, log = OptOutRegistry(), InMemoryDeliveryLog()
    log.claim(OutboundMessage("req-1", Channel.EMAIL, "r.lee@northfield-axle.example", {}), NOW)
    log.complete("req-1", DeliveryResult(DeliveryStatus.SENT), NOW)
    search = SemanticSearch(HashingEmbedder(), InMemoryIndex())
    gateway = LLMGateway(provider, default_registry()) if provider else None
    recruiters = iter(["u-rec-1", "u-rec-2"])
    intake = JDIntakeAgent(
        companies, jobs, opt_outs, log, JDAnalysisAgent(search, gateway), lambda: next(recruiters), gateway,
        clock=lambda: NOW,
    )  # fmt: skip
    return intake, companies, jobs, opt_outs, log, search


def test_reply_rules():
    def mail(body, attachments=""):
        return InboundEmail("a@b.co", "Re: hi", body, NOW, "m", attachments)

    assert classify_by_rules(mail("Please unsubscribe me", "JD attached")).category == "unsubscribe"
    assert (
        classify_by_rules(mail("I am out of the office until Monday")).category == "out_of_office"
    )
    assert classify_by_rules(mail("Thanks, we are not interested")).category == "not_interested"
    assert classify_by_rules(mail("I will send the JD next week")).category == "jd_to_follow"
    assert classify_by_rules(mail("", "Job description: ...")).category == "jd_received"
    assert classify_by_rules(mail("Hello")).category == "other"


def test_jd_reply_creates_analysed_searchable_jobs():
    provider = ScriptedProvider({
        "reply_classification": lambda req: {"category": "jd_received", "summary": "Two JDs"},
        "jd_extraction": extraction,
        "jd_analysis": lambda req: {
            "must_have_skills": ["LS-DYNA", "Ansa"], "nice_to_have_skills": ["Python"],
            "years_experience_min": 5, "industry": "Automotive", "software": ["LS-DYNA"], "cad_tools": [],
            "manufacturing_methods": [], "erp": [], "plm": [], "lean": False, "six_sigma": False,
            "automation": [], "programming": ["Python"], "search_summary": "Crash CAE engineer",
        },
    })  # fmt: skip
    intake, companies, jobs, _, log, search = intake_setup(provider)
    result = intake.process(JD_EMAIL)
    assert (result.category, result.classified_by, result.approval_request_id) == (
        "jd_received",
        "llm",
        "req-1",
    )
    assert log.get("req-1").replied_at == NOW
    cae, controls = (jobs.get(i) for i in result.job_ids)
    assert (cae.job_code, controls.job_code) == ("JOB-2026-0001", "JOB-2026-0002")
    assert (cae.recruiter_id, controls.recruiter_id) == ("u-rec-1", "u-rec-2")
    assert cae.priority == "urgent" and cae.source_email_ref == "<abc@mail>"
    assert cae.analysis["must_have_skills"] == ["LS-DYNA", "ANSA"]  # canonical names
    assert "visa_requirements" in cae.review_fields and "salary" in cae.review_fields
    assert controls.analysis["missing_info"] == ["location", "visa", "rate"]
    assert search.search("job", "crash LS-DYNA")[0].id == cae.id
    company = companies.get("c1")
    assert company.is_client and company.outreach_status is OutreachStatus.CLIENT


def test_rule_fallback_still_creates_a_job_for_review():
    intake, _, jobs, *_ = intake_setup()
    result = intake.process(JD_EMAIL)
    [job] = [jobs.get(i) for i in result.job_ids]
    assert result.classified_by == "rules"
    assert "LS-DYNA" in job.required_skills and "Rockwell" in job.required_skills
    assert "title" in job.review_fields and job.analysis["analysed_by"] == "rules"


def test_unsubscribe_reply_blocks_every_channel():
    intake, companies, jobs, opt_outs, *_ = intake_setup()
    reply = InboundEmail(
        "r.lee@northfield-axle.example", "Re: hi", "Please remove me from your list", NOW, "m2"
    )
    assert intake.process(reply).category == "unsubscribe"
    assert opt_outs.is_opted_out("R.LEE@northfield-axle.example")
    assert companies.get("c1").outreach_status is OutreachStatus.DO_NOT_CONTACT
    assert jobs.list() == []


def test_jd_from_unknown_sender_waits_for_a_person():
    intake, _, jobs, *_ = intake_setup()
    stranger = InboundEmail(
        "x@unknown.example", "JD", "", NOW, "m3", attachments_text="Job description ..."
    )
    result = intake.process(stranger)
    assert result.category == "jd_received" and result.company_id is None and jobs.list() == []


# ----- email channels and daily cap ------------------------------------------------------------

MESSAGE = OutboundMessage(
    "req-9", Channel.EMAIL, "lee@example.com", {"subject": "Hi", "body": "Body text"}
)


def test_microsoft_365_channel_calls_graph_send_mail():
    seen = {}

    def handler(request):
        seen.update(
            path=request.url.path,
            auth=request.headers["Authorization"],
            body=json.loads(request.content),
        )
        return httpx.Response(202)

    client = httpx.Client(
        base_url="https://graph.microsoft.com/v1.0", transport=httpx.MockTransport(handler)
    )
    result = Microsoft365EmailChannel("bd@cbs.example", lambda: "tok", client).deliver(MESSAGE)
    assert result.status is DeliveryStatus.SENT
    assert seen["path"] == "/v1.0/users/bd@cbs.example/sendMail" and seen["auth"] == "Bearer tok"
    msg = seen["body"]["message"]
    assert msg["toRecipients"][0]["emailAddress"]["address"] == "lee@example.com"
    assert msg["internetMessageHeaders"] == [{"name": "X-RAP-Approval-Id", "value": "req-9"}]


def test_gmail_channel_sends_raw_mime_with_list_unsubscribe():
    seen = {}

    def handler(request):
        raw = json.loads(request.content)["raw"]
        seen["mime"] = email.message_from_bytes(base64.urlsafe_b64decode(raw))
        return httpx.Response(200, json={"id": "gmail-1"})

    client = httpx.Client(
        base_url="https://gmail.googleapis.com", transport=httpx.MockTransport(handler)
    )
    result = GmailEmailChannel("bd@cbs.example", lambda: "tok", LINKS.url, client).deliver(MESSAGE)
    assert result.provider_message_id == "gmail-1"
    mime = seen["mime"]
    assert mime["To"] == "lee@example.com" and mime["X-RAP-Approval-Id"] == "req-9"
    assert mime["List-Unsubscribe"].startswith("<https://rap.example/unsubscribe?token=")
    assert mime["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


class CountingEmail:
    channel = Channel.EMAIL

    def deliver(self, message):
        return DeliveryResult(DeliveryStatus.SENT)


def test_daily_cap_defers_under_the_same_approval():
    engine = ApprovalEngine()
    now = [datetime(2026, 11, 24, 15, 0, tzinfo=UTC)]
    email = CountingEmail()
    service = OutreachService(
        engine, OptOutRegistry(), [email], daily_caps={Channel.EMAIL: 1}, clock=lambda: now[0]
    )
    ids = []
    for n in range(2):
        req = engine.submit(
            GateType.FIRST_CLIENT_EMAIL, f"s{n}", {"body": str(n)}, requested_by="agent"
        )
        engine.decide(req.id, Reviewer("u-bd", Role.BD_MANAGER), DecisionAction.APPROVE)
        ids.append(req.id)
    service.send(OutboundMessage(ids[0], Channel.EMAIL, "a@x.co", {"body": "0"}))
    assert service.remaining_today(Channel.EMAIL) == 0
    with pytest.raises(DailyCapReached):
        service.send(OutboundMessage(ids[1], Channel.EMAIL, "b@x.co", {"body": "1"}))
    # S24: scheduled for tomorrow under the same approval, not counted against today's cap.
    waiting = service.receipt(ids[1])
    assert waiting.status is DeliveryStatus.RETRY_SCHEDULED and waiting.attempts == 0
    assert waiting.next_attempt_at == datetime(2026, 11, 25, tzinfo=UTC)
    assert service.retry_due().due == 0  # not yet
    now[0] = datetime(2026, 11, 25, 8, 0, tzinfo=UTC)
    assert service.retry_due().sent == 1
    assert service.receipt(ids[1]).status is DeliveryStatus.SENT
