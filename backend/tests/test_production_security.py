"""Exercise the public boundary using synthetic passwords and temporary data."""

import asyncio
import base64
import io
import json
import logging
import subprocess
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.access import AccessBoundary, hash_password
from app.application import create_app
from app.config import Settings
from app.knowledge import KnowledgeError, _read_document_isolated
from app.knowledge_schemas import KnowledgeImport

PASSWORD = "synthetic-security-test-password"
ORIGIN = "https://verifier.example.com"


@pytest.fixture
def production(tmp_path):
    return Settings(database_path=tmp_path / "secure.db", environment="production",
                    password_hash=hash_password(PASSWORD), allowed_hosts=("verifier.example.com",),
                    allowed_origins=(ORIGIN,))


def login(client):
    response = client.post("/api/auth/login", json={"password": PASSWORD},
                           headers={"Origin": ORIGIN, "X-Verifier-Request": "1"})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response


@pytest.mark.parametrize("change", [
    {"password_hash": ""}, {"password_hash": "scrypt$bad$bad"},
    {"allowed_hosts": ("*",)}, {"allowed_hosts": ()},
    {"allowed_origins": ("http://verifier.example.com",)},
    {"allowed_origins": ("https://other.example.com",)},
    {"allowed_origins": (ORIGIN + "/",)}, {"environment": "prodution"},
])
def test_insecure_production_configuration_cannot_start(production, change):
    with pytest.raises(ValueError):
        create_app(replace(production, **change))
    assert not production.database_path.exists()


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/status"), ("GET", "/api/tasks/t_secret"),
    ("GET", "/api/tasks/t_secret/input"), ("GET", "/api/tasks/t_secret/export?format=json"),
    ("GET", "/api/claims/c_secret"), ("PATCH", "/api/claims/c_secret"),
    ("POST", "/api/tasks"), ("POST", "/api/claims/c_secret/retry"),
    ("GET", "/api/knowledge/documents"), ("GET", "/api/knowledge/documents/k_secret"),
    ("DELETE", "/api/knowledge/documents/k_secret"), ("POST", "/api/knowledge/reindex"),
])
def test_all_business_endpoints_require_login(production, method, path):
    with TestClient(create_app(production), base_url=ORIGIN) as client:
        response = client.request(method, path)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_REQUIRED"
        assert response.headers["cache-control"] == "no-store"


def test_authenticated_workspace_flow_and_logout_revocation(production):
    with TestClient(create_app(production), base_url=ORIGIN) as client:
        assert client.get("/api/auth/session").json()["authenticated"] is False
        response = login(client)
        cookie = response.headers["set-cookie"]
        assert all(value in cookie for value in ("__Host-", "HttpOnly", "Secure", "SameSite=Strict", "Path=/"))
        bearer = client.cookies.get("__Host-verifier_session")
        assert client.get("/api/auth/session").json()["authenticated"] is True
        created = client.post("/api/tasks", json={"input_text": "鲸属于哺乳动物。"})
        assert created.status_code == 202
        assert client.get(f"/api/tasks/{created.json()['task_id']}/input").status_code == 200
        assert client.post("/api/knowledge/documents", json={"title": "测试", "content": "正文"}).status_code == 201
        assert client.post("/api/auth/logout").status_code == 200
        client.cookies.set("__Host-verifier_session", bearer)
        assert client.get("/api/knowledge/documents").status_code == 401


def test_csrf_and_foreign_origin_block_bodyless_writes(production, monkeypatch):
    app = create_app(production)
    with TestClient(app, base_url=ORIGIN) as client:
        called = []
        def rebuild():
            called.append(True)
            return {"indexed": 0, "remaining": 0}
        monkeypatch.setattr(app.state.pipeline.knowledge, "reindex", rebuild)
        login(client)
        del client.headers["X-CSRF-Token"]
        assert client.post("/api/knowledge/reindex").json()["error"]["code"] == "CSRF_REJECTED"
        csrf = client.get("/api/auth/session").json()["csrf_token"]
        for headers in ({"Origin": "https://evil.example", "X-CSRF-Token": csrf},
                        {"Sec-Fetch-Site": "cross-site", "X-CSRF-Token": csrf}):
            response = client.post("/api/knowledge/reindex", headers=headers)
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "ORIGIN_REJECTED"
        assert not called
        assert client.post("/api/knowledge/reindex", headers={"X-CSRF-Token": csrf}).status_code == 200
        assert called == [True]


def test_login_rate_limit_and_cross_site_login(production):
    with TestClient(create_app(production), base_url=ORIGIN) as client:
        assert client.post("/api/auth/login", json={"password": PASSWORD}).status_code == 403
        assert client.post("/api/auth/login", json={"password": PASSWORD}, headers={
            "X-Verifier-Request": "1", "Origin": "https://evil.example"}).status_code == 403
        for _ in range(5):
            assert client.post("/api/auth/login", json={"password": "incorrect-password-value"},
                               headers={"X-Verifier-Request": "1"}).status_code == 401
        blocked = client.post("/api/auth/login", json={"password": PASSWORD}, headers={"X-Verifier-Request": "1"})
        assert blocked.status_code == 429 and blocked.headers["retry-after"] == "60"


def test_login_requires_json_and_bounds_body_and_cookie(production):
    with TestClient(create_app(production), base_url=ORIGIN) as client:
        assert client.post("/api/auth/login", content='{"password":"synthetic"}',
                           headers={"X-Verifier-Request": "1"}).status_code == 415
        assert client.post("/api/auth/login", content=b"x" * 9000,
                           headers={"X-Verifier-Request": "1", "Content-Type": "application/json"}).status_code == 413
        response = client.get("/api/status", headers={"Cookie": "invalid cookie==; __Host-verifier_session=invalid"})
        assert response.status_code == 401


def test_unexpected_errors_do_not_log_exception_secrets(tmp_path, caplog):
    app = create_app(Settings(database_path=tmp_path / "errors.db"))
    @app.get("/api/error-probe")
    def broken():
        raise RuntimeError("synthetic-secret-user-text-and-api-key")
    with TestClient(app, raise_server_exceptions=False) as client, caplog.at_level(logging.ERROR):
        response = client.get("/api/error-probe")
        assert response.status_code == 500
        assert response.headers["cache-control"] == "no-store"
        assert "synthetic-secret" not in response.text and "synthetic-secret" not in caplog.text
        assert "exception_type=RuntimeError" in caplog.text


def test_cookie_rotation_expiration_and_server_token_hash(production):
    app = create_app(production)
    with TestClient(app, base_url=ORIGIN) as client:
        first = login(client)
        old = client.cookies.get("__Host-verifier_session")
        login(client)
        middleware = app.middleware_stack
        while not isinstance(middleware, AccessBoundary):
            middleware = middleware.app
        assert len(middleware.sessions) == 1
        assert old not in middleware.sessions
        assert first.json()["csrf_token"] != client.get("/api/auth/session").json()["csrf_token"]
        key, session = next(iter(middleware.sessions.items()))
        middleware.sessions[key] = replace(session, expires=0)
        assert client.get("/api/status").status_code == 401
        assert not middleware.sessions


def test_production_https_hosts_docs_and_preflight(production):
    with TestClient(create_app(production), base_url=ORIGIN) as client:
        assert client.get("/api/auth/session", headers={"Host": "evil.example"}).status_code == 400
        assert client.get("http://verifier.example.com/api/auth/session").status_code == 400
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404
        response = client.options("/api/tasks", headers={"Origin": ORIGIN,
            "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-csrf-token,content-type"})
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == ORIGIN
        assert client.get("/api/auth/session").headers["strict-transport-security"] == "max-age=31536000"


def test_local_mode_rejects_remote_and_cross_site_write(tmp_path):
    app = create_app(Settings(database_path=tmp_path / "local.db"))
    with TestClient(app, client=("203.0.113.5", 1234)) as remote:
        assert remote.get("/api/status").status_code == 403
    with TestClient(app) as client:
        assert client.get("/api/auth/session").json()["required"] is False
        assert client.post("/api/knowledge/reindex", headers={"Origin": "https://evil.example"}).status_code == 403


@pytest.mark.parametrize("method,path", [
    ("PATCH", "/api/claims/c_test"), ("POST", "/api/claims/c_test/split"),
    ("POST", "/api/tasks/t_test/claims"), ("DELETE", "/api/knowledge/documents/k_test"),
    ("POST", "/api/knowledge/reindex"),
])
def test_all_mutations_bound_body_before_parsing(tmp_path, method, path):
    with TestClient(create_app(Settings(database_path=tmp_path / "bounds.db"))) as client:
        assert client.request(method, path, content=b"x" * 300_000).status_code == 413


def test_chunked_body_limit_is_enforced_without_content_length():
    from app.api.request_limits import TaskRequestBodyLimit
    invoked, messages = [], []
    async def downstream(*args):
        invoked.append(True)
    chunks = iter([{"type": "http.request", "body": b"x" * 150_000, "more_body": True},
                   {"type": "http.request", "body": b"x" * 150_000, "more_body": False}])
    async def receive():
        return next(chunks)
    async def send(message):
        messages.append(message)
    asyncio.run(TaskRequestBodyLimit(downstream)({"type": "http", "method": "PATCH", "path": "/api/claims/id", "headers": []}, receive, send))
    assert not invoked and messages[0]["status"] == 413


def test_knowledge_expensive_requests_share_admission_quota(tmp_path):
    with TestClient(create_app(Settings(database_path=tmp_path / "quota.db"))) as client:
        for _ in range(10):
            assert client.post("/api/knowledge/search", json={"query": "正文"}).status_code == 200
        assert client.post("/api/knowledge/reindex").status_code == 429
        assert client.post("/api/tasks", json={"input_text": "鲸属于哺乳动物。"}).status_code == 429


def test_small_compressed_pdf_cannot_expand_unbounded(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(300, 300)
    stream = DecodedStreamObject()
    stream.set_data(b" " * (4 * 1024 * 1024))
    page[NameObject("/Contents")] = writer._add_object(stream.flate_encode())
    output = io.BytesIO(); writer.write(output)
    assert len(output.getvalue()) < 10_000
    with TestClient(create_app(Settings(database_path=tmp_path / "pdf.db"))) as client:
        response = client.post("/api/knowledge/documents", json={"title": "压缩样例", "filename": "bomb.pdf",
            "file_base64": base64.b64encode(output.getvalue()).decode()})
        assert response.status_code in {400, 413}
        assert client.get("/api/knowledge/status").json()["document_count"] == 0


def test_parser_timeout_releases_slot_and_does_not_forward_secrets(monkeypatch):
    from app import knowledge
    captured = []
    def expired(args, **kwargs):
        captured.append(kwargs)
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])
    monkeypatch.setenv("MIMO_API_KEY", "synthetic-secret")
    monkeypatch.setattr(knowledge.subprocess, "run", expired)
    for _ in range(3):
        with pytest.raises(KnowledgeError, match="解析超时"):
            _read_document_isolated(b"synthetic", "pdf")
    assert all("MIMO_API_KEY" not in item["env"] and item["timeout"] == 15 for item in captured)
