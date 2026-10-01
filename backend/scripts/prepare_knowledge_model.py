"""Download the local knowledge encoder once, outside API requests."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.knowledge import DEFAULT_MODEL


def main():
    from fastembed import TextEmbedding

    settings = get_settings()
    cache = Path(os.getenv("VERIFIER_KB_CACHE_DIR", str(settings.database_path.parent / "knowledge-models"))).resolve()
    cache.mkdir(parents=True, exist_ok=True)
    name = os.getenv("VERIFIER_KB_MODEL", DEFAULT_MODEL)
    print(f"Preparing local knowledge model: {name}", flush=True)
    model = TextEmbedding(model_name=name, cache_dir=str(cache), threads=2)
    vector = next(iter(model.query_embed(["知识库检索测试"])))
    print(f"Ready: {len(vector)} dimensions. Restart the backend to enable hybrid retrieval.", flush=True)


if __name__ == "__main__":
    main()
