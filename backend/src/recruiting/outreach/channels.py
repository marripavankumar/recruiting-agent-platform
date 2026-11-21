"""Built-in channel adapters. Email adapters (Microsoft 365, Gmail) are added in Phase 2."""

from __future__ import annotations

from recruiting.domain import Channel
from recruiting.outreach.service import DeliveryResult, DeliveryStatus, OutboundMessage


class ManualLinkedInChannel:
    """Q15: LinkedIn has no public API for connection requests and bans automation.

    The approved request goes into the recruiter's task list; the recruiter sends it by hand in
    LinkedIn or LinkedIn Recruiter and marks it done.
    """

    channel = Channel.LINKEDIN

    def __init__(self) -> None:
        self.tasks: list[OutboundMessage] = []

    def deliver(self, message: OutboundMessage) -> DeliveryResult:
        self.tasks.append(message)
        return DeliveryResult(status=DeliveryStatus.QUEUED_FOR_MANUAL_SEND)
