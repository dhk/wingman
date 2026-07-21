"""The read surface (RFC-033): token gate, file confinement, uploads."""

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import wingman.webui as webui_module
from wingman.infrastructure.config import load_config
from wingman.infrastructure.storage import Storage
from wingman.mcp_server import _http_token, server
from wingman.providers.recorded import RecordedProvider
from wingman.webui import register_ui

RESUME = "# Jo\n\n- Shipped the search rewrite.\n\nSkills: Python.\n"
RESPONSE = json.dumps(
    {
        "items": [
            {
                "kind": "achievement",
                "name": "Search rewrite",
                "detail": "Shipped the search rewrite.",
                "classification": "fact",
                "confidence": 0.9,
                "quotes": ["Shipped the search rewrite."],
            }
        ]
    }
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, str]:
    monkeypatch.setenv("WINGMAN_DATA_DIR", str(tmp_path / "ws"))
    config = load_config()
    for directory in (config.data_dir, config.inbox_dir, config.reports_dir):
        directory.mkdir(parents=True)
    Storage(config.db_path).close()
    token = _http_token(config)
    register_ui(server)
    test_client = TestClient(server.streamable_http_app())
    return test_client, token


def test_wrong_or_missing_token_is_a_plain_404(client: tuple[TestClient, str]) -> None:
    http, token = client
    assert http.get("/ui/wrong-token").status_code == 404
    assert http.get(f"/ui/{token}x/file/x.html").status_code == 404
    ok = http.get(f"/ui/{token}/")
    assert ok.status_code == 200 and "Wingman" in ok.text


def test_home_lists_digest_and_reports(client: tuple[TestClient, str]) -> None:
    http, token = client
    config = load_config()
    digests = config.reports_dir / "digests"
    digests.mkdir(parents=True)
    (digests / "latest.html").write_text("<h1>MARKER-DIGEST</h1>", encoding="utf-8")
    (config.reports_dir / "packs").mkdir()
    (config.reports_dir / "packs" / "pack-staff-mle.md").write_text("pack!", encoding="utf-8")
    page = http.get(f"/ui/{token}/").text
    assert "Today" in page and "file/digests/latest.html" in page
    assert "pack-staff-mle.md" in page
    assert "Upload" in page  # the form is on the page
    served = http.get(f"/ui/{token}/file/digests/latest.html")
    assert served.status_code == 200 and "MARKER-DIGEST" in served.text
    assert served.headers["content-type"].startswith("text/html")


def test_file_serving_is_confined_to_reports(client: tuple[TestClient, str]) -> None:
    http, token = client
    config = load_config()
    # a secret OUTSIDE reports/ must be unreachable, traversal or not
    (config.data_dir / "mcp-http-token").exists()  # the crown jewel next door
    assert http.get(f"/ui/{token}/file/../mcp-http-token").status_code == 404
    assert http.get(f"/ui/{token}/file/%2e%2e/mcp-http-token").status_code == 404
    assert http.get(f"/ui/{token}/file/nope.html").status_code == 404
    # unsupported suffixes are refused even inside reports/
    (config.reports_dir / "x.sqlite").write_text("db", encoding="utf-8")
    assert http.get(f"/ui/{token}/file/x.sqlite").status_code == 404


def test_upload_linkedin_zip_imports(client: tuple[TestClient, str]) -> None:
    http, token = client
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "Positions.csv",
            "Company Name,Title,Description,Location,Started On,Finished On\nAcme,Head of Data,Built it.,SF,Jan 2020,\n",
        )
        archive.writestr("Skills.csv", "Name\nPython\n")
    response = http.post(
        f"/ui/{token}/upload",
        files={"file": ("linkedin-export.zip", buffer.getvalue(), "application/zip")},
    )
    assert response.status_code == 200
    assert "1 positions" in response.text and "1 skills" in response.text
    config = load_config()
    with Storage(config.db_path) as storage:
        assert storage.count_profile_items() == 2


def test_upload_resume_ingests(
    client: tuple[TestClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, token = client
    import wingman.providers.router as router_module

    monkeypatch.setattr(
        router_module, "get_provider", lambda capability, config: RecordedProvider(RESPONSE)
    )
    response = http.post(
        f"/ui/{token}/upload",
        files={"file": ("resume.md", RESUME.encode(), "text/markdown")},
    )
    assert response.status_code == 200 and "Accepted: 1" in response.text


def test_upload_rejects_junk(client: tuple[TestClient, str]) -> None:
    http, token = client
    response = http.post(
        f"/ui/{token}/upload", files={"file": ("malware.exe", b"MZ", "application/x-thing")}
    )
    assert response.status_code == 200 and "Unsupported type" in response.text
    big = b"x" * (webui_module.MAX_UPLOAD_BYTES + 1)
    response = http.post(f"/ui/{token}/upload", files={"file": ("resume.md", big, "text/plain")})
    assert "over the 20 MB limit" in response.text
    assert (
        http.post("/ui/bad/upload", files={"file": ("a.md", b"x", "text/plain")}).status_code == 404
    )


def test_key_form_validates_before_storing(
    client: tuple[TestClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, token = client
    config = load_config()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    calls: list[tuple[str, str]] = []

    def good(value: str) -> None:
        calls.append(("ok", value))
        return None

    def bad(value: str) -> str:
        calls.append(("bad", value))
        return "rejected the key (authentication failed)."

    monkeypatch.setitem(webui_module.VALIDATORS, "anthropic", good)
    monkeypatch.setitem(webui_module.VALIDATORS, "voyage", bad)

    page = http.get(f"/ui/{token}/").text
    assert "API keys" in page and "not set" in page

    response = http.post(f"/ui/{token}/keys", data={"anthropic": "sk-ant-good", "voyage": "pa-bad"})
    assert response.status_code == 200
    assert "anthropic: verified and live now." in response.text
    assert "voyage: rejected the key" in response.text and "Nothing was stored" in response.text

    from wingman.infrastructure.keys import read_workspace_keys, workspace_keys_path

    stored = read_workspace_keys(config.data_dir)
    assert stored == {"ANTHROPIC_API_KEY": "sk-ant-good"}  # only the verified key
    assert (workspace_keys_path(config.data_dir).stat().st_mode & 0o777) == 0o600
    import os

    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-good"  # live immediately

    empty = http.post(f"/ui/{token}/keys", data={})
    assert "No key was entered" in empty.text
    assert http.post("/ui/nope/keys", data={"anthropic": "x"}).status_code == 404


def test_env_always_shadows_workspace_key(
    client: tuple[TestClient, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    http, token = client
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-env")
    monkeypatch.setitem(webui_module.VALIDATORS, "anthropic", lambda value: None)
    response = http.post(f"/ui/{token}/keys", data={"anthropic": "sk-ant-newer"})
    assert "environment variable wins" in response.text
    import os

    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-from-env"  # untouched


def test_ui_is_path_mount_agnostic(client: tuple[TestClient, str]) -> None:
    """Behind 'tailscale serve --set-path /trent' the prefix is stripped before
    the backend: everything must work with zero prefix knowledge — a RELATIVE
    redirect to the slash landing, and only relative URLs in the page."""
    http, token = client
    bare = http.get(f"/ui/{token}", follow_redirects=False)
    assert bare.status_code == 307
    assert bare.headers["location"] == f"{token}/"  # relative: the browser keeps the prefix
    followed = http.get(f"/ui/{token}", follow_redirects=True)
    assert followed.status_code == 200 and "Wingman" in followed.text
    page = http.get(f"/ui/{token}/").text
    assert 'href="/' not in page and 'action="/' not in page  # no absolute self-URLs
    assert 'action="upload"' in page and 'action="keys"' in page
    # wrong token gets no redirect breadcrumb either
    assert http.get(f"/ui/{token}x", follow_redirects=False).status_code == 404
