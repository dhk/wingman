"""Value-dimension inference (v2 of issue #240, docs/RFC.md RFC-051):
the minimum-data floor, model-proposes/code-disposes axis validation, the
deterministic score/evidence contract, staleness, and the CLI+MCP surface.

Includes the #340/RFC-056 regressions: the score's SIGN comes from the
model's per-item "supports"/"opposes" direction, not from the item's
`_pro`/`_con` subtype suffix.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from wingman.agents.profile_curator import ProposalParseError
from wingman.agents.values_analyst import PROMPT_VERSION, WORK_PROMPT_VERSION
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
    parse_value_view,
    render_value_profile,
    work_grounding_note,
)
from wingman.domain.profile import SentimentIntensity
from wingman.domain.values import AxisDirection, ValueProfile, ValueView
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
        assert "Value profile (character view): Your corpus" in rendered
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


# --- the code half of staleness (#355) ----------------------------------------


def test_a_built_profile_records_the_scoring_rule_that_produced_it(workspace: Path) -> None:
    """`prompt_version` versions the MODEL half. The deterministic half had
    none, and #340 was a change to exactly that half — so every profile and
    every chart built before the fix asserted the opposite of the truth with
    nothing on it to say which rule had produced it. AGENTS.md already
    required this of anything that scores."""
    from wingman.domain.values import SCORING_CONTRACT_VERSION

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

    assert report.profile.scoring_version == SCORING_CONTRACT_VERSION
    # And it survives the round trip, or the stamp answers nothing later.
    with Storage(config.db_path) as storage:
        stored = storage.get_value_profile(report.profile.subject_id)
    assert stored is not None
    assert stored.scoring_version == SCORING_CONTRACT_VERSION
    assert SCORING_CONTRACT_VERSION in render_value_profile(stored)


def test_a_profile_scored_under_a_replaced_rule_says_so_on_read(workspace: Path) -> None:
    """The general case RFC-056's warning could not cover: directions were
    recorded, the evidence looks fine, and the numbers still came from a
    rule this codebase no longer runs."""
    from wingman.application.values import scoring_is_current
    from wingman.domain.values import (
        SCORING_CONTRACT_VERSION,
        ValueAxis,
        ValueAxisEvidence,
        ValueProfile,
    )

    superseded = ValueProfile(
        subject_id=CORPUS_PERSON_ID,
        subject_name=CORPUS_PERSON_NAME,
        items_used=1,
        source_item_ids=["seed-item"],
        provider="scripted",
        model="scripted-1",
        prompt_version="value_axes_v3",
        scoring_version="values-scoring-0",
        axes=[
            ValueAxis(
                name="Honesty and the right to informed choice",
                score=1.0,
                label="strongly drawn to",
                evidence=[
                    ValueAxisEvidence(
                        item_id="seed-item",
                        subtype="values_con",
                        target="A liar",
                        quote="They lied to people who could not check.",
                        intensity="strong",
                        direction="supports",
                        signed_weight=1.0,
                    )
                ],
            )
        ],
    )
    rendered = render_value_profile(superseded)

    assert not scoring_is_current(superseded)
    assert "values-scoring-0" in rendered
    assert SCORING_CONTRACT_VERSION in rendered
    assert "wingman values --refresh" in rendered
    # ...and only ONE warning: two overlapping ones train the reader to skip
    # both, so the specific #340 diagnosis is reserved for the profiles it
    # actually describes.
    assert "may have the wrong sign" not in rendered


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


def test_a_null_direction_costs_that_citation_not_the_whole_proposal(
    workspace: Path,
) -> None:
    """ProposedAxisCitation.direction is deliberately untyped so a malformed
    value is DROPPED by _direction. Annotating it `str` broke that before it
    could happen: pydantic rejected `"direction": null` during
    model_validate, so one bad citation took every other axis down with it
    and surfaced as ProposalParseError (#341 review)."""
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        ids = [item.item_id for item in storage.list_profile_items() if item.subtype]
        provider = ScriptedProvider(
            {
                "axes": [
                    {
                        "name": "Has a null citation",
                        "items": [
                            {"item_id": ids[0], "direction": None},
                            {"item_id": ids[1], "direction": "supports"},
                        ],
                    },
                    {"name": "filler two", "items": _cite(ids[:1])},
                    {"name": "filler three", "items": _cite(ids[:1])},
                ]
            }
        )

        report = build_value_profile(storage, provider)

        axis = report.profile.axes[0]
        assert axis.name == "Has a null citation"
        assert [span.item_id for span in axis.evidence] == [ids[1]]
        assert len(report.profile.axes) == 3  # the other axes survived


def test_an_unusable_citation_does_not_suppress_a_later_valid_one(
    workspace: Path,
) -> None:
    """Marking the item id 'seen' before validating it let a duplicate
    invalid citation poison a valid one for the same item — [no direction,
    supports] dropped BOTH, and could reject the axis for citing nothing
    usable (#341 review)."""
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        ids = [item.item_id for item in storage.list_profile_items() if item.subtype]
        provider = ScriptedProvider(
            {
                "axes": [
                    {
                        "name": "Cited twice, badly then well",
                        "items": [
                            {"item_id": ids[0], "direction": "sideways"},
                            {"item_id": ids[0], "direction": "supports"},
                        ],
                    },
                    {"name": "filler two", "items": _cite(ids[:1])},
                    {"name": "filler three", "items": _cite(ids[:1])},
                ]
            }
        )

        report = build_value_profile(storage, provider)

        axis = next(a for a in report.profile.axes if a.name == "Cited twice, badly then well")
        assert [span.item_id for span in axis.evidence] == [ids[0]]
        assert axis.evidence[0].direction is AxisDirection.SUPPORTS


# --- the direction cross-check (#342) ---------------------------------------


def test_an_opposing_citation_is_flagged_for_a_human_to_check(workspace: Path) -> None:
    """The capture knows which way it points about its TARGET; the model
    decides which way it points about the axis. Where those disagree is
    where #340's inversion would show itself, so it is surfaced — as a
    note, never as a rejection."""
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        ids = [item.item_id for item in storage.list_profile_items() if item.subtype]
        provider = ScriptedProvider(
            {
                "axes": [
                    {"name": "Winning at any cost", "items": _cite(ids, direction="opposes")},
                    {"name": "filler two", "items": _cite(ids[:1])},
                    {"name": "filler three", "items": _cite(ids[:1])},
                ]
            }
        )
        report = build_value_profile(storage, provider)

    rendered = render_value_profile(report.profile)

    assert "worth checking" in rendered
    assert f"{len(ids)} captures cited as OPPOSING" in rendered
    # ...and it is a note, not a refusal: the axis is still there and scored.
    assert "Winning at any cost" in rendered
    assert report.profile.axes[0].score < 0


def test_an_axis_everything_supports_says_nothing(workspace: Path) -> None:
    """No noise on the ordinary case — a value-shaped axis where both the
    admiration and the condemnation argue for it."""
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        ids = [item.item_id for item in storage.list_profile_items() if item.subtype]
        provider = ScriptedProvider(
            {
                "axes": [
                    {"name": "Honesty", "items": _cite(ids, direction="supports")},
                    {"name": "filler two", "items": _cite(ids[:1])},
                    {"name": "filler three", "items": _cite(ids[:1])},
                ]
            }
        )
        report = build_value_profile(storage, provider)

    assert "worth checking" not in render_value_profile(report.profile)


# --- The value statement reaching inference (RFC-057, issue #343) -----------


def _capture_with_value_statement(storage: Storage, config: Config) -> str:
    """One more con capture, this one carrying the person's own answer to
    'what does that tell us you value?' — returns its item_id."""
    capture_interview_reaction(
        "values_con",
        "David Duncan",
        "He shredded the documents that would have told people the truth.",
        config,
        storage,
        intensity="strong",
        value_statement="I value people having the information they need to choose.",
    )
    return next(
        item.item_id
        for item in storage.list_profile_items()
        if item.name == "values_con: David Duncan"
    )


def test_the_prompt_carries_a_captures_value_statement_alongside_its_quote(
    workspace: Path,
) -> None:
    """#343's whole point: the model should read a statement that is
    POSITIVE BY CONSTRUCTION, not infer the value from a verdict about
    somebody the person condemned. If the statement never reaches the
    prompt, capturing it changes nothing."""
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        _capture_with_value_statement(storage, config)
        ids = _all_item_ids(storage)
        provider = ScriptedProvider(
            {
                "axes": [
                    {"name": "One", "items": _cite(ids)},
                    {"name": "Two", "items": _cite(ids[:1])},
                    {"name": "Three", "items": _cite(ids[:1])},
                ]
            }
        )
        build_value_profile(storage, provider)

    assert provider.last_prompt is not None
    assert "I value people having the information they need to choose." in provider.last_prompt
    # and the prompt tells the model what that line is and how to use it
    assert "what this tells them they value" in provider.last_prompt


def test_a_capture_without_a_value_statement_carries_no_empty_label(workspace: Path) -> None:
    """Captures predating the question must keep working unchanged — and a
    labelled blank ('value: not given') is an invitation to fill it in,
    which is the model judgment #343 exists to remove."""
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        ids = _all_item_ids(storage)
        provider = ScriptedProvider(
            {
                "axes": [
                    {"name": "One", "items": _cite(ids)},
                    {"name": "Two", "items": _cite(ids[:1])},
                    {"name": "Three", "items": _cite(ids[:1])},
                ]
            }
        )
        report = build_value_profile(storage, provider)

    assert provider.last_prompt is not None
    assert "what this tells them they value:" not in provider.last_prompt
    # direction is untouched — RFC-056 still supplies every sign (#343
    # augments it; it does not replace it)
    assert all(
        span.direction is AxisDirection.SUPPORTS
        for axis in report.profile.axes
        for span in axis.evidence
    )


def test_a_cited_captures_value_statement_is_shown_with_its_evidence(workspace: Path) -> None:
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        item_id = _capture_with_value_statement(storage, config)
        ids = _all_item_ids(storage)
        provider = ScriptedProvider(
            {
                "axes": [
                    {"name": "Honesty", "items": _cite(ids)},
                    {"name": "Two", "items": _cite([item_id])},
                    {"name": "Three", "items": _cite(ids[:1])},
                ]
            }
        )
        report = build_value_profile(storage, provider)

    cited = {span.item_id: span for axis in report.profile.axes for span in axis.evidence}
    assert (
        cited[item_id].value_statement
        == "I value people having the information they need to choose."
    )
    rendered = render_value_profile(report.profile)
    assert 'values: "I value people having the information they need to choose."' in rendered


# --- the work view: the same captures, read as ways of working (#356) --------


def _seed_reactions(storage: Storage, config: Config, count: int = 3) -> list[str]:
    """Perspective reactions — the captures that are about IDEAS rather than
    about people, and the ones the character view cannot see at all."""
    for index in range(count):
        subtype = (
            "alignment_of_perspective_agree"
            if index % 2 == 0
            else "alignment_of_perspective_disagree"
        )
        capture_interview_reaction(
            subtype,
            f"https://example.com/essay-{index}",
            f"Shipping without a way to check it is how teams lose trust ({index}).",
            config,
            storage,
            fetcher=lambda url: b"<html><title>An Essay</title><body>Argument.</body></html>",
        )
    ids = [
        item.item_id
        for item in storage.list_profile_items()
        if (item.subtype or "").startswith("alignment_of_perspective")
    ]
    assert len(ids) == count
    return ids


def test_the_work_view_reads_perspective_reactions_the_character_view_cannot(
    workspace: Path,
) -> None:
    """The evidence question #356 asks. Reactions are responses to ideas,
    not verdicts about people, so they are the better raw material for an
    axis about how somebody works — and the character view, which asks a
    question about people, must not start reading them.
    """
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        reaction_ids = _seed_reactions(storage, config)

        character = ScriptedProvider(
            {
                "axes": [
                    {"name": f"C{i}", "items": _cite(_all_item_ids(storage)[:1])} for i in range(3)
                ]
            }
        )
        build_value_profile(storage, character, view=ValueView.CHARACTER)
        assert character.last_prompt is not None
        for reaction_id in reaction_ids:
            assert reaction_id not in character.last_prompt

        work = ScriptedProvider(
            {"axes": [{"name": f"W{i}", "items": _cite(reaction_ids)} for i in range(3)]}
        )
        report = build_value_profile(storage, work, view=ValueView.WORK)
        assert work.last_prompt is not None
        for reaction_id in reaction_ids:
            assert reaction_id in work.last_prompt
        cited = {span.item_id for axis in report.profile.axes for span in axis.evidence}
        assert cited == set(reaction_ids)


def test_the_work_view_is_named_by_its_own_prompt_and_stamps_that_version(
    workspace: Path,
) -> None:
    """Two views, one pipeline: what differs is the naming instruction the
    model is given, and `prompt_version` has to name the prompt that
    actually produced the stored profile — otherwise provenance points at a
    document that asked a different question."""
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        ids = _all_item_ids(storage)
        provider = ScriptedProvider(
            {"axes": [{"name": f"W{i}", "items": _cite(ids[:1])} for i in range(3)]}
        )
        report = build_value_profile(storage, provider, view=ValueView.WORK)

    assert report.profile.view is ValueView.WORK
    assert report.profile.prompt_version == WORK_PROMPT_VERSION != PROMPT_VERSION
    assert provider.last_prompt is not None
    # The register instruction is the whole difference, so assert on it
    # rather than on the file name: a work prompt that stopped saying this
    # would produce character axes under a work heading.
    assert "how this person wants to work" in provider.last_prompt
    assert "Name it as a way of working, not as a virtue." in provider.last_prompt


def test_both_views_are_stored_side_by_side_and_neither_replaces_the_other(
    workspace: Path,
) -> None:
    """The storage decision. Keyed on subject_id alone — as it was — the
    second view's rebuild REPLACES the first, so a workspace that has both
    silently has one."""
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        ids = _all_item_ids(storage)
        build_value_profile(
            storage,
            ScriptedProvider(
                {"axes": [{"name": f"Character {i}", "items": _cite(ids[:1])} for i in range(3)]}
            ),
        )
        build_value_profile(
            storage,
            ScriptedProvider(
                {"axes": [{"name": f"Work {i}", "items": _cite(ids[:1])} for i in range(3)]}
            ),
            view=ValueView.WORK,
        )

        character = storage.get_value_profile(CORPUS_PERSON_ID)
        work = storage.get_value_profile(CORPUS_PERSON_ID, ValueView.WORK)
        assert character is not None and work is not None
        assert [axis.name for axis in character.axes] == [
            "Character 0",
            "Character 1",
            "Character 2",
        ]
        assert [axis.name for axis in work.axes] == ["Work 0", "Work 1", "Work 2"]
        assert character.profile_id != work.profile_id


def test_a_work_profile_built_only_from_nominations_says_so(workspace: Path) -> None:
    """The work view's characteristic failure is not thin evidence, it is a
    leap of REGISTER: work axes read entirely off verdicts about people.
    Disclosed rather than refused (see `work_grounding_note`) — but it must
    be disclosed, or a fit brief cites axes whose grounding nobody can see.
    """
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        ids = _all_item_ids(storage)
        report = build_value_profile(
            storage,
            ScriptedProvider(
                {"axes": [{"name": f"W{i}", "items": _cite(ids[:1])} for i in range(3)]}
            ),
            view=ValueView.WORK,
        )

    note = work_grounding_note(report.profile)
    assert "read off nominations about PEOPLE" in note
    assert "alignment_of_perspective" in note
    assert note in render_value_profile(report.profile)


def test_a_work_profile_grounded_in_reactions_carries_no_such_note(workspace: Path) -> None:
    """The control. A caveat that never clears is a caveat people learn to
    scroll past."""
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        reaction_ids = _seed_reactions(storage, config)
        report = build_value_profile(
            storage,
            ScriptedProvider(
                {"axes": [{"name": f"W{i}", "items": _cite(reaction_ids)} for i in range(3)]}
            ),
            view=ValueView.WORK,
        )

    assert work_grounding_note(report.profile) == ""
    assert "read off nominations about PEOPLE" not in render_value_profile(report.profile)


def test_the_character_view_never_carries_the_work_caveat(workspace: Path) -> None:
    """It is not a claim about the character view, which is not making a
    work claim at all."""
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        ids = _all_item_ids(storage)
        report = build_value_profile(
            storage,
            ScriptedProvider(
                {"axes": [{"name": f"C{i}", "items": _cite(ids[:1])} for i in range(3)]}
            ),
        )
    assert work_grounding_note(report.profile) == ""


def test_staleness_is_answered_against_the_profiles_own_view(workspace: Path) -> None:
    """A new REACTION is new evidence for the work profile and no evidence
    at all for the character one. Reading eligibility off the stored
    profile's view rather than from a caller is what keeps the two answers
    from being swapped."""
    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        ids = _all_item_ids(storage)
        proposal = {"axes": [{"name": f"A{i}", "items": _cite(ids[:1])} for i in range(3)]}
        character = build_value_profile(storage, ScriptedProvider(proposal)).profile
        work = build_value_profile(storage, ScriptedProvider(proposal), view=ValueView.WORK).profile

        _seed_reactions(storage, config, count=1)

        assert new_captures_since(storage, character) == 0
        assert new_captures_since(storage, work) == 1


def test_a_view_typo_refuses_instead_of_building_the_other_reading(workspace: Path) -> None:
    """Silently falling back to the character view is how somebody quotes
    character axes at a hiring conversation believing they are work ones."""
    with pytest.raises(IngestError, match="unknown value view"):
        parse_value_view("wrok")
    assert parse_value_view("") is ValueView.CHARACTER
    assert parse_value_view(" WORK ") is ValueView.WORK


def test_the_work_views_refusal_names_the_evidence_that_would_help(workspace: Path) -> None:
    """Below the floor, the character view's advice ('nominate more people')
    is the wrong work to send somebody off to do for a work profile."""
    config = load_config()
    with Storage(config.db_path) as storage:
        capture_interview_reaction(
            "values_pro", "Jane Goodall", "Steadfast dedication.", config, storage
        )
        with pytest.raises(IngestError, match="alignment_of_perspective") as excinfo:
            build_value_profile(storage, ScriptedProvider({"axes": []}), view=ValueView.WORK)
        assert "work dimensions" in str(excinfo.value)


def test_a_pre_356_database_migrates_without_losing_its_profile(tmp_path: Path) -> None:
    """An existing workspace's `value_profiles` table declares
    `subject_id TEXT NOT NULL UNIQUE`, which SQLite cannot un-declare. Left
    alone, the first work-view rebuild would hit that constraint and REPLACE
    the character profile — two readings of the same captures deleting each
    other, in a table whose whole lifecycle is "one current artefact".

    Builds the old schema by hand rather than checking in a fixture
    database: the point is the exact constraint that existed, and a
    hand-written CREATE says which one that was.
    """
    db_path = tmp_path / "old.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE value_profiles (
            profile_id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL UNIQUE,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    stored = ValueProfile(
        subject_id=CORPUS_PERSON_ID,
        subject_name=CORPUS_PERSON_NAME,
        axes=[],
        items_used=6,
        provider="scripted",
        model="scripted-1",
        prompt_version=PROMPT_VERSION,
    )
    connection.execute(
        "INSERT INTO value_profiles VALUES (?, ?, ?, ?)",
        (
            stored.profile_id,
            stored.subject_id,
            stored.model_dump_json(),
            stored.generated_at.isoformat(),
        ),
    )
    connection.commit()
    connection.close()

    with Storage(db_path) as storage:
        migrated = storage.get_value_profile(CORPUS_PERSON_ID)
        assert migrated is not None
        assert migrated.profile_id == stored.profile_id
        # A row written before the field existed IS the character view —
        # the only reading there was.
        assert migrated.view is ValueView.CHARACTER
        assert storage.get_value_profile(CORPUS_PERSON_ID, ValueView.WORK) is None

        work = stored.model_copy(update={"profile_id": "work-profile", "view": ValueView.WORK})
        storage.save_value_profile(work)
        assert storage.get_value_profile(CORPUS_PERSON_ID) is not None
        assert storage.get_value_profile(CORPUS_PERSON_ID).profile_id == stored.profile_id


def test_cli_values_view_work_builds_and_stores_the_work_reading(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The surface a person actually reaches for. Without --view there is no
    way to ask for the reading a fit brief can cite."""
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
                {"name": "Verification before shipping", "items": _cite(item_ids)},
                {"name": "Two", "items": _cite(item_ids[:1])},
                {"name": "Three", "items": _cite(item_ids[:1])},
            ]
        }
    )
    monkeypatch.setattr(cli_main, "get_provider", lambda capability, config: provider)
    monkeypatch.setenv(ENV_DATA_DIR, str(config.data_dir))
    runner = CliRunner()
    result = runner.invoke(cli_main.app, ["values", "--refresh", "--view", "work"])
    assert result.exit_code == 0, result.output
    assert "Work profile (how you want to work)" in result.output

    with Storage(config.db_path) as storage:
        assert storage.get_value_profile(CORPUS_PERSON_ID) is None
        stored = storage.get_value_profile(CORPUS_PERSON_ID, ValueView.WORK)
        assert stored is not None
        assert stored.view is ValueView.WORK

    bad = runner.invoke(cli_main.app, ["values", "--view", "wrok"])
    assert bad.exit_code == 1
    assert "unknown value view" in bad.output


def test_mcp_my_values_view_work_returns_the_work_reading(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wingman.mcp_server import my_values

    config = load_config()
    with Storage(config.db_path) as storage:
        _seed_min_floor(storage, config)
        item_ids = _all_item_ids(storage)
        build_value_profile(
            storage,
            ScriptedProvider(
                {
                    "axes": [
                        {"name": "Verification before shipping", "items": _cite(item_ids)},
                        {"name": "Two", "items": _cite(item_ids[:1])},
                        {"name": "Three", "items": _cite(item_ids[:1])},
                    ]
                }
            ),
            view=ValueView.WORK,
        )

    out = my_values(view="work")
    assert "Work profile (how you want to work)" in out
    assert "Verification before shipping" in out
    assert "unknown value view" in my_values(view="wrok")
