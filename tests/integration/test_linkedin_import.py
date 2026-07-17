import json
import zipfile
from pathlib import Path

import pytest

from wingman.application.ingest import IngestError
from wingman.application.linkedin import import_linkedin
from wingman.domain.profile import ProfileItemKind
from wingman.infrastructure.config import ENV_DATA_DIR, Config, load_config
from wingman.infrastructure.storage import Storage

POSITIONS = (
    "Company Name,Title,Description,Location,Started On,Finished On\n"
    'Acme Analytics,Head of Data,"Built the data org from 2 to 15 people.",SF,Jan 2020,Mar 2023\n'
    "Beta Corp,Advisor,,Remote,Apr 2023,\n"
)
SKILLS = "Name\nPython\nData Engineering\n"
RECOMMENDATIONS = (
    "First Name,Last Name,Company,Job Title,Text,Creation Date\n"
    'Jamie,Lee,Acme Analytics,VP Engineering,"The best data leader I have worked with.",2023-01-05\n'
    "Empty,Row,Acme,CTO,,2023-02-01\n"
)


@pytest.fixture
def workspace(tmp_path: Path) -> Config:
    config = load_config(env={ENV_DATA_DIR: str(tmp_path / "ws")})
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    return config


def _export_zip(tmp_path: Path) -> Path:
    archive = tmp_path / "linkedin-export.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Positions.csv", POSITIONS)
        zf.writestr("Skills.csv", SKILLS)
        zf.writestr("Recommendations_Received.csv", RECOMMENDATIONS)
        zf.writestr("Connections.csv", "First Name,Last Name\nPrivate,Person\n")
        zf.writestr("messages.csv", "from,to,body\na,b,private\n")
    return archive


def test_import_builds_cited_profile_items(workspace: Config, tmp_path: Path) -> None:
    with Storage(workspace.db_path) as storage:
        report = import_linkedin(_export_zip(tmp_path), workspace, storage)
        items = storage.list_profile_items()

    assert report.positions == 2
    assert report.skills == 2
    assert report.recommendations == 1  # the empty-text row is skipped
    assert report.counts.accepted == 5
    assert report.sources_created == 3  # Connections/messages never become sources

    by_kind = {kind: [i for i in items if i.kind is kind] for kind in ProfileItemKind}
    role = next(i for i in by_kind[ProfileItemKind.ROLE] if "Head of Data" in i.name)
    assert role.name == "Head of Data at Acme Analytics"
    assert role.detail == "Built the data org from 2 to 15 people. (Jan 2020 – Mar 2023)"
    assert role.evidence[0].quote == "Built the data org from 2 to 15 people."
    testimonial = by_kind[ProfileItemKind.TESTIMONIAL][0]
    assert testimonial.name == "Recommendation from Jamie Lee"
    assert testimonial.detail == "VP Engineering, Acme Analytics"
    assert testimonial.evidence[0].quote == "The best data leader I have worked with."

    markdown = report.career_md_path.read_text(encoding="utf-8")
    assert "## Roles" in markdown
    assert "## Testimonials" in markdown
    assert "Head of Data at Acme Analytics" in markdown
    payload = json.loads(report.career_json_path.read_text(encoding="utf-8"))
    assert len(payload["roles"]) == 2
    assert len(payload["testimonials"]) == 1


def test_reimport_is_idempotent(workspace: Config, tmp_path: Path) -> None:
    archive = _export_zip(tmp_path)
    with Storage(workspace.db_path) as storage:
        import_linkedin(archive, workspace, storage)
        second = import_linkedin(archive, workspace, storage)
        assert second.counts.accepted == 0
        assert second.counts.skipped_duplicates == 5
        assert second.sources_created == 0
        assert storage.count_profile_items() == 5


def test_export_without_relevant_csvs_fails_visibly(workspace: Config, tmp_path: Path) -> None:
    archive = tmp_path / "not-linkedin.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("random.txt", "hello")
    with Storage(workspace.db_path) as storage:
        with pytest.raises(IngestError, match="Nothing was imported"):
            import_linkedin(archive, workspace, storage)
        assert storage.count_source_records() == 0
