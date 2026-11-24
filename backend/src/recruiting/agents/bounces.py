"""Email bounces (scenario S6): mark the address invalid and suggest another contact.

The mail-routing workflow forwards each bounce notice from the email provider. A hard bounce:
1. marks the delivery receipt BOUNCED (so it no longer counts as sent, and Agent 12 stops
   chasing a reply that cannot come);
2. marks the company contact's email invalid, so Agent 3 never picks it again;
3. puts the company back to "not contacted" when the bounced email was the first outreach,
   so the next Agent 3 run drafts to the next-best contact. That draft still waits at the
   first-client-email approval gate (BRD section 10); nothing is sent automatically.
Soft bounces (mailbox full, greylisting) are left to the provider's own retries.
A bounce to a candidate address is flagged for the recruiter to find a new address.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from recruiting.agents.client_outreach import ClientOutreachAgent
from recruiting.compliance import OptOutRegistry
from recruiting.outreach.service import DeliveryLog, DeliveryReceipt, DeliveryStatus
from recruiting.records.memory import CandidateRepository, CompanyRepository
from recruiting.records.models import OutreachStatus

# Statuses a bounce can apply to (BOUNCED again is a repeat notice and is a no-op).
_BOUNCEABLE = frozenset({DeliveryStatus.SENT, DeliveryStatus.BOUNCED})


class BounceError(Exception):
    pass


@dataclass
class BounceResult:
    recipient: str
    hard: bool
    approval_request_id: str | None = None
    contact_id: str | None = None
    company_id: str | None = None
    candidate_id: str | None = None
    alternative_contact: dict[str, Any] | None = None
    no_alternative_reason: str | None = None
    action: str = "none"


class BounceHandler:
    def __init__(
        self,
        deliveries: DeliveryLog,
        companies: CompanyRepository,
        candidates: CandidateRepository,
        client_outreach: ClientOutreachAgent | None,
    ) -> None:
        self._deliveries = deliveries
        self._companies = companies
        self._candidates = candidates
        self._client_outreach = client_outreach

    def _receipt(self, recipient: str, approval_request_id: str | None) -> DeliveryReceipt | None:
        if approval_request_id is None:
            return self._deliveries.latest_to(recipient)
        receipt = self._deliveries.get(approval_request_id)
        if receipt is None:
            raise BounceError(f"No delivery for approval {approval_request_id}")
        if OptOutRegistry.normalize(receipt.message.recipient) != OptOutRegistry.normalize(
            recipient
        ):
            raise BounceError("The bounce recipient does not match that delivery")
        return receipt

    def process(
        self,
        recipient: str,
        reason: str,
        *,
        hard: bool = True,
        approval_request_id: str | None = None,
    ) -> BounceResult:
        recipient = recipient.strip()
        result = BounceResult(recipient=recipient, hard=hard)
        receipt = self._receipt(recipient, approval_request_id)
        if receipt is not None:
            result.approval_request_id = receipt.message.approval_request_id
        if not hard:
            result.action = "soft_bounce_ignored"
            return result

        reason = (reason.strip() or "hard bounce")[:500]
        if receipt is not None and receipt.status in _BOUNCEABLE:
            self._deliveries.record_bounce(receipt.message.approval_request_id, reason)
            result.action = "receipt_bounced"

        contact = self._companies.contact_by_email(recipient)
        if contact is not None:
            result.contact_id, result.company_id = contact.id, contact.company_id
            if contact.email_valid:
                self._companies.save_contact(replace(contact, email_valid=False))
            result.action = "contact_invalidated"
            company = self._companies.get(contact.company_id)
            if company is not None and company.outreach_status is OutreachStatus.CONTACTED:
                # The only outreach went nowhere: let Agent 3 try the next contact.
                self._companies.set_outreach_status(company.id, OutreachStatus.NOT_CONTACTED)
                company = replace(company, outreach_status=OutreachStatus.NOT_CONTACTED)
            if company is not None and self._client_outreach is not None:
                alternative, why = self._client_outreach.choose_contact(company)
                if alternative is not None:
                    result.alternative_contact = {
                        "contact_id": alternative.id,
                        "full_name": alternative.full_name,
                        "title": alternative.title,
                        "role": alternative.role.value,
                        "email": alternative.email,
                    }
                else:
                    result.no_alternative_reason = why
            return result

        candidate = self._candidates.find_duplicate(recipient.lower(), None, None)
        if candidate is not None:
            result.candidate_id = candidate.id
            result.action = "candidate_flagged"  # recruiter finds a new address (LinkedIn, ATS)
        return result
