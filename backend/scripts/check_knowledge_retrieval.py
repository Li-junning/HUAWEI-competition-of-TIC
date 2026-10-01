"""Real cached-model smoke check; synthetic documents stay in an isolated database."""

import json
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.knowledge import KnowledgeBase, LocalEmbedder
from app.knowledge_schemas import KnowledgeImport
from app.storage import Storage


def main():
    backend = Path(__file__).resolve().parents[1]
    embedder = LocalEmbedder(backend / "data" / "knowledge-models")
    if embedder.model is None:
        raise RuntimeError("Prepare the local knowledge model before running this check.")
    path = backend / "data" / "knowledge-smoke" / f"{uuid4().hex}.db"
    storage = Storage(path)
    try:
        library = KnowledgeBase(storage, embedder)
        library.import_document(KnowledgeImport(title="合成资料 A", content="节能措施包括关闭闲置设备，采用高效照明，并按需调整空调温度。"))
        library.import_document(KnowledgeImport(title="合成资料 B", content="小林每周六前往图书馆阅读文学作品。"))
        library.import_document(KnowledgeImport(title="合成资料 C", content="猫是哺乳动物，以肺呼吸，并以乳汁哺育幼崽。"))
        hits = library.search("如何减少电力消耗？")
        assert hits and hits[0].title == "合成资料 A"
        assert "semantic" in hits[0].matched_by and "keyword" not in hits[0].matched_by
        second_hits = library.search("猫咪用什么器官换气？")
        assert second_hits and second_hits[0].title == "合成资料 C"
        assert second_hits[0].matched_by == ["semantic"]
        assert library.search("hello qzxwv") == []
        document = library.get_document(hits[0].document_id)
        assert document["pages"][0][hits[0].char_start:hits[0].char_end] == hits[0].excerpt
        report = {"passed": True, "model": embedder.name, "status": library.status(),
                  "query": "如何减少电力消耗？", "hits": [hit.model_dump(mode="json") for hit in hits],
                  "second_query": "猫咪用什么器官换气？",
                  "second_hits": [hit.model_dump(mode="json") for hit in second_hits],
                  "scope": "Two synthetic paraphrases, one unrelated query, and exact source slices; not an accuracy benchmark."}
        output = backend / "data" / "knowledge-smoke" / "report.json"
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"passed": True, "model": embedder.name, "indexed_chunks": library.status()["indexed_chunks"],
                          "top_match": hits[0].matched_by, "report": str(output)}, ensure_ascii=True))
    finally:
        storage.close()


if __name__ == "__main__":
    main()
