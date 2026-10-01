"""Small local RAG index: SQLite FTS5, persisted vectors, and exact source slices."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import logging
import math
import os
import threading
import time
import zipfile
from pathlib import Path

from .knowledge_schemas import KnowledgeDocument, KnowledgeHit, KnowledgeImport
from .schemas import now_utc, new_id
from .text_processing import query_terms

logger = logging.getLogger("verifier")
DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"
MAX_DOCUMENTS = 200
MAX_CHUNKS = 5000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_DOCX_EXPANDED_BYTES = 32 * 1024 * 1024
MAX_DOCX_BODY_BYTES = 8 * 1024 * 1024


class KnowledgeError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


class LocalEmbedder:
    """Only read cached model weights at runtime; never download inside requests."""

    def __init__(self, cache_dir: Path):
        self.name = os.getenv("VERIFIER_KB_MODEL", DEFAULT_MODEL)
        self.model = None
        self.message = "中文语义模型尚未准备，当前使用关键词检索。"
        if os.getenv("VERIFIER_KB_EMBEDDINGS", "auto").lower() == "off":
            self.message = "语义检索已关闭，当前使用关键词检索。"
            return
        if not cache_dir.exists():
            return
        try:
            from fastembed import TextEmbedding
            self.model = TextEmbedding(model_name=self.name, cache_dir=str(cache_dir),
                                       local_files_only=True, threads=2)
            self.message = "中文语义模型已就绪，资料和检索文字在本机生成向量。"
        except Exception as exc:
            logger.warning("knowledge model unavailable: exception_type=%s", type(exc).__name__)

    def encode(self, texts: list[str], *, query: bool = False):
        method = self.model.query_embed if query else self.model.passage_embed
        return [vector.tolist() for vector in method(texts, batch_size=16)]


def _tokens(text: str) -> str:
    # The existing tokenizer emits CJK bigrams, avoiding FTS5's default treatment
    # of an entire Chinese sentence as one token. No user FTS syntax is accepted.
    return " ".join(query_terms(text))


def _normalized(vector):
    values = [float(v) for v in vector]
    norm = math.sqrt(sum(v * v for v in values))
    if not values or len(values) > 4096 or not math.isfinite(norm) or norm <= 0:
        raise ValueError("invalid embedding")
    return [v / norm for v in values]


def _read_docx(data: bytes) -> str:
    """Read body paragraphs and table cells in order, without Office automation."""
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError:
        raise KnowledgeError("KB_WORD_UNAVAILABLE", "Word 解析依赖尚未安装，请安装后端依赖并重启服务。", 503) from None

    try:
        # Bound decompression before python-docx opens the entire package.
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 2048 or sum(entry.file_size for entry in entries) > MAX_DOCX_EXPANDED_BYTES:
                raise KnowledgeError("KB_WORD_LIMIT", "Word 文档解压后过大，请精简或拆分资料。", 413)
            if archive.getinfo("word/document.xml").file_size > MAX_DOCX_BODY_BYTES:
                raise KnowledgeError("KB_WORD_LIMIT", "Word 正文结构过大，请精简或拆分资料。", 413)
            if any(entry.flag_bits & 1 for entry in entries):
                raise KnowledgeError("KB_WORD_ENCRYPTED", "请先取消 Word 文档的密码保护再导入。")
        document = Document(io.BytesIO(data))

        def blocks(container, depth=0):
            if depth > 32:
                raise KnowledgeError("KB_WORD_LIMIT", "Word 表格嵌套过深，请简化后导入。", 413)
            for block in container.iter_inner_content():
                if isinstance(block, Paragraph):
                    yield block.text
                elif isinstance(block, Table):
                    seen_cells = set()
                    for row in block.rows:
                        cells = []
                        for cell in row.cells:
                            # Merged cells can occupy multiple grid positions.
                            if cell._tc in seen_cells:
                                continue
                            seen_cells.add(cell._tc)
                            cells.append("\n".join(blocks(cell, depth + 1)))
                        yield "\t".join(cells)

        parts = []
        count = 0
        for text in blocks(document):
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            count += len(text) + bool(parts)
            if count > 100_000:
                raise KnowledgeError("KB_TEXT_LIMIT", "提取正文超过 100,000 字符，请拆分资料。", 413)
            parts.append(text)
        return "\n".join(parts)
    except KnowledgeError:
        raise
    except Exception:
        raise KnowledgeError("KB_WORD_INVALID", "无法读取此 Word 文档，请取消密码保护并重新保存为 .docx。") from None


def _read_pages(request: KnowledgeImport) -> list[str]:
    if request.content is not None:
        pages = [request.content.replace("\r\n", "\n").replace("\r", "\n")]
    else:
        try:
            data = base64.b64decode(request.file_base64, validate=True)
        except (ValueError, binascii.Error):
            raise KnowledgeError("KB_FILE_INVALID", "文件编码无效。") from None
        if len(data) > MAX_FILE_BYTES:
            raise KnowledgeError("KB_FILE_TOO_LARGE", "单个文件最多 2 MiB。", 413)
        suffix = Path(request.filename).suffix.lower()
        if suffix in {".txt", ".md"}:
            try:
                text = data.decode("utf-8-sig")
            except UnicodeDecodeError:
                raise KnowledgeError("KB_ENCODING", "TXT / Markdown 请使用 UTF-8 编码。") from None
            pages = [text.replace("\r\n", "\n").replace("\r", "\n")]
        elif suffix == ".pdf":
            try:
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(data))
                if reader.is_encrypted:
                    raise KnowledgeError("KB_PDF_ENCRYPTED", "请先解密 PDF 再导入。")
                if len(reader.pages) > 100:
                    raise KnowledgeError("KB_PDF_LIMIT", "单个 PDF 最多 100 页，请拆分后导入。")
                pages = []
                for page in reader.pages:
                    pages.append((page.extract_text() or "").replace("\r\n", "\n"))
                    if sum(map(len, pages)) > 100_000:
                        raise KnowledgeError("KB_TEXT_LIMIT", "提取正文超过 100,000 字符，请拆分资料。", 413)
            except KnowledgeError:
                raise
            except ImportError:
                raise KnowledgeError("KB_PDF_UNAVAILABLE", "请先安装后端 PDF 依赖 pypdf。", 503) from None
            except Exception:
                raise KnowledgeError("KB_PDF_INVALID", "无法读取此 PDF，请检查文件或粘贴正文。") from None
        elif suffix == ".docx":
            pages = [_read_docx(data)]
        elif suffix == ".doc":
            raise KnowledgeError("KB_WORD_LEGACY", "旧版 .doc 请先在 Word 中另存为 .docx 后导入。")
        else:
            raise KnowledgeError("KB_FILE_TYPE", "支持 Word（.docx）、TXT、Markdown 和可提取文字的 PDF。")
    if sum(map(len, pages)) > 100_000:
        raise KnowledgeError("KB_TEXT_LIMIT", "单份资料正文最多 100,000 字符。", 413)
    if not any(page.strip() for page in pages):
        raise KnowledgeError("KB_TEXT_EMPTY", "未提取到正文；图片或扫描件中的文字需要先进行 OCR。")
    return pages


def _split_pages(pages: list[str], *, paginated: bool):
    """Overlapping slices preserve page-local Python character offsets verbatim."""
    records = []
    for page_number, text in enumerate(pages, 1):
        start = 0
        while start < len(text):
            end = min(start + 420, len(text))
            if end < len(text):
                boundary = max(text.rfind(mark, start + 260, end) for mark in ("\n", "。", "！", "？", ". "))
                if boundary >= start + 260:
                    end = boundary + 1
            if text[start:end].strip():
                records.append({"page": page_number if paginated else None, "char_start": start,
                                "char_end": end, "excerpt": text[start:end]})
            if end == len(text):
                break
            start = end - 60
    return records


class KnowledgeBase:
    def __init__(self, storage, embedder=None):
        self.storage = storage
        cache = Path(os.getenv("VERIFIER_KB_CACHE_DIR", str(Path(storage.path).parent / "knowledge-models")))
        self.embedder = embedder if embedder is not None else LocalEmbedder(cache)
        self._model_lock = threading.Lock()
        self._index_warning = None
        with storage._lock:
            storage.conn.executescript("""
                CREATE TABLE IF NOT EXISTS kb_documents (
                    document_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL UNIQUE,
                    metadata TEXT NOT NULL, pages TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS kb_chunks (
                    chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL,
                    data TEXT NOT NULL, embedding TEXT, embedding_model TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_kb_chunks_document ON kb_chunks(document_id);
                CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(chunk_id UNINDEXED, terms);
            """)
            storage.conn.commit()

    @property
    def semantic_ready(self):
        return self.embedder.model is not None

    def status(self):
        with self.storage._lock:
            documents = self.storage.conn.execute("SELECT count(*) FROM kb_documents").fetchone()[0]
            chunks = self.storage.conn.execute("SELECT count(*) FROM kb_chunks").fetchone()[0]
            indexed = self.storage.conn.execute(
                "SELECT count(*) FROM kb_chunks WHERE embedding_model=? AND embedding IS NOT NULL",
                (self.embedder.name,),
            ).fetchone()[0]
        return {"document_count": documents, "chunk_count": chunks, "indexed_chunks": indexed,
                "mode": "hybrid" if self.semantic_ready else "keyword", "model": self.embedder.name,
                "semantic_ready": self.semantic_ready,
                "message": self._index_warning or self.embedder.message,
                "max_documents": MAX_DOCUMENTS, "max_chunks": MAX_CHUNKS}

    def _document(self, row):
        metadata = json.loads(row["metadata"])
        indexed = self.storage.conn.execute(
            "SELECT count(*) FROM kb_chunks WHERE document_id=? AND embedding_model=? AND embedding IS NOT NULL",
            (row["document_id"], self.embedder.name),
        ).fetchone()[0]
        return KnowledgeDocument(**metadata, indexed_chunks=indexed)

    def list_documents(self, offset=0, limit=30):
        with self.storage._lock:
            rows = self.storage.conn.execute(
                "SELECT * FROM kb_documents ORDER BY rowid DESC LIMIT ? OFFSET ?", (limit, offset),
            ).fetchall()
            total = self.storage.conn.execute("SELECT count(*) FROM kb_documents").fetchone()[0]
            return {"items": [self._document(row) for row in rows], "total": total,
                    "offset": offset, "limit": limit}

    def get_document(self, document_id):
        with self.storage._lock:
            row = self.storage.conn.execute("SELECT * FROM kb_documents WHERE document_id=?", (document_id,)).fetchone()
            if row is None:
                raise KnowledgeError("KB_NOT_FOUND", "资料不存在或已删除。", 404)
            return {"document": self._document(row), "pages": json.loads(row["pages"])}

    def import_document(self, request: KnowledgeImport):
        pages = _read_pages(request)
        digest = hashlib.sha256(json.dumps(pages, ensure_ascii=False).encode()).hexdigest()
        chunks = _split_pages(pages, paginated=bool(request.filename and request.filename.lower().endswith(".pdf")))
        vectors = self._encode([c["excerpt"] for c in chunks]) if self.semantic_ready else None
        document_id = new_id("kd")
        metadata = request.model_dump(mode="json", exclude={"content", "file_base64"})
        metadata.update(document_id=document_id, content_hash=digest, created_at=now_utc().isoformat(),
                        char_count=sum(map(len, pages)), page_count=len(pages), chunk_count=len(chunks))
        with self.storage._lock, self.storage.conn:
            if self.storage.conn.execute("SELECT 1 FROM kb_documents WHERE content_hash=?", (digest,)).fetchone():
                raise KnowledgeError("KB_DUPLICATE", "相同正文已在知识库中，未重复导入。", 409)
            count = self.storage.conn.execute("SELECT count(*) FROM kb_documents").fetchone()[0]
            chunk_count = self.storage.conn.execute("SELECT count(*) FROM kb_chunks").fetchone()[0]
            if count >= MAX_DOCUMENTS or chunk_count + len(chunks) > MAX_CHUNKS:
                raise KnowledgeError("KB_CAPACITY", "知识库已达到本机容量上限，请整理资料后重试。", 409)
            self.storage.conn.execute("INSERT INTO kb_documents VALUES(?,?,?,?)",
                                      (document_id, digest, json.dumps(metadata, ensure_ascii=False), json.dumps(pages, ensure_ascii=False)))
            for index, chunk in enumerate(chunks):
                chunk_id = new_id("kc")
                vector = json.dumps(vectors[index]) if vectors is not None else None
                self.storage.conn.execute("INSERT INTO kb_chunks VALUES(?,?,?,?,?)",
                    (chunk_id, document_id, json.dumps(chunk, ensure_ascii=False), vector,
                     self.embedder.name if vector is not None else None))
                self.storage.conn.execute("INSERT INTO kb_fts VALUES(?,?)",
                    (chunk_id, _tokens(request.title + " " + chunk["excerpt"])))
            row = self.storage.conn.execute("SELECT * FROM kb_documents WHERE document_id=?", (document_id,)).fetchone()
            return self._document(row)

    def delete_document(self, document_id):
        with self.storage._lock, self.storage.conn:
            if not self.storage.conn.execute("SELECT 1 FROM kb_documents WHERE document_id=?", (document_id,)).fetchone():
                raise KnowledgeError("KB_NOT_FOUND", "资料不存在或已删除。", 404)
            self.storage.conn.execute("DELETE FROM kb_fts WHERE chunk_id IN (SELECT chunk_id FROM kb_chunks WHERE document_id=?)", (document_id,))
            self.storage.conn.execute("DELETE FROM kb_chunks WHERE document_id=?", (document_id,))
            self.storage.conn.execute("DELETE FROM kb_documents WHERE document_id=?", (document_id,))

    def _encode(self, texts, *, query=False, deadline=None):
        wait = max(0, deadline - time.monotonic()) if deadline else 20
        if not self._model_lock.acquire(timeout=min(wait, 20)):
            self._index_warning = "语义模型繁忙，本次使用关键词检索；未生成的向量可稍后补建。"
            return None
        try:
            vectors = [_normalized(v) for v in self.embedder.encode(texts, query=query)]
            if len(vectors) != len(texts) or len({len(v) for v in vectors}) != 1:
                raise ValueError("inconsistent embedding output")
            self._index_warning = None
            return vectors
        except Exception as exc:
            logger.warning("knowledge embedding failed: exception_type=%s", type(exc).__name__)
            self._index_warning = "语义向量生成失败，本次使用关键词检索；可稍后补建向量。"
            return None
        finally:
            self._model_lock.release()

    def reindex(self):
        if not self.semantic_ready:
            raise KnowledgeError("KB_MODEL_UNAVAILABLE", "中文语义模型尚未就绪，请先运行知识库模型准备脚本并重启后端。", 503)
        with self.storage._lock:
            rows = self.storage.conn.execute(
                "SELECT chunk_id,data FROM kb_chunks WHERE embedding IS NULL OR embedding_model IS NULL OR embedding_model!=? LIMIT 200",
                (self.embedder.name,),
            ).fetchall()
        if not rows:
            return {"indexed": 0, "remaining": 0}
        vectors = self._encode([json.loads(row["data"])["excerpt"] for row in rows])
        if vectors is None:
            raise KnowledgeError("KB_INDEX_FAILED", "向量生成失败，原始资料和关键词索引仍然保留。", 503)
        with self.storage._lock, self.storage.conn:
            for row, vector in zip(rows, vectors):
                self.storage.conn.execute("UPDATE kb_chunks SET embedding=?,embedding_model=? WHERE chunk_id=?",
                                          (json.dumps(vector), self.embedder.name, row["chunk_id"]))
        state = self.status()
        return {"indexed": len(rows), "remaining": state["chunk_count"] - state["indexed_chunks"]}

    def search(self, query: str, *, tag=None, limit=5, deadline=None):
        if not query.strip() or (deadline is not None and time.monotonic() >= deadline):
            return []
        terms = list(dict.fromkeys(query_terms(query)))[:64]
        match = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        with self.storage._lock:
            rows = self.storage.conn.execute("SELECT c.*,d.metadata FROM kb_chunks c JOIN kb_documents d USING(document_id)").fetchall()
            lexical = self.storage.conn.execute(
                "SELECT kb_fts.chunk_id,bm25(kb_fts) AS rank FROM kb_fts "
                "JOIN kb_chunks c ON c.chunk_id=kb_fts.chunk_id JOIN kb_documents d USING(document_id) "
                "WHERE kb_fts MATCH ? AND (? IS NULL OR EXISTS "
                "(SELECT 1 FROM json_each(d.metadata,'$.tags') WHERE value=?)) ORDER BY rank LIMIT 200",
                (match, tag, tag),
            ).fetchall() if match else []
        candidates = {row["chunk_id"]: row for row in rows
                      if tag is None or tag in json.loads(row["metadata"])["tags"]}
        keyword_ids = [row["chunk_id"] for row in lexical if row["chunk_id"] in candidates]
        semantic_ids = []
        if self.semantic_ready and candidates:
            encoded = self._encode([query], query=True, deadline=deadline)
            if encoded is not None:
                query_vector = encoded[0]
                ranked = []
                for chunk_id, row in candidates.items():
                    if row["embedding_model"] != self.embedder.name or not row["embedding"]:
                        continue
                    vector = json.loads(row["embedding"])
                    if len(vector) != len(query_vector):
                        continue
                    similarity = sum(left * right for left, right in zip(vector, query_vector))
                    # Initial retrieval gate, not a truth/confidence threshold.
                    # BGE Chinese paraphrases often score below .60; judgment
                    # still checks factual alignment against the source text.
                    if similarity >= .50:
                        ranked.append((similarity, chunk_id))
                semantic_ids = [chunk_id for _, chunk_id in sorted(ranked, reverse=True)[:40]]
        scores, methods = {}, {}
        for method, ids in (("keyword", keyword_ids[:40]), ("semantic", semantic_ids)):
            for rank, chunk_id in enumerate(ids, 1):
                scores[chunk_id] = scores.get(chunk_id, 0) + 1 / (60 + rank)
                methods.setdefault(chunk_id, []).append(method)
        results, per_document = [], {}
        for chunk_id in sorted(scores, key=lambda key: (-scores[key], key)):
            row = candidates[chunk_id]
            document_id = row["document_id"]
            if per_document.get(document_id, 0) >= 2:
                continue
            metadata = json.loads(row["metadata"])
            results.append(KnowledgeHit(chunk_id=chunk_id, document_id=document_id,
                **{key: metadata[key] for key in ("title", "publisher", "source_url", "published_at", "tags", "content_hash")},
                **json.loads(row["data"]), retrieval_score=round(scores[chunk_id], 6), matched_by=methods[chunk_id]))
            per_document[document_id] = per_document.get(document_id, 0) + 1
            if len(results) >= max(1, min(limit, 20)):
                break
        return results
