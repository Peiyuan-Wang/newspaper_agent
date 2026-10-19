"""个人候选来源与反馈的持久化边界；不注册工具、不联网。"""
from pathlib import Path
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4
from contextlib import contextmanager

from newspaper_agent.sources.contracts import DOMAINS, identifier, public_url


class SourceRepository:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS source_candidates (
                    user_id TEXT, id TEXT, name TEXT, domain TEXT, url TEXT,
                    status TEXT NOT NULL CHECK(status='discovered'), created_at TEXT,
                    PRIMARY KEY(user_id, id)
                );
                CREATE TABLE IF NOT EXISTS source_feedback (
                    id TEXT PRIMARY KEY, user_id TEXT, source_id TEXT, kind TEXT,
                    active INTEGER NOT NULL, created_at TEXT
                );
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add_candidate(self, user, source_id, name, domain, url):
        identifier(user)
        identifier(source_id)
        public_url(url)
        if domain not in DOMAINS or not isinstance(name, str) or not 1 <= len(name) <= 120:
            raise ValueError("无效来源名称或领域")
        try:
            with self._connect() as db:
                db.execute("INSERT INTO source_candidates VALUES (?,?,?,?,?,?,?)",
                           (user, source_id, name, domain, url, "discovered", datetime.now(timezone.utc).isoformat()))
        except sqlite3.IntegrityError:
            raise ValueError("该用户的候选来源 id 已存在") from None

    def candidates(self, user):
        identifier(user)
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,name,domain,url,status,created_at FROM source_candidates WHERE user_id=? ORDER BY id", (user,))]

    def feedback(self, user, source_id, kind):
        identifier(user)
        identifier(source_id)
        if kind not in ("useful", "irrelevant", "fact_error"):
            raise ValueError("反馈类别无效")
        event_id = uuid4().hex
        with self._connect() as db:
            db.execute("INSERT INTO source_feedback VALUES (?,?,?,?,1,?)",
                       (event_id, user, source_id, kind, datetime.now(timezone.utc).isoformat()))
        return event_id

    def undo_feedback(self, user, event_id):
        identifier(user)
        with self._connect() as db:
            changed = db.execute("UPDATE source_feedback SET active=0 WHERE user_id=? AND id=? AND active=1",
                                 (user, event_id)).rowcount
        if not changed:
            raise ValueError("没有可撤销的反馈")

    def preferences(self, user):
        identifier(user)
        with self._connect() as db:
            rows = db.execute("""SELECT source_id, SUM(CASE kind WHEN 'useful' THEN 1
                WHEN 'irrelevant' THEN -1 ELSE 0 END) AS score
                FROM source_feedback WHERE user_id=? AND active=1 GROUP BY source_id""", (user,))
            return {row["source_id"]: max(-2, min(2, row["score"])) for row in rows}
