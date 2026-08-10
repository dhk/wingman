"""Value-dimension inference (v2 of issue #240, docs/RFC.md RFC-051):
the minimum-data floor, model-proposes/code-disposes axis validation, the
deterministic score/evidence contract, staleness, and the CLI+MCP surface.

Includes the #340/RFC-056 regressions: the score's SIGN comes from the
model's per-item "supports"/"opposes" direction, not from the item's
`_pro`/`_con` subtype suffix.
"""

import json
from pathlib import Path

import pytest

from wingman.agents.profile_curator import ProposalParseError
from wingman.agents.values_analyst import PROMPT_VERSION
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
from wingman.domain.profile import SentimentIntensity
from wingman.domain.values import AxisDirection
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


def _cite(item_ids: list[str], direction: str = "supports") -> list[dict[str, str]]:
    """The proposal shape the model must emit (RFC-056): every cited item
    carries its own direction relative to the axis as named."""
    return [{"item_id": item_id, "direction": direction} for item_id in item_ids]


def _cite_by_subtype(storage: Storage) -> list[dict[str, str]]:
    """Every seeded item, with the direction the OLD sign-from-subtype rule
    implied — pro supports, con opposes. Used where a test cares about the
    averaging arithmetic rather than the sign rule."""
    citations = []
    for item in storage.list_profile_items():
        direction = "supports" if (item.subtype or "").endswith("_pro") else "opposes"
        citations.append({"item_id": item.item_id, "direction": direction})
    return citations


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
                        "items": _cite_by_subtype(storage),
                    },
                    {"name": "filler two", "items": _cite([item_ids[0]])},
                    {"name": "filler three", "items": _cite([item_ids[0]])},
                ]
            }
        )
        report = build_value_profile(storage, provider)
        assert provider.last_prompt is not None
        axis = report.profile.axes[0]
        assert axis.name == "Steadfastness"
        # 3 supporting (mild=1/3, moderate=2/3, strong=1.0) + 3 opposing
        # (negated) sums to exactly zero — the model never sees or proposes
        # this number, only the per-item directions it is averaged from.
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
                    {"name": "Devotion", "items": _cite(pro_ids)},
                    {"name": "filler two", "items": _cite(pro_ids[:1])},
                    {"name": "filler three", "items": _cite(pro_ids[:1])},
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
                    {"name": "Steadfastness", "items": _cite(item_ids)},
                    {"name": "filler two", "items": _cite([item_ids[0]])},
                    {"name": "filler three", "items": _cite([item_ids[0]])},
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
        assert span.direction is AxisDirection.SUPPORTS
        targets = {s.target for s in report.profile.axes[0].evidence}
        assert "Jane Goodall" in targets


# --- the sign comes from direction, not the subtype (#340, RFC-056) ---------


def test_con_evidence_supporting_the_axis_scores_strongly_positive(workspace: Path) -> None:
    """The #340 regression, exactly as it was reported: an axis named for a
    VALUE ("Honesty and the right to informed choice") and evidenced only by
    condemnations of people who violated it. Under the old sign-from-subtype
    rule this scored -1.00 "strongly repelled by" — the tool telling its user
    they were repelled by honesty."""
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        con_ids = [
            item.item_id for item in storage.list_profile_items() if item.subtype == "values_con"
        ]
        strong_con_id = next(
            item.item_id
            for item in storage.list_profile_items()
            if item.subtype == "values_con" and item.intensity is SentimentIntensity.STRONG
        )
        provider = ScriptedProvider(
            {
                "axes": [
                    {
                        "name": "Honesty and the right to informed choice",
                        "description": "People are owed the truth they need to choose.",
                        "items": _cite([strong_con_id]),
                    },
                    {"name": "Accountability for wrongdoing", "items": _cite(con_ids)},
                    {"name": "filler three", "items": _cite(con_ids[:1])},
                ]
            }
        )
        report = build_value_profile(storage, provider)
        honesty = report.profile.axes[0]
        assert honesty.score == pytest.approx(1.0)
        assert honesty.label == "strongly drawn to"
        accountability = report.profile.axes[1]
        # (1/3 + 2/3 + 1.0) / 3 == 2/3, positive — three condemnations, all
        # of them evidence FOR the value they name.
        assert accountability.score == pytest.approx(2 / 3)
        assert accountability.label == "strongly drawn to"


def test_admiration_and_condemnation_of_the_same_value_reinforce(workspace: Path) -> None:
    """The Francis/Ratzinger shape from #340: one strong pro nomination
    (exemplified moral courage) and one strong con nomination (its absence
    disgusted the user) on the SAME axis. Both cut the same way, so they must
    reinforce; the old rule cancelled them to exactly 0.00 "mixed /
    ambivalent" and reported unanimous evidence as ambivalence."""
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        strong_pro_id = next(
            item.item_id
            for item in storage.list_profile_items()
            if item.subtype == "values_pro" and item.intensity is SentimentIntensity.STRONG
        )
        strong_con_id = next(
            item.item_id
            for item in storage.list_profile_items()
            if item.subtype == "values_con" and item.intensity is SentimentIntensity.STRONG
        )
        provider = ScriptedProvider(
            {
                "axes": [
                    {
                        "name": "Moral courage in defense of the vulnerable",
                        "items": _cite([strong_pro_id, strong_con_id]),
                    },
                    {"name": "filler two", "items": _cite([strong_pro_id])},
                    {"name": "filler three", "items": _cite([strong_con_id])},
                ]
            }
        )
        report = build_value_profile(storage, provider)
        axis = report.profile.axes[0]
        assert axis.score == pytest.approx(1.0)
        assert axis.label == "strongly drawn to"
        assert [span.signed_weight for span in axis.evidence] == [1.0, 1.0]


def test_a_pro_item_that_opposes_the_axis_scores_negative(workspace: Path) -> None:
    """The other half of the rule, and why "cons always support" would have
    been the wrong fix: the model can name an axis the person's ADMIRATION
    argues against, and then a pro nomination is negative evidence."""
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        pro_ids = [
            item.item_id for item in storage.list_profile_items() if item.subtype == "values_pro"
        ]
        provider = ScriptedProvider(
            {
                "axes": [
                    {
                        "name": "Winning at any cost",
                        "items": _cite(pro_ids, direction="opposes"),
                    },
                    {"name": "filler two", "items": _cite(pro_ids[:1])},
                    {"name": "filler three", "items": _cite(pro_ids[:1])},
                ]
            }
        )
        report = build_value_profile(storage, provider)
        axis = report.profile.axes[0]
        assert axis.score == pytest.approx(-2 / 3)
        assert axis.label == "strongly repelled by"
        assert all(span.direction is AxisDirection.OPPOSES for span in axis.evidence)


def test_missing_or_unparseable_direction_drops_the_citation(workspace: Path) -> None:
    """Same discipline as an unknown item_id: an unusable citation is
    dropped, never defaulted to a sign — a defaulted sign is the bug."""
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        item_ids = _all_item_ids(storage)
        provider = ScriptedProvider(
            {
                "axes": [
                    {
                        "name": "Partially directed",
                        "items": [
                            {"item_id": item_ids[0], "direction": "supports"},
                            {"item_id": item_ids[1]},  # no direction at all
                            {"item_id": item_ids[2], "direction": "neutral"},
                            {"item_id": item_ids[3], "direction": ""},
                        ],
                    },
                    {
                        "name": "Wholly undirected",
                        "items": [{"item_id": item_ids[4], "direction": "unsure"}],
                    },
                    {"name": "filler three", "items": _cite([item_ids[0]])},
                    {"name": "filler four", "items": _cite([item_ids[0]])},
                ]
            }
        )
        report = build_value_profile(storage, provider)
        names = {axis.name for axis in report.profile.axes}
        assert "Wholly undirected" not in names
        surviving = next(a for a in report.profile.axes if a.name == "Partially directed")
        assert [span.item_id for span in surviving.evidence] == [item_ids[0]]
        rejected_reasons = {r.name: r.reason for r in report.rejected}
        assert "usable direction" in rejected_reasons["Wholly undirected"]


def test_direction_is_case_and_whitespace_tolerant(workspace: Path) -> None:
    """The direction is a model-written string in a JSON field; " Supports "
    is the same judgment as "supports" and rejecting it would drop real
    evidence over presentation. Anything that isn't one of the two words is
    still dropped."""
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        item_ids = _all_item_ids(storage)
        provider = ScriptedProvider(
            {
                "axes": [
                    {"name": "One", "items": _cite(item_ids, direction=" Supports ")},
                    {"name": "Two", "items": _cite(item_ids[:1], direction="OPPOSES")},
                    {"name": "Three", "items": _cite(item_ids[:1])},
                ]
            }
        )
        report = build_value_profile(storage, provider)
        assert report.profile.axes[0].score > 0
        assert report.profile.axes[1].score < 0


def test_prompt_asks_for_a_direction_per_cited_item(workspace: Path) -> None:
    """The contract is only as good as what the model is told — the score's
    sign now comes from a field the prompt has to ask for."""
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        item_ids = _all_item_ids(storage)
        provider = ScriptedProvider(
            {
                "axes": [
                    {"name": "One", "items": _cite(item_ids)},
                    {"name": "Two", "items": _cite(item_ids[:1])},
                    {"name": "Three", "items": _cite(item_ids[:1])},
                ]
            }
        )
        report = build_value_profile(storage, provider)
        assert provider.last_prompt is not None
        assert '"direction"' in provider.last_prompt
        assert "supports" in provider.last_prompt
        assert "opposes" in provider.last_prompt
        assert report.profile.prompt_version == PROMPT_VERSION


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
                        "items": _cite([item_ids[0], "not-a-real-item"]),
                    },
                    {"name": "Wholly fabricated", "items": _cite(["also-not-real"])},
                    {"name": "filler three", "items": _cite([item_ids[0]])},
                    {"name": "filler four", "items": _cite([item_ids[0]])},
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
                    {"name": "", "items": _cite(item_ids)},
                    {"name": "Real axis one", "items": _cite(item_ids)},
                    {"name": "Real axis two", "items": _cite(item_ids)},
                    {"name": "Real axis three", "items": _cite(item_ids)},
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
                    {"name": f"Axis {index}", "items": _cite(item_ids)}
                    for index in range(MAX_AXES + 2)
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
        provider = ScriptedProvider({"axes": [{"name": "Only one", "items": _cite(item_ids)}]})
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
                    {"name": "One", "items": _cite(persona_item_ids)},
                    {"name": "Two", "items": _cite(persona_item_ids)},
                    {"name": "Three", "items": _cite(persona_item_ids)},
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
                    {"name": "One", "items": _cite(item_ids)},
                    {"name": "Two", "items": _cite(item_ids)},
                    {"name": "Three", "items": _cite(item_ids)},
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
                        "items": _cite(item_ids),
                    },
                    {"name": "Two", "items": _cite(item_ids[:1])},
                    {"name": "Three", "items": _cite(item_ids[:1])},
                ]
            }
        )
        report = build_value_profile(storage, provider)
        rendered = render_value_profile(report.profile)
        assert "Value profile: Your corpus" in rendered
        assert "Steadfastness" in rendered
        assert "Sticks with one cause." in rendered
        assert "Jane Goodall" in rendered
        # Direction is part of the score's provenance, so it is shown with
        # the evidence, not left implicit behind the number.
        assert "supports this axis" in rendered
        assert "may have the wrong sign" not in rendered


def test_render_warns_that_a_pre_direction_profile_may_have_the_wrong_sign(
    workspace: Path,
) -> None:
    """A profile stored before RFC-056 is read back as-is (nothing forces a
    rebuild), and its scores came from the inverted rule. Showing those
    numbers with no caveat is how #340 read as authoritative in the first
    place."""
    from wingman.domain.values import ValueAxis, ValueAxisEvidence, ValueProfile

    legacy = ValueProfile(
        subject_id=CORPUS_PERSON_ID,
        subject_name=CORPUS_PERSON_NAME,
        items_used=1,
        source_item_ids=["seed-item"],
        provider="scripted",
        model="scripted-1",
        prompt_version="value_axes_v1",
        axes=[
            ValueAxis(
                name="Honesty and the right to informed choice",
                score=-1.0,
                label="strongly repelled by",
                evidence=[
                    ValueAxisEvidence(
                        item_id="seed-item",
                        subtype="values_con",
                        target="A liar",
                        quote="They lied to people who could not check.",
                        intensity="strong",
                        signed_weight=-1.0,
                    )
                ],
            )
        ],
    )
    rendered = render_value_profile(legacy)
    assert "may have the wrong sign" in rendered
    assert "wingman values --refresh" in rendered


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
                                direction="supports",
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
                {"name": "Steadfastness", "items": _cite(item_ids)},
                {"name": "Two", "items": _cite(item_ids[:1])},
                {"name": "Three", "items": _cite(item_ids[:1])},
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
                            direction="supports",
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
                {"name": "Steadfastness", "items": _cite(item_ids)},
                {"name": "Two", "items": _cite(item_ids[:1])},
                {"name": "Three", "items": _cite(item_ids[:1])},
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
