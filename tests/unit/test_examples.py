"""Good and bad examples (#438).

The corpus half only: nothing here should feed into generation, and one
test pins that this stays a store rather than quietly becoming a prompt.
What matters most is the required reason — the single piece of friction
the feature deliberately keeps.
"""

from pathlib import Path

import pytest

from wingman.application.examples import (
    example_kinds,
    find_example,
    list_examples,
    remove_example,
    render_example,
    render_examples,
    render_kinds,
    save_example,
)
from wingman.application.ingest import IngestError
from wingman.domain.examples import ExampleAuthor, ExampleVerdict
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    config = load_config()
    config.data_dir.mkdir(parents=True)
    return config


def _save(storage: Storage, **kwargs: str):
    defaults = {
        "text": "Dear hiring manager, ...",
        "verdict": "good",
        "kind": "cover letter",
        "reason": "concrete, no over-claiming",
    }
    return save_example(storage=storage, **{**defaults, **kwargs})


def test_a_saved_example_keeps_verdict_kind_reason_and_the_document(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        saved = _save(storage)
        assert saved.verdict is ExampleVerdict.GOOD
        assert saved.kind == "cover letter"
        assert saved.reason == "concrete, no over-claiming"
        assert saved.text == "Dear hiring manager, ..."
        assert saved.author is ExampleAuthor.WINGMAN
        assert list_examples(storage) == [saved]


def test_a_reason_is_required_on_good_and_on_bad(workspace: Config) -> None:
    """The one bit of friction the feature keeps on purpose: a bare verdict
    teaches nothing later and cannot be reviewed by anyone else."""
    with Storage(workspace.db_path) as storage:
        for verdict in ("good", "bad"):
            with pytest.raises(IngestError) as exc:
                _save(storage, verdict=verdict, reason="   ")
            assert "reason" in str(exc.value)
        assert list_examples(storage) == []


def test_an_empty_document_or_kind_is_refused(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        with pytest.raises(IngestError, match="nothing to save"):
            _save(storage, text="  ")
        with pytest.raises(IngestError, match="kind"):
            _save(storage, kind="")


def test_an_unknown_verdict_names_the_valid_ones(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        with pytest.raises(IngestError) as exc:
            _save(storage, verdict="mediocre")
        assert "good" in str(exc.value) and "bad" in str(exc.value)


def test_the_users_own_text_can_be_saved_and_stays_distinguishable(workspace: Config) -> None:
    """ "wingman wrote this and it was bad" and "someone else wrote this and
    it was good" are different lessons."""
    with Storage(workspace.db_path) as storage:
        mine = _save(storage, author="wingman")
        theirs = _save(storage, author="user", text="Someone else's letter", reason="great opening")
        assert mine.author is ExampleAuthor.WINGMAN
        assert theirs.author is ExampleAuthor.USER


def test_kinds_are_normalised_so_one_vocabulary_forms(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        a = _save(storage, kind="  Cover   Letter ")
        b = _save(storage, kind="cover letter", reason="also good")
        assert a.kind == b.kind == "cover letter"
        assert example_kinds(storage) == [("cover letter", 2)]


def test_filtering_by_kind_and_verdict(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        _save(storage, kind="briefing", verdict="bad", reason="buried the ask")
        _save(storage, kind="briefing", verdict="good", reason="led with the ask")
        _save(storage, kind="cover letter", verdict="bad", reason="over-claims seniority")

        assert len(list_examples(storage, kind="briefing")) == 2
        assert len(list_examples(storage, verdict="bad")) == 2
        assert len(list_examples(storage, kind="briefing", verdict="bad")) == 1
        # An empty filter means no filter, not "match empty".
        assert len(list_examples(storage)) == 3


def test_free_text_search_covers_document_reason_and_kind(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        _save(storage, text="I led the platform rebuild", reason="specific")
        _save(storage, text="Generic waffle", reason="over-claims seniority", verdict="bad")
        assert len(list_examples(storage, contains="platform")) == 1
        assert len(list_examples(storage, contains="SENIORITY")) == 1
        assert len(list_examples(storage, contains="nothing here")) == 0


def test_newest_first(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        _save(storage, text="first", reason="a")
        second = _save(storage, text="second", reason="b")
        assert list_examples(storage)[0].example_id == second.example_id


def test_find_and_remove_by_id_prefix(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        saved = _save(storage)
        assert find_example(saved.example_id[:8], storage).example_id == saved.example_id
        removed = remove_example(saved.example_id[:8], storage)
        assert removed.example_id == saved.example_id
        assert list_examples(storage) == []


def test_an_unknown_or_empty_id_says_so(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        _save(storage)
        with pytest.raises(IngestError, match="which example"):
            find_example("   ", storage)
        with pytest.raises(IngestError, match="no example"):
            find_example("zzzzzzzz", storage)


def test_listing_previews_but_show_gives_the_whole_document(workspace: Config) -> None:
    long_text = "word " * 200
    with Storage(workspace.db_path) as storage:
        saved = _save(storage, text=long_text, reason="length is the point")
        listed = render_examples(list_examples(storage))
        assert "..." in listed
        assert len(listed) < len(long_text)
        assert saved.text in render_example(saved)


def test_renderers_are_honest_when_empty(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        assert "No examples saved yet" in render_examples(list_examples(storage))
        assert "no kinds" in render_kinds(example_kinds(storage))


def test_saving_an_example_never_logs_the_document(
    workspace: Config, caplog: pytest.LogCaptureFixture
) -> None:
    """An example can be a cover letter, which is about as personal as this
    workspace gets."""
    secret = "I was fired from Acme in 2019"
    with caplog.at_level("INFO"), Storage(workspace.db_path) as storage:
        _save(storage, text=secret, reason="too candid")
    assert secret not in caplog.text


def test_the_tool_round_trips_and_refuses_a_bare_verdict(workspace: Config) -> None:
    from wingman.mcp_server import examples as examples_tool

    Storage(workspace.db_path).close()  # _ready_config requires the db file to exist

    refused = examples_tool(
        action="add", text="A draft", verdict="good", kind="cover letter", reason=""
    )
    assert "reason" in refused
    assert "No examples saved yet" in examples_tool(action="list")

    added = examples_tool(
        action="add",
        text="A draft that led with the ask",
        verdict="good",
        kind="briefing",
        reason="led with the ask",
    )
    assert "good example of 'briefing'" in added

    listed = examples_tool(action="list")
    assert "briefing" in listed and "led with the ask" in listed
    assert "briefing" in examples_tool(action="kinds")
    assert len(examples_tool(action="list", verdict="bad").splitlines()) == 1

    with Storage(workspace.db_path) as storage:
        example_id = list_examples(storage)[0].example_id
    assert "led with the ask" in examples_tool(action="show", example_id=example_id[:8])
    assert "Removed" in examples_tool(action="remove", example_id=example_id[:8])
    assert "unknown action" in examples_tool(action="bogus")


def test_the_tool_tells_the_client_not_to_promise_steering(workspace: Config) -> None:
    """Scope is deliberate: this is a corpus, not a style reference. A tool
    that lets the client imply otherwise would be lying to the user."""
    from wingman.mcp_server import examples as examples_tool

    doc = " ".join((examples_tool.__doc__ or "").split())
    assert "does NOT feed examples back into what wingman writes" in doc
    assert "do not tell the user their saved examples will change future drafts" in doc
    # And the protocol says where "this" comes from, so the user is never
    # asked to paste back something wingman just wrote.
    assert "the one you just produced" in doc
    assert "reason is REQUIRED" in doc
