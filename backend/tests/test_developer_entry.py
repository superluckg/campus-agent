"""本机工作台无需登录，并使用稳定的后端默认身份。"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url
from sqlalchemy.dialects import postgresql

from app.api.dependencies import get_container, workspace_user
from app.core.config import get_settings
from app.db.repositories.users import UserRepository


@pytest.fixture
def workbench():
    container = SimpleNamespace(
        settings=SimpleNamespace(environment="local"), workspace_user_id=str(uuid4()),
    )
    app = FastAPI()
    app.dependency_overrides[get_container] = lambda: container

    @app.get("/api/workspace-probe")
    async def probe(identity=Depends(workspace_user)):
        return {"user_id": identity.user_id}

    return app, container


def test_local_workbench_uses_backend_identity_without_credentials(workbench):
    app, container = workbench
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/workspace-probe").json() == {"user_id": container.workspace_user_id}
        response = client.get("/api/workspace-probe", params={"user_id": str(uuid4())})
        assert response.json() == {"user_id": container.workspace_user_id}


@pytest.mark.parametrize("environment", ["local", "development"])
def test_local_environments_can_open_workbench(workbench, environment):
    app, container = workbench
    container.settings.environment = environment
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/workspace-probe").status_code == 200


def test_workbench_is_disabled_outside_local_environments(workbench):
    app, container = workbench
    container.settings.environment = "production"
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/workspace-probe").status_code == 404


@pytest.mark.parametrize(("host", "peer", "headers"), [
    ("localhost", "192.0.2.1", {}),
    ("192.0.2.1", "127.0.0.1", {}),
    ("localhost", "127.0.0.1", {"Origin": "https://example.com"}),
    ("localhost", "127.0.0.1", {"Origin": "null"}),
    ("localhost", "127.0.0.1", {"Origin": "http://["}),
    ("localhost", "127.0.0.1", {"X-Forwarded-For": "192.0.2.1"}),
    ("localhost", "127.0.0.1", {"X-Forwarded-For": "127.0.0.1, 192.0.2.1"}),
    ("localhost", "127.0.0.1", {"X-Forwarded-For": ""}),
])
def test_workbench_rejects_remote_or_malformed_requests(workbench, host, peer, headers):
    app, _ = workbench
    with TestClient(app, base_url=f"http://{host}", client=(peer, 50000)) as client:
        assert client.get("/api/workspace-probe", headers=headers).status_code == 403


def test_workbench_waits_for_default_identity(workbench):
    app, container = workbench
    container.workspace_user_id = None
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/workspace-probe").status_code == 503


@pytest.mark.asyncio
@pytest.mark.parametrize("results", ["existing", "created", "concurrent"])
async def test_workspace_identity_reuses_existing_id_and_handles_concurrent_creation(results):
    identifier = uuid4()
    values = {
        "existing": [identifier], "created": [None, identifier],
        "concurrent": [None, None, identifier],
    }[results]
    session = SimpleNamespace(scalar=AsyncMock(side_effect=values))

    @asynccontextmanager
    async def database_session():
        yield session

    repository = UserRepository(SimpleNamespace(session=database_session))
    assert await repository.ensure_workspace_user() == str(identifier)
    assert session.scalar.await_count == len(values)
    if results != "existing":
        statement = session.scalar.await_args_list[1].args[0]
        compiled = statement.compile(dialect=postgresql.dialect())
        assert "ON CONFLICT (username) DO NOTHING" in str(compiled)
        assert compiled.params["password_hash"] == "!"


@pytest.mark.parametrize("password", [None, "", "existing@database:password"])
def test_database_password_defaults_only_when_unconfigured(monkeypatch, password):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-key")
    if password is None:
        monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("POSTGRES_PASSWORD", password)
    get_settings.cache_clear()
    try:
        settings = get_settings(require_secrets=True)
        assert make_url(settings.database_url).password == (password or "qust_local_dev")
    finally:
        get_settings.cache_clear()


def test_api_exposes_no_sign_in_routes_or_security_scheme():
    from app.main import app

    schema = app.openapi()
    assert not any(path.startswith("/api/auth/") for path in schema["paths"])
    assert not schema.get("components", {}).get("securitySchemes")
