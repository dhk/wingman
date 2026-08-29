"""Commentary never leaks into an evidence path (#339).

The whole point of a separate store is that the exclusion is structural
rather than a filter each reader has to remember. These tests are the proof
of that claim, one per path the issue names: POV stances / `my_pov`,
outreach briefs, fit briefs, the `evidence` search, workspace `search`, and
profile rendering (career.md/json and the profile page). Each one seeds a
workspace whose ONLY new content is a saved reading with a distinctive
phrase, runs the real path, and asserts the phrase is nowhere in what the
path produced — nor in the prompt any model on that path was given, which
is where contamination would actually start.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import wingman.application.people as people_module
from wingman.application.assess import assess_job
from wingman.application.commentary import save_commentary
from wingman.application.corpus import add_to_corpus, find_evidence
from wingman.application.ingest import IngestError
from wingman.application.outreach import build_outreach_brief
from wingman.application.people import add_person, fetch_person_feed
from wingman.application.pov import build_own_pov, build_pov_card
from wingman.application.search import render_search_report, search_workspace
from wingman.domain.profile import ProfileItem
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage
from wingman.providers.base import ModelRequest, ModelResponse
from wingman.reporting.career import render_career

# Distinctive enough that a substring check is a real test: no fixture, no
# prompt, and no rendering in this repo contains it by accident.
READING = (
    "Zarquon: your con nominations and your job criteria are the same argument in two domains."
)

FIT_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "fit_assessment" / "case_001_basic"

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel><item>
    <title>Explainable Analytics</title>
    <link>https://jane.substack.com/p/explainable</link>
    <content:encoded><![CDATA[<p>Every answer should show its work the way an analyst would.</p>]]></content:encoded>
  </item></channel></rss>
"""


class RecordingProvider:
    """Returns a canned proposal and keeps every prompt it was handed."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.prompts: list[str] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.prompts.append(f"{request.system}\n{request.prompt}")
        return ModelResponse(
            text=json.dumps(self._payload), provider="scripted", model="scripted-1", latency_ms=0
        )


class RecordingTemplatedProvider:
    """The fit-brief fixtures' templated replay, recording prompts too."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.prompts: list[str] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.prompts.append(f"{request.system}\n{request.prompt}")
        ids = re.findall(r'"requirement_id": "([^"]+)"', request.prompt)
        text = self._text
        for index, requirement_id in enumerate(ids):
            text = text.replace(f"__REQ_{index}__", requirement_id)
        return ModelResponse(text=text, provider="recorded", model="templated", latency_ms=0)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    monkeypatch.setenv(ENV_DATA_DIR, str(tmp_path / "ws"))
    # add_person verifies a passed substack_url's feed before storing it
    # (#483) -- a default valid feed so that verification never hits the
    # real network; tests that care about specific feed content patch
    # fetch_url again afterward.
    monkeypatch.setattr(
        people_module,
        "fetch_url",
        lambda url: (
            b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title></channel></rss>'
        ),
    )
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return config


def _essay(config: Config, storage: Storage) -> str:
    essay = config.data_dir / "essay.md"
    essay.write_text(
        "Explainable analytics pipelines beat black boxes in production.", encoding="utf-8"
    )
    add_to_corpus(essay, "writing", config, storage)
    return storage.list_corpus_documents()[0].doc_id


def test_evidence_search_never_returns_commentary(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        _essay(workspace, storage)
        save_commentary(READING, storage, model="claude-opus-4")
        assert find_evidence("Zarquon", storage) == []
        # and the corpus itself is untouched by the save
        assert storage.count_corpus_documents() == 1


def test_workspace_search_never_returns_commentary_but_says_where_it_is(
    workspace: Config,
) -> None:
    with Storage(workspace.db_path) as storage:
        _essay(workspace, storage)
        save_commentary(READING, storage, model="claude-opus-4", topic="Zarquon")
        report = search_workspace("Zarquon", storage, workspace)
        assert report.hits == []
        rendered = render_search_report(report)
        assert "Zarquon" not in "".join(hit.title + hit.snippet for hit in report.hits)
        assert "commentary not searched (1 entries)" in rendered
        assert "wingman commentary find" in rendered


def test_my_pov_never_sees_commentary(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        doc_id = _essay(workspace, storage)
        save_commentary(READING, storage, model="claude-opus-4")
        provider = RecordingProvider(
            {
                "stances": [
                    {
                        "statement": "The author believes pipelines should be explainable.",
                        "quote": "Explainable analytics pipelines beat black boxes",
                        "doc_id": doc_id,
                    }
                ],
                "topics": ["explainable analytics"],
            }
        )
        report = build_own_pov(storage, provider)
        assert provider.prompts and all(READING not in prompt for prompt in provider.prompts)
        assert all(READING not in stance.quote for stance in report.card.stances)


def test_my_pov_with_only_commentary_still_reports_an_empty_corpus(workspace: Config) -> None:
    """Commentary cannot substitute for evidence, even when it is the only
    thing in the workspace: the path refuses exactly as it did before."""
    with Storage(workspace.db_path) as storage:
        save_commentary(READING, storage, model="claude-opus-4")
        with pytest.raises(IngestError, match="corpus is empty"):
            build_own_pov(storage, RecordingProvider({"stances": [], "topics": []}))


def test_outreach_brief_never_sees_commentary(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        person, _ = add_person("Jane Author", storage, substack_url="https://jane.substack.com")
        fetch_person_feed(person, workspace, storage, fetcher=lambda url: FEED.encode())
        external_id = storage.list_external_documents(person.person_id)[0].doc_id
        build_pov_card(
            "Jane Author",
            storage,
            RecordingProvider(
                {
                    "stances": [
                        {
                            "statement": "Believes analytics answers must be explainable.",
                            "quote": "Every answer should show its work",
                            "doc_id": external_id,
                        }
                    ],
                    "topics": ["explainable analytics"],
                }
            ),
        )
        _essay(workspace, storage)
        save_commentary(READING, storage, model="claude-opus-4")

        provider = RecordingProvider({"talking_points": [], "intro_points": []})
        with pytest.raises(IngestError):
            # No point survives validation with an empty proposal; the prompt
            # is what matters here, and it was already built and recorded.
            build_outreach_brief("Jane Author", storage, provider)
        assert provider.prompts and all(READING not in prompt for prompt in provider.prompts)


def test_fit_brief_never_sees_commentary(workspace: Config) -> None:
    with Storage(workspace.db_path) as storage:
        for entry in json.loads((FIT_FIXTURE / "profile.json").read_text(encoding="utf-8")):
            storage.add_profile_item(ProfileItem.model_validate(entry))
        save_commentary(READING, storage, model="claude-opus-4")
        extract = RecordingTemplatedProvider(
            (FIT_FIXTURE / "requirements_response.json").read_text(encoding="utf-8")
        )
        assess = RecordingTemplatedProvider(
            (FIT_FIXTURE / "assessment_response.json").read_text(encoding="utf-8")
        )
        report = assess_job(FIT_FIXTURE / "job.md", workspace, storage, extract, assess)
    assert extract.prompts and assess.prompts
    assert all(READING not in prompt for prompt in [*extract.prompts, *assess.prompts])
    assert READING not in report.brief_md_path.read_text(encoding="utf-8")
    assert READING not in report.brief_json_path.read_text(encoding="utf-8")


def test_profile_rendering_never_shows_commentary(workspace: Config) -> None:
    from wingman.webui import render_profile_html

    with Storage(workspace.db_path) as storage:
        for entry in json.loads((FIT_FIXTURE / "profile.json").read_text(encoding="utf-8")):
            storage.add_profile_item(ProfileItem.model_validate(entry))
        save_commentary(READING, storage, model="claude-opus-4")
        career_md, career_json = render_career(storage, workspace, {"run": "test"})
        # The store is invisible to the item listing every profile surface walks.
        assert all(
            READING not in item.name and READING not in item.detail
            for item in storage.list_profile_items()
        )
    assert READING not in career_md.read_text(encoding="utf-8")
    assert READING not in career_json.read_text(encoding="utf-8")
    assert READING not in render_profile_html(workspace)
