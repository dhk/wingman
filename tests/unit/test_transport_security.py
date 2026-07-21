"""#100: the HTTP transport must accept its own tunnel's Host header.

The SDK auto-enables DNS-rebinding protection with a loopback-only
allow-list, so every request arriving through 'tailscale serve/funnel'
(Host: <machine>.<tailnet>.ts.net) was rejected with "Invalid Host
header" despite carrying the correct capability token. These tests pin
the widened allow-list against the SDK's real validator.
"""

import json
from typing import Any

from mcp.server.transport_security import TransportSecurityMiddleware

from wingman.mcp_server import _extra_allowed_hosts, _tailscale_dns_name, build_transport_security

FUNNEL = "macbook-dhk.tail08dfce.ts.net"


def test_defaults_stay_loopback_only_with_protection_on() -> None:
    settings = build_transport_security([])
    assert settings.enable_dns_rebinding_protection
    middleware = TransportSecurityMiddleware(settings)
    assert middleware._validate_host("127.0.0.1:8787")
    assert middleware._validate_host("localhost:8787")
    assert not middleware._validate_host(FUNNEL)
    assert not middleware._validate_host("evil.example.com")


def test_funnel_hostname_passes_with_and_without_port() -> None:
    settings = build_transport_security([FUNNEL])
    middleware = TransportSecurityMiddleware(settings)
    assert middleware._validate_host(FUNNEL)  # funnel: implicit :443, portless Host
    assert middleware._validate_host(f"{FUNNEL}:8443")
    assert middleware._validate_host("127.0.0.1:8787")  # loopback still fine
    assert not middleware._validate_host("evil.example.com")  # still ON, not off
    assert middleware._validate_origin(f"https://{FUNNEL}")
    assert not middleware._validate_origin("https://evil.example.com")


def test_sources_merge_dedupe_and_normalize() -> None:
    hosts = _extra_allowed_hosts(
        ["cli.example", "https://pasted.example/mcp/tok"],
        env={"WINGMAN_ALLOWED_HOSTS": "a.example, b.example ,a.example, "},
        tailscale=lambda: "ts.example",
    )
    assert hosts == ["cli.example", "pasted.example", "a.example", "b.example", "ts.example"]


def test_no_config_and_no_tailscale_means_no_extras() -> None:
    assert _extra_allowed_hosts(None, env={}, tailscale=lambda: None) == []


class _FakeProc:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def test_tailscale_detection_strips_trailing_dot() -> None:
    def runner(*args: Any, **kwargs: Any) -> _FakeProc:
        return _FakeProc(json.dumps({"Self": {"DNSName": f"{FUNNEL}."}}))

    assert _tailscale_dns_name(runner) == FUNNEL


def test_tailscale_absent_or_odd_is_none() -> None:
    def missing(*args: Any, **kwargs: Any) -> _FakeProc:
        raise FileNotFoundError("tailscale")

    assert _tailscale_dns_name(missing) is None
    assert _tailscale_dns_name(lambda *a, **k: _FakeProc("not json")) is None
    assert _tailscale_dns_name(lambda *a, **k: _FakeProc(json.dumps({"Self": {}}))) is None
    assert (
        _tailscale_dns_name(lambda *a, **k: _FakeProc(json.dumps({"Self": {"DNSName": ""}})))
        is None
    )
