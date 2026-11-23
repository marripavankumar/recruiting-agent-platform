"""Built-in channel adapters: manual LinkedIn (Q15), Microsoft 365 and Gmail email (Q11).

Email adapters take a token provider (a callable returning an OAuth access token from the
secrets adapter), so they work the same on every cloud. Each message carries an
X-RAP-Approval-Id header, which lets replies and bounces be traced to the approval.
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from email.message import EmailMessage
from email.policy import SMTP

import httpx

from recruiting.domain import Channel
from recruiting.outreach.service import DeliveryResult, DeliveryStatus, OutboundMessage

TokenProvider = Callable[[], str]
APPROVAL_HEADER = "X-RAP-Approval-Id"
SMTP_POLICY = SMTP.clone(max_line_length=998)


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


class Microsoft365EmailChannel:
    """Microsoft Graph `POST /users/{sender}/sendMail`. Graph accepts only X- custom headers, so
    the unsubscribe link lives in the approved body footer."""

    channel = Channel.EMAIL

    def __init__(
        self, sender: str, token: TokenProvider, client: httpx.Client | None = None
    ) -> None:
        self._sender = sender
        self._token = token
        self._http = client or httpx.Client(base_url="https://graph.microsoft.com/v1.0", timeout=30)

    def deliver(self, message: OutboundMessage) -> DeliveryResult:
        payload = {
            "message": {
                "subject": message.content.get("subject", ""),
                "body": {"contentType": "Text", "content": message.content.get("body", "")},
                "toRecipients": [{"emailAddress": {"address": message.recipient}}],
                "internetMessageHeaders": [
                    {"name": APPROVAL_HEADER, "value": message.approval_request_id}
                ],
            },
            "saveToSentItems": True,
        }
        response = self._http.post(
            f"/users/{self._sender}/sendMail",
            json=payload,
            headers={"Authorization": f"Bearer {self._token()}"},
        )
        response.raise_for_status()  # 202 Accepted; Graph returns no message id
        return DeliveryResult(DeliveryStatus.SENT, provider_message_id=None)


class GmailEmailChannel:
    """Gmail API `POST /gmail/v1/users/me/messages/send` with a raw RFC 5322 message."""

    channel = Channel.EMAIL

    def __init__(
        self,
        sender: str,
        token: TokenProvider,
        unsubscribe_url: Callable[[str], str] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._sender = sender
        self._token = token
        self._unsubscribe_url = unsubscribe_url
        self._http = client or httpx.Client(base_url="https://gmail.googleapis.com", timeout=30)

    def mime(self, message: OutboundMessage) -> EmailMessage:
        # RFC 5322 allows 998-character lines; without this the long List-Unsubscribe URL is
        # folded into an encoded word that mail clients do not recognise.
        mime = EmailMessage(policy=SMTP_POLICY)
        mime["From"] = self._sender
        mime["To"] = message.recipient
        mime["Subject"] = message.content.get("subject", "")
        mime[APPROVAL_HEADER] = message.approval_request_id
        if self._unsubscribe_url:
            mime["List-Unsubscribe"] = f"<{self._unsubscribe_url(message.recipient)}>"
            mime["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        mime.set_content(message.content.get("body", ""))
        return mime

    def deliver(self, message: OutboundMessage) -> DeliveryResult:
        raw = base64.urlsafe_b64encode(self.mime(message).as_bytes()).decode("ascii")
        response = self._http.post(
            "/gmail/v1/users/me/messages/send",
            json={"raw": raw},
            headers={"Authorization": f"Bearer {self._token()}"},
        )
        response.raise_for_status()
        return DeliveryResult(DeliveryStatus.SENT, provider_message_id=response.json().get("id"))
