"""Who approves at each gate (Q14): configurable, with an optional second approver."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from recruiting.domain import GateType, Role


@dataclass(frozen=True)
class GatePolicy:
    gate: GateType
    first_approver: Role
    second_approver: Role | None = None

    @property
    def steps(self) -> tuple[Role, ...]:
        if self.second_approver is None:
            return (self.first_approver,)
        return (self.first_approver, self.second_approver)


# Q14 defaults. The Administrator can change the role and add a second approver per gate.
DEFAULT_POLICIES: dict[GateType, GatePolicy] = {
    GateType.FIRST_CLIENT_EMAIL: GatePolicy(GateType.FIRST_CLIENT_EMAIL, Role.BD_MANAGER),
    GateType.LINKEDIN_CONNECTION_REQUEST: GatePolicy(
        GateType.LINKEDIN_CONNECTION_REQUEST, Role.RECRUITER
    ),
    GateType.CANDIDATE_OUTREACH_EMAIL: GatePolicy(
        GateType.CANDIDATE_OUTREACH_EMAIL, Role.RECRUITER
    ),
    GateType.RESUME_SUBMISSION: GatePolicy(GateType.RESUME_SUBMISSION, Role.RECRUITER),
}


class PolicyStore(Protocol):
    def load(self) -> dict[GateType, GatePolicy]: ...

    def save(self, policy: GatePolicy, updated_by: str) -> None: ...


class InMemoryPolicyStore:
    def __init__(self, policies: Mapping[GateType, GatePolicy] = DEFAULT_POLICIES) -> None:
        self._policies = dict(policies)

    def load(self) -> dict[GateType, GatePolicy]:
        return dict(self._policies)

    def save(self, policy: GatePolicy, updated_by: str) -> None:
        self._policies[policy.gate] = policy
