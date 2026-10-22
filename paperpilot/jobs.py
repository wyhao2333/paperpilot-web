import asyncio
import json
import logging
from datetime import datetime, timezone

from .parse_runner import parse_remote, parse_subprocess
from .ai_config import translation_concurrency
from .chat import CHAT_PROMPT, chat_context, chat_question, completed_history
from .db import dumps, now, uid
from .providers import ProviderError, TranslationStopped, ensure_configured, provider_label, translation_signature
from .semantic import REFLOW_PROMPT, batch_signature, compose, reflow_batches, required_starts, source_units, validate_groups

logger = logging.getLogger("paperpilot")


def context_for(paragraphs, index):
    previous = paragraphs[index - 1]["text"][-700:] if index else ""
    following = paragraphs[index + 1]["text"][:700] if index + 1 < len(paragraphs) else ""
    return f"Previous paragraph: {previous}\nNext paragraph: {following}"


class Jobs:
    def __init__(self, db, vault, providers, config):
        self.db, self.vault, self.providers, self.config = db, vault, providers, config
        self.tasks = []
        self.parse_semaphore = asyncio.Semaphore(1)

    def enqueue(self, user_id, document_id, kind, payload=None, total=0):
        job_id = uid()
        self.db.execute("INSERT INTO jobs(id,user_id,document_id,kind,payload,total,created,updated) VALUES (?,?,?,?,?,?,?,?)", (job_id, user_id, document_id, kind, dumps(payload or {}), total, now(), now()))
        return job_id

    async def start(self):
        self.db.execute("UPDATE jobs SET status=CASE WHEN cancel_requested=1 THEN 'cancelled' ELSE 'queued' END WHERE status='running'")
        self.db.execute("UPDATE translations SET status='error',error='上次任务被中断，可重试；已完成的译文仍然保留。' WHERE status='running'")
        self.db.execute("UPDATE documents SET status='queued' WHERE status='parsing'")
        self.db.execute("DELETE FROM sessions WHERE expires<?", (now(),))
        # Reserve a worker for interactive answers even when both document
        # workers are translating long papers.
        self.tasks = [asyncio.create_task(self.worker()) for _ in range(2)]
        self.tasks.append(asyncio.create_task(self.worker(chat_only=True)))

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def worker(self, chat_only=False):
        while True:
            job = None
            with self.db.connect() as con:
                con.execute("BEGIN IMMEDIATE")
                row = con.execute("SELECT * FROM jobs WHERE status='queued' AND " + ("kind='chat'" if chat_only else "kind<>'chat'") + " ORDER BY CASE WHEN total=1 THEN 0 ELSE 1 END, created, rowid LIMIT 1").fetchone()
                if row:
                    job = dict(row)
                    con.execute("UPDATE jobs SET status='running',updated=? WHERE id=?", (now(), job["id"]))
            if not job:
                await asyncio.sleep(.3)
                continue
            try:
                if job["cancel_requested"]:
                    self.db.execute("UPDATE jobs SET status='cancelled',updated=? WHERE id=?", (now(), job["id"]))
                    continue
                if job["kind"] == "parse":
                    await self.parse(job)
                elif job["kind"] == "translate":
                    await self.translate(job)
                elif job["kind"] == "chat":
                    await self.chat(job)
                elif job['kind'] == 'ai_reflow':
                    await self.ai_reflow(job)
            except asyncio.CancelledError:
                # Leave running jobs recoverable on the next startup.
                raise
            except Exception as exc:
                message = str(exc) if isinstance(exc, (ProviderError, ValueError)) else f"任务失败（{type(exc).__name__}），请重试或查看服务器日志。"
                if job['kind']=='ai_reflow' and isinstance(exc,ProviderError):
                    cached=self.db.one('SELECT COUNT(*) AS n FROM reflow_cache WHERE document_id=?',(job['document_id'],))['n']
                    message += ' 已校验批次已保存，重试会继续；当前阅读版和译文未修改。' if cached else ' 当前阅读版和译文未修改，可稍后重试。'
                self.db.execute("UPDATE jobs SET status='failed',error=?,updated=? WHERE id=?", (message[:500], now(), job["id"]))
                if job["kind"] == "parse":
                    self.db.execute("UPDATE documents SET status='error',error=?,updated=? WHERE id=?", (message[:500], now(), job["document_id"]))
                if job["kind"] == "chat":
                    answer_id = json.loads(job["payload"])["answer_id"]
                    self.db.execute("UPDATE messages SET status='error',text=? WHERE id=?", (message[:500], answer_id))
                logger.warning("Job %s failed: %s", job["id"], type(exc).__name__)

    async def parse(self, job):
        async with self.parse_semaphore:
            self.db.execute("UPDATE documents SET status='parsing',updated=? WHERE id=?", (now(), job["document_id"]))
            args = (self.db.pdf_path(job["document_id"]), self.config.max_pages, self.db.media_path(job["document_id"]))
            parsed = await parse_remote(*args, self.config.parser_url) if self.config.parser_url else await parse_subprocess(*args)
            with self.db.connect() as con:
                old_titles = [r['text'] for r in con.execute("SELECT text FROM (SELECT kind,text FROM paragraphs WHERE document_id=? ORDER BY ordinal LIMIT 5) WHERE kind='title'", (job['document_id'],))]
                self.db.replace_paragraphs(con, job['document_id'], parsed['paragraphs'])
                current_title = con.execute("SELECT title,filename FROM documents WHERE id=?", (job["document_id"],)).fetchone()
                titles = [p["text"] for p in parsed["paragraphs"][:5] if p["kind"] == "title"]
                generated_before=' '.join(old_titles)[:200]
                title = " ".join(titles)[:200] if titles and current_title["title"] in {current_title["filename"], generated_before} else current_title["title"]
                con.execute("UPDATE documents SET title=?,page_count=?,status=?,warnings=?,error=NULL,updated=? WHERE id=?", (title, parsed["page_count"], "needs_ocr" if parsed["needs_ocr"] else "ready", dumps(parsed["warnings"]), now(), job["document_id"]))
                con.execute('UPDATE documents SET parser_version=? WHERE id=?', (parsed.get('parser_version', 1),job['document_id']))
                con.execute("UPDATE jobs SET status='done',completed=1,total=1,updated=? WHERE id=?", (now(), job["id"]))

    def charge(self, user_id, count):
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self.db.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("INSERT OR IGNORE INTO usage(user_id,day) VALUES (?,?)", (user_id, day))
            used = con.execute("SELECT characters FROM usage WHERE user_id=? AND day=?", (user_id, day)).fetchone()[0]
            if self.config.daily_text_limit > 0 and used + count > self.config.daily_text_limit:
                raise ProviderError(f"已达到管理员设置的每日发送字符上限（{self.config.daily_text_limit:,}，UTC 日期重置）。这是本站限额，非 API 余额；管理员可将 DAILY_TEXT_LIMIT 设为 0 取消限制。")
            con.execute("UPDATE usage SET characters=characters+?,calls=calls+1 WHERE user_id=? AND day=?", (count, user_id, day))

    async def translate(self, job):
        payload = json.loads(job["payload"])
        engine = payload["engine"]
        settings = self.vault.get(self.db, job["user_id"])
        ensure_configured(settings, engine)
        paragraphs = self.db.all("SELECT * FROM paragraphs WHERE document_id=? ORDER BY ordinal", (job["document_id"],))
        wanted = set(payload["paragraph_ids"])
        completed, failed = 0, 0
        abort = False
        self.db.execute("UPDATE jobs SET total=?,completed=0,failed=0,error=NULL WHERE id=?", (len(wanted), job["id"]))
        pending = iter((i, p) for i, p in enumerate(paragraphs) if p['id'] in wanted)

        def cancelled():
            return self.db.one('SELECT cancel_requested FROM jobs WHERE id=?', (job['id'],))['cancel_requested']

        def charge(count):
            if abort or cancelled():
                raise TranslationStopped()
            self.charge(job['user_id'], count)

        async def translate_next():
            nonlocal completed, failed, abort
            while not abort and not cancelled():
                item = next(pending, None)
                if item is None:
                    return
                index, paragraph = item
                context = context_for(paragraphs, index) if engine == 'ai' else ''
                signature = translation_signature(settings, engine, paragraph['text'], context)
                args = (paragraph['id'], engine, signature)
                cached = self.db.one('SELECT status FROM translations WHERE paragraph_id=? AND engine=? AND signature=?', args)
                if cached and cached['status'] == 'done' and not payload.get('force'):
                    completed += 1
                else:
                    async with self.providers.translation_slot(settings, engine):
                        if abort or cancelled():
                            return
                        self.db.execute("INSERT INTO translations(id,paragraph_id,engine,signature,status,provider,updated) VALUES (?,?,?,?,?,?,?) ON CONFLICT(paragraph_id,engine,signature) DO UPDATE SET status='running',error=NULL,updated=excluded.updated", (uid(), *args, 'running', provider_label(settings, engine), now()))
                        try:
                            text = await self.providers.translate(settings, engine, paragraph['text'], context, charge)
                            self.db.execute("UPDATE translations SET status='done',text=?,error=NULL,updated=? WHERE paragraph_id=? AND engine=? AND signature=?", (text, now(), *args))
                            completed += 1
                        except (TranslationStopped, asyncio.CancelledError) as exc:
                            self.db.execute("UPDATE translations SET status='error',error=?,updated=? WHERE paragraph_id=? AND engine=? AND signature=?", ('任务已停止，已完成的译文保留，可继续翻译。', now(), *args))
                            if isinstance(exc, asyncio.CancelledError):
                                raise
                            return
                        except ProviderError as exc:
                            failed += 1
                            self.db.execute("UPDATE translations SET status='error',error=?,updated=? WHERE paragraph_id=? AND engine=? AND signature=?", (str(exc), now(), *args))
                            self.db.execute('UPDATE jobs SET error=? WHERE id=?', (str(exc), job['id']))
                            if any(s in str(exc) for s in ['上限', '凭证无效', '余额不足', '欠费', '套餐已到期', '没有所选模型权限']):
                                abort = True
                self.db.execute('UPDATE jobs SET completed=?,failed=?,updated=? WHERE id=?', (completed, failed, now(), job['id']))

        workers = [asyncio.create_task(translate_next()) for _ in range(min(len(wanted), translation_concurrency(settings, engine)))]
        try:
            await asyncio.gather(*workers)
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
        status = 'cancelled' if cancelled() else 'failed' if abort else 'partial' if failed else 'done'
        self.db.execute('UPDATE jobs SET status=?,completed=?,failed=?,updated=? WHERE id=?', (status, completed, failed, now(), job['id']))

    async def ai_reflow(self, job):
        settings=self.vault.get(self.db,job['user_id'])
        ensure_configured(settings,'ai', 'chat')
        rows=self.db.all('SELECT * FROM paragraphs WHERE document_id=? ORDER BY ordinal',(job['document_id'],))
        units=source_units(rows);result=[];completed=0
        batches=reflow_batches(units)
        self.db.execute('UPDATE jobs SET total=? WHERE id=?',(len(batches),job['id']))
        for batch in batches:
            if sum(u['kind']=='body' for u in batch) <= 1:
                groups=validate_groups('{"starts":[0]}',batch)
            else:
                signature=batch_signature(settings,batch)
                cached=self.db.one('SELECT groups_json FROM reflow_cache WHERE document_id=? AND signature=?',(job['document_id'],signature))
                groups=validate_groups(cached['groups_json'],batch) if cached else None
                if groups is None:
                    message=dumps({'required_starts':required_starts(batch),'lines':[{'id':i,'kind':u['kind'],'text':u['text']} for i,u in enumerate(batch)]})
                    messages=[{'role':'system','content':REFLOW_PROMPT},{'role':'user','content':message}]
                    for attempt in range(2):
                        raw=await self.providers.ai(settings,messages,lambda n:self.charge(job['user_id'],n),json_output=True)
                        try:
                            groups=validate_groups(raw,batch)
                            break
                        except ValueError as exc:
                            if attempt:
                                raise ValueError(f'AI 重排第 {completed+1}/{len(batches)} 批未通过原文完整性检查，当前阅读版和译文未修改。请重试或更换模型。') from exc
                            messages[0]={'role':'system','content':REFLOW_PROMPT+'\nYour previous response failed validation. Use only {"starts":[0,...]} with strictly increasing supplied integer ids. Do not return source text or explanations.'}
                    self.db.execute('INSERT OR REPLACE INTO reflow_cache(document_id,signature,groups_json,created) VALUES (?,?,?,?)',(job['document_id'],signature,dumps({'groups':groups}),now()))
            result.extend(compose(groups,batch));completed+=1
            self.db.execute('UPDATE jobs SET completed=?,updated=? WHERE id=?',(completed,now(),job['id']))
        # Entire document changes atomically, only after every batch validates.
        with self.db.connect() as con:
            self.db.replace_paragraphs(con,job['document_id'],result)
            con.execute('DELETE FROM reflow_cache WHERE document_id=?',(job['document_id'],))
            con.execute('UPDATE documents SET updated=? WHERE id=?',(now(),job['document_id']))
            con.execute("UPDATE jobs SET status='done',updated=? WHERE id=?",(now(),job['id']))

    async def chat(self, job):
        payload = json.loads(job["payload"])
        paragraphs = self.db.all("SELECT * FROM paragraphs WHERE document_id=? ORDER BY ordinal", (job["document_id"],))
        evidence, excerpts = chat_context(paragraphs, payload['question'], payload.get('paragraph_id'))
        messages = [{"role": "system", "content": CHAT_PROMPT}]
        history = self.db.all("SELECT role,status,text,evidence FROM messages WHERE document_id=? AND rowid<(SELECT rowid FROM messages WHERE id=?) ORDER BY rowid DESC LIMIT 12", (job["document_id"], payload["question_id"]))
        messages.extend(completed_history(list(reversed(history)), {p['id'] for p in paragraphs}))
        messages.append({"role": "user", "content": chat_question(excerpts, payload['question'])})
        settings = self.vault.get(self.db, job["user_id"])
        answer = await self.providers.ai(settings, messages, lambda n: self.charge(job["user_id"], n))
        self.db.execute("UPDATE messages SET text=?,status='done',evidence=? WHERE id=?", (answer, dumps(evidence), payload["answer_id"]))
        self.db.execute("UPDATE jobs SET status='done',completed=1,total=1,updated=? WHERE id=?", (now(), job["id"]))
