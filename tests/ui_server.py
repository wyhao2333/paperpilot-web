"""Isolated browser-QA server. ALL translation responses are explicitly simulated.

Run with .venv Python from the project root. Never use this entrypoint for real reading.
The production entrypoint is `python -m paperpilot` / `python run.py`.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
import uvicorn

from paperpilot.config import Config
from paperpilot.main import create_app


def reply(request):
    if "baidu.com" in request.url.host:
        return httpx.Response(200, json={"trans_result": [{"dst": "【模拟机器翻译】此文本仅用于检查页面显示，不是实际接口译文。"}]})
    body = json.loads(request.content)
    content = body["messages"][-1]["content"]
    try:
        target = json.loads(content).get("TARGET", "")
    except ValueError:
        target = ""
    if target:
        text = "【模拟接口，仅验证界面】这是一份用于测试文献阅读器的合成文档。真实翻译需要在正式服务中配置自己的 API。"
    else:
        text = "【模拟问答】此响应仅验证问答界面及原文定位 [P1]，不代表真实模型回答。"
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


if __name__ == "__main__":
    cfg = Config(data_dir=ROOT / "data-qa", origin="http://localhost:8766", allow_local_api=True)
    uvicorn.run(create_app(cfg, transport=httpx.MockTransport(reply)), host="127.0.0.1", port=8766, proxy_headers=False)
