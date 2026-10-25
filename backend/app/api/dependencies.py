"""本机工作台访问限制与默认数据归属。"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status

from app.api.schemas import WorkspaceIdentity
from app.container import AppContainer


def get_container(request: Request) -> AppContainer:
    return request.app.state.container


async def workspace_user(
    request: Request,
    container: AppContainer = Depends(get_container),
) -> WorkspaceIdentity:
    if container.settings.environment not in {"local", "development"}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工作台仅供本机使用。")
    loopback = {"localhost", "127.0.0.1", "::1", "::ffff:127.0.0.1"}
    origin = request.headers.get("origin")
    forwarded = request.headers.get("x-forwarded-for")
    try:
        origin_host = urlsplit(origin).hostname if origin is not None else None
        host = request.url.hostname
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="工作台仅接受本机请求。") from exc
    if (
        request.client is None
        or request.client.host not in loopback
        or host not in loopback
        or (origin is not None and origin_host not in loopback)
        or (forwarded is not None and forwarded.split(",")[-1].strip() not in loopback)
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="工作台仅接受本机请求。")
    if container.workspace_user_id is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="工作台正在准备默认数据归属。")
    return WorkspaceIdentity(user_id=container.workspace_user_id)
