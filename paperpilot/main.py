import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlsplit

import httpx
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .config import Config, ROOT
from .db import Database, dumps, now, uid
from .jobs import Jobs, context_for
from .providers import Providers, ProviderError, ENGINES, ensure_configured, translation_signature, validate_base_url
from .security import Vault, hash_password, token_hash, verify_password

COOKIE = "pp_session"
SESSION_DAYS = 30


class Credentials(BaseModel):
    username: str = Field(min_length=2, max_length=40)
    password: str = Field(max_length=256)
    display_name: str = Field(default="", max_length=60)


class TranslationRequest(BaseModel):
    engine: str = "ai"
    paragraph_ids: list[str] | None = Field(default=None, max_length=12000)
    force: bool = False


class AISettings(BaseModel):
    provider: str = Field(default="deepseek", max_length=60)
    base_url: str = Field(default="https://api.deepseek.com", max_length=500)
    model: str = Field(default="deepseek-flash", min_length=1, max_length=160)
    api_key: str | None = Field(default=None, max_length=2048)


class BaiduSettings(BaseModel):
    app_id: str = Field(default="", max_length=64)
    secret: str | None = Field(default=None, max_length=2048)


class AITaskSettings(BaseModel):
    provider: str = Field(min_length=1, max_length=60)
    model: str = Field(min_length=1, max_length=160)


class SettingsRequest(BaseModel):
    ai: AISettings
    ai_profiles: dict[str, AISettings] = Field(default_factory=dict, max_length=12)
    ai_tasks: dict[Literal['translation', 'chat'], AITaskSettings] | None = None
    translation_concurrency: int | None = Field(default=None, ge=1, le=16)
    baidu: BaiduSettings = Field(default_factory=BaiduSettings)
    youdao: BaiduSettings = Field(default_factory=BaiduSettings)
    microsoft: dict[str, str | None] = Field(default_factory=dict, max_length=3)
    glossary: str = Field(default="", max_length=5000)


class TestSettingsRequest(BaseModel):
    engine: str = 'ai'
    purpose: Literal['translation', 'chat'] = 'translation'


class TextRequest(BaseModel):
    text: str = Field(max_length=50000)


class ProgressRequest(BaseModel):
    paragraph_id: str | None = None
    page: int = Field(default=1, ge=1)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2500)
    paragraph_id: str | None = None


def create_app(config=None, *, transport=None):
    config = config or Config.from_env()
    db = Database(config.data_dir)
    vault = Vault(config.data_dir)
    login_attempts = defaultdict(deque)

    @asynccontextmanager
    async def lifespan(app):
        # Explicit proxy selection avoids httpx parsing host-wide NO_PROXY entries
        # such as an unbracketed IPv6 ::1. Loopback model servers stay direct.
        proxy = (os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or os.getenv("HTTP_PROXY") or os.getenv("http_proxy") or None) if transport is None else None
        mounts = {"all://localhost": None, "all://127.0.0.1": None, "all://[::1]": None} if proxy else None
        async with httpx.AsyncClient(transport=transport, proxy=proxy, mounts=mounts, trust_env=False, limits=httpx.Limits(max_connections=40, max_keepalive_connections=20), timeout=55) as client:
            jobs = Jobs(db, vault, Providers(client, config), config)
            app.state.jobs = jobs
            await jobs.start()
            yield
            await jobs.stop()

    app = FastAPI(title="PaperPilot Web", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.db, app.state.vault, app.state.config = db, vault, config

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, exc):
        # Never echo credential values from a validation error.
        return JSONResponse({"detail": "输入格式有误，请检查填写内容。", "fields": [".".join(map(str, x["loc"])) for x in exc.errors()]}, status_code=422)

    @app.middleware("http")
    async def boundaries(request, call_next):
        if request.url.path.startswith("/api/"):
            length = request.headers.get("content-length", "0")
            try:
                size = int(length)
            except ValueError:
                return JSONResponse({"detail": "Content-Length 无效。"}, status_code=400)
            is_upload = request.url.path == "/api/documents" and request.method == "POST"
            maximum = (config.max_upload_mb * 1024 * 1024 + 1024 * 1024) if is_upload else 512 * 1024
            if size > maximum:
                return JSONResponse({"detail": f"上传上限为 {config.max_upload_mb}MB。" if is_upload else "请求内容过大。"}, status_code=413)
            if is_upload and not request.headers.get("content-length"):
                return JSONResponse({"detail": "上传需要 Content-Length，请使用网页上传。"}, status_code=411)
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                origin = request.headers.get("origin")
                allowed = {config.origin}
                parsed = urlsplit(config.origin)
                if parsed.hostname in {"localhost", "127.0.0.1"}:
                    port = parsed.port or 80
                    allowed.update({f"http://localhost:{port}", f"http://127.0.0.1:{port}"})
                if (origin and origin not in allowed) or request.headers.get("x-paperpilot") != "1":
                    return JSONResponse({"detail": "请求来源未获允许，请从本服务页面操作。"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; worker-src 'self' blob:; font-src 'self' data:; frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
        if request.url.path.startswith("/api/") or request.url.path in {"/", "/index.html"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    def user(request: Request):
        token = request.cookies.get(COOKIE, "")
        session = db.one("SELECT u.id,u.username,u.display_name,u.role,s.csrf FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>?", (token_hash(token), now())) if token else None
        if not session:
            raise HTTPException(401, "请登录后继续。")
        if request.method not in {"GET", "HEAD"} and not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session["csrf"]):
            raise HTTPException(403, "会话校验失败，请刷新页面后重试。")
        return session

    def admin(current=Depends(user)):
        if current["role"] != "admin":
            raise HTTPException(403, "需要管理员账号。")
        return current

    def owned(document_id, current):
        doc = db.one("SELECT * FROM documents WHERE id=? AND user_id=?", (document_id, current["id"]))
        if not doc:
            raise HTTPException(404, "未找到这篇文献。")
        return doc

    def public_user(value):
        return {key: value[key] for key in ["id", "username", "display_name", "role"]}

    def make_session(response, account):
        token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(28)
        db.execute("INSERT INTO sessions(token_hash,user_id,csrf,expires) VALUES (?,?,?,?)", (token_hash(token), account["id"], csrf, now() + SESSION_DAYS * 86400))
        response.set_cookie(COOKIE, token, httponly=True, secure=config.secure_cookies, samesite="lax", max_age=SESSION_DAYS * 86400, path="/")
        return {"user": public_user(account), "csrf": csrf, "needs_setup": False}

    def new_account(body, role="user"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{2,40}", body.username):
            raise HTTPException(400, "用户名请使用 2～40 个英文字母、数字、点、短横线或下划线。")
        try:
            hashed = hash_password(body.password)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"id": uid(), "username": body.username.lower(), "display_name": body.display_name.strip() or body.username, "role": role, "password_hash": hashed}

    def insert_account(con, account):
        con.execute("INSERT INTO users(id,username,display_name,password_hash,role,created) VALUES (?,?,?,?,?,?)", (account["id"], account["username"], account["display_name"], account["password_hash"], account["role"], now()))

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/api/session")
    def session(request: Request, response: Response):
        local = config.local_admin_autologin and urlsplit(config.origin).hostname in {'localhost', '127.0.0.1', '::1'} and request.url.hostname in {'localhost', '127.0.0.1', '::1'} and request.client.host in {'127.0.0.1', '::1'}
        try:
            current = user(request)
            return {"user": public_user(current), "csrf": current["csrf"], "local_admin": local}
        except HTTPException:
            if local:
                with db.connect() as con:
                    con.execute('BEGIN IMMEDIATE')
                    row = con.execute("SELECT * FROM users WHERE role='admin' ORDER BY created LIMIT 1").fetchone()
                    if row:
                        account = dict(row)
                    else:
                        account = new_account(Credentials(username='admin', password=secrets.token_urlsafe(40), display_name='管理员'), 'admin')
                        insert_account(con, account)
                return {**make_session(response, account), 'local_admin': True}
            return {'user': None, 'csrf': None, 'local_admin': False, 'needs_admin': db.one("SELECT COUNT(*) AS n FROM users WHERE role='admin'")['n'] == 0}

    @app.post("/api/login")
    def login(body: Credentials, request: Request, response: Response):
        key = (request.client.host, body.username.lower())
        attempts = login_attempts[key]
        moment = time.monotonic()
        while attempts and attempts[0] < moment - 900:
            attempts.popleft()
        if len(attempts) >= 10:
            raise HTTPException(429, "登录尝试过多，请 15 分钟后重试。")
        attempts.append(moment)
        account = db.one("SELECT * FROM users WHERE username=?", (body.username.lower(),))
        dummy = "scrypt$" + "00" * 16 + "$" + "00" * 64
        valid = verify_password(body.password, account["password_hash"] if account else dummy)
        if not account or not valid:
            raise HTTPException(401, "用户名或密码错误。")
        attempts.clear()
        if len(login_attempts) > 3000:
            login_attempts.clear()
        return make_session(response, account)

    @app.post("/api/logout")
    def logout(request: Request, response: Response, current=Depends(user)):
        db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash(request.cookies.get(COOKIE, "")),))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/admin/users")
    def users(current=Depends(admin)):
        return db.all("SELECT id,username,display_name,role,created FROM users ORDER BY created")

    @app.post("/api/admin/users")
    def create_user(body: Credentials, current=Depends(admin)):
        account = new_account(body)
        try:
            with db.connect() as con:
                insert_account(con, account)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "这个用户名已经存在。") from exc
        return public_user(account)

    @app.get("/api/settings")
    def settings(current=Depends(user)):
        return vault.public(db, current["id"])

    @app.put("/api/settings")
    def save_settings(body: SettingsRequest, current=Depends(user)):
        try:
            base = validate_base_url(body.ai.base_url, config.allow_local_api)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        previous = vault.get(db, current["id"])
        profiles = previous.get('ai_profiles', {})
        profiles[previous['ai']['provider']] = previous['ai']
        incoming = dict(body.ai_profiles)
        incoming[body.ai.provider] = body.ai
        for name, profile in incoming.items():
            value = profile.model_dump()
            try:
                value['base_url'] = validate_base_url(value['base_url'], config.allow_local_api)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            if value['api_key'] is None:
                old = profiles.get(name, {})
                # A key is never carried to a different endpoint automatically.
                value['api_key'] = old.get('api_key', '') if old.get('base_url', '').rstrip('/') == value['base_url'] else ''
            profiles[name] = value
        value = {'ai': profiles[body.ai.provider], 'ai_profiles': profiles, 'glossary': body.glossary}
        value['ai_tasks'] = {k: v.model_dump() for k, v in body.ai_tasks.items()} if body.ai_tasks is not None else previous.get('ai_tasks', {})
        for selection in value['ai_tasks'].values():
            if selection['provider'] not in profiles:
                raise HTTPException(400, '请先配置任务所选服务商的 API 连接。')
        value['translation_concurrency'] = body.translation_concurrency if body.translation_concurrency is not None else previous.get('translation_concurrency', 6)
        for name in ['baidu', 'youdao']:
            value[name] = getattr(body, name).model_dump()
            if value[name]['secret'] is None:
                value[name]['secret'] = previous[name]['secret']
        ms = body.microsoft
        if any(len(str(v or '')) > 2048 for v in ms.values()) or not re.fullmatch(r'[a-z0-9-]{0,60}', ms.get('region') or 'eastasia'):
            raise HTTPException(400, 'Microsoft 配置格式有误。')
        value['microsoft'] = {'api_key': ms.get('api_key') if ms.get('api_key') is not None else previous['microsoft']['api_key'], 'region': ms.get('region') or previous['microsoft']['region']}
        vault.put(db, current['id'], value)
        return vault.public(db, current["id"])

    @app.get("/api/usage")
    def usage(current=Depends(user)):
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        used = db.one("SELECT characters,calls FROM usage WHERE user_id=? AND day=?", (current["id"], day)) or {"characters": 0, "calls": 0}
        return {**used, "day": day, "limit": config.daily_text_limit, "max_upload_mb": config.max_upload_mb, "max_pages": config.max_pages}

    @app.post('/api/settings/test')
    async def test_settings(body: TestSettingsRequest, current=Depends(user)):
        settings=vault.get(db,current['id'])
        try:
            charge = lambda n: app.state.jobs.charge(current['id'], n)
            if body.engine == 'ai' and body.purpose == 'chat':
                text = await app.state.jobs.providers.ai(settings, [{'role':'user', 'content':'请只回复：问答连接正常。'}], charge, purpose='chat')
            else:
                text=await app.state.jobs.providers.translate(settings,body.engine,'A good question is the beginning of understanding.','',charge)
        except ProviderError as exc:
            raise HTTPException(400,str(exc)) from exc
        return {'text':text[:800]}

    def document_public(doc):
        result = dict(doc)
        result.pop("user_id", None)
        result.pop("sha256", None)
        result["warnings"] = json.loads(result["warnings"])
        return result

    @app.get("/api/documents")
    def list_documents(current=Depends(user)):
        rows = db.all("SELECT d.*, (SELECT COUNT(*) FROM paragraphs p WHERE p.document_id=d.id) AS paragraph_count, r.paragraph_id AS reading_paragraph, r.page AS reading_page FROM documents d LEFT JOIN reading_progress r ON r.document_id=d.id WHERE d.user_id=? ORDER BY d.created DESC,d.rowid DESC", (current["id"],))
        return [document_public(x) for x in rows]

    async def import_file(handle, filename, current):
        document_id, size = uid(), 0
        path = db.pdf_path(document_id)
        digest = hashlib.sha256()
        try:
            with path.open("wb") as output:
                while chunk := await handle.read(1024 * 1024):
                    if size == 0 and b"%PDF-" not in chunk[:1024]:
                        raise HTTPException(400, "文件不是有效的 PDF。")
                    size += len(chunk)
                    if size > config.max_upload_mb * 1024 * 1024:
                        raise HTTPException(413, f"单篇 PDF 上限为 {config.max_upload_mb}MB。")
                    digest.update(chunk)
                    output.write(chunk)
            if not size:
                raise HTTPException(400, "上传文件为空。")
            existing = db.one("SELECT * FROM documents WHERE user_id=? AND sha256=?", (current["id"], digest.hexdigest()))
            if existing:
                path.unlink(missing_ok=True)
                return {"document": document_public(existing), "duplicate": True}
            filename = filename.replace("\\", "/").rsplit("/", 1)[-1][:200] or "paper.pdf"
            with db.connect() as con:
                con.execute("BEGIN IMMEDIATE")
                used = con.execute("SELECT COALESCE(SUM(size),0) FROM documents WHERE user_id=?", (current["id"],)).fetchone()[0]
                if used + size > config.max_storage_mb * 1024 * 1024:
                    raise HTTPException(413, "已达到个人文献存储上限，请清理文件或联系管理员。")
                con.execute("INSERT INTO documents(id,user_id,filename,title,sha256,size,created,updated) VALUES (?,?,?,?,?,?,?,?)", (document_id, current["id"], filename, filename, digest.hexdigest(), size, now(), now()))
                con.execute("INSERT INTO jobs(id,user_id,document_id,kind,total,created,updated) VALUES (?,?,?,?,?,?,?)", (uid(), current["id"], document_id, "parse", 1, now(), now()))
            return {"document": document_public(owned(document_id, current)), "duplicate": False}
        except sqlite3.IntegrityError:
            path.unlink(missing_ok=True)
            existing = db.one("SELECT * FROM documents WHERE user_id=? AND sha256=?", (current["id"], digest.hexdigest()))
            if existing:
                return {"document": document_public(existing), "duplicate": True}
            raise
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            await handle.close()

    @app.post("/api/documents", status_code=202)
    async def upload(file: UploadFile = File(...), current=Depends(user)):
        return await import_file(file, file.filename or "paper.pdf", current)

    @app.post("/api/sample", status_code=202)
    async def sample(current=Depends(user)):
        path = ROOT / "examples" / "paperpilot-sample.pdf"
        from io import BytesIO
        return await import_file(UploadFile(file=BytesIO(path.read_bytes()), filename="PaperPilot 合成测试文献.pdf"), "PaperPilot 合成测试文献.pdf", current)

    def detail(document_id, current, engine):
        doc = owned(document_id, current)
        if engine not in ENGINES:
            raise HTTPException(400, "未知翻译引擎。")
        paragraphs = db.all("SELECT * FROM paragraphs WHERE document_id=? ORDER BY ordinal", (document_id,))
        settings_value = vault.get(db, current["id"])
        translations = db.all("SELECT t.* FROM translations t JOIN paragraphs p ON p.id=t.paragraph_id WHERE p.document_id=? AND t.engine=?", (document_id, engine))
        lookup = {(t["paragraph_id"], t["signature"]): t for t in translations}
        for index, paragraph in enumerate(paragraphs):
            signature = translation_signature(settings_value, engine, paragraph["text"], context_for(paragraphs, index) if engine == "ai" else "")
            paragraph["translation"] = lookup.get((paragraph["id"], signature))
            paragraph["locations"] = json.loads(paragraph["locations"])
            paragraph['content'] = json.loads(paragraph.get('content') or '{}')
            paragraph["edited"] = paragraph.pop("source_text") != paragraph["text"]
        jobs = db.all("SELECT id,kind,status,total,completed,failed,error,cancel_requested,created,updated FROM jobs WHERE document_id=? ORDER BY created DESC,rowid DESC LIMIT 12", (document_id,))
        messages = db.all("SELECT * FROM messages WHERE document_id=? ORDER BY rowid", (document_id,))
        for message in messages:
            message["evidence"] = json.loads(message["evidence"])
        return {"document": document_public(doc), "paragraphs": paragraphs, "jobs": jobs, "progress": db.one("SELECT * FROM reading_progress WHERE document_id=?", (document_id,)), "notes": db.one("SELECT text,updated FROM notes WHERE document_id=?", (document_id,)) or {"text": "", "updated": None}, "messages": messages, 'has_previous_layout': bool(db.one('SELECT id FROM document_versions WHERE document_id=? LIMIT 1', (document_id,)))}

    @app.get("/api/documents/{document_id}")
    def get_document(document_id: str, engine: str = "ai", current=Depends(user)):
        return detail(document_id, current, engine)

    @app.get("/api/documents/{document_id}/pdf")
    def pdf(document_id: str, current=Depends(user)):
        doc = owned(document_id, current)
        return FileResponse(db.pdf_path(document_id), media_type="application/pdf", filename=doc["filename"], content_disposition_type="inline")

    @app.get('/api/documents/{document_id}/assets/{asset}')
    def asset(document_id: str, asset: str, current=Depends(user)):
        owned(document_id, current)
        if not re.fullmatch(r'[a-f0-9]{32}\.png', asset):
            raise HTTPException(404)
        path = db.media_path(document_id) / asset
        if not path.is_file():
            raise HTTPException(404, '片段尚未生成，请更新文献重排。')
        return FileResponse(path, media_type='image/png')

    @app.patch("/api/documents/{document_id}")
    def rename(document_id: str, body: TextRequest, current=Depends(user)):
        owned(document_id, current)
        title = body.text.strip()[:200]
        if not title:
            raise HTTPException(400, "标题不能为空。")
        db.execute("UPDATE documents SET title=?,updated=? WHERE id=?", (title, now(), document_id))
        return {"ok": True}

    def active_job(document_id, kind=None):
        sql = "SELECT * FROM jobs WHERE document_id=? AND status IN ('queued','running')"
        args = [document_id]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        return db.one(sql, args)

    @app.delete("/api/documents/{document_id}")
    def delete_document(document_id: str, current=Depends(user)):
        owned(document_id, current)
        if active_job(document_id):
            raise HTTPException(409, "文献仍有任务进行中，请先停止翻译或等待解析完成。")
        db.execute("DELETE FROM documents WHERE id=?", (document_id,))
        db.pdf_path(document_id).unlink(missing_ok=True)
        media = db.media_path(document_id)
        if media.is_dir() and media.resolve().parent == (db.directory / 'media').resolve():
            shutil.rmtree(media)
        return {"ok": True}

    @app.post("/api/documents/{document_id}/reparse", status_code=202)
    def reparse(document_id: str, current=Depends(user)):
        doc = owned(document_id, current)
        if active_job(document_id):
            raise HTTPException(409, "请等待当前任务完成后再更新重排。")
        with db.connect() as con:
            con.execute("UPDATE documents SET status='queued',error=NULL WHERE id=?", (document_id,))
        return {"job_id": app.state.jobs.enqueue(current["id"], document_id, "parse", total=1)}

    @app.post('/api/documents/{document_id}/ai-reflow', status_code=202)
    def ai_reflow(document_id: str, current=Depends(user)):
        doc = owned(document_id, current)
        if active_job(document_id) or doc['status'] != 'ready':
            raise HTTPException(409, '请等待文献解析和其他任务完成。')
        if doc['parser_version'] < 2:
            raise HTTPException(409, '请先更新基础重排，再使用 AI 调整段落。')
        try:
            ensure_configured(vault.get(db, current['id']), 'ai', 'chat')
        except ProviderError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {'job_id': app.state.jobs.enqueue(current['id'], document_id, 'ai_reflow', total=doc['page_count'])}

    @app.post('/api/documents/{document_id}/restore-layout')
    def restore_layout(document_id: str, current=Depends(user)):
        owned(document_id, current)
        if active_job(document_id):
            raise HTTPException(409, '请等待当前任务完成。')
        row = db.one('SELECT * FROM document_versions WHERE document_id=? ORDER BY created DESC,rowid DESC LIMIT 1', (document_id,))
        if not row:
            raise HTTPException(404, '没有之前的重排版本。')
        saved = json.loads(row['payload'])
        with db.connect() as con:
            db.snapshot(con, document_id)
            con.execute('DELETE FROM paragraphs WHERE document_id=?', (document_id,))
            for table in ['paragraphs', 'translations']:
                for value in saved[table]:
                    keys=list(value)
                    con.execute(f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})", tuple(value[k] for k in keys))
            old=saved['document']
            con.execute("UPDATE documents SET status='ready',warnings=?,parser_version=?,updated=? WHERE id=?", (old['warnings'], old.get('parser_version', 1), now(), document_id))
            if saved['progress']:
                p=saved['progress'];con.execute('INSERT OR REPLACE INTO reading_progress(document_id,paragraph_id,page,updated) VALUES (?,?,?,?)', (document_id,p['paragraph_id'],p['page'],now()))
            con.execute('DELETE FROM document_versions WHERE id=?', (row['id'],))
        return {'ok': True}

    @app.patch("/api/documents/{document_id}/paragraphs/{paragraph_id}")
    def edit_paragraph(document_id: str, paragraph_id: str, body: TextRequest, current=Depends(user)):
        owned(document_id, current)
        if active_job(document_id):
            raise HTTPException(409, "请等待当前任务完成后再修正原文。")
        if not body.text.strip():
            raise HTTPException(400, "段落内容不能为空。")
        changed = db.execute("UPDATE paragraphs SET text=? WHERE id=? AND document_id=?", (body.text.strip(), paragraph_id, document_id))
        if not changed:
            raise HTTPException(404, "未找到段落。")
        return {"ok": True}

    @app.post("/api/documents/{document_id}/translate", status_code=202)
    def translate(document_id: str, body: TranslationRequest, current=Depends(user)):
        doc = owned(document_id, current)
        if doc["status"] != "ready":
            raise HTTPException(409, "请等待 PDF 解析完成；扫描件需要先完成 OCR。")
        try:
            ensure_configured(vault.get(db, current["id"]), body.engine)
        except ProviderError as exc:
            raise HTTPException(400, str(exc)) from exc
        if active_job(document_id, 'ai_reflow'):
            raise HTTPException(409, 'AI 重排进行中，请完成后再翻译。')
        if active_job(document_id, "translate"):
            raise HTTPException(409, "这篇文献已有翻译任务，请等待完成或先停止它。")
        rows = db.all("SELECT id FROM paragraphs WHERE document_id=? AND kind NOT IN ('equation','figure','table','authors') ORDER BY ordinal", (document_id,))
        valid_ids = {p["id"] for p in rows}
        selected = list(dict.fromkeys(body.paragraph_ids)) if body.paragraph_ids is not None else [p["id"] for p in rows]
        if not selected or not set(selected).issubset(valid_ids):
            raise HTTPException(400, "需要选择属于当前文献的有效段落。")
        try:
            job_id = app.state.jobs.enqueue(current["id"], document_id, "translate", {"engine": body.engine, "paragraph_ids": selected, "force": body.force}, len(selected))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "这篇文献已有翻译任务。") from exc
        return {"job_id": job_id}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str, current=Depends(user)):
        job = db.one("SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, current["id"]))
        if not job:
            raise HTTPException(404, "未找到任务。")
        if job["kind"] != "translate":
            raise HTTPException(400, "仅翻译任务支持停止。")
        db.execute("UPDATE jobs SET cancel_requested=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,updated=? WHERE id=?", (now(), job_id))
        return {"ok": True}

    @app.put("/api/documents/{document_id}/progress")
    def progress(document_id: str, body: ProgressRequest, current=Depends(user)):
        doc = owned(document_id, current)
        if body.paragraph_id and not db.one("SELECT id FROM paragraphs WHERE id=? AND document_id=?", (body.paragraph_id, document_id)):
            raise HTTPException(400, "阅读位置不属于这篇文献。")
        page = min(body.page, max(1, doc["page_count"]))
        db.execute("INSERT INTO reading_progress(document_id,paragraph_id,page,updated) VALUES (?,?,?,?) ON CONFLICT(document_id) DO UPDATE SET paragraph_id=excluded.paragraph_id,page=excluded.page,updated=excluded.updated", (document_id, body.paragraph_id, page, now()))
        return {"ok": True}

    @app.put("/api/documents/{document_id}/notes")
    def notes(document_id: str, body: TextRequest, current=Depends(user)):
        owned(document_id, current)
        db.execute("INSERT INTO notes(document_id,text,updated) VALUES (?,?,?) ON CONFLICT(document_id) DO UPDATE SET text=excluded.text,updated=excluded.updated", (document_id, body.text, now()))
        return {"ok": True}

    @app.post("/api/documents/{document_id}/chat", status_code=202)
    def chat(document_id: str, body: ChatRequest, current=Depends(user)):
        doc = owned(document_id, current)
        if doc["status"] != "ready":
            raise HTTPException(409, "请先完成 PDF 文字解析。")
        if body.paragraph_id and not db.one("SELECT id FROM paragraphs WHERE id=? AND document_id=?", (body.paragraph_id, document_id)):
            raise HTTPException(400, "提问段落不属于当前文献。")
        try:
            ensure_configured(vault.get(db, current["id"]), "ai", 'chat')
        except ProviderError as exc:
            raise HTTPException(400, str(exc)) from exc
        if active_job(document_id, "chat"):
            raise HTTPException(409, "请等待上一条回答完成。")
        question_id, answer_id = uid(), uid()
        job_id = uid()
        try:
            with db.connect() as con:
                con.execute("INSERT INTO messages(id,document_id,role,text,created) VALUES (?,?,?,?,?)", (question_id, document_id, "user", body.question.strip(), now()))
                con.execute("INSERT INTO messages(id,document_id,role,text,status,created) VALUES (?,?,?,?,?,?)", (answer_id, document_id, "assistant", "", "running", now()))
                payload = {"question_id": question_id, "answer_id": answer_id, "question": body.question.strip(), "paragraph_id": body.paragraph_id}
                con.execute("INSERT INTO jobs(id,user_id,document_id,kind,payload,total,created,updated) VALUES (?,?,?,?,?,?,?,?)", (job_id, current["id"], document_id, "chat", dumps(payload), 1, now(), now()))
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "请等待上一条回答完成。") from exc
        return {"job_id": job_id}

    @app.get("/api/documents/{document_id}/export")
    def export(document_id: str, engine: str = "ai", current=Depends(user)):
        result = detail(document_id, current, engine)
        lines = [f"# {result['document']['title']}", "", f"来源文件：{result['document']['filename']}", ""]
        for p in result["paragraphs"]:
            lines.extend([f"## P{p['ordinal'] + 1} · PDF 第 {p['page']} 页", "", p["text"], ""])
            t = p["translation"]
            if t and t["status"] == "done":
                lines.extend([t["text"], "", f"译文引擎：{t['provider']}", ""])
        if result["notes"]["text"]:
            lines.extend(["# 阅读笔记", "", result["notes"]["text"]])
        return Response("\n".join(lines), media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(result['document']['title'][:80] + '.md')}"})

    if config.frontend_dir.exists():
        app.mount("/", StaticFiles(directory=config.frontend_dir, html=True), name="frontend")
    else:
        @app.get("/")
        def missing_build():
            return JSONResponse({"detail": "前端尚未构建，请运行 python run.py，或在 frontend 执行 npm ci 与 npm run build。"}, status_code=503)

    return app
