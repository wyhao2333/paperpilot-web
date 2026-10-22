import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.formparsers import MultiPartParser

from paperpilot.config import Config
from paperpilot.http_security import WindowLimiter
from paperpilot.main import create_app
from test_reader import environment, setup


HEADERS = {"X-PaperPilot": "1", "Origin": "http://localhost:8765"}


def test_login_rotation_and_forwarded_headers_do_not_bypass_ip_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("paperpilot.main.verify_password", lambda *_: False)
    app = create_app(Config(data_dir=tmp_path, local_admin_autologin=False))
    with TestClient(app, base_url=HEADERS["Origin"], headers=HEADERS) as client:
        for index in range(30):
            response = client.post("/api/login", json={"username": f"unknown{index}", "password": "wrong"}, headers={"X-Forwarded-For": f"192.0.2.{index}"})
            assert response.status_code == 401
        response = client.post("/api/login", json={"username": "another", "password": "wrong"})
        assert response.status_code == 429
        assert int(response.headers["Retry-After"]) > 0
        assert response.headers["Cache-Control"] == "no-store"


def test_account_limit_preserves_penalty_and_expires_without_successful_login(tmp_path, monkeypatch):
    monkeypatch.setattr("paperpilot.main.verify_password", lambda *_: False)
    app = create_app(Config(data_dir=tmp_path, local_admin_autologin=False))
    with TestClient(app, base_url=HEADERS["Origin"], headers=HEADERS) as client:
        for _ in range(10):
            assert client.post("/api/login", json={"username": "absent", "password": "wrong"}).status_code == 401
        assert client.post("/api/login", json={"username": "absent", "password": "wrong"}).status_code == 429
    moment = [0]
    limiter = WindowLimiter(2, 10, max_keys=2, clock=lambda: moment[0])
    assert limiter.take("first") is None
    assert limiter.take("second") is None
    assert limiter.take("rotated") == 10
    assert len(limiter.entries) == 2
    moment[0] = 11
    assert limiter.take("rotated") is None
    assert list(limiter.entries) == ["rotated"]


def test_global_login_limit_survives_rotating_client_ips(tmp_path, monkeypatch):
    monkeypatch.setattr("paperpilot.main.verify_password", lambda *_: False)
    app = create_app(Config(data_dir=tmp_path, local_admin_autologin=False))

    async def check():
        statuses = []
        for index in range(121):
            transport = httpx.ASGITransport(app, client=(f"198.51.100.{index}", 40000))
            async with httpx.AsyncClient(transport=transport, base_url=HEADERS["Origin"], headers=HEADERS) as client:
                response = await client.post("/api/login", json={"username": "unknown", "password": "wrong"})
                statuses.append(response.status_code)
        return statuses

    statuses = asyncio.run(check())
    assert statuses[:120] == [401] * 120
    assert statuses[-1] == 429


@pytest.mark.parametrize("length", [None, "10"])
def test_actual_json_bytes_are_limited_even_without_accurate_length(tmp_path, length):
    app = create_app(Config(data_dir=tmp_path, local_admin_autologin=False))
    body = json.dumps({"username": "absent", "password": "wrong", "padding": "x" * (600 * 1024)}).encode()

    async def check():
        async def chunks():
            for index in range(0, len(body), 16384):
                yield body[index:index + 16384]
        headers = {**HEADERS, "Content-Type": "application/json"}
        if length is not None:
            headers["Content-Length"] = length
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url=HEADERS["Origin"]) as client:
            return await client.post("/api/login", content=chunks(), headers=headers)

    response = asyncio.run(check())
    assert response.status_code == 413
    assert response.headers["Cache-Control"] == "no-store"


def test_anonymous_and_invalid_csrf_uploads_do_not_parse_multipart(environment, monkeypatch):
    client, *_ = environment
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Unauthorised multipart parser was reached")
    monkeypatch.setattr(MultiPartParser, "parse", forbidden)
    file = {"file": ("synthetic.pdf", b"%PDF-1.7\n", "application/pdf")}
    assert client.post("/api/documents", files=file, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    client.cookies.clear()
    assert client.post("/api/documents", files=file).status_code == 401


def test_upload_rejects_extra_parts_and_hides_openapi(environment):
    client, *_ = environment
    file = ("synthetic.pdf", b"%PDF-1.7\n", "application/pdf")
    assert client.post("/api/documents", files=[("file", file), ("other", file)]).status_code == 400
    assert client.post("/api/documents", files={"file": file}, data={"padding": "ignored"}).status_code == 400
    assert client.get("/openapi.json").status_code == 404


def test_https_cookie_configuration_defaults_secure_and_rejects_unsafe_override(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_ORIGIN", "https://reader.example.com")
    monkeypatch.setenv("APP_SECURE_COOKIES", "auto")
    config = Config.from_env()
    assert config.secure_cookies is True
    app = create_app(config)
    with TestClient(app, base_url=config.origin, headers={"X-PaperPilot": "1", "Origin": config.origin}) as client:
        setup(client)
        assert client.cookies.get("pp_session")
        response = client.post("/api/login", json={"username": "researcher", "password": "test-only-password-12345"})
        assert "Secure" in response.headers["set-cookie"] and "HttpOnly" in response.headers["set-cookie"]
    monkeypatch.setenv("APP_SECURE_COOKIES", "false")
    with pytest.raises(ValueError, match="HTTPS deployments"):
        Config.from_env()
