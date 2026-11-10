"""#963: optional public ``owned_by`` branding on /v1/models (``API_SERVER_MODEL_OWNER``)."""
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter


def _make_adapter() -> APIServerAdapter:
    return APIServerAdapter(PlatformConfig(enabled=True, extra={}))


def _create_app(adapter: APIServerAdapter) -> web.Application:
    app = web.Application()
    app["api_server_adapter"] = adapter
    app.router.add_get("/v1/models", adapter._handle_models)
    app.router.add_post("/v1/chat/completions", adapter._handle_chat_completions)
    return app


class TestPublicModelOwner:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("source", ["config", "env"])
    async def test_models_advertises_configured_public_owner(self, source, monkeypatch):
        """#963: owned_by is optional public branding (config extra or env); routes keep truth."""
        routes = {"fast": {"model": "openai/gpt-5", "provider": "openrouter"}}
        extra: dict = {"model_name": "teamshift", "model_routes": routes}
        if source == "config":
            extra["model_owner"] = "acme"
        else:
            monkeypatch.setenv("API_SERVER_MODEL_OWNER", "acme")
        adapter = APIServerAdapter(PlatformConfig(enabled=True, extra=extra))
        app = _create_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.get("/v1/models")
            assert resp.status == 200
            data = (await resp.json())["data"]
        assert [(m["id"], m["owned_by"], m["root"]) for m in data] == [
            ("teamshift", "acme", "teamshift"), ("fast", "acme", "openai/gpt-5")]
        assert adapter._model_routes == {"fast": {"model": "openai/gpt-5", "provider": "openrouter"}}

    @pytest.mark.asyncio
    async def test_blank_public_owner_keeps_hermes_default(self, monkeypatch):
        monkeypatch.setenv("API_SERVER_MODEL_OWNER", "   ")
        adapter = _make_adapter()
        app = _create_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            data = (await (await cli.get("/v1/models")).json())["data"]
        assert data[0]["owned_by"] == "hermes"

    @pytest.mark.asyncio
    async def test_public_owner_does_not_change_inference_route(self):
        """#963: branding is discovery metadata only; the alias still routes to its upstream."""
        routes = {"minimax-m2": {"model": "minimax/minimax-m1", "provider": "openrouter"}}
        adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={
            "model_routes": routes, "model_name": "teamshift", "model_owner": "acme"}))
        app = _create_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            with patch.object(adapter, "_run_agent", new_callable=AsyncMock) as mock_run:
                mock_run.return_value = (
                    {"final_response": "hi", "messages": [], "api_calls": 1},
                    {"input_tokens": 5, "output_tokens": 5, "total_tokens": 10},
                )
                resp = await cli.post("/v1/chat/completions", json={
                    "model": "minimax-m2",
                    "messages": [{"role": "user", "content": "hello"}],
                })
                assert resp.status == 200
                assert mock_run.call_args.kwargs.get("route") == {
                    "model": "minimax/minimax-m1", "provider": "openrouter",
                }
