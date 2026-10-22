"""Private parser container entry point; no database, vault or internet access."""
import argparse
import asyncio
import json
import os
import re
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .parse_runner import MAX_REPLY_BYTES, encode_assets, parse_subprocess


class ParserHandler(BaseHTTPRequestHandler):
    # Close every response: an idle keep-alive connection cannot hold this
    # deliberately single-job server. This port is never published to the host.
    protocol_version = "HTTP/1.0"

    def log_message(self, *_args):
        pass

    def reply(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        if len(body) > MAX_REPLY_BYTES:
            status, body = 413, b'{"error":"PDF parser response exceeds resource limit."}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.reply(200 if self.path == "/health" else 404, {"status": "ok"} if self.path == "/health" else {"error": "Not found"})

    def do_POST(self):
        self.connection.settimeout(30)
        if self.path != "/parse":
            return self.reply(404, {"error": "Not found"})
        lengths = self.headers.get_all("Content-Length", [])
        pages = self.headers.get("X-Max-Pages", str(self.server.max_pages))
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,12}", lengths[0]) or self.headers.get("Transfer-Encoding"):
            return self.reply(400, {"error": "Invalid content length"})
        length = int(lengths[0])
        if not 0 < length <= self.server.max_upload_bytes:
            return self.reply(413, {"error": "PDF exceeds upload limit"})
        if not re.fullmatch(r"[0-9]{1,5}", pages) or not 1 <= int(pages) <= self.server.max_pages:
            return self.reply(400, {"error": "Invalid page limit"})
        try:
            with tempfile.TemporaryDirectory(prefix="paperpilot-parse-") as temporary:
                directory = Path(temporary)
                pdf, assets = directory / "source.pdf", directory / "assets"
                assets.mkdir()
                with pdf.open("wb") as handle:
                    remaining = length
                    while remaining:
                        chunk = self.rfile.read(min(65536, remaining))
                        if not chunk:
                            raise ValueError("PDF 上传中断，请重试。")
                        handle.write(chunk)
                        remaining -= len(chunk)
                result = asyncio.run(parse_subprocess(pdf, int(pages), assets))
                self.reply(200, {"result": result, "assets": encode_assets(assets)})
        except (ValueError, OSError) as exc:
            # This worker receives only PDFs, never provider credentials.
            self.reply(400, {"error": str(exc)[:350]})


def make_server(host="0.0.0.0", port=8766, *, max_upload_mb=50, max_pages=200):
    server = HTTPServer((host, port), ParserHandler)
    server.max_upload_bytes = max_upload_mb * 1024 * 1024
    server.max_pages = max_pages
    return server


def main():
    parser = argparse.ArgumentParser(description="Private PaperPilot PDF parser")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    with make_server(args.host, args.port, max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "50")), max_pages=int(os.getenv("MAX_PDF_PAGES", "200"))) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
