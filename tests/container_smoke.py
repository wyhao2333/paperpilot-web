"""Run through stdin in the disposable CI container, never in production data."""
import json
import re
import secrets
import time

import httpx

from paperpilot.config import Config
from paperpilot.db import Database, now, uid
from paperpilot.security import hash_password


config = Config.from_env()
assert config.parser_url, "CI must exercise the separate parser container"
db = Database(config.data_dir)
name, password = "smoke-" + secrets.token_hex(6), secrets.token_urlsafe(24)
db.execute("INSERT INTO users(id,username,display_name,password_hash,role,created) VALUES (?,?,?,?,?,?)", (uid(), name, "Synthetic CI", hash_password(password), "admin", now()))
with httpx.Client(base_url="http://127.0.0.1:8765", trust_env=False, headers={"X-PaperPilot": "1", "Origin": config.origin}) as client:
    response = client.post("/api/login", json={"username": name, "password": password})
    assert response.status_code == 200, response.status_code
    client.headers["X-CSRF-Token"] = response.json()["csrf"]
    response = client.post("/api/sample")
    assert response.status_code == 202, response.status_code
    document_id = response.json()["document"]["id"]
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        state = client.get(f"/api/documents/{document_id}").json()
        if state["document"]["status"] not in {"queued", "parsing"}:
            break
        time.sleep(.3)
    assert state["document"]["status"] == "ready", state["document"].get("error")
    assert state["paragraphs"]
    assets = re.findall(r"[a-f0-9]{32}\.png", json.dumps(state))
    assert assets, "Synthetic sample should yield figure/formula images"
    response = client.get(f"/api/documents/{document_id}/assets/{assets[0]}")
    assert response.status_code == 200 and response.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert client.get(f"/api/documents/{document_id}/pdf").content.startswith(b"%PDF-")
print("Container login, isolated parser, PDF and figure smoke checks passed.")
