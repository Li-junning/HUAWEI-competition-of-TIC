"""Application-owned knowledge library endpoints; no arbitrary URL fetching."""

from fastapi import APIRouter, Query
from fastapi.responses import Response

from .dependencies import PipelineDependency
from .errors import safe_error
from ..knowledge import KnowledgeError
from ..knowledge_schemas import KnowledgeImport, KnowledgeSearch

router = APIRouter(prefix="/knowledge")


@router.get("/status")
def status(pipeline: PipelineDependency):
    return pipeline.knowledge.status()


@router.get("/documents")
def documents(pipeline: PipelineDependency, offset: int = Query(default=0, ge=0), limit: int = Query(default=30, ge=1, le=200)):
    return pipeline.knowledge.list_documents(offset, limit)


@router.post("/documents", status_code=201)
def import_document(body: KnowledgeImport, pipeline: PipelineDependency):
    try:
        return pipeline.knowledge.import_document(body)
    except KnowledgeError as exc:
        return safe_error(exc.code, exc.message, exc.status)


@router.get("/documents/{document_id}")
def document(document_id: str, pipeline: PipelineDependency):
    try:
        return pipeline.knowledge.get_document(document_id)
    except KnowledgeError as exc:
        return safe_error(exc.code, exc.message, exc.status)


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: str, pipeline: PipelineDependency):
    try:
        pipeline.knowledge.delete_document(document_id)
        return Response(status_code=204)
    except KnowledgeError as exc:
        return safe_error(exc.code, exc.message, exc.status)


@router.post("/search")
def search(body: KnowledgeSearch, pipeline: PipelineDependency):
    hits = pipeline.knowledge.search(body.query, tag=body.tag, limit=body.limit)
    return {"items": hits, "status": pipeline.knowledge.status(),
            "score_note": "检索分数仅用于排序，不是事实可信度或正确概率。"}


@router.post("/reindex")
def reindex(pipeline: PipelineDependency):
    try:
        return pipeline.knowledge.reindex()
    except KnowledgeError as exc:
        return safe_error(exc.code, exc.message, exc.status)
