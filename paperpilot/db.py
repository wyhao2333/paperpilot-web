import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


def uid():
    return uuid.uuid4().hex


def now():
    return int(time.time())


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
 display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
 role TEXT NOT NULL DEFAULT 'user', created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 csrf TEXT NOT NULL, expires INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 filename TEXT NOT NULL, title TEXT NOT NULL, sha256 TEXT NOT NULL, size INTEGER NOT NULL,
 page_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'queued',
 error TEXT, warnings TEXT NOT NULL DEFAULT '[]', created INTEGER NOT NULL, updated INTEGER NOT NULL,
 UNIQUE(user_id, sha256)
);
CREATE TABLE IF NOT EXISTS paragraphs (
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, page INTEGER NOT NULL, kind TEXT NOT NULL,
 text TEXT NOT NULL, source_text TEXT NOT NULL, locations TEXT NOT NULL,
 UNIQUE(document_id, ordinal)
);
CREATE TABLE IF NOT EXISTS translations (
 id TEXT PRIMARY KEY, paragraph_id TEXT NOT NULL REFERENCES paragraphs(id) ON DELETE CASCADE,
 engine TEXT NOT NULL, signature TEXT NOT NULL, status TEXT NOT NULL,
 text TEXT NOT NULL DEFAULT '', error TEXT, provider TEXT NOT NULL, updated INTEGER NOT NULL,
 UNIQUE(paragraph_id, engine, signature)
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', payload TEXT NOT NULL DEFAULT '{}',
 total INTEGER NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0,
 failed INTEGER NOT NULL DEFAULT 0, error TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
 created INTEGER NOT NULL, updated INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, created);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_one_active ON jobs(document_id,kind) WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS reading_progress (
 document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
 paragraph_id TEXT, page INTEGER NOT NULL DEFAULT 1, updated INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
 document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
 text TEXT NOT NULL DEFAULT '', updated INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
 user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, encrypted TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS usage (
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, day TEXT NOT NULL,
 characters INTEGER NOT NULL DEFAULT 0, calls INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(user_id, day)
);
CREATE TABLE IF NOT EXISTS messages (
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 role TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'done',
 evidence TEXT NOT NULL DEFAULT '[]', created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS document_versions (
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 created INTEGER NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reflow_cache (
 document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 signature TEXT NOT NULL, groups_json TEXT NOT NULL, created INTEGER NOT NULL,
 PRIMARY KEY(document_id, signature)
);
PRAGMA user_version=1;
"""


class Database:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "pdfs").mkdir(exist_ok=True)
        (directory / "tmp").mkdir(exist_ok=True)
        self.directory = directory
        self.path = directory / "paperpilot.sqlite3"
        with self.connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(SCHEMA)
            for table, column, declaration in [
                ('paragraphs', 'content', "TEXT NOT NULL DEFAULT '{}'"),
                ('documents', 'parser_version', 'INTEGER NOT NULL DEFAULT 1'),
            ]:
                if column not in {r[1] for r in con.execute(f'PRAGMA table_info({table})')}:
                    con.execute(f'ALTER TABLE {table} ADD COLUMN {column} {declaration}')
            con.execute('PRAGMA user_version=2')

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def one(self, sql, params=()):
        with self.connect() as con:
            row = con.execute(sql, params).fetchone()
            return dict(row) if row else None

    def all(self, sql, params=()):
        with self.connect() as con:
            return [dict(x) for x in con.execute(sql, params).fetchall()]

    def execute(self, sql, params=()):
        with self.connect() as con:
            return con.execute(sql, params).rowcount

    def pdf_path(self, document_id):
        # IDs are generated by the server; never use an uploaded filename as a path.
        return self.directory / "pdfs" / f"{document_id}.pdf"

    def media_path(self, document_id):
        return self.directory / 'media' / document_id

    def snapshot(self, con, document_id):
        paragraphs = [dict(r) for r in con.execute('SELECT * FROM paragraphs WHERE document_id=? ORDER BY ordinal', (document_id,))]
        if not paragraphs:
            return
        payload = {
            'paragraphs': paragraphs,
            'translations': [dict(r) for r in con.execute('SELECT t.* FROM translations t JOIN paragraphs p ON t.paragraph_id=p.id WHERE p.document_id=?', (document_id,))],
            'document': dict(con.execute('SELECT * FROM documents WHERE id=?', (document_id,)).fetchone()),
            'progress': dict(r) if (r := con.execute('SELECT * FROM reading_progress WHERE document_id=?', (document_id,)).fetchone()) else None,
        }
        con.execute('INSERT INTO document_versions(id,document_id,created,payload) VALUES (?,?,?,?)', (uid(), document_id, now(), dumps(payload)))

    def replace_paragraphs(self, con, document_id, paragraphs, snapshot=True):
        if snapshot:
            self.snapshot(con, document_id)
        con.execute('DELETE FROM paragraphs WHERE document_id=?', (document_id,))
        for i, p in enumerate(paragraphs):
            con.execute('INSERT INTO paragraphs(id,document_id,ordinal,page,kind,text,source_text,locations,content) VALUES (?,?,?,?,?,?,?,?,?)',
                        (uid(), document_id, i, p['page'], p['kind'], p['text'], p['text'], dumps(p['locations']), dumps(p.get('content', {}))))
        progress = con.execute('SELECT page FROM reading_progress WHERE document_id=?', (document_id,)).fetchone()
        if progress:
            p = con.execute('SELECT id,page FROM paragraphs WHERE document_id=? ORDER BY ABS(page-?),ordinal LIMIT 1', (document_id, progress['page'])).fetchone()
            if p:
                con.execute('UPDATE reading_progress SET paragraph_id=?,page=? WHERE document_id=?', (p['id'], p['page'], document_id))
