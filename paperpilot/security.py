import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

from .db import dumps


def hash_password(password: str):
    if not 12 <= len(password) <= 256:
        raise ValueError("密码需要 12～256 个字符。")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str):
    try:
        if len(password) > 256:
            return False
        _, salt, expected = stored.split("$")
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def token_hash(token: str):
    return hashlib.sha256(token.encode()).hexdigest()


DEFAULT_SETTINGS = {
    "ai": {"provider": "deepseek", "base_url": "https://api.deepseek.com", "model": "deepseek-flash", "api_key": ""},
    'ai_profiles': {},
    'ai_tasks': {},
    'translation_concurrency': 6,
    "baidu": {"app_id": "", "secret": ""},
    'youdao': {'app_id': '', 'secret': ''},
    'microsoft': {'api_key': '', 'region': 'eastasia'},
    "glossary": "",
}


class Vault:
    def __init__(self, directory: Path):
        path = directory / "secret.key"
        if not path.exists():
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(Fernet.generate_key())
            except FileExistsError:
                pass
        self.fernet = Fernet(path.read_bytes().strip())

    def get(self, db, user_id):
        row = db.one("SELECT encrypted FROM settings WHERE user_id=?", (user_id,))
        if not row:
            return json.loads(dumps(DEFAULT_SETTINGS))
        value = json.loads(self.fernet.decrypt(row["encrypted"].encode()).decode())
        for key, default in DEFAULT_SETTINGS.items():
            value.setdefault(key, json.loads(dumps(default)))
        # Migrate only the obsolete official default; preserve custom model IDs.
        if value['ai']['provider'] == 'deepseek' and value['ai']['model'] == 'deepseek-chat' and value['ai']['base_url'].rstrip('/') in {'https://api.deepseek.com', 'https://api.deepseek.com/v1'}:
            value['ai']['model'] = 'deepseek-flash'
        return value

    def put(self, db, user_id, value):
        encrypted = self.fernet.encrypt(dumps(value).encode()).decode()
        db.execute("INSERT INTO settings(user_id,encrypted) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET encrypted=excluded.encrypted", (user_id, encrypted))

    def public(self, db, user_id):
        value = self.get(db, user_id)
        # Old installations keep using their existing model until explicitly
        # assigning tasks. Expose selections so the editor can show both roles.
        for role in ['translation', 'chat']:
            value['ai_tasks'].setdefault(role, {k: value['ai'][k] for k in ['provider', 'model']})
        value['ai_profiles'].setdefault(value['ai']['provider'], dict(value['ai']))
        value["ai"]["has_key"] = bool(value["ai"].pop("api_key", ""))
        value["baidu"]["has_secret"] = bool(value["baidu"].pop("secret", ""))
        value['youdao']['has_secret'] = bool(value['youdao'].pop('secret', ''))
        value['microsoft']['has_key'] = bool(value['microsoft'].pop('api_key', ''))
        for profile in value.get('ai_profiles', {}).values():
            profile['has_key'] = bool(profile.pop('api_key', ''))
        return value
