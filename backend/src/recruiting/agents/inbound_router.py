"""Routes an inbound email to the agent that handles it (plan sections 7, 9 and 14).

Used by the mailbox reader and by the /inbound/email webhook, so both behave the same:
- a reply from a known candidate goes to Agent 9 (interest, details, consent, unsubscribe);
- anything else goes to Agent 4 (client replies, JDs, unsubscribes);
- an automatic reply (out-of-office, auto-responder) is never counted as a reply (S9).
Bounce notices are recognised earlier, by the mailbox reader, and go to the bounce handler (S6).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from recruiting.agents.jd_intake import InboundEmail

if TYPE_CHECKING:
    from recruiting.api.platform import Platform


def route_email(platform: Platform, email: InboundEmail) -> dict[str, Any]:
    candidate = platform.candidates.find_duplicate(email.from_address.lower(), None, None)
    if candidate is not None:
        if email.auto_reply:
            # S9 for candidates: no reply recorded, so Agent 12 keeps its reminder.
            return {"handled_by": "agent-9", "category": "out_of_office",
                    "candidate_id": candidate.id}  # fmt: skip
        result = platform.resume_collection.process_reply(email, candidate)
        return {"handled_by": "agent-9", **asdict(result)}
    return {"handled_by": "agent-4", **asdict(platform.jd_intake.process(email))}
