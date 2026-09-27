"""Task, claim, retry and report HTTP endpoints."""

import re

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response
from fastapi.responses import PlainTextResponse

from ..export import to_json, to_markdown
from ..pipeline import Pipeline
from ..schemas import (AddClaimRequest, ClaimListResponse, CreateTaskRequest, CreateTaskResponse,
                       EditClaimRequest, MergeClaimsRequest, SplitClaimRequest, TaskStatus, UndoReviewRequest)
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
    existing = pipeline.storage.get_claim(claim_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="声明不存在")
    claims, error = pipeline.storage.review_change(existing.task_id, "edit", request.reviewer,
                                                    claim_ids=[claim_id], texts=[request.normalized_claim])
    if failure := _review_error(error):
        return failure
    return claims[0]


@router.delete("/claims/{claim_id}", status_code=204)
async def delete_claim(claim_id: str, pipeline: PipelineDependency, reviewer: str = Query("未署名", min_length=1, max_length=40)):
    existing = pipeline.storage.get_claim(claim_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="声明不存在")
    _, error = pipeline.storage.review_change(existing.task_id, "delete", reviewer, claim_ids=[claim_id])
    if failure := _review_error(error):
        return failure
    return Response(status_code=204)


def _review_error(error: str | None) -> Response | None:
    if error is None:
        return
    if error == "NOT_FOUND":
        raise HTTPException(status_code=404, detail="任务或声明不存在")
    messages = {
        "TASK_BUSY": "任务处理中，暂不能调整声明",
        "INVALID_RANGE": "所选原文范围无效、与其他声明重叠，或两条声明不相邻",
        "INVALID_REVIEW": "人工复核内容不完整",
        "CLAIM_LIMIT": "声明数量已达到本任务上限",
        "UNDO_ORDER": "请从最近一次未撤销的操作开始撤销",
        "STATE_CHANGED": "声明在该操作后发生了变化，无法安全撤销",
    }
    return safe_error(error, messages.get(error, "操作失败"), 409)


@router.post("/tasks/{task_id}/claims")
async def add_claim(task_id: str, request: AddClaimRequest, pipeline: PipelineDependency):
    claims, error = pipeline.storage.review_change(task_id, "add", request.reviewer,
        char_start=request.char_start, char_end=request.char_end, texts=[request.normalized_claim])
    if failure := _review_error(error):
        return failure
    return claims[0]


@router.post("/claims/{claim_id}/split")
async def split_claim(claim_id: str, request: SplitClaimRequest, pipeline: PipelineDependency):
    existing = pipeline.storage.get_claim(claim_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="声明不存在")
    claims, error = pipeline.storage.review_change(existing.task_id, "split", request.reviewer,
        claim_ids=[claim_id], split_at=request.split_at, texts=[request.first_claim, request.second_claim])
    if failure := _review_error(error):
        return failure
    return claims


@router.post("/tasks/{task_id}/claims/merge")
async def merge_claims(task_id: str, request: MergeClaimsRequest, pipeline: PipelineDependency):
    claims, error = pipeline.storage.review_change(task_id, "merge", request.reviewer,
        claim_ids=request.claim_ids, texts=[request.normalized_claim])
    if failure := _review_error(error):
        return failure
    return claims[0]


@router.get("/tasks/{task_id}/review-history")
async def review_history(task_id: str, pipeline: PipelineDependency):
    events = pipeline.storage.list_review_events(task_id)
    if events is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"items": events}


@router.post("/tasks/{task_id}/review-history/{event_id}/undo")
async def undo_review(task_id: str, event_id: str, request: UndoReviewRequest, pipeline: PipelineDependency):
    error = pipeline.storage.undo_review_event(task_id, event_id, request.reviewer)
    if failure := _review_error(error):
        return failure
    return {"undone": True}


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
