"""Core vocabulary shared by every agent (BRD sections 9 and 10)."""

from enum import StrEnum


class Role(StrEnum):
    """The six user roles in BRD section 9."""

    ADMINISTRATOR = "administrator"
    BD_MANAGER = "bd_manager"
    RECRUITER = "recruiter"
    ACCOUNT_MANAGER = "account_manager"
    SALES_MANAGER = "sales_manager"
    CLIENT = "client"


class GateType(StrEnum):
    """The four human approval gates in BRD section 10."""

    FIRST_CLIENT_EMAIL = "first_client_email"
    LINKEDIN_CONNECTION_REQUEST = "linkedin_connection_request"
    CANDIDATE_OUTREACH_EMAIL = "candidate_outreach_email"
    RESUME_SUBMISSION = "resume_submission"


class Channel(StrEnum):
    """Delivery channels of the shared Outreach Service (Q7)."""

    EMAIL = "email"
    LINKEDIN = "linkedin"
