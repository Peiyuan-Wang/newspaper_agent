"""Local research accounts, personal preferences and source publication boundary.

Callers authenticate before selecting a user. The repository scopes every private
read and mutation to that user; it neither fetches URLs nor executes adapters.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
from uuid import uuid4

from newspaper_agent.sources.contracts import DOMAINS, identifier, public_url
from newspaper_agent.preferences import validate_interest_units


_ITERATIONS = 600_000
_PROFILE_FIELDS = {"interests", "excluded", "domains", "style", "language", 'purpose',
                   'knowledge_level', 'output_format', 'expression_rules', 'glossary', 'domain_reading_rules', 'interest_units'}
_FEEDBACK_KINDS = {"missed", "irrelevant", "useful", "fact_error", "too_long", "unclear",
                   'not_new', 'too_shallow', 'too_hard', 'source_quality', 'not_now'}
_STATUSES = {"discovered", "reviewed", "validated", "enabled", "suspended", "rejected", "archived"}
_DEFAULT_PROFILE = {"interests": [], "excluded": [], "domains": [], "style": "standard", "language": "zh",
                    'purpose': '', 'knowledge_level': 'intermediate', 'output_format': 'cards',
                    'expression_rules': [], 'glossary': [], 'domain_reading_rules': [], 'interest_units': []}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value, field: str, maximum: int, *, empty: bool = False) -> str:
    if (not isinstance(value, str) or len(value) > maximum or "\x00" in value
            or (not empty and not value.strip())):
        raise ValueError(f"{field} 无效或过长")
    return value.strip()


def _json(value, maximum: int = 1_000_000) -> str:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise ValueError("数据必须是有效 JSON") from None
    if len(encoded) > maximum:
        raise ValueError("数据过大")
    return encoded


def _changes(changes: dict) -> dict:
    if not isinstance(changes, dict) or not changes or set(changes) - _PROFILE_FIELDS:
        raise ValueError("偏好字段无效")
    clean = {}
    for key, value in changes.items():
        if key == 'interest_units':
            clean[key] = validate_interest_units(value)
        elif key in ("interests", "excluded", "domains", 'expression_rules', 'glossary', 'domain_reading_rules'):
            if not isinstance(value, list) or len(value) > 30:
                raise ValueError("偏好列表最多 30 项")
            terms = [_text(item, "偏好条目", 120) for item in value]
            if key == "domains" and any(item not in DOMAINS for item in terms):
                raise ValueError("领域无效")
            clean[key] = list(dict.fromkeys(terms))
        elif key == "style":
            if value not in ("brief", "standard", "expert"):
                raise ValueError("输出风格无效")
            clean[key] = value
        elif key == 'purpose':
            clean[key] = _text(value, '阅读用途', 300, empty=True)
        elif key in ('knowledge_level', 'output_format'):
            allowed = ('beginner', 'intermediate', 'expert') if key == 'knowledge_level' else ('cards', 'table', 'outline')
            if value not in allowed:
                raise ValueError('阅读偏好无效')
            clean[key] = value
        elif key == "language":
            if value not in ("zh", "en"):
                raise ValueError("语言无效")
            clean[key] = value
    return clean


class ResearchRepository:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS research_users (
                    id TEXT PRIMARY KEY, salt BLOB NOT NULL, password_hash BLOB NOT NULL,
                    iterations INTEGER NOT NULL, admin INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_profiles (
                    user_id TEXT PRIMARY KEY REFERENCES research_users(id),
                    content TEXT NOT NULL, version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    user_id TEXT NOT NULL REFERENCES research_users(id), kind TEXT NOT NULL,
                    detail TEXT NOT NULL, changes TEXT NOT NULL, before_profile TEXT,
                    after_profile TEXT, after_version INTEGER, evidence_id TEXT,
                    active INTEGER NOT NULL, created_at TEXT NOT NULL, undone_at TEXT
                );
                CREATE TABLE IF NOT EXISTS research_sources (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES research_users(id),
                    name TEXT NOT NULL, url TEXT NOT NULL, domain TEXT NOT NULL,
                    scope TEXT NOT NULL CHECK(scope IN ('private','shared')),
                    status TEXT NOT NULL, adapter TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(owner,url,scope)
                );
                CREATE TABLE IF NOT EXISTS research_reports (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES research_users(id),
                    question TEXT NOT NULL, report TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS research_events_user ON research_events(user_id,sequence);
                CREATE INDEX IF NOT EXISTS research_reports_user ON research_reports(user_id,created_at);
                CREATE TABLE IF NOT EXISTS research_profile_proposals (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES research_users(id),
                    base_version INTEGER NOT NULL, changes TEXT NOT NULL, user_quote TEXT NOT NULL,
                    reason TEXT NOT NULL, status TEXT NOT NULL, event_id TEXT, created_at TEXT NOT NULL
                );
            """)

    @contextmanager
    def _connect(self, *, write: bool = False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _require_user(db, user: str):
        identifier(user)
        row = db.execute("SELECT id,admin FROM research_users WHERE id=?", (user,)).fetchone()
        if row is None:
            raise ValueError("账号不存在")
        return row

    def user_exists(self, user: str) -> bool:
        identifier(user)
        with self._connect() as db:
            return db.execute("SELECT 1 FROM research_users WHERE id=?", (user,)).fetchone() is not None

    def register(self, user: str, password: str) -> None:
        identifier(user)
        if not isinstance(password, str) or not 8 <= len(password) <= 1024 or "\x00" in password:
            raise ValueError("密码需要 8 到 1024 个字符")
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
        try:
            with self._connect(write=True) as db:
                first = db.execute("SELECT 1 FROM research_users LIMIT 1").fetchone() is None
                db.execute("INSERT INTO research_users VALUES (?,?,?,?,?,?)",
                           (user, salt, digest, _ITERATIONS, int(first), _now()))
                db.execute("INSERT INTO research_profiles VALUES (?,?,0)", (user, _json(_DEFAULT_PROFILE)))
        except sqlite3.IntegrityError:
            raise ValueError("账号已存在") from None

    def authenticate(self, user: str, password: str) -> bool:
        try:
            identifier(user)
        except ValueError:
            return False
        if not isinstance(password, str) or len(password) > 1024:
            return False
        with self._connect() as db:
            row = db.execute("SELECT salt,password_hash,iterations FROM research_users WHERE id=?", (user,)).fetchone()
        # Do equal-cost work for unknown accounts to avoid a cheap account oracle.
        salt = row["salt"] if row else bytes(16)
        iterations = row["iterations"] if row else _ITERATIONS
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        expected = row["password_hash"] if row else bytes(32)
        return secrets.compare_digest(actual, expected) and row is not None

    def is_admin(self, user: str) -> bool:
        with self._connect() as db:
            return bool(self._require_user(db, user)["admin"])

    @staticmethod
    def _profile(db, user):
        row = db.execute("SELECT content,version FROM research_profiles WHERE user_id=?", (user,)).fetchone()
        return {**deepcopy(_DEFAULT_PROFILE), **json.loads(row["content"]), "version": row["version"]}

    def profile(self, user: str) -> dict:
        with self._connect() as db:
            self._require_user(db, user)
            return self._profile(db, user)

    def _record(self, user, kind, detail, changes, evidence_id):
        with self._connect(write=True) as db:
            return self._record_in(db, user, kind, detail, changes, evidence_id)

    def _record_in(self, db, user, kind, detail, changes, evidence_id):
        event_id = "event_" + uuid4().hex
        self._require_user(db, user)
        before = self._profile(db, user)
        after_version = before["version"]
        after = {key: value for key, value in before.items() if key != "version"}
        if changes:
            after.update(changes)
            after_version += 1
            db.execute("UPDATE research_profiles SET content=?,version=? WHERE user_id=? AND version=?",
                       (_json(after), after_version, user, before["version"]))
        db.execute("""INSERT INTO research_events
            (id,user_id,kind,detail,changes,before_profile,after_profile,after_version,evidence_id,active,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,1,?)""",
                   (event_id, user, kind, detail, _json(changes), _json(before),
                    _json(after), after_version, evidence_id, _now()))
        return event_id

    def propose_profile(self, user, changes, user_quote, reason):
        changes = _changes(changes)
        quote = _text(user_quote, '用户原话', 1000)
        reason = _text(reason, '建议理由', 1000)
        with self._connect(write=True) as db:
            self._require_user(db, user)
            before = self._profile(db, user)
            if db.execute("SELECT count(*) FROM research_profile_proposals WHERE user_id=? AND status='pending'", (user,)).fetchone()[0] >= 20:
                raise ValueError('待确认建议最多20项，请先处理已有建议')
            proposal_id = 'proposal_' + uuid4().hex
            db.execute('''INSERT INTO research_profile_proposals
                (id,user_id,base_version,changes,user_quote,reason,status,event_id,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)''',
                (proposal_id, user, before['version'], _json(changes), quote, reason, 'pending', None, _now()))
        return proposal_id

    def proposals(self, user):
        with self._connect() as db:
            self._require_user(db, user)
            return [{**dict(row), 'changes': json.loads(row['changes'])} for row in db.execute(
                "SELECT * FROM research_profile_proposals WHERE user_id=? AND status='pending' ORDER BY created_at DESC", (user,))]

    def resolve_proposal(self, user, proposal_id, *, accept):
        if type(accept) is not bool:
            raise ValueError('确认参数无效')
        with self._connect(write=True) as db:
            self._require_user(db, user)
            row = db.execute("SELECT * FROM research_profile_proposals WHERE id=? AND user_id=? AND status='pending'", (proposal_id, user)).fetchone()
            if row is None:
                raise ValueError('没有待确认的建议')
            event = None
            if accept:
                if self._profile(db, user)['version'] != row['base_version']:
                    raise ValueError('档案已改变，请拒绝旧建议并请AI重新提出，避免覆盖新偏好')
                event = self._record_in(db, user, 'profile', '用户确认AI建议：' + row['reason'],
                                        _changes(json.loads(row['changes'])), None)
            db.execute('UPDATE research_profile_proposals SET status=?,event_id=? WHERE id=?',
                ('accepted' if accept else 'rejected', event, proposal_id))
            return event

    def update_profile(self, user: str, changes: dict, reason: str) -> str:
        return self._record(user, "profile", _text(reason, "修改理由", 2000), _changes(changes), None)

    def feedback(self, user: str, kind: str, detail: str, changes: dict | None = None,
                 evidence_id: str | None = None) -> str:
        if not isinstance(kind, str) or kind not in _FEEDBACK_KINDS:
            raise ValueError("反馈类别无效")
        if kind in {'not_new', 'too_shallow', 'too_hard', 'source_quality', 'not_now'} and changes:
            raise ValueError('此类反馈不修改长期档案；请另提待确认建议')
        if evidence_id is not None:
            evidence_id = _text(evidence_id, "证据标识", 200)
        return self._record(user, kind, _text(detail, "反馈内容", 4000),
                            _changes(changes) if changes is not None else {}, evidence_id)

    def events(self, user: str, limit: int = 20) -> list[dict]:
        self._limit(limit, 100)
        with self._connect() as db:
            self._require_user(db, user)
            rows = db.execute("""SELECT id,kind,detail,changes,evidence_id,active,created_at,undone_at,
                after_version FROM research_events WHERE user_id=? ORDER BY sequence DESC LIMIT ?""", (user, limit))
            return [{**dict(row), "changes": json.loads(row["changes"]), "active": bool(row["active"])} for row in rows]

    def undo(self, user: str, event_id: str) -> None:
        _text(event_id, "事件标识", 100)
        with self._connect(write=True) as db:
            self._require_user(db, user)
            event = db.execute("SELECT * FROM research_events WHERE user_id=? AND id=? AND active=1",
                               (user, event_id)).fetchone()
            if event is None:
                raise ValueError("没有可撤销的事件")
            if json.loads(event["changes"]):
                current = self._profile(db, user)
                if current["version"] != event["after_version"]:
                    raise ValueError("偏好已有后续修改，不能覆盖；请显式调整当前偏好")
                before = json.loads(event["before_profile"])
                before.pop("version", None)
                changed = db.execute("""UPDATE research_profiles SET content=?,version=version+1
                    WHERE user_id=? AND version=?""", (_json(before), user, event["after_version"])).rowcount
                if changed != 1:
                    raise ValueError("偏好版本冲突")
            db.execute("UPDATE research_events SET active=0,undone_at=? WHERE user_id=? AND id=?",
                       (_now(), user, event_id))

    @staticmethod
    def _limit(limit, maximum):
        if type(limit) is not int or not 1 <= limit <= maximum:
            raise ValueError("数量限制无效")

    def store_source(self, user: str, name: str, url: str, domain: str, scope: str = "private",
                     status: str = "discovered", adapter: str = "rss") -> dict:
        name = _text(name, "来源名称", 120)
        public_url(url)
        if domain not in DOMAINS or scope not in ("private", "shared"):
            raise ValueError("来源领域或范围无效")
        if status != "discovered" or adapter != "rss":
            raise ValueError("新来源必须是待验证的 RSS 候选")
        source_id = "source_" + uuid4().hex
        now = _now()
        try:
            with self._connect(write=True) as db:
                self._require_user(db, user)
                db.execute("INSERT INTO research_sources VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (source_id, user, name, url, domain, scope, status, adapter, now, now))
        except sqlite3.IntegrityError:
            raise ValueError("该范围内已有相同来源 URL") from None
        return self.source(user, source_id)

    def list_sources(self, user: str, include_pending: bool = False) -> list[dict]:
        if type(include_pending) is not bool:
            raise ValueError("候选选项无效")
        with self._connect() as db:
            admin = self._require_user(db, user)["admin"]
            rows = db.execute("""SELECT * FROM research_sources WHERE
                (scope='private' AND owner=?) OR
                (scope='shared' AND (status='enabled' OR (? AND (owner=? OR ?))))
                ORDER BY created_at,id""", (user, include_pending, user, bool(admin)))
            return [dict(row) for row in rows]

    @staticmethod
    def _visible_source(db, user, source_id, admin):
        _text(source_id, "来源标识", 100)
        row = db.execute("""SELECT * FROM research_sources WHERE id=? AND
            ((scope='private' AND owner=?) OR (scope='shared' AND
            (status='enabled' OR owner=? OR ?)))""", (source_id, user, user, bool(admin))).fetchone()
        if row is None:
            raise ValueError("来源不存在或不可访问")
        return dict(row)

    def source(self, user: str, source_id: str) -> dict:
        _text(source_id, "来源标识", 100)
        with self._connect() as db:
            account = self._require_user(db, user)
            return self._visible_source(db, user, source_id, account["admin"])

    def set_source_status(self, user: str, source_id: str, status: str) -> None:
        if not isinstance(status, str) or status not in _STATUSES:
            raise ValueError("来源状态无效")
        with self._connect(write=True) as db:
            account = self._require_user(db, user)
            source = self._visible_source(db, user, source_id, account["admin"])
            if source["scope"] == "shared":
                # Contributors may withdraw their unpublished contribution.
                withdrawing = (source["owner"] == user and source["status"] in
                               ("discovered", "reviewed", "validated") and status == "archived")
                if status == "enabled" or (not account["admin"] and not withdrawing):
                    raise ValueError("共享来源需由管理员审核，并使用发布操作启用")
            elif source["owner"] != user:
                raise ValueError("不能修改其他用户的私密来源")
            transitions = {
                "discovered": {"reviewed", "validated", "rejected", "archived"},
                "reviewed": {"validated", "rejected", "archived"},
                "validated": {"enabled", "rejected", "archived"},
                "enabled": {"suspended", "archived"},
                "suspended": {"validated", "rejected", "archived"},
                "rejected": {"reviewed", "archived"}, "archived": set(),
            }
            if status not in transitions[source["status"]]:
                raise ValueError("来源状态变更无效，需要先验证来源")
            db.execute("UPDATE research_sources SET status=?,updated_at=? WHERE id=?",
                       (status, _now(), source_id))

    def share_source(self, user: str, source_id: str) -> dict:
        original = self.source(user, source_id)
        if original["owner"] != user or original["scope"] != "private":
            raise ValueError("只能贡献自己的私密来源")
        # Explicit allowlist: no profile, query, feedback, evidence or report is copied.
        return self.store_source(user, original["name"], original["url"], original["domain"], scope="shared")

    def publish_source(self, user: str, source_id: str) -> None:
        with self._connect(write=True) as db:
            account = self._require_user(db, user)
            if not account["admin"]:
                raise ValueError("只有管理员可以发布共享来源")
            source = self._visible_source(db, user, source_id, True)
            if source["scope"] != "shared" or source["status"] != "validated":
                raise ValueError("共享来源需先通过验证")
            db.execute("UPDATE research_sources SET status='enabled',updated_at=? WHERE id=?", (_now(), source_id))

    def save_report(self, user: str, question: str, report: dict) -> str:
        question = _text(question, "研究问题", 4000)
        if not isinstance(report, dict):
            raise ValueError("报告必须为 JSON 对象")
        encoded = _json(report)
        report_id = "report_" + uuid4().hex
        with self._connect(write=True) as db:
            self._require_user(db, user)
            db.execute("INSERT INTO research_reports VALUES (?,?,?,?,?)", (report_id, user, question, encoded, _now()))
        return report_id

    def reports(self, user: str, limit: int = 10) -> list[dict]:
        self._limit(limit, 100)
        with self._connect() as db:
            self._require_user(db, user)
            return [dict(row) for row in db.execute("""SELECT id,question,created_at FROM research_reports
                WHERE user_id=? ORDER BY created_at DESC,id DESC LIMIT ?""", (user, limit))]

    def report(self, user: str, report_id: str) -> dict:
        _text(report_id, "报告标识", 100)
        with self._connect() as db:
            self._require_user(db, user)
            row = db.execute("SELECT id,question,report,created_at FROM research_reports WHERE user_id=? AND id=?",
                             (user, report_id)).fetchone()
            if row is None:
                raise ValueError("报告不存在或不可访问")
            return {**dict(row), "report": json.loads(row["report"])}
