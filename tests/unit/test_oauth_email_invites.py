"""Email-bound OAuth invites: the store and CLI (#584, RFC-081 amendment #583).

Recording only. Nothing here changes what a sign-in does; claiming an invite
on a verified sign-in is #585.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from wingman.cli.main import app
from wingman.infrastructure.oauth_onboarding import (
    OAuthOnboardingError,
    OAuthOnboardingStore,
    normalise_email,
    onboarding_path_for,
)

cli = CliRunner()
ISSUER = "https://idp.example.com"
DAY = 86400.0
EMAIL = "Pat.Example+jobs@Example.COM"


def _store(tmp_path: Path) -> OAuthOnboardingStore:
    return OAuthOnboardingStore(onboarding_path_for(tmp_path / "oauth-identities.toml"))


def _files(tmp_path: Path) -> tuple[Path, Path, Path]:
    identities = tmp_path / "oauth-identities.toml"
    identities.write_text("", encoding="utf-8")
    registry = tmp_path / "tenants.toml"
    registry.write_text(
        f'[[tenant]]\nslug = "existing"\ndata_dir = "{tmp_path / "existing"}"\n',
        encoding="utf-8",
    )
    return identities, registry, onboarding_path_for(identities)


def _everything_on_disk(directory: Path) -> str:
    return "\n".join(
        path.read_bytes().decode("latin-1") for path in directory.iterdir() if path.is_file()
    )


def test_normalisation_is_lowercase_and_trim_only() -> None:
    assert normalise_email("  Pat@Example.COM \n") == "pat@example.com"
    # No provider-specific folding: these are different addresses to Wingman.
    assert normalise_email("p.at@gmail.com") != normalise_email("pat@gmail.com")
    assert normalise_email("pat+x@gmail.com") != normalise_email("pat@gmail.com")
    for bad in ("", "pat", "pat@", "@example.com", "pat@@example.com", "p at@example.com"):
        with pytest.raises(OAuthOnboardingError):
            normalise_email(bad)


def test_email_invite_matches_case_insensitively_and_never_stores_plaintext(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.reserve_invite("pat", existing_slugs=set(), email=EMAIL, now=100.0)

    assert store.match_email("pat.example+jobs@example.com", now=101.0) == "pat"
    assert store.match_email("  PAT.EXAMPLE+JOBS@EXAMPLE.COM ", now=101.0) == "pat"
    assert store.match_email("patexample+jobs@example.com", now=101.0) is None
    assert store.match_email("pat.example@example.com", now=101.0) is None
    assert store.match_email("not an email", now=101.0) is None

    on_disk = _everything_on_disk(tmp_path).lower()
    assert "example.com" not in on_disk
    assert "pat.example" not in on_disk
    for path in tmp_path.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path.name


def test_email_invite_expires_for_matching_but_keeps_the_slug_reserved(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.reserve_invite("pat", existing_slugs=set(), email=EMAIL, expires_days=30, now=0.0)

    assert store.match_email(EMAIL, now=30 * DAY - 1) == "pat"
    assert store.match_email(EMAIL, now=30 * DAY) is None
    [invite] = store.invites()
    assert invite.email_bound
    assert invite.expires_at == 30 * DAY
    assert invite.status(now=30 * DAY) == "expired"
    assert invite.status(now=1.0) == "active"


def test_slug_only_invites_are_unchanged(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.reserve_invite("taylor", existing_slugs=set(), now=1.0)

    [invite] = store.invites()
    assert (invite.slug, invite.email_bound, invite.expires_at) == ("taylor", False, None)
    assert invite.status(now=10 * 365 * DAY) == "active"
    assert store.match_email("taylor@example.com") is None
    # A slug-only invite never creates the email key.
    assert not any(path.suffix == ".key" for path in tmp_path.iterdir())


def test_duplicate_email_or_slug_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.reserve_invite("pat", existing_slugs=set(), email=EMAIL, now=1.0)

    with pytest.raises(OAuthOnboardingError, match="already has an invite"):
        store.reserve_invite("pat-two", existing_slugs=set(), email=EMAIL.lower(), now=2.0)
    with pytest.raises(OAuthOnboardingError, match="already reserved"):
        store.reserve_invite("pat", existing_slugs=set(), email="other@example.com", now=2.0)
    assert [invite.slug for invite in store.invites()] == ["pat"]


def test_batch_is_all_or_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.reserve_invite("taken", existing_slugs=set(), now=1.0)

    with pytest.raises(OAuthOnboardingError, match="line 3"):
        store.reserve_email_invites(
            [("a@example.com", "alpha"), ("b@example.com", "taken")],
            existing_slugs=set(),
            first_line=2,
            now=2.0,
        )
    assert [invite.slug for invite in store.invites()] == ["taken"]
    assert store.match_email("a@example.com", now=3.0) is None


def test_revoke_removes_the_invite_and_its_hash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.reserve_invite("pat", existing_slugs=set(), email=EMAIL, now=1.0)
    before = onboarding_path_for(tmp_path / "oauth-identities.toml").read_text()

    assert store.revoke_invite("pat")
    assert not store.revoke_invite("pat")
    assert store.invites() == []
    assert store.match_email(EMAIL, now=2.0) is None
    after = onboarding_path_for(tmp_path / "oauth-identities.toml").read_text()
    assert "email_hmac" in before and "email_hmac" not in after


def test_revoke_refuses_an_invite_that_is_being_approved(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.reserve_invite("pat", existing_slugs=set(), email=EMAIL, now=1.0)
    assert store.record_pending(ISSUER, "user_pat", now=2.0)
    store.begin_approval("pat", ISSUER, "user_pat")

    with pytest.raises(OAuthOnboardingError, match="being approved"):
        store.revoke_invite("pat")


def test_approval_consumes_an_email_invite_and_deletes_its_hash(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.reserve_invite("pat", existing_slugs=set(), email=EMAIL, now=1.0)
    assert store.record_pending(ISSUER, "user_pat", now=2.0)

    token = store.begin_approval("pat", ISSUER, "user_pat")
    store.finish_approval("pat", token)

    assert store.invites() == []
    assert "email_hmac" not in onboarding_path_for(tmp_path / "oauth-identities.toml").read_text()


def test_a_replaced_key_makes_old_email_invites_unmatchable(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.reserve_invite("pat", existing_slugs=set(), email=EMAIL, now=1.0)
    [key] = [path for path in tmp_path.iterdir() if path.suffix == ".key"]
    key.write_bytes(b"\x01" * 32)

    assert store.match_email(EMAIL, now=2.0) is None
    [invite] = store.invites()
    assert invite.status(now=2.0) == "key-mismatch"


def test_key_readable_by_others_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.reserve_invite("pat", existing_slugs=set(), email=EMAIL, now=1.0)
    [key] = [path for path in tmp_path.iterdir() if path.suffix == ".key"]
    key.chmod(0o644)

    with pytest.raises(OAuthOnboardingError, match="readable by other users"):
        store.reserve_invite("sam", existing_slugs=set(), email="sam@example.com", now=2.0)


def test_matching_on_a_fresh_host_creates_no_files(tmp_path: Path) -> None:
    assert _store(tmp_path).match_email(EMAIL) is None
    assert list(tmp_path.iterdir()) == []


def test_malformed_email_invite_fields_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "onboarding.json"
    path.write_text(
        '{"version": 1, "pending": [], "invites": '
        '[{"slug": "pat", "created_at": 1, "email_hmac": "nothex"}]}\n',
        encoding="utf-8",
    )

    with pytest.raises(OAuthOnboardingError, match="malformed invite entry 1"):
        OAuthOnboardingStore(path).invites()


def test_invite_count_is_bounded(tmp_path: Path) -> None:
    store = OAuthOnboardingStore(tmp_path / "onboarding.json", invite_limit=2)
    store.reserve_invite("one", existing_slugs=set(), now=1.0)
    store.reserve_invite("two", existing_slugs=set(), email="two@example.com", now=1.0)

    with pytest.raises(OAuthOnboardingError, match="limit of 2"):
        store.reserve_invite("three", existing_slugs=set(), email="three@example.com", now=1.0)


# --- CLI -------------------------------------------------------------------


def test_cli_email_invite_reserves_without_echoing_the_address(tmp_path: Path) -> None:
    identities, registry, _state = _files(tmp_path)

    result = cli.invoke(
        app,
        [
            "tenant",
            "oauth-invite",
            "--email",
            EMAIL,
            "--slug",
            "pat",
            "--identities",
            str(identities),
            "--registry",
            str(registry),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "pat" in result.output
    assert "example.com" not in result.output.lower()
    assert "#585" in result.output  # says plainly it is not claimed automatically yet
    assert _store(tmp_path).match_email(EMAIL) == "pat"


def test_cli_positional_slug_invite_still_works(tmp_path: Path) -> None:
    identities, registry, _state = _files(tmp_path)

    result = cli.invoke(
        app,
        ["tenant", "oauth-invite", "taylor", "--identities", str(identities)]
        + ["--registry", str(registry)],
    )

    assert result.exit_code == 0, result.output
    assert [invite.slug for invite in _store(tmp_path).invites()] == ["taylor"]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["taylor", "--slug", "taylor"], "either"),
        (["--email", "pat@example.com"], "--slug"),
        ([], "slug"),
        (["--from-csv", "x.csv", "--slug", "pat"], "--from-csv"),
    ],
)
def test_cli_rejects_ambiguous_invite_arguments(
    tmp_path: Path, args: list[str], message: str
) -> None:
    identities, registry, _state = _files(tmp_path)

    result = cli.invoke(
        app,
        ["tenant", "oauth-invite", *args, "--identities", str(identities)]
        + ["--registry", str(registry)],
    )

    assert result.exit_code != 0
    assert message in result.output
    assert _store(tmp_path).invites() == []


def test_cli_csv_import_is_all_or_nothing_and_names_lines_not_addresses(tmp_path: Path) -> None:
    identities, registry, _state = _files(tmp_path)
    bad = tmp_path / "invites.csv"
    bad.write_text(
        "email,slug\nalpha@example.com,alpha\nbeta@example.com,existing\n", encoding="utf-8"
    )

    refused = cli.invoke(
        app,
        ["tenant", "oauth-invite", "--from-csv", str(bad), "--identities", str(identities)]
        + ["--registry", str(registry)],
    )

    assert refused.exit_code == 1
    assert "line 3" in refused.output
    assert "example.com" not in refused.output
    assert _store(tmp_path).invites() == []

    good = tmp_path / "good.csv"
    good.write_text(
        "email,slug\n alpha@example.com ,alpha\nBETA@example.com,beta\n", encoding="utf-8"
    )
    accepted = cli.invoke(
        app,
        ["tenant", "oauth-invite", "--from-csv", str(good), "--identities", str(identities)]
        + ["--registry", str(registry)],
    )

    assert accepted.exit_code == 0, accepted.output
    assert "2 email invites" in accepted.output
    assert "example.com" not in accepted.output
    store = _store(tmp_path)
    assert store.match_email("beta@example.com") == "beta"
    assert store.match_email("alpha@example.com") == "alpha"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("slug,email\na@example.com,alpha\n", "header"),
        ("email,slug\na@example.com,alpha\nb@example.com,alpha\n", "line 3"),
        ("email,slug\na@example.com,alpha\nA@example.com ,beta\n", "line 3"),
        ("email,slug\nnot-an-email,alpha\n", "line 2"),
        ("email,slug\na@example.com,Bad Slug\n", "line 2"),
        ("email,slug\n", "no invites"),
    ],
)
def test_cli_csv_validation(tmp_path: Path, body: str, message: str) -> None:
    identities, registry, _state = _files(tmp_path)
    csv_path = tmp_path / "invites.csv"
    csv_path.write_text(body, encoding="utf-8")

    result = cli.invoke(
        app,
        ["tenant", "oauth-invite", "--from-csv", str(csv_path), "--identities", str(identities)]
        + ["--registry", str(registry)],
    )

    assert result.exit_code == 1
    assert message in result.output
    assert "example.com" not in result.output
    assert _store(tmp_path).invites() == []


def test_cli_lists_and_revokes_invites_without_addresses(tmp_path: Path) -> None:
    identities, _registry, _state = _files(tmp_path)
    store = _store(tmp_path)
    store.reserve_invite("pat", existing_slugs=set(), email=EMAIL)
    store.reserve_invite("taylor", existing_slugs=set())

    listed = cli.invoke(app, ["tenant", "oauth-invites", "--identities", str(identities)])

    assert listed.exit_code == 0, listed.output
    assert "pat" in listed.output and "taylor" in listed.output
    assert "email" in listed.output and "active" in listed.output
    assert "example.com" not in listed.output.lower()

    revoked = cli.invoke(
        app, ["tenant", "oauth-invite-revoke", "pat", "--identities", str(identities)]
    )
    missing = cli.invoke(
        app, ["tenant", "oauth-invite-revoke", "pat", "--identities", str(identities)]
    )

    assert revoked.exit_code == 0, revoked.output
    assert missing.exit_code == 1
    assert [invite.slug for invite in store.invites()] == ["taylor"]
