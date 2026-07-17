from wingman.policies.approval import ActionRequest, ApprovalPolicy, Decision


def _request() -> ActionRequest:
    return ActionRequest(
        action_type="send_email",
        target="recruiter@example.com",
        effect="send one email with the drafted introduction request",
    )


def test_denies_by_default() -> None:
    decision = ApprovalPolicy().check_action(_request())
    assert decision.decision is Decision.DENIED
    assert "no explicit grant" in decision.reason
    assert decision.decided_at.tzinfo is not None


def test_grant_must_match_action_type() -> None:
    policy = ApprovalPolicy(grants=frozenset({"update_contact"}))
    assert policy.check_action(_request()).decision is Decision.DENIED


def test_explicit_grant_approves() -> None:
    policy = ApprovalPolicy(grants=frozenset({"send_email"}))
    decision = policy.check_action(_request())
    assert decision.decision is Decision.APPROVED
    assert decision.request.target == "recruiter@example.com"
