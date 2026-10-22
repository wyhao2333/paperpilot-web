"""Request boundaries that run before JSON or multipart parsing."""
import math
import re
import threading
import time
from collections import OrderedDict, deque
from urllib.parse import urlsplit

from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse


class WindowLimiter:
    """Bound both request counts and storage; never evict an active penalty."""

    def __init__(self, limit, seconds, *, max_keys=4096, clock=time.monotonic):
        self.limit, self.seconds, self.max_keys = limit, seconds, max_keys
        self.clock, self.entries, self.lock = clock, OrderedDict(), threading.Lock()

    def take(self, key):
        with self.lock:
            moment = self.clock()
            cutoff = moment - self.seconds
            while self.entries and next(iter(self.entries.values()))[-1] <= cutoff:
                self.entries.popitem(last=False)
            attempts = self.entries.get(key)
            if attempts is None:
                if len(self.entries) >= self.max_keys:
                    first = next(iter(self.entries.values()))[-1]
                    return max(1, math.ceil(first + self.seconds - moment))
                attempts = self.entries[key] = deque()
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= self.limit:
                return max(1, math.ceil(attempts[0] + self.seconds - moment))
            attempts.append(moment)
            self.entries.move_to_end(key)
            return None

    def forget(self, key):
        with self.lock:
            self.entries.pop(key, None)


class LoginProtection:
    def __init__(self):
        self.per_ip = WindowLimiter(30, 60, max_keys=2048)
        self.global_requests = WindowLimiter(120, 60, max_keys=1)
        self.accounts = WindowLimiter(10, 900)

    def request(self, host):
        return self.per_ip.take(host) or self.global_requests.take("login")


class BodyTooLarge(HTTPException):
    def __init__(self, detail):
        super().__init__(413, detail)


class RequestBoundaryMiddleware:
    def __init__(self, app, *, config, login_protection):
        self.app, self.config, self.login_protection = app, config, login_protection

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path, method = scope["path"], scope["method"]
        is_api = path.startswith("/api/")
        started = False

        async def secure_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "same-origin"
                headers["X-Frame-Options"] = "DENY"
                headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; worker-src 'self' blob:; font-src 'self' data:; frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
                if is_api or path in {"/", "/index.html"}:
                    headers["Cache-Control"] = "no-store"
            await send(message)

        async def reject(status, detail, headers=None):
            await JSONResponse({"detail": detail}, status_code=status, headers=headers)(scope, receive, secure_send)

        maximum, detail = 512 * 1024, "请求内容过大。"
        if is_api:
            headers = Headers(scope=scope)
            if method not in {"GET", "HEAD", "OPTIONS"}:
                allowed = {self.config.origin}
                parsed = urlsplit(self.config.origin)
                if parsed.hostname in {"localhost", "127.0.0.1"}:
                    port = parsed.port or 80
                    allowed.update({f"http://localhost:{port}", f"http://127.0.0.1:{port}"})
                origin = headers.get("origin")
                if (origin and origin not in allowed) or headers.get("x-paperpilot") != "1":
                    return await reject(403, "请求来源未获允许，请从本服务页面操作。")
            if path.rstrip("/") == "/api/documents" and method == "POST":
                maximum = (self.config.max_upload_mb + 1) * 1024 * 1024
                detail = f"上传上限为 {self.config.max_upload_mb}MB。"
            lengths = headers.getlist("content-length")
            if lengths:
                if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,12}", lengths[0]) or "transfer-encoding" in headers:
                    return await reject(400, "Content-Length 无效。")
                if int(lengths[0]) > maximum:
                    return await reject(413, detail)
            if path.rstrip("/") == "/api/login" and method == "POST":
                # Uvicorn ignores forwarded headers; arbitrary X-Forwarded-For
                # values cannot create new buckets. Tunnel users share this cap.
                host = scope.get("client", ("unknown", 0))[0]
                retry = self.login_protection.request(host)
                if retry:
                    return await reject(429, "登录请求过多，请稍后重试。", {"Retry-After": str(retry)})

        received = 0

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if is_api and message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > maximum:
                    raise BodyTooLarge(detail)
            return message

        try:
            await self.app(scope, bounded_receive, secure_send)
        except BodyTooLarge:
            if started:
                raise
            await reject(413, detail)
