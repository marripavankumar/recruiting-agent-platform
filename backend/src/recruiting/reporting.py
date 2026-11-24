"""KPI dashboard (BRD section 12, plan section 12) and the operations view.

Every number is computed from the platform's own records, so the dashboard shows pilot data as
soon as there is any. Each KPI states its basis (numerator and denominator) so a reviewer can
check it, and `no_data` rather than zero when nothing has happened yet.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from recruiting.approvals import ApprovalEngine
from recruiting.domain import GateType
from recruiting.outreach import DeliveryLog, DeliveryStatus
from recruiting.records.engagements import EngagementStore
from recruiting.records.memory import CompanyRepository, JobRepository
from recruiting.records.models import EngagementStatus, OutreachStatus, PlacementStage, Submission
from recruiting.records.rankings import RankingStore

INTERVIEW = {
    PlacementStage.INTERVIEW_REQUESTED, PlacementStage.INTERVIEW_SCHEDULED,
    PlacementStage.INTERVIEWED, PlacementStage.OFFER_EXTENDED, PlacementStage.OFFER_ACCEPTED,
    PlacementStage.OFFER_DECLINED, PlacementStage.STARTED,
}  # fmt: skip
INTERVIEWED = {
    PlacementStage.INTERVIEWED, PlacementStage.OFFER_EXTENDED, PlacementStage.OFFER_ACCEPTED,
    PlacementStage.OFFER_DECLINED, PlacementStage.STARTED,
}  # fmt: skip
OFFER = {
    PlacementStage.OFFER_EXTENDED, PlacementStage.OFFER_ACCEPTED, PlacementStage.OFFER_DECLINED,
    PlacementStage.STARTED,
}  # fmt: skip
CANDIDATE_REPLIED = {
    EngagementStatus.REPLIED, EngagementStatus.INTERESTED, EngagementStatus.NOT_INTERESTED,
    EngagementStatus.DETAILS_REQUESTED, EngagementStatus.COMPLETE, EngagementStatus.SUBMITTED,
}  # fmt: skip


def reached(submission: Submission, stages: set[PlacementStage]) -> bool:
    """True if the submission is, or ever was, in one of these stages."""
    if submission.stage in stages:
        return True
    return any(
        h.get("event") == "stage" and h.get("stage") in {s.value for s in stages}
        for h in submission.history
    )


@dataclass(frozen=True)
class Target:
    low: float | None = None  # at least
    high: float | None = None  # at most
    text: str = ""

    def status(self, value: float | None) -> str:
        if value is None:
            return "no_data"
        if self.low is not None and value < self.low:
            return "below_target"
        if self.high is not None and value > self.high:
            return "above_target"
        return "on_track"


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 3) if den else None


def _in(at: datetime | None, start: datetime, end: datetime) -> bool:
    return at is not None and start <= at < end


class Reporting:
    def __init__(
        self,
        companies: CompanyRepository,
        jobs: JobRepository,
        engagements: EngagementStore,
        submissions: Any,  # ClientSubmissionAgent (refreshes statuses on read)
        rankings: RankingStore,
        deliveries: DeliveryLog,
        approvals: ApprovalEngine | None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._companies = companies
        self._jobs = jobs
        self._engagements = engagements
        self._submissions = submissions
        self._rankings = rankings
        self._deliveries = deliveries
        self._approvals = approvals
        self._clock = clock

    def _first_client_emails(self, start: datetime, end: datetime):
        if self._approvals is None:
            return [], []
        sent, first = [], []
        for r in self._deliveries.receipts():
            if r.status is not DeliveryStatus.SENT or not _in(r.delivered_at, start, end):
                continue
            request = self._approvals.get(r.message.approval_request_id)
            if request.gate is GateType.FIRST_CLIENT_EMAIL:
                sent.append(r)
                if request.requested_by == "agent-3":
                    first.append(r)
        return sent, first

    def kpis(self, days: int = 30, end: datetime | None = None) -> dict[str, Any]:
        end = end or self._clock()
        start = end - timedelta(days=days)
        months, weeks = days / 30, days / 7
        out: list[dict[str, Any]] = []

        def kpi(key, label, value, target: Target, unit, **basis):
            out.append({"key": key, "label": label, "value": value, "unit": unit,
                        "target": target.text, "status": target.status(value),
                        "basis": basis})  # fmt: skip

        companies = [
            c for c in self._companies.list(limit=1_000_000) if _in(c.created_at, start, end)
        ]
        kpi("companies_identified", "New target companies identified",
            round(len(companies) / months, 1), Target(500, text="500+ per month"),
            "per month", companies=len(companies))  # fmt: skip

        sent, first = self._first_client_emails(start, end)
        kpi("outreach_emails", "Personalised outreach emails sent (after approval)",
            round(len(sent) / weeks, 1) if sent else None,
            Target(200, 500, "200-500 per week"), "per week", emails=len(sent))  # fmt: skip
        replied = 0
        for r in first:
            contact = self._companies.contact_by_email(r.message.recipient)
            company = self._companies.get(contact.company_id) if contact else None
            answered = company is not None and company.outreach_status in (
                OutreachStatus.REPLIED, OutreachStatus.CLIENT, OutreachStatus.DO_NOT_CONTACT,
            )  # fmt: skip
            replied += r.replied_at is not None or answered
        kpi(
            "client_response_rate",
            "Client response rate",
            _rate(replied, len(first)),
            Target(0.15, text=">15%"),
            "ratio",
            replied=replied,
            first_emails=len(first),
        )

        jobs = [j for j in self._jobs.list() if _in(j.created_at, start, end)]
        kpi("jds_received", "New job descriptions received", round(len(jobs) / months, 1),
            Target(40, text=">40 per month"), "per month", jobs=len(jobs))  # fmt: skip

        reviewed = [r for j in jobs if (r := self._rankings.get(j.id)) and any(
            c.verdict is not None for c in r.candidates)]  # fmt: skip
        qualified = [r.qualified_in_top_20 for r in reviewed]
        kpi("qualified_per_jd", "Qualified candidates identified per JD",
            round(statistics.mean(qualified), 1) if qualified else None,
            Target(10, 20, "10-20"), "candidates", reviewed_jobs=len(reviewed))  # fmt: skip

        contacted = replied_c = 0
        for e in self._engagements.list():
            events = [h for h in e.history if h.get("event") == "contacted"]
            if not events or not _in(datetime.fromisoformat(events[0]["at"]), start, end):
                continue
            contacted += 1
            replied_c += e.status in CANDIDATE_REPLIED
        kpi("candidate_response_rate", "Candidate response rate", _rate(replied_c, contacted),
            Target(0.30, text=">30%"), "ratio", replied=replied_c, contacted=contacted)  # fmt: skip

        subs = [s for s in self._submissions.list() if s.sent_at]
        hours = []
        for job in jobs:
            sent_at = [s.sent_at for s in subs if s.job_id == job.id]
            if sent_at and job.created_at:
                hours.append((min(sent_at) - job.created_at).total_seconds() / 3600)
        kpi(
            "hours_to_first_submission",
            "Time from JD receipt to first submission (median)",
            round(statistics.median(hours), 1) if hours else None,
            Target(high=24, text="<24 hours"),
            "hours",
            jobs_with_submissions=len(hours),
        )

        period = [s for s in subs if _in(s.sent_at, start, end)]
        interviews = [s for s in period if reached(s, INTERVIEW)]
        kpi("resume_to_interview", "Resume-to-interview ratio",
            _rate(len(interviews), len(period)), Target(0.25, text=">25%"), "ratio",
            interviews=len(interviews), submissions=len(period))  # fmt: skip
        held = [s for s in period if reached(s, INTERVIEWED)]
        offers = [s for s in held if reached(s, OFFER)]
        kpi(
            "interview_to_offer",
            "Interview-to-offer ratio",
            _rate(len(offers), len(held)),
            Target(0.20, text=">20%"),
            "ratio",
            offers=len(offers),
            interviews=len(held),
        )

        funnel = {
            "companies": len(companies),
            "first_client_emails": len(first),
            "client_replies": replied,
            "jobs": len(jobs),
            "candidates_contacted": contacted,
            "candidates_replied": replied_c,
            "submissions": len(period),
            "interviews": len(interviews),
            "offers": sum(1 for s in period if reached(s, OFFER)),
            "started": sum(1 for s in period if reached(s, {PlacementStage.STARTED})),
        }
        return {"from": start, "to": end, "days": days, "kpis": out, "funnel": funnel}

    def operations(
        self, follow_up_summary: dict[str, Any], llm_entries: list[Any], denied: int
    ) -> dict[str, Any]:
        """Support and monitoring (plan section 12, activity 6)."""
        now = self._clock()
        since = now - timedelta(days=30)
        receipts = [r for r in self._deliveries.receipts() if r.delivered_at >= since]
        email = [r for r in receipts if r.message.channel.value == "email"]
        failed = [r for r in email if r.status is DeliveryStatus.FAILED]
        pending = self._approvals.pending() if self._approvals else []
        oldest = min((r.created_at for r in pending), default=None)
        by_gate: dict[str, int] = {}
        for r in pending:
            by_gate[r.gate.value] = by_gate.get(r.gate.value, 0) + 1
        llm = [e for e in llm_entries if e.at >= since]
        alerts = []
        failure_rate = _rate(len(failed), len(email))
        if failure_rate is not None and failure_rate > 0.05:
            alerts.append(f"Email failure rate {failure_rate:.0%} in 30 days (alert above 5%)")
        if oldest and now - oldest > timedelta(hours=48):
            alerts.append("Approvals have waited more than 48 hours")
        if follow_up_summary.get("overdue"):
            alerts.append(f"{follow_up_summary['overdue']} follow-ups are overdue")
        return {
            "at": now,
            "email_30d": {
                "sent": len(email) - len(failed),
                "failed": len(failed),
                "failure_rate": failure_rate,
            },  # fmt: skip
            "linkedin_tasks_waiting": sum(
                1 for r in receipts if r.status is DeliveryStatus.QUEUED_FOR_MANUAL_SEND
            ),
            "approvals_pending": {
                "total": len(pending),
                "by_gate": by_gate,
                "oldest_hours": round((now - oldest).total_seconds() / 3600, 1) if oldest else None,
            },  # fmt: skip
            "llm_30d": {
                "calls": len(llm),
                "cost_usd": round(sum(e.cost_usd or 0 for e in llm), 4),
            },  # fmt: skip
            "follow_ups": follow_up_summary,
            "access_denied_recent": denied,
            "alerts": alerts,
        }
