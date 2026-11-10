"""Device login must negotiate JSON from form-default OAuth servers (#1060).

GitHub's device and token endpoints answer ``application/x-www-form-urlencoded``
unless the request sends ``Accept: application/json`` (GitHub docs, "Authorizing
OAuth apps"). ``tools.mcp_oauth_device`` parses JSON only, so without the header
the native login dies at "Device authorization: invalid JSON response" before a
user code is shown. The stand-in below reproduces that negotiation offline with a
real ``httpx.MockTransport``; no network, browser or real OAuth endpoint is used.
"""

import asyncio
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode

import pytest

pytest.importorskip("mcp.client.auth.oauth2", reason="MCP SDK required for OAuth support")

BASE = "https://auth.example.test"


def _negotiating_handler(seen, token_replies):
    """Form-encoded by default, JSON only when the request accepts it (GitHub's behavior)."""

    def handler(request):
        seen.append((request.url.path, request.headers.get("accept"), parse_qs(request.content.decode())))
        if request.url.path == "/login/device/code":
            payload = {"device_code": "fixture-device-code", "user_code": "TEST-CODE",
                       "verification_uri": f"{BASE}/login/device", "interval": 0.01, "expires_in": 30}
            status = 200
        elif request.url.path == "/login/oauth/access_token":
            status, payload = token_replies.pop(0)
        else:
            return _httpx().Response(404)
        if "application/json" in (request.headers.get("accept") or ""):
            return _httpx().Response(status, content=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
        return _httpx().Response(status, content=urlencode(payload).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})

    return handler


def _httpx():
    from tools.mcp_tool import sdk_httpx
    return sdk_httpx()


def _provider(tmp_path, monkeypatch, *, secret=None):
    from mcp.shared.auth import OAuthClientInformationFull

    from tools.mcp_oauth import _build_client_metadata
    from tools.mcp_oauth_manager import HermesMCPOAuthProvider
    from tools.mcp_oauth_provider import prepare_oauth_config

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    cfg, storage = prepare_oauth_config("github", "https://mcp.example.test/mcp/", {"scope": "repo read:org"})
    cfg["_resolved_port"] = cfg.get("redirect_port", 8420)
    provider = HermesMCPOAuthProvider(server_url="https://mcp.example.test/mcp/", server_name="github",
                                      storage=storage, client_metadata=_build_client_metadata(cfg),
                                      token_user_agent=cfg.get("user_agent"))
    provider.context.oauth_metadata = SimpleNamespace(
        issuer=BASE, token_endpoint=f"{BASE}/login/oauth/access_token",
        device_authorization_endpoint=f"{BASE}/login/device/code")
    info = {"client_id": "fixture-public-client", "redirect_uris": ["http://127.0.0.1:33333/callback"],
            "token_endpoint_auth_method": "client_secret_basic" if secret else "none"}
    if secret:
        info["client_secret"] = secret
    provider.context.client_info = OAuthClientInformationFull.model_validate(info)
    return provider


def _run(provider, handler):
    from tools.mcp_oauth_device import _authorize

    async def go():
        async with _httpx().AsyncClient(transport=_httpx().MockTransport(handler)) as client:
            return await _authorize(client, provider, {"timeout": 60})

    return asyncio.run(go())


def test_device_login_negotiates_json_on_authorization_and_every_poll(tmp_path, monkeypatch, capsys):
    seen = []
    replies = [(200, {"error": "authorization_pending"}), (200, {"error": "slow_down", "interval": 1}),
               (200, {"access_token": "fixture-at", "token_type": "bearer", "scope": "repo,read:org"})]
    provider = _provider(tmp_path, monkeypatch)
    # slow_down adds 5s; keep the test fast without changing the production rule.
    real_sleep = asyncio.sleep
    monkeypatch.setattr("tools.mcp_oauth_device.asyncio.sleep", lambda _s: real_sleep(0))
    tokens = _run(provider, _negotiating_handler(seen, replies))

    assert tokens.access_token == "fixture-at"
    assert [path for path, _, _ in seen] == ["/login/device/code"] + ["/login/oauth/access_token"] * 3
    assert all(accept == "application/json" for _, accept, _ in seen), seen
    device_form = seen[0][2]
    assert device_form["client_id"] == ["fixture-public-client"]
    assert device_form["scope"] == ["repo read:org"]
    assert seen[1][2]["grant_type"] == ["urn:ietf:params:oauth:grant-type:device_code"]
    assert "TEST-CODE" in capsys.readouterr().err


def test_json_accept_keeps_client_secret_basic_authorization(tmp_path, monkeypatch):
    seen_auth = []
    replies = [(200, {"access_token": "fixture-at", "token_type": "bearer"})]
    inner = _negotiating_handler([], replies)

    def handler(request):
        seen_auth.append((request.headers.get("accept"), request.headers.get("authorization")))
        return inner(request)

    provider = _provider(tmp_path, monkeypatch, secret="fixture-secret")
    assert _run(provider, handler).access_token == "fixture-at"
    assert [accept for accept, _ in seen_auth] == ["application/json"] * 2
    assert all(auth and auth.startswith("Basic ") for _, auth in seen_auth), "basic auth dropped"


def test_access_denied_still_refuses_with_safe_error(tmp_path, monkeypatch):
    replies = [(200, {"error": "access_denied", "error_description": "secret-ish detail"})]
    provider = _provider(tmp_path, monkeypatch)
    with pytest.raises(RuntimeError, match=r"^Device authorization failed: access_denied$"):
        _run(provider, _negotiating_handler([], replies))
