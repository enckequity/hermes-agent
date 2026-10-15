"""POST /v1/runs whose launch fails after the run was registered drops that registration: no
"queued" status nobody will ever run, and an exact Idempotency-Key retry runs the work instead of
replaying a ghost run_id."""
from unittest.mock import MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from tests.gateway.test_api_server_runs import _create_runs_app, _make_adapter, _use_idempotency_db


@pytest.mark.asyncio
async def test_launch_failure_forgets_run_and_releases_idempotency_key(tmp_path):
    adapter = _make_adapter()
    _use_idempotency_db(adapter, tmp_path / "idem.db")
    headers = {"Idempotency-Key": "launch-failure"}
    body = {"input": "hello", "session_id": "api-session"}
    agent = MagicMock()
    agent.run_conversation.return_value = {"final_response": "done"}
    agent.session_prompt_tokens = agent.session_completion_tokens = agent.session_total_tokens = 0
    try:
        async with TestClient(TestServer(_create_runs_app(adapter))) as cli:
            with patch.object(adapter, "_admit_to_live_bot_chat", side_effect=OSError("mailbox unwritable")):
                failed = await cli.post("/v1/runs", json=body, headers=headers)
            assert failed.status == 500
            assert not adapter._run_statuses and not adapter._run_streams and not adapter._run_owners
            assert not adapter._run_idempotency_ids
            with patch.object(adapter, "_admit_to_live_bot_chat", return_value=None), \
                    patch.object(adapter, "_create_agent", return_value=agent):
                retry = await cli.post("/v1/runs", json=body, headers=headers)
                assert retry.status == 202, await retry.text()
                assert retry.headers.get("Idempotency-Replayed") != "true"
    finally:
        for task in adapter._active_run_tasks.values():
            task.cancel()
        adapter._run_idempotency_store.close()
