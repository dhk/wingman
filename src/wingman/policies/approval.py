"""Deterministic approval gate for external actions (RFC-003, RFC-006).

External actions do not exist yet; this is the gate they will pass through.
The gate fails closed: anything without an explicit grant is denied, and no
model participates in the decision.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from wingman.infrastructure.logs import get_logger

_logger = get_logger("policies.approval")


class Decision(StrEnum):
    APPROVED = "approved"
    DENIED = "denied"


class ActionRequest(BaseModel):
    """A proposed external action, stated as its exact effect."""

    action_type: str
    target: str
    effect: str


class ActionDecision(BaseModel):
    """The recorded outcome of a policy check."""

    request: ActionRequest
    decision: Decision
    reason: str
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ApprovalPolicy:
    """Checks action requests against explicit per-action-type grants.

    No grants exist in Phase 0, so every request is denied.
    """

    def __init__(self, grants: frozenset[str] = frozenset()) -> None:
        self._grants = grants

    def check_action(self, request: ActionRequest) -> ActionDecision:
        if request.action_type in self._grants:
            decision = ActionDecision(
                request=request,
                decision=Decision.APPROVED,
                reason=f"explicit grant for action type '{request.action_type}'",
            )
        else:
            decision = ActionDecision(
                request=request,
                decision=Decision.DENIED,
                reason=f"no explicit grant for action type '{request.action_type}'; "
                "the gate fails closed",
            )
        _logger.info(
            "action=%s target=%s decision=%s reason=%r",
            request.action_type,
            request.target,
            decision.decision.value,
            decision.reason,
        )
        return decision
