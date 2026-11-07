"""Coordinate input after a full-screen capture uses cua-driver's desktop scope when no window can be targeted.

On Linux the driver drops every window whose ``_NET_WM_PID`` is not alive in its own /proc, so an app running in
another pid namespace (e.g. a browser in a sibling container) never shows up in ``list_windows``. There is no sticky
target, and every input action used to refuse with "No active window". The driver already accepts
``scope: "desktop"`` with screen x/y and no pid; ``get_desktop_state`` pixels are those screen coordinates.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAYAAADED76LAAAADUlEQVR4nG"
    "NgGAUgAAABCAABgukLHQAAAABJRU5ErkJggg=="
)
_SCOPED = {"click", "drag", "scroll", "type_text", "press_key", "hotkey"}
_OK = {"isError": False, "data": "ok", "images": [], "structuredContent": {}}


class _FakeSession:
    def __init__(self, scoped: set = _SCOPED, tools: Optional[set] = None):
        self._scoped = scoped
        self._tools = {"get_desktop_state", "escalate_session"} if tools is None else tools
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, name: str, args: dict[str, Any], timeout: float = 30.0):
        self.calls.append((name, dict(args or {})))
        if name == "get_config":
            return {**_OK, "structuredContent": {"capture_scope": "window"}}
        if name == "get_desktop_state":
            return {**_OK, "images": [_PNG_B64], "image_mime_types": ["image/png"],
                    "structuredContent": {"screen_width": 8, "screen_height": 8}}
        return _OK

    def supports_capability(self, capability: str, tool: Optional[str] = None) -> bool:
        return False

    def supports_input_property(self, tool: str, property_name: str) -> bool:
        return property_name == "scope" and tool in self._scoped

    def _has_tool(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return [n for n, _ in self.calls if n not in {"get_config", "set_config", "get_desktop_state"}]

    def last(self, name: str) -> dict[str, Any]:
        return [a for n, a in self.calls if n == name][-1]


def _backend(session: _FakeSession, *, screen: bool = True):
    from tools.computer_use.cua_backend import CuaDriverBackend
    be = CuaDriverBackend.__new__(CuaDriverBackend)
    be._session, be._session_id = session, "test-run"
    be._clear_active_target()
    if screen:
        assert be.capture(mode="vision", app="screen").png_b64 == _PNG_B64
    return be


def _input(name: str, session: _FakeSession) -> dict[str, Any]:
    args = session.last(name)
    args.pop("session", None)
    return args


def test_click_after_full_screen_capture_uses_desktop_scope_and_escalates_once():
    session = _FakeSession()
    be = _backend(session)

    assert be.click(x=120, y=45).ok
    assert be.click(x=10, y=20, click_count=2).ok

    assert session.names() == ["escalate_session", "click", "click"]
    assert session.last("escalate_session") == {"session": "test-run", "reason": "no_window_target"}
    assert _input("click", session) == {"scope": "desktop", "x": 10, "y": 20, "button": "left", "count": 2}


@pytest.mark.parametrize("call, tool, expected", [
    (lambda be: be.type_text("hello"), "type_text", {"scope": "desktop", "text": "hello"}),
    (lambda be: be.key("Return"), "press_key", {"scope": "desktop", "key": "return"}),
    (lambda be: be.key("ctrl+l"), "hotkey", {"scope": "desktop", "keys": ["ctrl", "l"]}),
    (lambda be: be.scroll(direction="down", amount=4, x=5, y=6), "scroll",
     {"scope": "desktop", "direction": "down", "amount": 4, "x": 5, "y": 6}),
    (lambda be: be.drag(from_xy=(1, 2), to_xy=(3, 4)), "drag",
     {"scope": "desktop", "from_x": 1, "from_y": 2, "to_x": 3, "to_y": 4}),
])
def test_keyboard_scroll_and_drag_use_desktop_scope(call, tool, expected):
    session = _FakeSession()
    assert call(_backend(session)).ok
    assert _input(tool, session) == expected
    assert "pid" not in expected and "window_id" not in expected


@pytest.mark.parametrize("session, screen, call", [
    (_FakeSession(), False, lambda be: be.click(x=1, y=2)),                # no full-screen capture: x/y not screen px
    (_FakeSession(), True, lambda be: be.click(element=3)),                # element actions still need a window
    (_FakeSession(), True, lambda be: be.scroll(direction="down")),        # no point to aim the wheel at
    (_FakeSession(scoped=set()), True, lambda be: be.click(x=1, y=2)),     # driver schema has no desktop scope
])
def test_desktop_scope_not_used_without_screen_coordinates_or_driver_support(session, screen, call):
    res = call(_backend(session, screen=screen))
    assert res.ok is False and "No active window" in res.message
    assert session.names() == []


def test_window_target_keeps_window_args():
    session = _FakeSession()
    be = _backend(session)
    be._set_active_target({"pid": 4242, "window_id": 7})

    assert be.click(x=3, y=4).ok
    assert _input("click", session) == {"pid": 4242, "window_id": 7, "button": "left", "x": 3, "y": 4}
    assert "escalate_session" not in session.names()


def test_driver_without_escalate_session_still_sends_desktop_input():
    session = _FakeSession(tools={"get_desktop_state"})
    assert _backend(session).click(x=1, y=2).ok
    assert session.names() == ["click"]
