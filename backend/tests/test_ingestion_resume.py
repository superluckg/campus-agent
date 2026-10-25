"""恢复入库页面时，读取持久化进度，不能重新启动任务。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import workspace_user, get_container
from app.api.routes.developer import router


def ingestion_client(job, items):
    repository = SimpleNamespace(
        current_job=AsyncMock(return_value=job),
        get_job=AsyncMock(),
        list_items=AsyncMock(return_value=items),
        mark_running=AsyncMock(),
    )
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_container] = lambda: SimpleNamespace(
        ingestion_repository=repository,
    )
    app.dependency_overrides[workspace_user] = lambda: SimpleNamespace(user_id=str(uuid4()))
    return app, repository


def test_current_job_without_history_is_null():
    app, repository = ingestion_client(None, [])
    with TestClient(app) as client:
        response = client.get("/api/developer/ingestion/current")
    assert response.status_code == 200
    assert response.json() is None
    repository.current_job.assert_awaited_once()
    repository.get_job.assert_not_awaited()
    repository.list_items.assert_not_awaited()
    repository.mark_running.assert_not_awaited()


@pytest.mark.parametrize(
    ("status", "completed", "failed"),
    [
        ("awaiting_confirmation", 0, 0),
        ("confirmed", 0, 0),
        ("running", 23, 0),
        ("completed", 60, 0),
        ("partial", 59, 1),
        ("failed", 5, 1),
    ],
)
def test_current_job_restores_saved_identity_progress_and_items(status, completed, failed):
    job_id, item_id = uuid4(), uuid4()
    job = SimpleNamespace(
        job_id=job_id, status=status, total_items=60,
        completed_items=completed, failed_items=failed,
    )
    item = SimpleNamespace(
        item_id=item_id, relative_path="入库测试.pdf", status="pending",
        detected_metadata={"title": "入库测试"}, confirmed_metadata={}, error_message=None,
    )
    app, repository = ingestion_client(job, [item])
    with TestClient(app) as client:
        response = client.get("/api/developer/ingestion/current")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == str(job_id)
    assert body["status"] == status
    assert body["total_items"] == 60
    assert body["completed_items"] == completed
    assert body["failed_items"] == failed
    assert body["items"][0]["item_id"] == str(item_id)
    assert body["items"][0]["relative_path"] == item.relative_path
    repository.current_job.assert_awaited_once()
    repository.list_items.assert_awaited_once_with(str(job_id))
    repository.get_job.assert_not_awaited()
    repository.mark_running.assert_not_awaited()


def test_current_job_rejects_remote_access_before_loading_progress():
    app, repository = ingestion_client(None, [])
    app.dependency_overrides.pop(workspace_user)
    app.dependency_overrides[get_container] = lambda: SimpleNamespace(
        settings=SimpleNamespace(environment="local"), workspace_user_id=str(uuid4()),
        ingestion_repository=repository,
    )
    with TestClient(app, base_url="http://localhost", client=("192.0.2.1", 50000)) as client:
        assert client.get("/api/developer/ingestion/current").status_code == 403
    repository.current_job.assert_not_awaited()
