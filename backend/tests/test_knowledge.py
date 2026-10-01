"""End-to-end library provenance, retrieval, persistence, and failure semantics."""

import base64
import io

import pytest
from fastapi.testclient import TestClient

from app.application import create_app
from app.config import Settings
from app.knowledge import KnowledgeBase
from app.knowledge_retrieval import KnowledgeBackedRetriever, merge_evidence
from app.knowledge_schemas import KnowledgeImport
from app.pipeline import Pipeline
from app.providers.base import SearchProviderError
from app.schemas import Claim, EvidenceCluster, EvidenceItem
from app.storage import Storage


@pytest.fixture
def library(tmp_path):
    storage = Storage(tmp_path / "library.db")
    yield KnowledgeBase(storage)
    storage.close()


def test_api_import_search_verify_delete_preserves_report_snapshot(tmp_path):
    database = tmp_path / "api.db"
    app = create_app(Settings(database_path=database))
    with TestClient(app) as client:
        created = client.post("/api/knowledge/documents", json={
            "title": "项目技术手册", "content": "海川公司的研发中心位于南京。", "tags": ["技术"],
            "publisher": "项目资料", "published_at": "2024-02-01",
        })
        assert created.status_code == 201
        doc = created.json()
        assert doc["published_at"] == "2024-02-01"
        duplicate = client.post("/api/knowledge/documents", json={"title": "改名的手册", "content": "海川公司的研发中心位于南京。"})
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "KB_DUPLICATE"
        search = client.post("/api/knowledge/search", json={"query": "海川公司研发中心", "tag": "技术"}).json()
        hit = search["items"][0]
        assert hit["excerpt"] == "海川公司的研发中心位于南京。"
        assert hit["matched_by"] == ["keyword"]
        task_id = client.post("/api/tasks", json={"input_text": "海川公司的研发中心位于南京。"}).json()["task_id"]
        claims = client.get(f"/api/tasks/{task_id}/claims").json()["items"]
        claim_id = claims[0]["claim_id"]
        detail = client.get(f"/api/claims/{claim_id}").json()
        assert detail["label"] == "evidence_insufficient"  # Hit != proof.
        evidence = detail["evidence_clusters"][0]["items"][0]
        assert evidence["source_type"] == "knowledge"
        assert evidence["knowledge_document_id"] == doc["document_id"]
        assert evidence["content_hash"] == doc["content_hash"]
        assert evidence["published_at"].startswith("2024-02-01")
        assert "知识库出处" in client.get(f"/api/tasks/{task_id}/export?format=md").text
        assert client.delete(f"/api/knowledge/documents/{doc['document_id']}").status_code == 204
        assert client.post("/api/knowledge/search", json={"query": "海川公司"}).json()["items"] == []
        assert client.get(f"/api/knowledge/documents/{doc['document_id']}").status_code == 404
        assert client.get(f"/api/claims/{claim_id}").json()["evidence_clusters"] == detail["evidence_clusters"]
    with TestClient(app) as client:
        assert client.get(f"/api/claims/{claim_id}").json()["evidence_clusters"] == detail["evidence_clusters"]


def test_documents_and_keyword_index_survive_restart(tmp_path):
    path = tmp_path / "restart.db"
    first = Storage(path)
    KnowledgeBase(first).import_document(KnowledgeImport(title="中文资料", content="鲸属于哺乳动物，而非鱼类。"))
    first.close()
    second = Storage(path)
    try:
        assert KnowledgeBase(second).search("鲸 哺乳动物")[0].excerpt == "鲸属于哺乳动物，而非鱼类。"
    finally:
        second.close()


def test_source_slices_are_exact_and_late_facts_are_retrievable(library):
    text = "无关介绍。" * 220 + "海川公司的研发中心位于南京。" + "附录说明。" * 30
    doc = library.import_document(KnowledgeImport(title="长文", content=text))
    hits = library.search("海川公司研发中心")
    assert hits
    assert any("研发中心位于南京" in hit.excerpt for hit in hits)
    assert all(text[hit.char_start:hit.char_end] == hit.excerpt for hit in hits)
    assert library.get_document(doc.document_id)["pages"] == [text]


def test_retrieval_retains_conflicting_answers_and_tag_filter(library):
    library.import_document(KnowledgeImport(title="甲资料", content="海川公司的研发中心位于南京。", tags=["技术"]))
    library.import_document(KnowledgeImport(title="乙资料", content="海川公司的研发中心位于杭州。", tags=["新闻"]))
    assert len(library.search("海川公司 研发中心")) == 2
    hits = library.search("海川公司 研发中心", tag="技术")
    assert len(hits) == 1 and "南京" in hits[0].excerpt
    assert library.search("完全不存在的术语qzxw") == []
    library.search('" OR * ) --')  # User input never becomes raw FTS syntax.


class ConceptEmbedder:
    name = "test-concepts-v1"
    model = object()
    message = "fixture embeddings"

    def encode(self, texts, **kwargs):
        return [[1., 0.] if any(word in text for word in ("猫", "feline")) else [0., 1.] for text in texts]


def test_semantic_search_matches_without_shared_words_and_reindex(library):
    doc = library.import_document(KnowledgeImport(title="动物资料", content="猫喜欢晒太阳。"))
    assert library.search("feline") == []
    library.embedder = ConceptEmbedder()
    assert library.status()["indexed_chunks"] == 0
    assert library.reindex() == {"indexed": 1, "remaining": 0}
    hit = library.search("feline")[0]
    assert hit.document_id == doc.document_id and hit.matched_by == ["semantic"]
    assert library.search("鲸鱼") == []


def test_failed_embeddings_preserve_original_and_keyword_search(library):
    class Broken(ConceptEmbedder):
        def encode(self, texts, **kwargs):
            raise RuntimeError("private upstream detail")
    library.embedder = Broken()
    document = library.import_document(KnowledgeImport(title="资料", content="猫喜欢晒太阳。"))
    assert document.indexed_chunks == 0
    assert library.search("晒太阳")[0].excerpt == "猫喜欢晒太阳。"
    assert "private upstream detail" not in library.status()["message"]


def _text_pdf():
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")}))
    for text in ("First page background.", "The laboratory is in Nanjing."):
        page = writer.add_blank_page(width=300, height=300)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 20 200 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO(); writer.write(output)
    return output.getvalue()


def test_pdf_import_retains_page_numbers(library):
    document = library.import_document(KnowledgeImport(title="PDF", filename="document.pdf",
        file_base64=base64.b64encode(_text_pdf()).decode()))
    hit = library.search("laboratory Nanjing")[0]
    assert document.page_count == 2 and hit.page == 2
    page = library.get_document(document.document_id)["pages"][1]
    assert page[hit.char_start:hit.char_end] == hit.excerpt


@pytest.mark.parametrize("body,status,code", [
    ({"title": "资料", "content": "   "}, 400, "KB_TEXT_EMPTY"),
    ({"title": "资料", "content": "正文", "source_url": "javascript:alert(1)"}, 422, "REQUEST_INVALID"),
    ({"title": "资料", "content": "正文", "source_url": "http://127.0.0.1/secret"}, 422, "REQUEST_INVALID"),
    ({"title": "资料", "filename": "d.txt", "file_base64": "!!!"}, 400, "KB_FILE_INVALID"),
    ({"title": "资料", "filename": "d.exe", "file_base64": "dGV4dA=="}, 400, "KB_FILE_TYPE"),
    ({"title": "资料", "content": "正文", "file_base64": "dGV4dA==", "filename": "d.txt"}, 422, "REQUEST_INVALID"),
    ({"title": "资料", "content": "正文", "published_at": "不明"}, 422, "REQUEST_INVALID"),
])
def test_import_rejects_invalid_input_without_persisting(tmp_path, body, status, code):
    with TestClient(create_app(Settings(database_path=tmp_path / "invalid.db"))) as client:
        response = client.post("/api/knowledge/documents", json=body)
        assert response.status_code == status
        assert response.json()["error"]["code"] == code
        assert client.get("/api/knowledge/status").json()["document_count"] == 0


def test_library_request_body_is_bounded_before_parsing(tmp_path):
    with TestClient(create_app(Settings(database_path=tmp_path / "large.db"))) as client:
        response = client.post("/api/knowledge/documents", content=b"x" * (4 * 1024 * 1024 + 1))
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_web_failure_with_local_evidence_remains_partial(tmp_path):
    class BrokenWeb:
        provider = "tavily"
        def retrieve(self, claim, **kwargs):
            raise SearchProviderError("SEARCH_CONNECTION")
    storage = Storage(tmp_path / "partial.db")
    try:
        pipeline = Pipeline(storage, Settings(database_path=tmp_path / "partial.db"))
        pipeline.knowledge.import_document(KnowledgeImport(title="资料", content="海川公司的研发中心位于南京。"))
        pipeline.retriever = KnowledgeBackedRetriever(pipeline.knowledge, BrokenWeb())
        task = pipeline.create("海川公司的研发中心位于南京。", 15)
        result = pipeline.run_sync(task.task_id)
        assert result.status.value == "partial" and result.score is None
        claim = storage.list_claims(task.task_id)[0][0]
        assert claim.evidence_clusters and claim.retrieval_warnings
        assert claim.label.value == "evidence_insufficient"
    finally:
        storage.close()


def test_same_site_and_duplicate_local_web_material_count_once():
    local = EvidenceItem(evidence_id="kc_1", url="https://example.com/doc", excerpt="相同事实正文", source_type="knowledge")
    web = EvidenceItem(evidence_id="e_1", url="https://news.example.com/doc", excerpt="相同事实正文")
    clusters = merge_evidence([EvidenceCluster(cluster_id="a", items=[local]), EvidenceCluster(cluster_id="b", items=[web])])
    assert len(clusters) == 1
    # Even uploads without addresses cannot fabricate multiple independent sources.
    unknown = merge_evidence([EvidenceCluster(cluster_id="a", items=[EvidenceItem(evidence_id="a", excerpt="甲")]),
                              EvidenceCluster(cluster_id="b", items=[EvidenceItem(evidence_id="b", excerpt="乙")])])
    assert len(unknown) == 1
