"""One-shot document parser, executed outside the web server process."""

import io
import json
import sys
from pathlib import Path

# -I disables user-site and PYTHONPATH; only this project's app is added back.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def read_pdf(data):
    from pypdf import PdfReader, apply_configuration
    from pypdf.generic import IndirectObject, StreamObject
    from app.knowledge import KnowledgeError

    stream_limit = 2 * 1024 * 1024
    with apply_configuration(
        maximum_declared_stream_length=stream_limit,
        array_based_stream_maximum_output_length=stream_limit,
        zlib_maximum_output_length=stream_limit,
        lzw_maximum_output_length=stream_limit,
        run_length_maximum_output_length=stream_limit,
        jbig2_maximum_output_length=stream_limit,
        image_maximum_buffer_size=stream_limit,
        flate_maximum_row_length=stream_limit,
        xmp_maximum_input_length=stream_limit,
        page_tree_maximum_entries=1000,
        page_tree_maximum_depth=32,
        xform_maximum_invocations_per_extraction=100,
        jbig2dec_binary=None,
    ):
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise KnowledgeError("KB_PDF_ENCRYPTED", "请先解密 PDF 再导入。")
        if len(reader.pages) > 100:
            raise KnowledgeError("KB_PDF_LIMIT", "单个 PDF 最多 100 页，请拆分后导入。")
        # Bound aggregate stream expansion before extract_text joins contents.
        objects = {(generation, number) for generation, entries in reader.xref.items() for number in entries if number}
        objects.update((0, number) for number in reader.xref_objStm)
        if len(objects) > 10_000:
            raise KnowledgeError("KB_PDF_LIMIT", "PDF 对象过多，请精简后导入。", 413)
        decoded = 0
        for generation, number in objects:
            obj = reader.get_object(IndirectObject(number, generation, reader))
            if isinstance(obj, StreamObject):
                decoded += len(obj.get_data())
                if decoded > 8 * 1024 * 1024:
                    raise KnowledgeError("KB_PDF_LIMIT", "PDF 解压内容过大，请拆分后导入。", 413)
        pages, count = [], 0
        for page in reader.pages:
            text = (page.extract_text() or "").replace("\r\n", "\n")
            count += len(text)
            if count > 100_000:
                raise KnowledgeError("KB_TEXT_LIMIT", "提取正文超过 100,000 字符，请拆分资料。", 413)
            pages.append(text)
        return pages


def main():
    # Linux production worker: hard memory/CPU ceilings in addition to parent timeout.
    if sys.platform != "win32":
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    from app.knowledge import KnowledgeError, _read_docx
    data = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
    kind = sys.argv[1]
    try:
        if len(data) > 2 * 1024 * 1024:
            raise KnowledgeError("KB_FILE_TOO_LARGE", "单个文件最多 2 MiB。", 413)
        pages = read_pdf(data) if kind == "pdf" else [_read_docx(data)]
        result = {"pages": pages}
    except KnowledgeError as exc:
        result = {"error": {"code": exc.code, "message": exc.message, "status": exc.status}}
    except ImportError:
        result = {"error": {"code": "KB_PDF_UNAVAILABLE" if kind == "pdf" else "KB_WORD_UNAVAILABLE",
                            "message": "文档解析依赖尚未安装，请安装后端依赖。", "status": 503}}
    except Exception:
        result = {"error": {"code": "KB_PDF_INVALID" if kind == "pdf" else "KB_WORD_INVALID",
                            "message": "无法安全解析文档，请精简后导入或粘贴正文。", "status": 400}}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()
