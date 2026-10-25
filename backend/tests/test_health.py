"""本机模型就绪检查不受系统代理影响。"""

from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.routes.health import ready


@pytest.mark.asyncio
async def test_ollama_probe_connects_directly_when_process_proxy_is_unavailable(monkeypatch):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            content = b'{"models": []}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, *_):
            pass

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")

    @asynccontextmanager
    async def session():
        yield SimpleNamespace(execute=AsyncMock())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        container = SimpleNamespace(
            database=SimpleNamespace(session=session),
            redis=SimpleNamespace(client=SimpleNamespace(ping=AsyncMock(return_value=True))),
            settings=SimpleNamespace(ollama_base_url="http://127.0.0.1:%d" % server.server_port),
            retrieval_config={"route_top_k": 20, "rrf_candidate_k": 10},
            intent_embedding_warmup={"status": "ready"},
            agent_recursion_limit=50,
        )
        result = await ready(container)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result.status == "ready"
    assert result.details["ollama"] is True
    assert requests == ["/api/tags"]
