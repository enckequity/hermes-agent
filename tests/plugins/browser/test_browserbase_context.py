"""Browserbase ``BROWSERBASE_CONTEXT_ID``: sessions attach the Context with persist, merged with stealth."""

from __future__ import annotations

import pytest

from plugins.browser.browserbase.provider import BrowserbaseBrowserProvider


class _Resp:
    status_code = 200

    def json(self):
        return {"id": "sess-1", "connectUrl": "wss://example.invalid/sess-1"}


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    monkeypatch.setenv("BROWSERBASE_API_KEY", "k")
    monkeypatch.setenv("BROWSERBASE_PROJECT_ID", "p")
    for name in ("BROWSERBASE_CONTEXT_ID", "BROWSERBASE_ADVANCED_STEALTH", "BROWSERBASE_SESSION_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    bodies: list[dict] = []
    monkeypatch.setattr(BrowserbaseBrowserProvider, "_post_create", lambda self, url, headers, body: bodies.append(dict(body)) or _Resp())
    monkeypatch.setattr(BrowserbaseBrowserProvider, "_check_created", lambda self, response: None)
    return bodies


def test_no_context_leaves_payload_unchanged(sent: list[dict]) -> None:
    info = BrowserbaseBrowserProvider().create_session("t1")
    assert "browserSettings" not in sent[0]
    assert info["features"]["persistent_context"] is False


def test_context_attached_with_persist(sent: list[dict], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROWSERBASE_CONTEXT_ID", " ctx-123 ")
    info = BrowserbaseBrowserProvider().create_session("t1")
    assert sent[0]["browserSettings"] == {"context": {"id": "ctx-123", "persist": True}}
    assert info["features"]["persistent_context"] is True


def test_context_merges_with_advanced_stealth(sent: list[dict], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BROWSERBASE_CONTEXT_ID", "ctx-123")
    monkeypatch.setenv("BROWSERBASE_ADVANCED_STEALTH", "true")
    BrowserbaseBrowserProvider().create_session("t1")
    assert sent[0]["browserSettings"] == {"advancedStealth": True, "context": {"id": "ctx-123", "persist": True}}
