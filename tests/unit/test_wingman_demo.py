"""wingman_demo (issue #428): a zero-setup, zero-network walkthrough.

Deliberately does NOT use the `workspace` fixture other MCP-tool tests
share (see tests/unit/test_interview.py) — the whole point of this tool is
that it works with no workspace configured at all. `_ready_config()` would
return None here (no WINGMAN_DATA_DIR is set, and tests/conftest.py's
autouse `_isolated_home` fixture points HOME at a fresh, empty directory
per test, so the platform-default data dir never exists either) — and
wingman_demo must never even call it.
"""

from __future__ import annotations

from wingman.mcp_server import wingman_demo


def test_guided_tier_works_with_no_workspace_configured() -> None:
    result = wingman_demo()
    assert isinstance(result, str)
    assert len(result) > 500
    assert "WINGMAN DEMO" in result


def test_guided_is_the_default_tier() -> None:
    assert wingman_demo() == wingman_demo(tier="guided")


def test_guided_tier_states_it_is_fabricated_and_side_effect_free() -> None:
    result = wingman_demo(tier="guided")
    assert "invented for this walkthrough" in result
    assert "no network call" in result
    assert "nothing saved anywhere" in result


def test_guided_tier_uses_one_consistent_fictional_persona() -> None:
    result = wingman_demo(tier="guided")
    assert "Alex Rivera" in result
    assert "fictional" in result.lower()
    assert "not a real person" in result
    # the same persona and company thread runs through every step
    assert result.count("Alex") > 5
    assert "Meridian Health" in result
    assert "Priya Desai" in result


def test_guided_tier_covers_the_six_core_loop_steps() -> None:
    result = wingman_demo(tier="guided")
    assert "STEP 1 of 6" in result
    assert "career profile" in result.lower()
    assert "perspectives_start" in result

    assert "STEP 2 of 6" in result
    assert "job criteria" in result.lower()
    assert "job_criteria" in result

    assert "STEP 3 of 6" in result
    assert "fit brief" in result.lower()
    assert "MET" in result and "PARTIAL" in result and "GAP" in result
    assert "assess_job" in result

    assert "STEP 4 of 6" in result
    assert "POV card" in result
    assert "people_deep_dive" in result or "people_pov" in result

    assert "STEP 5 of 6" in result
    assert "relationship_log" in result

    assert "STEP 6 of 6" in result
    assert "digest" in result


def test_guided_tier_names_the_identity_and_synthesis_guardrails() -> None:
    result = wingman_demo(tier="guided")
    # never auto-resolve an identity from thin metadata
    assert "auto-resolving" in result or "auto-resolve" in result
    # a POV card is inference, not verified fact
    assert "inference" in result.lower()
    assert "not verified fact" in result.lower() or "not a verified fact" in result.lower()


def test_guided_tier_points_at_the_real_first_step_and_the_other_tier() -> None:
    result = wingman_demo(tier="guided")
    assert "perspectives_start()" in result
    assert "wingman_flow()" in result
    assert "tier='interactive'" in result


def test_interactive_tier_names_the_env_var_and_the_no_keys_reasoning() -> None:
    result = wingman_demo(tier="interactive")
    assert "WINGMAN_DATA_DIR" in result
    assert "API key" in result or "API keys" in result
    assert "skip" in result.lower()
    # a concrete, copy-pasteable CLI invocation
    assert "WINGMAN_DATA_DIR=" in result
    assert "wingman init" in result
    # the MCP client env-var equivalent
    assert "mcpServers" in result
    assert "env" in result


def test_interactive_tier_works_with_no_workspace_configured() -> None:
    result = wingman_demo(tier="interactive")
    assert isinstance(result, str)
    assert len(result) > 200


def test_unrecognized_tier_returns_a_plain_error_not_a_raise() -> None:
    result = wingman_demo(tier="nonsense")
    assert "guided" in result
    assert "interactive" in result
    assert "unrecognized tier" in result.lower()


def test_tier_is_normalized() -> None:
    assert wingman_demo(tier="  GUIDED  ") == wingman_demo(tier="guided")
    assert wingman_demo(tier="Interactive") == wingman_demo(tier="interactive")
