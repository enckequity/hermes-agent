"""Per-server ``parked_retry_interval``: how often a parked MCP server self-probes."""

import asyncio

import pytest


@pytest.mark.parametrize("configured, expected", [
    (None, 300),            # unset keeps the global default
    (30, 30.0),
    ("45", 45.0),
    (1, 5),                 # floored so a tiny value cannot busy-loop
    (0, 300),
    (-10, 300),
    ("soon", 300),          # invalid values warn and keep the default
    (float("inf"), 300),
])
def test_parked_retry_interval_resolution(configured, expected):
    from tools.mcp_tool import MCPServerTask

    task = MCPServerTask("srv")
    task._config = {"command": "x"} if configured is None else {"command": "x", "parked_retry_interval": configured}
    assert task._parked_retry_interval() == expected


@pytest.mark.no_isolate
def test_parked_server_self_probes_on_configured_interval(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from tools import mcp_tool
    from tools.mcp_tool import MCPServerTask

    monkeypatch.setattr(mcp_tool, "_MAX_INITIAL_CONNECT_RETRIES", 0)
    # The global default would never fire within the test; only the per-server value can revive it.
    monkeypatch.setattr(mcp_tool, "_PARKED_RETRY_INTERVAL", 3600)
    monkeypatch.setattr(mcp_tool, "_MIN_PARKED_RETRY_INTERVAL", 0)
    config = {"command": "x", "parked_retry_interval": 0.05}

    from tools import mcp_tool_config as _config
    monkeypatch.setattr(_config, "_load_mcp_config", lambda: {"srv": config})
    _real_sleep = asyncio.sleep

    async def _fast_sleep(_delay, *a, **kw):
        await _real_sleep(0)

    monkeypatch.setattr(mcp_tool.asyncio, "sleep", _fast_sleep)
    attempts: list = []

    class _Task(MCPServerTask):
        def _is_http(self):
            return False

        def _deregister_tools(self):
            self._registered_tool_names = []

        async def _run_stdio(self, config):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("down")  # parks at once (initial ladder = 0 retries)
            self.session = object()
            await self._wait_for_lifecycle_event()
            return "shutdown"

    async def _scenario():
        task = _Task("srv")
        run_task = asyncio.ensure_future(task.run(config))
        for _ in range(400):
            await _real_sleep(0.01)
            if len(attempts) >= 2:
                break
        await task.shutdown()
        await asyncio.wait_for(run_task, timeout=5)

    asyncio.run(_scenario())
    assert len(attempts) >= 2, "parked server never self-probed on its configured interval"
