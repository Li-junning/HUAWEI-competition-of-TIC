"""Task, claim, retry and report HTTP endpoints."""

import re

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse

from ..export import to_json, to_markdown
from ..pipeline import Pipeline
from ..schemas import ClaimListResponse, CreateTaskRequest, CreateTaskResponse, EditClaimRequest, TaskStatus
from .dependencies import PipelineDependency
from .errors import safe_error

router = APIRouter()


@router.post("/tasks", response_model=CreateTaskResponse, status_code=202)
async def create_task(request: CreateTaskRequest, background_tasks: BackgroundTasks, pipeline: PipelineDependency):
    if len(request.input_text) > pipeline.settings.max_input_chars:
        return safe_error("INPUT_TOO_LARGE", "输入文本超过 20000 字符", 422)
    task = pipeline.create(request.input_text, request.claim_limit)
    # BackgroundTasks is the production default, while tests can call run_sync directly.
    background_tasks.add_task(pipeline.run_sync, task.task_id)
    return CreateTaskResponse(task_id=task.task_id, status=TaskStatus.CREATED)


def _load_summary(task_id: str, pipeline: Pipeline):
    summary = pipeline.get_summary(task_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return summary


@router.get("/tasks/{task_id}")
async def get_task(task_id: str, pipeline: PipelineDependency):
    if not re.fullmatch(r"t_[0-9a-fA-F-]{36}", task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    return _load_summary(task_id, pipeline)


@router.get("/tasks/{task_id}/claims", response_model=ClaimListResponse)
async def get_task_claims(task_id: str, pipeline: PipelineDependency, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
    if not pipeline.storage.get_task(task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    claims, total = pipeline.storage.list_claims(task_id, offset, limit)
    return ClaimListResponse(items=claims, total=total, offset=offset, limit=limit)


@router.get("/tasks/{task_id}/input")
async def get_task_input(task_id: str, response: Response, pipeline: PipelineDependency):
    """Return stored text so UTF-16 claim offsets survive reloads."""
    if not re.fullmatch(r"t_[0-9a-fA-F-]{36}", task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    record = pipeline.storage.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    response.headers["Cache-Control"] = "no-store"
    return {"task_id": task_id, "input_text": record[0]["input_text"]}


@router.get("/claims/{claim_id}")
async def get_claim(claim_id: str, pipeline: PipelineDependency):
    claim = pipeline.storage.get_claim(claim_id)
    if not claim:
        raise HTTPException(status_code=404, detail="声明不存在")
    return claim


@router.patch("/claims/{claim_id}")
async def edit_claim(claim_id: str, request: EditClaimRequest, pipeline: PipelineDependency):
    claim, error = pipeline.storage.edit_claim(claim_id, request.normalized_claim)
    if error == "NOT_FOUND":
        raise HTTPException(status_code=404, detail="声明不存在")
    if error == "TASK_BUSY":
        return safe_error("TASK_BUSY", "任务处理中，暂不能修改声明", 409)
    return claim


@router.delete("/claims/{claim_id}", status_code=204)
async def delete_claim(claim_id: str, pipeline: PipelineDependency):
    deleted, error = pipeline.storage.delete_claim(claim_id)
    if error == "NOT_FOUND":
        raise HTTPException(status_code=404, detail="声明不存在")
    if error == "TASK_BUSY":
        return safe_error("TASK_BUSY", "任务处理中，暂不能删除声明", 409)
    return Response(status_code=204)


@router.post("/claims/{claim_id}/retry", status_code=202)
async def retry_claim(claim_id: str, background_tasks: BackgroundTasks, pipeline: PipelineDependency):
    claim, error = pipeline.storage.begin_claim_retry(claim_id, pipeline.settings.max_retries)
    if error == "NOT_FOUND":
        raise HTTPException(status_code=404, detail="声明不存在")
    if error == "RETRY_LIMIT":
        return safe_error("RETRY_LIMIT", "该声明已达到最大重试次数", 409)
    if error == "RETRY_NOT_ALLOWED":
        return safe_error("RETRY_NOT_ALLOWED", "只有证据不足或技术失败的声明可重试", 409)
    if error == "TASK_BUSY":
        return safe_error("TASK_BUSY", "该任务仍在处理中，请等待完成后再重试", 409)
    assert claim is not None
    background_tasks.add_task(pipeline.retry_sync, claim_id)
    return {"claim_id": claim_id, "state": "retrieving", "retry_count": claim.retry_count}


@router.get("/tasks/{task_id}/export")
async def export_task(task_id: str, pipeline: PipelineDependency, format: str = Query("json", pattern="^(json|md)$")):
    summary = _load_summary(task_id, pipeline)
    if summary.status not in {TaskStatus.SUCCEEDED, TaskStatus.PARTIAL}:
        return safe_error("TASK_NOT_READY", "任务尚未完成，暂不可导出", 409)
    claims, _ = pipeline.storage.list_claims(task_id, 0, 1000)
    if format == "md":
        return PlainTextResponse(to_markdown(summary, claims), media_type="text/markdown; charset=utf-8")
    return PlainTextResponse(to_json(summary, claims), media_type="application/json; charset=utf-8")
