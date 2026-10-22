import asyncio
import base64
import http.client
import json
import re
import threading

import httpx
import pytest

from paperpilot.parse_runner import decode_reply, parse_remote
from paperpilot.parse_service import make_server
from test_reader import pdf_bytes, environment, wait_ready


@pytest.fixture
def parser_url():
    with make_server("127.0.0.1", 0, max_upload_mb=1, max_pages=10) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}"
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_remote_parser_roundtrip_upload_and_limits(parser_url, tmp_path):
    path = tmp_path / "synthetic.pdf"
    path.write_bytes(pdf_bytes())
    parsed = asyncio.run(parse_remote(path, 10, tmp_path / "assets", parser_url))
    assert parsed["paragraphs"]
    assert parsed["page_count"] == 1
    # Reject a declared oversize before accepting its bytes. Sending the whole
    # rejected body may cause an expected TCP reset on Windows.
    connection = http.client.HTTPConnection(parser_url.removeprefix("http://"))
    connection.request("POST", "/parse", headers={"Content-Length": str(1024 * 1024 + 1)})
    assert connection.getresponse().status == 413
    connection.close()
    with httpx.Client(trust_env=False) as client:
        assert client.post(parser_url + "/parse", content=path.read_bytes(), headers={"X-Max-Pages": "11"}).status_code == 400
        assert client.get(parser_url + "/health").status_code == 200
        assert client.get(parser_url + "/unknown").status_code == 404


def test_background_job_uses_remote_parser_and_keeps_assets(environment, parser_url):
    client, app, *_ = environment
    app.state.config.parser_url = parser_url
    app.state.config.max_pages = 10
    response = client.post("/api/sample")
    assert response.status_code == 202
    document_id = response.json()["document"]["id"]
    state = wait_ready(client, document_id)
    assert state["document"]["status"] == "ready"
    assert state["paragraphs"]
    assets = re.findall(r"[a-f0-9]{32}\.png", json.dumps(state))
    assert assets
    response = client.get(f"/api/documents/{document_id}/assets/{assets[0]}")
    assert response.status_code == 200 and response.content.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.parametrize("name,data", [
    ("../outside.png", b"\x89PNG\r\n\x1a\n"),
    ("a" * 32 + ".png", b"not an image"),
])
def test_remote_output_cannot_write_paths_or_non_png_files(tmp_path, name, data):
    with pytest.raises(ValueError):
        decode_reply({"result": {}, "assets": {name: base64.b64encode(data).decode()}}, tmp_path / "assets")
    assert not list(tmp_path.rglob("*.png"))
