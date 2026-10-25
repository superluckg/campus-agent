"""统一评测的统一评测入口；全部接口仅开发者可访问。"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse

from app.evaluation.sources import EvaluationSources

from app.api.dependencies import workspace_user, get_container
from app.core.errors import EvaluationConflict
from app.evaluation.unified_scoring import parse_import
from app.schemas.unified_evaluation import (
    SUPPORTED_INTENTS,
    validation_message,
    UnifiedCase,
    UnifiedImport,
    UnifiedRunCreate,
)

class ChineseValidationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def localized(request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                return JSONResponse(status_code=422, content={"detail": validation_message(exc.errors())})
        return localized


router = APIRouter(
    prefix="/developer/evaluation",
    route_class=ChineseValidationRoute,
    tags=["unified-evaluation"],
    dependencies=[Depends(workspace_user)],
)


def failure(exc):
    return HTTPException(
        409
        if isinstance(exc, EvaluationConflict)
        else 404
        if isinstance(exc, LookupError)
        else 422,
        str(exc),
    )


@router.get("/catalog")
async def catalog(container=Depends(get_container)):
    return {
        "contract": "unified-evaluation",
        "intents": [
            {"value": intent.value, "label": item.display_name}
            for intent, item in container.intent_service.catalog.items()
            if intent.value in SUPPORTED_INTENTS
        ],
    }


def sources(container):
    return EvaluationSources(container.knowledge, container.web_cache, container.web_registry)


@router.get("/sources")
async def source_catalog(container=Depends(get_container)):
    return await sources(container).catalog()


@router.get("/sources/local/{document_id}")
async def local_preview(document_id: UUID, container=Depends(get_container)):
    try:
        return await sources(container).local(str(document_id))
    except LookupError as exc:
        raise failure(exc) from exc


@router.get("/sources/web/{source_key}")
async def web_preview(source_key: str, container=Depends(get_container)):
    try:
        return await sources(container).web(source_key)
    except LookupError as exc:
        raise failure(exc) from exc


@router.get("/cases")
async def cases(container=Depends(get_container)):
    return await container.unified_evaluations.cases()


@router.post("/cases", status_code=201)
async def create(body: UnifiedCase, container=Depends(get_container)):
    return await container.unified_evaluations.save([body])


@router.put("/cases/{case_id}")
async def edit(case_id: UUID, body: UnifiedCase, container=Depends(get_container)):
    try:
        return await container.unified_evaluations.save([body], str(case_id))
    except (LookupError, ValueError) as exc:
        raise failure(exc) from exc


@router.delete("/cases/{case_id}")
async def remove(case_id: UUID, container=Depends(get_container)):
    try:
        return await container.unified_evaluations.remove(str(case_id))
    except LookupError as exc:
        raise failure(exc) from exc


@router.post("/import/preview")
async def preview(body: UnifiedImport):
    values, errors = parse_import(body.text)
    return {"valid_count": len(values), "errors": errors}


@router.post("/import", status_code=201)
async def import_cases(body: UnifiedImport, container=Depends(get_container)):
    values, errors = parse_import(body.text)
    if errors:
        raise HTTPException(422, {"message": "整批未写入。", "errors": errors})
    return await container.unified_evaluations.save(values)


@router.post("/runs", status_code=202)
async def start(
    body: UnifiedRunCreate, user=Depends(workspace_user), container=Depends(get_container)
):
    try:
        run = await container.unified_evaluation.start(user.user_id, body)
        return {key: value for key, value in run.items() if key != "snapshot"}
    except (ValueError, LookupError, EvaluationConflict) as exc:
        raise failure(exc) from exc


@router.get("/current")
async def current(include_results: bool = True, container=Depends(get_container)):
    run = await container.unified_evaluations.current()
    if run is None:
        return {"run": None, "results": []}
    # 不向浏览器传输整个知识库快照或配置；结果包含各Case的独立快照。
    return {
        "run": {key: value for key, value in run.items() if key != "snapshot"},
        "results": await container.unified_evaluations.results(run["run_id"])
        if include_results
        else [],
    }


@router.post("/runs/{run_id}/stop")
async def stop(run_id: UUID, container=Depends(get_container)):
    try:
        run = await container.unified_evaluation.stop(str(run_id))
        return {key: value for key, value in run.items() if key != "snapshot"}
    except LookupError as exc:
        raise failure(exc) from exc
