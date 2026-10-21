import io
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from paperpilot.config import Config, ROOT
from paperpilot.db import dumps, now, uid
from paperpilot.main import create_app
from paperpilot.pdf_worker import parse_pdf
from paperpilot.security import hash_password

PASSWORD = "test-only-password-12345"
TEST_KEY = "test-fixture-not-a-real-api-key"


def pdf_bytes(text="A measured association does not establish physical causality. Check the original evidence."):
    output = io.BytesIO()
    pdf = canvas.Canvas(output, invariant=1)
    pdf.setFont("Times-Bold", 19)
    pdf.drawString(50, 770, "Synthetic parsing test")
    pdf.setFont("Times-Roman", 11)
    pdf.drawString(50, 720, text)
    pdf.drawString(50, 680, "This second paragraph describes an imaginary workflow for testing software behavior.")
    pdf.save()
    return output.getvalue()


def setup(client, name="researcher"):
    # Test fixture provisions an account offline, like the administrator CLI.
    db=client.app.state.db
    if not db.one('SELECT id FROM users WHERE username=?',(name,)):
        db.execute('INSERT INTO users(id,username,display_name,password_hash,role,created) VALUES (?,?,?,?,?,?)',(uid(),name,'Test reader',hash_password(PASSWORD),'admin',now()))
    login(client,name)
    return client.get('/api/session').json()


def login(client, name):
    response = client.post("/api/login", json={"username": name, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf"]


def wait_ready(client, document_id, timeout=25):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = client.get(f"/api/documents/{document_id}").json()
        if result["document"]["status"] not in {"queued", "parsing"}:
            return result
        time.sleep(.07)
    pytest.fail("PDF did not finish parsing")


def wait_jobs(client, document_id, timeout=25):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = client.get(f"/api/documents/{document_id}").json()
        if not any(j["status"] in {"queued", "running"} for j in result["jobs"]):
            return result
        time.sleep(.07)
    pytest.fail("Background job did not finish")


def upload(client, data=None, name="测试论文.pdf"):
    response = client.post("/api/documents", files={"file": (name, data or pdf_bytes(), "application/pdf")})
    assert response.status_code == 202, response.text
    return response.json()["document"]["id"]


def configure(client):
    response = client.put("/api/settings", json={"ai": {"provider": "test-fixture", "base_url": "http://localhost:9999/v1", "model": "synthetic-model", "api_key": TEST_KEY}, "baidu": {"app_id": "123456", "secret": "not-a-real-baidu-secret"}, "glossary": "association = 关联"})
    assert response.status_code == 200, response.text
    return response


@pytest.fixture
def environment(tmp_path):
    requests = []
    mode = {"fail": False, 'bad_reflow': False}

    def handler(request):
        requests.append(request)
        if mode["fail"]:
            return httpx.Response(401, json={"error": {"message": "fixture failure"}})
        if "baidu.com" in request.url.host:
            return httpx.Response(200, json={"trans_result": [{"dst": "机器翻译测试结果（模拟接口）"}]})
        body = json.loads(request.content)
        if 'immutable source lines' in body['messages'][0]['content']:
            lines=json.loads(body['messages'][-1]['content'])['lines']
            groups=[[i] for i in range(len(lines))]
            if mode['bad_reflow']:
                groups=[[0],[0]]
            text=json.dumps({'groups':groups})
        elif "Translate only TARGET" in body["messages"][0]["content"]:
            target = json.loads(body["messages"][-1]["content"])["TARGET"]
            text = "模拟接口译文：" + target[:50]
        else:
            text = "根据给定摘录，本例用于验证阅读器 [P1]。当前摘录不足以确认其他结论。"
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})

    cfg = Config(data_dir=tmp_path / "data", allow_local_api=True)
    transport = httpx.MockTransport(handler)
    app = create_app(cfg, transport=transport)
    with TestClient(app, base_url="http://localhost:8765", client=("127.0.0.1", 41000), headers={"X-PaperPilot": "1", "Origin": "http://localhost:8765"}) as client:
        setup(client)
        yield client, app, requests, mode, cfg, transport


def test_pdf_roundtrip_dedup_progress_notes_export(environment):
    client, app, requests, *_ = environment
    document_id = upload(client)
    result = wait_ready(client, document_id)
    assert result["document"]["status"] == "ready"
    assert len(result["paragraphs"]) >= 3
    paragraph = result["paragraphs"][1]
    assert paragraph["locations"][0]["bbox"][0] > 0
    assert requests == []  # Upload and parsing never send document text to a provider.
    duplicate = client.post("/api/documents", files={"file": ("other.pdf", pdf_bytes(), "application/pdf")}).json()
    assert duplicate["duplicate"] is True
    assert duplicate["document"]["id"] == document_id
    assert client.put(f"/api/documents/{document_id}/progress", json={"paragraph_id": paragraph["id"], "page": 1}).status_code == 200
    assert client.put(f"/api/documents/{document_id}/notes", json={"text": "核对证据边界。"}).status_code == 200
    restored = client.get(f"/api/documents/{document_id}").json()
    assert restored["progress"]["paragraph_id"] == paragraph["id"]
    assert restored["notes"]["text"] == "核对证据边界。"
    exported = client.get(f"/api/documents/{document_id}/export")
    assert "核对证据边界" in exported.text and paragraph["text"] in exported.text
    raw = client.get(f"/api/documents/{document_id}/pdf")
    assert raw.content.startswith(b"%PDF") and raw.headers["cache-control"] == "no-store"


def test_ai_cache_failures_retry_and_context_invalidation(environment):
    client, _app, requests, mode, *_ = environment
    document_id = upload(client)
    result = wait_ready(client, document_id)
    assert client.post(f"/api/documents/{document_id}/translate", json={"engine": "ai"}).status_code == 400
    configure(client)
    assert client.post(f"/api/documents/{document_id}/translate", json={"engine": "ai"}).status_code == 202
    result = wait_jobs(client, document_id)
    assert all(p["translation"]["status"] == "done" for p in result["paragraphs"])
    count = len(requests)
    assert count == len(result["paragraphs"])
    client.post(f"/api/documents/{document_id}/translate", json={"engine": "ai"})
    wait_jobs(client, document_id)
    assert len(requests) == count  # A full-document resume reuses valid cache entries.
    target = result["paragraphs"][1]
    mode["fail"] = True
    client.post(f"/api/documents/{document_id}/translate", json={"engine": "ai", "paragraph_ids": [target["id"]], "force": True})
    failed = wait_jobs(client, document_id)
    assert failed["paragraphs"][1]["translation"]["status"] == "error"
    assert failed["paragraphs"][0]["translation"]["status"] == "done"
    mode["fail"] = False
    client.post(f"/api/documents/{document_id}/translate", json={"engine": "ai", "paragraph_ids": [target["id"]]})
    recovered = wait_jobs(client, document_id)
    assert recovered["paragraphs"][1]["translation"]["status"] == "done"
    assert client.patch(f"/api/documents/{document_id}/paragraphs/{target['id']}", json={"text": target["text"] + " Corrected extraction."}).status_code == 200
    changed = client.get(f"/api/documents/{document_id}").json()
    assert changed["paragraphs"][1]["translation"] is None
    assert changed["paragraphs"][0]["translation"] is None  # AI background context changed too.


def test_machine_translation_and_grounded_chat(environment):
    client, _app, requests, *_ = environment
    document_id = upload(client)
    result = wait_ready(client, document_id)
    configure(client)
    paragraph_id = result["paragraphs"][0]["id"]
    client.post(f"/api/documents/{document_id}/translate", json={"engine": "baidu", "paragraph_ids": [paragraph_id]})
    wait_jobs(client, document_id)
    translated = client.get(f"/api/documents/{document_id}?engine=baidu").json()
    assert translated["paragraphs"][0]["translation"]["text"] == "机器翻译测试结果（模拟接口）"
    assert b"sign=" in requests[0].content
    response = client.post(f"/api/documents/{document_id}/chat", json={"question": "请解释这一段", "paragraph_id": paragraph_id})
    assert response.status_code == 202
    answer = wait_jobs(client, document_id)["messages"][-1]
    assert answer["status"] == "done" and answer["evidence"]
    assert answer["evidence"][0]["id"] == paragraph_id
    sent = json.loads(requests[-1].content)
    assert "source_excerpts" in sent["messages"][-1]["content"]


def test_account_isolation_csrf_and_secret_redaction(environment):
    client, app, _requests, *_ = environment
    document_id = upload(client)
    result = wait_ready(client, document_id)
    configure(client)
    assert TEST_KEY not in client.get("/api/settings").text
    assert TEST_KEY.encode() not in app.state.db.path.read_bytes()
    assert client.put("/api/settings", json={"ai": {"api_key": ""}, "baidu": {}} , headers={"X-CSRF-Token": "incorrect"}).status_code == 403
    assert client.post("/api/logout", headers={"Origin": "https://unrelated.example"}).status_code == 403
    assert client.post("/api/admin/users", json={"username": "colleague", "password": PASSWORD}).status_code == 200
    login(client, "colleague")
    for suffix in ["", "/pdf", "/export"]:
        assert client.get(f"/api/documents/{document_id}{suffix}").status_code == 404
    assert client.put(f"/api/documents/{document_id}/notes", json={"text": "not allowed"}).status_code == 404
    assert client.get("/api/documents").json() == []
    assert client.get("/api/settings").json()["ai"]["has_key"] is False
    assert client.post("/api/admin/users", json={"username": "outsider", "password": PASSWORD}).status_code == 403
    assert client.post(f"/api/documents/{document_id}/translate", json={"paragraph_ids": [result['paragraphs'][0]['id']]}).status_code == 404


def test_invalid_pdf_scanned_page_and_two_column_order(environment, tmp_path):
    client, *_ = environment
    assert client.post("/api/documents", files={"file": ("not.pdf", b"not a pdf", "application/pdf")}).status_code == 400
    output = io.BytesIO()
    blank = canvas.Canvas(output); blank.rect(100, 100, 200, 200); blank.showPage(); blank.save()
    document_id = upload(client, output.getvalue())
    assert wait_ready(client, document_id)["document"]["status"] == "needs_ocr"
    sample = parse_pdf(ROOT / "examples/paperpilot-sample.pdf")
    text = " ".join(p["text"] for p in sample["paragraphs"])
    assert text.index("LEFT COLUMN FIRST") < text.index("A hypothetical workflow") < text.index("RIGHT COLUMN SECOND")
    assert any("双栏" in warning for warning in sample["warnings"])
    with pytest.raises(ValueError, match="上限"):
        parse_pdf(ROOT / "examples/paperpilot-sample.pdf", max_pages=1)


def test_restart_resumes_persisted_jobs(tmp_path):
    requests = []

    def response(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "模拟接口：恢复后的译文。"}}]})

    cfg = Config(data_dir=tmp_path / "restart", allow_local_api=True)
    transport = httpx.MockTransport(response)
    headers = {"X-PaperPilot": "1", "Origin": "http://localhost:8765"}
    app = create_app(cfg, transport=transport)
    with TestClient(app, base_url="http://localhost:8765", client=("127.0.0.1", 41000), headers=headers) as client:
        setup(client)
        document_id = upload(client)
        result = wait_ready(client, document_id)
        configure(client)
        paragraph_id = result["paragraphs"][0]["id"]
        # Persist an interrupted state, then shut down the original application.
        app.state.db.execute("INSERT INTO jobs(id,user_id,document_id,kind,status,payload,total,created,updated) VALUES (?,?,?,?,?,?,?,?,?)", (uid(), client.get('/api/session').json()['user']['id'], document_id, "translate", "running", dumps({"engine": "ai", "paragraph_ids": [paragraph_id]}), 1, now(), now()))
    assert requests == []
    restored_app = create_app(cfg, transport=transport)
    with TestClient(restored_app, base_url="http://localhost:8765", client=("127.0.0.1", 41001), headers=headers) as restored:
        login(restored, "researcher")
        state = wait_jobs(restored, document_id)
        assert state["paragraphs"][0]["translation"]["status"] == "done"
        assert len(requests) == 1


def test_daily_limit_blocks_outbound_request(environment):
    client, app, requests, *_ = environment
    document_id = upload(client)
    wait_ready(client, document_id); configure(client)
    app.state.config.daily_text_limit = 1
    client.post(f"/api/documents/{document_id}/translate", json={"engine": "ai"})
    result = wait_jobs(client, document_id)
    assert result["jobs"][0]["status"] == "failed"
    assert "上限" in result["jobs"][0]["error"]
    assert requests == []


def test_cancel_stops_following_paragraphs(environment):
    client, _app, requests, *_ = environment
    document_id = upload(client)
    state = wait_ready(client, document_id); configure(client)
    response = client.post(f"/api/documents/{document_id}/translate", json={"engine": "baidu"})
    assert response.status_code == 202
    end = time.monotonic() + 5
    while not requests and time.monotonic() < end:
        time.sleep(.03)
    assert client.post(f"/api/jobs/{response.json()['job_id']}/cancel").status_code == 200
    finished = wait_jobs(client, document_id)
    assert finished["jobs"][0]["status"] == "cancelled"
    assert len(requests) == 1
    assert finished["jobs"][0]["completed"] < len(state["paragraphs"])


def test_public_hostname_cannot_bootstrap_and_private_api_requires_opt_in(tmp_path):
    app = create_app(Config(data_dir=tmp_path / "fresh"))
    with TestClient(app, base_url="http://localhost:8765", client=("127.0.0.1", 40001), headers={"X-PaperPilot": "1", "Origin": "http://localhost:8765"}) as client:
        assert client.post("/api/setup", json={"username": "admin", "password": PASSWORD}, headers={"Host": "reader.example.com"}).status_code in {404,405}
        assert client.get('/api/session',headers={'Host':'reader.example.com'}).json()['user'] is None
        setup(client)
        response = client.put("/api/settings", json={"ai": {"base_url": "https://127.0.0.1/v1", "model": "test", "api_key": TEST_KEY}, "baidu": {}})
        assert response.status_code == 400
