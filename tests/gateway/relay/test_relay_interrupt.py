"""Relay /stop interrupt routing (relay Phase 1, Task 1.4).

Proves a connector-delivered mid-turn interrupt reaches the existing per-session
interrupt mechanism and cancels exactly the targeted session_key's turn — never
a sibling's. Mirrors the isolation discipline of test_stop_thread_sibling.py.
"""

from __future__ import annotations

import asyncio

import pytest

from gateway.config import PlatformConfig
from gateway.relay.adapter import RelayAdapter
from gateway.relay.descriptor import CONTRACT_VERSION, CapabilityDescriptor

from tests.gateway.relay.stub_connector import StubConnector


def _desc() -> CapabilityDescriptor:
    return CapabilityDescriptor(
        contract_version=CONTRACT_VERSION,
        platform="discord",
        label="Discord",
        max_message_length=2000,
        supports_draft_streaming=False,
        supports_edit=True,
        supports_threads=True,
        markdown_dialect="discord",
        len_unit="chars",
    )


@pytest.fixture
def adapter():
    return RelayAdapter(PlatformConfig(), _desc(), transport=StubConnector(_desc()))


@pytest.mark.asyncio
async def test_interrupt_sets_only_target_session_event(adapter):
    key_a = "agent:main:discord:group:chanA:userX"
    key_b = "agent:main:discord:group:chanB:userY"
    ev_a = asyncio.Event()
    ev_b = asyncio.Event()
    adapter._active_sessions[key_a] = ev_a
    adapter._active_sessions[key_b] = ev_b

    await adapter.on_interrupt(key_a, chat_id="chanA")

    assert ev_a.is_set() is True, "target session's interrupt Event must be set"
    assert ev_b.is_set() is False, "sibling session must be untouched"




def _runner_with_running_agent(key):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    agent = MagicMock()
    agent.session_id = "sess-1"
    runner._running_agents = {key: agent}
    runner._pending_messages = {}
    runner._invalidate_session_run_generation = lambda *a, **kw: 7
    runner._release_running_agent_state = lambda *a, **kw: None
    runner._peek_session_state = lambda k: SimpleNamespace(turn=SimpleNamespace(agent=runner._running_agents.get(k)))
    return runner, agent


@pytest.mark.asyncio
async def test_interrupt_stops_running_agent_and_keeps_queued_followups(adapter):
    from unittest.mock import AsyncMock, patch

    key = "agent:main:discord:group:chanA:userX"
    runner, agent = _runner_with_running_agent(key)
    adapter.gateway_runner = runner
    adapter.send = AsyncMock()
    adapter._active_sessions[key] = asyncio.Event()
    queued = object()
    adapter._pending_messages[key] = queued

    with patch("hermes_cli.plugins.invoke_hook") as hook, \
            patch("tools.async_delegation.interrupt_for_session") as delegations:
        await adapter.on_interrupt(key, chat_id="chanA")

    agent.interrupt.assert_called_once()
    delegations.assert_called_once()
    hook.assert_any_call(
        "agent_loop_stopped", session_key=key, platform="relay",
        reason="Relay interrupt", invalidation_reason="Relay interrupt")
    assert adapter._pending_messages[key] is queued, "queued follow-up must survive the interrupt"
    assert runner._running_agents[key] is agent, "the interrupted turn releases its own slot"
    assert adapter._active_sessions[key].is_set()
    adapter.send.assert_not_awaited()  # no "Stopped" reply
