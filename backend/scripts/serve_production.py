"""Fail closed, then serve one private workspace behind a loopback HTTPS proxy."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.access import validate_access_settings
from app.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    if settings.environment != "production":
        raise SystemExit("请先设置 VERIFIER_ENV=production；此入口拒绝启动匿名本地模式。")
    validate_access_settings(settings)
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, workers=1,
                proxy_headers=True, forwarded_allow_ips="127.0.0.1", limit_concurrency=32,
                backlog=64, timeout_keep_alive=5, server_header=False, access_log=False,
                ws="none")
