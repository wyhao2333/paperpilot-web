import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


@dataclass
class Config:
    data_dir: Path
    origin: str = "http://localhost:8765"
    secure_cookies: bool = False
    max_upload_mb: int = 50
    max_pages: int = 200
    max_storage_mb: int = 1024
    daily_text_limit: int = 0  # 0 disables the optional application quota.
    allow_local_api: bool = False
    local_admin_autologin: bool = True
    frontend_dir: Path = ROOT / "frontend" / "dist"
    parser_url: str | None = None

    @classmethod
    def from_env(cls):
        path = Path(os.getenv("APP_DATA_DIR", "data"))
        origin = os.getenv("APP_ORIGIN", "http://localhost:8765").rstrip("/")
        secure = os.getenv("APP_SECURE_COOKIES", "auto").lower()
        if secure not in {"auto", "true", "false"}:
            raise ValueError("APP_SECURE_COOKIES must be auto, true or false")
        if urlsplit(origin).scheme == "https" and secure == "false":
            raise ValueError("HTTPS deployments require APP_SECURE_COOKIES=true or auto")
        return cls(
            data_dir=(ROOT / path).resolve() if not path.is_absolute() else path,
            origin=origin,
            secure_cookies=secure == "true" or (secure == "auto" and urlsplit(origin).scheme == "https"),
            max_upload_mb=int(os.getenv("MAX_UPLOAD_MB", "50")),
            max_pages=int(os.getenv("MAX_PDF_PAGES", "200")),
            max_storage_mb=int(os.getenv("MAX_USER_STORAGE_MB", "1024")),
            daily_text_limit=int(os.getenv("DAILY_TEXT_LIMIT", "0")),
            allow_local_api=os.getenv("ALLOW_LOCAL_API", "false").lower() == "true",
            local_admin_autologin=os.getenv('LOCAL_ADMIN_AUTOLOGIN', 'true').lower() == 'true',
            parser_url=os.getenv("PDF_PARSER_URL") or None,
        )
