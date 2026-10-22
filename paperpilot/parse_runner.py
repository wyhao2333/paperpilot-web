"""Bounded parser IPC. Production uses a separate container without app data."""
import asyncio
import base64
import json
import os
import re
import sys
from pathlib import Path

import httpx

MAX_RESULT_BYTES = 32 * 1024 * 1024
MAX_ASSET_BYTES = 16 * 1024 * 1024
MAX_ASSETS_BYTES = 64 * 1024 * 1024
MAX_REPLY_BYTES = 128 * 1024 * 1024
MAX_ASSETS = 4096
ASSET_NAME = re.compile(r"[a-f0-9]{32}\.png")
ROOT = Path(__file__).resolve().parents[1]


async def parse_subprocess(path, max_pages, assets_dir):
    # Do not forward API/proxy/tunnel credentials into a native PDF parser.
    environment = {k: v for k, v in os.environ.items() if k.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL",
        "LD_LIBRARY_PATH", "VIRTUAL_ENV",
    }}
    environment.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="1")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "paperpilot.pdf_worker", str(path),
        "--max-pages", str(max_pages), "--assets-dir", str(assets_dir),
        cwd=str(ROOT), env=environment,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )

    async def output():
        chunks, size = [], 0
        while chunk := await process.stdout.read(65536):
            size += len(chunk)
            if size > MAX_RESULT_BYTES:
                raise ValueError("PDF 解析结果过大，请拆分文件后重试。")
            chunks.append(chunk)
        return b"".join(chunks)

    async def discard_errors():
        while await process.stderr.read(65536):
            pass

    tasks = [asyncio.create_task(output()), asyncio.create_task(discard_errors()), asyncio.create_task(process.wait())]
    try:
        raw, _, _ = await asyncio.wait_for(asyncio.gather(*tasks), timeout=120)
    except BaseException as exc:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await process.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if isinstance(exc, asyncio.TimeoutError):
            raise ValueError("PDF 解析超过 120 秒上限，请拆分文件或检查复杂版面。") from exc
        raise
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("PDF 解析进程未返回有效结果，请检查文件是否损坏。") from exc
    if not isinstance(parsed, dict):
        raise ValueError("PDF 解析结果格式无效。")
    if process.returncode != 0 or parsed.get("error"):
        raise ValueError(parsed.get("error") or "PDF 解析失败。")
    return parsed


def encode_assets(directory):
    assets, size = {}, 0
    for path in Path(directory).iterdir():
        if path.is_symlink() or not path.is_file() or not ASSET_NAME.fullmatch(path.name):
            raise ValueError("PDF 解析产生了无效图片文件。")
        length = path.stat().st_size
        size += length
        if length > MAX_ASSET_BYTES or size > MAX_ASSETS_BYTES or len(assets) >= MAX_ASSETS:
            raise ValueError("PDF 图片超过解析资源上限，请拆分文件后重试。")
        assets[path.name] = base64.b64encode(path.read_bytes()).decode("ascii")
    return assets


def decode_reply(payload, assets_dir):
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict) or not isinstance(payload.get("assets"), dict):
        raise ValueError("PDF 解析服务返回格式无效。")
    assets = payload["assets"]
    if len(assets) > MAX_ASSETS:
        raise ValueError("PDF 图片数量超过上限。")
    decoded, size = {}, 0
    for name, encoded in assets.items():
        if not ASSET_NAME.fullmatch(name) or not isinstance(encoded, str) or len(encoded) > (MAX_ASSET_BYTES + 2) // 3 * 4:
            raise ValueError("PDF 解析服务返回了无效图片。")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("PDF 图片编码无效。") from exc
        size += len(data)
        if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > MAX_ASSET_BYTES or size > MAX_ASSETS_BYTES:
            raise ValueError("PDF 图片无效或超过资源上限。")
        decoded[name] = data
    directory = Path(assets_dir)
    directory.mkdir(parents=True, exist_ok=True)
    for name, data in decoded.items():
        # The untrusted worker cannot choose a destination outside this job.
        with (directory / name).open("xb") as handle:
            handle.write(data)
    return payload["result"]


async def parse_remote(path, max_pages, assets_dir, base_url):
    async def content():
        with Path(path).open("rb") as handle:
            while chunk := handle.read(65536):
                yield chunk

    try:
        async with httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(150, connect=5)) as client:
            async with client.stream(
                "POST", base_url.rstrip("/") + "/parse", content=content(),
                headers={"Content-Type": "application/pdf", "Content-Length": str(Path(path).stat().st_size), "X-Max-Pages": str(max_pages)},
                follow_redirects=False,
            ) as response:
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_REPLY_BYTES:
                        raise ValueError("PDF 解析服务返回内容过大。")
                    chunks.append(chunk)
                payload = json.loads(b"".join(chunks))
                if response.status_code != 200:
                    message = payload.get("error", "PDF 解析服务出错，请稍后重试。") if isinstance(payload, dict) else "PDF 解析服务出错。"
                    raise ValueError(str(message)[:350])
                return decode_reply(payload, assets_dir)
    except httpx.HTTPError as exc:
        raise ValueError("无法连接独立 PDF 解析服务，请检查 parser 容器状态。") from exc
