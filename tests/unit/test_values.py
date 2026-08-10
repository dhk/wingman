"""Value-dimension inference (v2 of issue #240, docs/RFC.md RFC-051):
the minimum-data floor, model-proposes/code-disposes axis validation, the
deterministic score/evidence contract, staleness, and the CLI+MCP surface.
"""

import json
from pathlib import Path

import pytest

from wingman.agents.profile_curator import ProposalParseError
from wingman.application.ingest import IngestError
from wingman.application.interview import capture_interview_reaction
from wingman.application.pov import CORPUS_PERSON_ID, CORPUS_PERSON_NAME, persona_card_id
from wingman.application.values import (
    MAX_AXES,
    MIN_AXES_REQUIRED,
    MIN_ITEMS,
    MIN_SUBTYPES,
    build_value_profile,
    new_captures_since,
    render_value_profile,
)
from wingman.infrastructure.config import Config, load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.base import ModelRequest, ModelResponse


class ScriptedProvider:
    """Returns a canned JSON proposal; records the prompt it was given."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.last_prompt: str | None = None

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.last_prompt = request.prompt
        return ModelResponse(
            text=json.dumps(self._payload), provider="scripted", model="scripted-1", latency_ms=0
        )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return tmp_path


def _seed_min_floor(storage: Storage, config: Config, persona_id: str | None = None) -> None:
    """3 values_pro + 3 values_con — the minimum evidence floor
    (MIN_ITEMS=6 across MIN_SUBTYPES=2), one of each SentimentIntensity
    level per side so the deterministic score math has real spread."""
    for target, intensity in (
        ("Jane Goodall", "mild"),
        ("Fred Rogers", "moderate"),
        ("Marie Curie", "strong"),
    ):
        capture_interview_reaction(
            "values_pro",
            target,
            f"{target} embodies steadfast dedication.",
            config,
            storage,
            intensity=intensity,
            persona_id=persona_id,
        )
    for target, intensity in (
        ("Figure Con Mild", "mild"),
        ("Figure Con Moderate", "moderate"),
        ("Figure Con Strong", "strong"),
    ):
        capture_interview_reaction(
            "values_con",
            target,
            f"{target} abandoned their cause halfway through.",
            config,
            storage,
            intensity=intensity,
            persona_id=persona_id,
        )


def _all_item_ids(storage: Storage) -> list[str]:
    return [item.item_id for item in storage.list_profile_items()]


# --- the minimum-data floor -------------------------------------------------


def test_floor_refuses_below_min_items(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        capture_interview_reaction(
            "values_pro", "Jane Goodall", "Steadfast dedication.", config, storage
        )
        capture_interview_reaction(
            "values_con", "Someone", "Abandoned their cause.", config, storage
        )
        with pytest.raises(IngestError, match="not enough captured evidence"):
            build_value_profile(storage, ScriptedProvider({"axes": []}))
        assert storage.get_value_profile(CORPUS_PERSON_ID) is None


def test_floor_refuses_with_only_one_subtype(workspace: Path) -> None:
    """MIN_ITEMS alone isn't enough — six values_pro nominations and
    nothing else still refuses (design question #2's MIN_SUBTYPES rule)."""
    config = load_config()
    with Storage(config.db_path) as storage:
        for index in range(MIN_ITEMS):
            capture_interview_reaction(
                "values_pro", f"Person {index}", "Steadfast dedication.", config, storage
            )
        with pytest.raises(IngestError, match="across 1 subtype"):
            build_value_profile(storage, ScriptedProvider({"axes": []}))


def test_floor_error_names_what_to_capture_more_of(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        with pytest.raises(IngestError, match="wingman interview") as excinfo:
            build_value_profile(storage, ScriptedProvider({"axes": []}))
        assert f"at least {MIN_ITEMS} items" in str(excinfo.value)
        assert f"at least {MIN_SUBTYPES} subtypes" in str(excinfo.value)


# --- deterministic scoring and evidence traceability ------------------------


def test_score_is_computed_deterministically_never_from_the_model(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        item_ids = _all_item_ids(storage)
        provider = ScriptedProvider(
            {
                "axes": [
                    {
                        "name": "Steadfastness",
                        "description": "Commitment to a single cause over time.",
                        "item_ids": item_ids,
                    },
                    {"name": "filler two", "item_ids": [item_ids[0]]},
                    {"name": "filler three", "item_ids": [item_ids[0]]},
                ]
            }
        )
        report = build_value_profile(storage, provider)
        assert provider.last_prompt is not None
        axis = report.profile.axes[0]
        assert axis.name == "Steadfastness"
        # 3 pro (mild=1/3, moderate=2/3, strong=1.0) + 3 con (negated) sums to
        # exactly zero — the model never sees or proposes this number.
        assert axis.score == pytest.approx(0.0, abs=1e-9)
        assert axis.label == "mixed / ambivalent"
        assert len(axis.evidence) == 6
        assert {span.item_id for span in axis.evidence} == set(item_ids)


def test_score_pro_only_axis_is_strongly_positive(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        pro_ids = [
            item.item_id for item in storage.list_profile_items() if item.subtype == "values_pro"
        ]
        provider = ScriptedProvider(
            {
                "axes": [
                    {"name": "Devotion", "item_ids": pro_ids},
                    {"name": "filler two", "item_ids": pro_ids[:1]},
                    {"name": "filler three", "item_ids": pro_ids[:1]},
                ]
            }
        )
        report = build_value_profile(storage, provider)
        axis = report.profile.axes[0]
        # (1/3 + 2/3 + 1.0) / 3 == 2/3
        assert axis.score == pytest.approx(2 / 3)
        assert axis.label == "strongly drawn to"


def test_evidence_traces_back_to_subtype_target_and_quote(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        item_ids = _all_item_ids(storage)
        provider = ScriptedProvider(
            {
                "axes": [
                    {"name": "Steadfastness", "item_ids": item_ids},
                    {"name": "filler two", "item_ids": [item_ids[0]]},
                    {"name": "filler three", "item_ids": [item_ids[0]]},
                ]
            }
        )
        report = build_value_profile(storage, provider)
        span = report.profile.axes[0].evidence[0]
        stored_item = storage.get_profile_item(span.item_id)
        assert stored_item is not None
        assert span.subtype == stored_item.subtype
        assert span.quote == stored_item.detail
        assert stored_item.intensity is not None
        assert span.intensity == stored_item.intensity.value
        targets = {s.target for s in report.profile.axes[0].evidence}
        assert "Jane Goodall" in targets


def test_unknown_item_id_is_dropped_and_all_unknown_axis_is_rejected(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        item_ids = _all_item_ids(storage)
        provider = ScriptedProvider(
            {
                "axes": [
                    {
                        "name": "Partially fabricated",
                        "item_ids": [item_ids[0], "not-a-real-item"],
                    },
                    {"name": "Wholly fabricated", "item_ids": ["also-not-real"]},
                    {"name": "filler three", "item_ids": [item_ids[0]]},
                    {"name": "filler four", "item_ids": [item_ids[0]]},
                ]
            }
        )
        report = build_value_profile(storage, provider)
        names = {axis.name for axis in report.profile.axes}
        assert "Partially fabricated" in names
        surviving = next(a for a in report.profile.axes if a.name == "Partially fabricated")
        assert len(surviving.evidence) == 1  # the fabricated id was silently dropped
        assert "Wholly fabricated" not in names
        rejected_reasons = {r.name: r.reason for r in report.rejected}
        assert "cited no item_id" in rejected_reasons["Wholly fabricated"]


def test_empty_axis_name_is_rejected(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        item_ids = _all_item_ids(storage)
        provider = ScriptedProvider(
            {
                "axes": [
                    {"name": "", "item_ids": item_ids},
                    {"name": "Real axis one", "item_ids": item_ids},
                    {"name": "Real axis two", "item_ids": item_ids},
                    {"name": "Real axis three", "item_ids": item_ids},
                ]
            }
        )
        report = build_value_profile(storage, provider)
        assert any(r.reason == "empty axis name" for r in report.rejected)
        assert len(report.profile.axes) == 3


# --- axis-count bounds -------------------------------------------------------


def test_over_max_axes_are_rejected_deterministically(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        item_ids = _all_item_ids(storage)
        provider = ScriptedProvider(
            {
                "axes": [
                    {"name": f"Axis {index}", "item_ids": item_ids} for index in range(MAX_AXES + 2)
                ]
            }
        )
        report = build_value_profile(storage, provider)
        assert len(report.profile.axes) == MAX_AXES
        over_limit = [r for r in report.rejected if "limit" in r.reason]
        assert len(over_limit) == 2


def test_too_few_surviving_axes_raises_and_stores_nothing(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        item_ids = _all_item_ids(storage)
        provider = ScriptedProvider({"axes": [{"name": "Only one", "item_ids": item_ids}]})
        with pytest.raises(IngestError, match="only 1 value axis") as excinfo:
            build_value_profile(storage, provider)
        assert f"need at least {MIN_AXES_REQUIRED}" in str(excinfo.value)
        assert storage.get_value_profile(CORPUS_PERSON_ID) is None


def test_unparseable_model_output_raises(workspace: Path) -> None:
    config = load_config()

    class GarbageProvider:
        def complete(self, request: ModelRequest) -> ModelResponse:
            return ModelResponse(text="not json", provider="x", model="x", latency_ms=0)

    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        with pytest.raises(ProposalParseError):
            build_value_profile(storage, GarbageProvider())


# --- persona scoping ---------------------------------------------------------


def test_persona_floor_error_message_is_persona_specific(workspace: Path) -> None:
    from wingman.application.coaching import find_or_create_persona

    config = load_config()
    with Storage(config.db_path) as storage:
        persona = find_or_create_persona("Mike Chen", storage)
        with pytest.raises(IngestError, match="for Mike Chen yet"):
            build_value_profile(storage, ScriptedProvider({"axes": []}), persona=persona)


def test_persona_scoping_isolates_captures(workspace: Path) -> None:
    from wingman.application.coaching import find_or_create_persona

    config = load_config()
    with Storage(config.db_path) as storage:
        persona = find_or_create_persona("Mike Chen", storage)
        _seed_min_floor(storage, config, persona_id=persona.persona_id)
        # the coach's OWN captures exist too, well past the floor on their
        # own — but they must never leak into the persona's profile.
        _seed_min_floor(storage, config)

        persona_item_ids = [
            item.item_id
            for item in storage.list_profile_items()
            if item.persona_id == persona.persona_id
        ]
        provider = ScriptedProvider(
            {
                "axes": [
                    {"name": "One", "item_ids": persona_item_ids},
                    {"name": "Two", "item_ids": persona_item_ids},
                    {"name": "Three", "item_ids": persona_item_ids},
                ]
            }
        )
        report = build_value_profile(storage, provider, persona=persona)
        assert report.profile.subject_id == persona_card_id(persona.persona_id)
        assert report.profile.subject_name == "Mike Chen"
        assert set(report.profile.source_item_ids) == set(persona_item_ids)
        coach_item_ids = {
            item.item_id for item in storage.list_profile_items() if item.persona_id is None
        }
        assert coach_item_ids.isdisjoint(report.profile.source_item_ids)
        assert storage.get_value_profile(CORPUS_PERSON_ID) is None


# --- staleness ----------------------------------------------------------------


def test_new_captures_since_reports_staleness(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        item_ids = _all_item_ids(storage)
        provider = ScriptedProvider(
            {
                "axes": [
                    {"name": "One", "item_ids": item_ids},
                    {"name": "Two", "item_ids": item_ids},
                    {"name": "Three", "item_ids": item_ids},
                ]
            }
        )
        report = build_value_profile(storage, provider)
        assert new_captures_since(storage, report.profile) == 0

        capture_interview_reaction(
            "mission_alignment_pro",
            "Acme Robotics",
            "They ship what they promise.",
            config,
            storage,
            primary_purpose="Builds robots.",
            intensity="strong",
        )
        assert new_captures_since(storage, report.profile) == 1

        rendered = render_value_profile(
            report.profile, stale_new_captures=new_captures_since(storage, report.profile)
        )
        assert "1 new capture since this was built" in rendered


def test_render_value_profile_shows_axes_and_evidence(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        item_ids = _all_item_ids(storage)
        provider = ScriptedProvider(
            {
                "axes": [
                    {
                        "name": "Steadfastness",
                        "description": "Sticks with one cause.",
                        "item_ids": item_ids,
                    },
                    {"name": "Two", "item_ids": item_ids[:1]},
                    {"name": "Three", "item_ids": item_ids[:1]},
                ]
            }
        )
        report = build_value_profile(storage, provider)
        rendered = render_value_profile(report.profile)
        assert "Value profile: Your corpus" in rendered
        assert "Steadfastness" in rendered
        assert "Sticks with one cause." in rendered
        assert "Jane Goodall" in rendered


# --- CLI + MCP surface --------------------------------------------------------


def test_cli_values_shows_stored_profile_with_persona_and_staleness(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from wingman.application.coaching import find_or_create_persona
    from wingman.cli.main import app
    from wingman.domain.values import ValueAxis, ValueAxisEvidence, ValueProfile
    from wingman.infrastructure.config import ENV_DATA_DIR

    config = load_config()
    with Storage(config.db_path) as storage:
        persona = find_or_create_persona("Mike Chen", storage)
        storage.save_value_profile(
            ValueProfile(
                subject_id=persona_card_id(persona.persona_id),
                subject_name="Mike Chen",
                items_used=1,
                source_item_ids=["seed-item"],
                provider="scripted",
                model="scripted-1",
                prompt_version="v0",
                axes=[
                    ValueAxis(
                        name="Steadfastness",
                        score=0.5,
                        label="leans toward",
                        evidence=[
                            ValueAxisEvidence(
                                item_id="seed-item",
                                subtype="values_pro",
                                target="Jane Goodall",
                                quote="Never wavered.",
                                intensity="moderate",
                                signed_weight=0.5,
                            )
                        ],
                    )
                ],
            )
        )

    monkeypatch.setenv(ENV_DATA_DIR, str(config.data_dir))
    runner = CliRunner()
    runner.invoke(app, ["coach-persona", "set", "Mike Chen"])
    result = runner.invoke(app, ["values"])
    assert result.exit_code == 0
    assert "Acting as: coach for Mike Chen." in result.output
    assert "Mike Chen" in result.output
    assert "Steadfastness" in result.output
    assert "(stored profile — rebuild with --refresh)" in result.output


def test_cli_values_refresh_builds_via_scripted_provider(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    import wingman.cli.main as cli_main
    from wingman.infrastructure.config import ENV_DATA_DIR

    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        item_ids = _all_item_ids(storage)

    provider = ScriptedProvider(
        {
            "axes": [
                {"name": "Steadfastness", "item_ids": item_ids},
                {"name": "Two", "item_ids": item_ids[:1]},
                {"name": "Three", "item_ids": item_ids[:1]},
            ]
        }
    )
    monkeypatch.setattr(cli_main, "get_provider", lambda capability, config: provider)
    monkeypatch.setenv(ENV_DATA_DIR, str(config.data_dir))
    runner = CliRunner()
    result = runner.invoke(cli_main.app, ["values", "--refresh"])
    assert result.exit_code == 0, result.output
    assert "Steadfastness" in result.output

    with Storage(config.db_path) as storage:
        stored = storage.get_value_profile(CORPUS_PERSON_ID)
        assert stored is not None
        assert stored.subject_name == CORPUS_PERSON_NAME


def test_mcp_my_values_stored_profile_reads_active_persona(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.application.coaching import find_or_create_persona
    from wingman.domain.values import ValueAxis, ValueAxisEvidence, ValueProfile
    from wingman.mcp_server import coach_persona, my_values

    config = load_config()
    with Storage(config.db_path) as storage:
        persona = find_or_create_persona("Mike Chen", storage)
        own_profile = ValueProfile(
            subject_id=CORPUS_PERSON_ID,
            subject_name=CORPUS_PERSON_NAME,
            items_used=1,
            source_item_ids=["own-item"],
            provider="scripted",
            model="scripted-1",
            prompt_version="v0",
            axes=[
                ValueAxis(
                    name="Coach axis",
                    score=0.1,
                    label="mixed / ambivalent",
                    evidence=[
                        ValueAxisEvidence(
                            item_id="own-item",
                            subtype="values_pro",
                            target="Someone",
                            quote="Why.",
                            signed_weight=0.1,
                        )
                    ],
                )
            ],
        )
        storage.save_value_profile(own_profile)
        mike_profile = own_profile.model_copy(
            update={
                "profile_id": "mike-profile-id",
                "subject_id": persona_card_id(persona.persona_id),
                "subject_name": "Mike Chen",
            }
        )
        storage.save_value_profile(mike_profile)

    coach_persona("set", "Mike Chen")
    result = my_values(refresh=False)
    assert result.startswith("Acting as: coach for Mike Chen.")
    assert "Mike Chen" in result
    assert "Your corpus" not in result

    coach_persona("clear")
    own_result = my_values(refresh=False)
    assert own_result.startswith("Acting as: yourself.")
    assert "Your corpus" in own_result


def test_mcp_my_values_refresh_builds_via_scripted_provider(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman import mcp_server

    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        item_ids = _all_item_ids(storage)

    provider = ScriptedProvider(
        {
            "axes": [
                {"name": "Steadfastness", "item_ids": item_ids},
                {"name": "Two", "item_ids": item_ids[:1]},
                {"name": "Three", "item_ids": item_ids[:1]},
            ]
        }
    )
    monkeypatch.setattr(mcp_server, "get_provider", lambda capability, config: provider)
    result = mcp_server.my_values(refresh=True)
    assert "Steadfastness" in result
    with Storage(config.db_path) as storage:
        assert storage.get_value_profile(CORPUS_PERSON_ID) is not None


def test_mcp_my_values_floor_error_surfaces_as_a_message_not_a_traceback(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman import mcp_server

    config = load_config()
    Storage(config.db_path).close()  # initializes the workspace db file
    # The floor check happens before the model is ever called — a provider
    # that would error if actually invoked still proves that, since it's
    # never reached.
    monkeypatch.setattr(
        mcp_server, "get_provider", lambda capability, config: ScriptedProvider({"axes": []})
    )
    result = mcp_server.my_values(refresh=True)
    assert "values failed" in result
    assert "not enough captured evidence" in result
