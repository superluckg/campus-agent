"""数据库观察页面：只允许开发者读取固定目录中的业务表。"""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.dependencies import workspace_user, get_container
from app.container import AppContainer
from app.db.repositories.database_browser import DatabaseBrowserRepository

router = APIRouter(
    prefix="/developer/database", tags=["database"], dependencies=[Depends(workspace_user)]
)


def browser(
    container: Annotated[AppContainer, Depends(get_container)],
) -> DatabaseBrowserRepository:
    return DatabaseBrowserRepository(container.database)


BrowserDependency = Annotated[DatabaseBrowserRepository, Depends(browser)]


@router.get("")
async def catalog(repository: BrowserDependency):
    return await repository.catalog()


@router.get("/{schema}/{table}")
async def rows(
    schema: str,
    table: str,
    repository: BrowserDependency,
    page: int = Query(default=1, ge=1, le=100000),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=200),
    filters: str = Query(default="{}", max_length=6000),
    sort_by: str | None = None,
    descending: bool = True,
):
    try:
        values = json.loads(filters)
        if not isinstance(values, dict):
            raise ValueError("筛选条件必须是对象。")
        return await repository.rows(
            schema,
            table,
            page=page,
            page_size=page_size,
            filters=values,
            query=q.strip(),
            sort_by=sort_by,
            descending=descending,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{schema}/{table}/records/{key}")
async def record(schema: str, table: str, key: str, repository: BrowserDependency):
    try:
        return await repository.record(schema, table, key)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
