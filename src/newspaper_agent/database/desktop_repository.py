"""Account-scoped article ratings, independent of model-generated preferences."""
from .research_repository import _now
import json
from newspaper_agent.news_options import DEFAULT_NEWS_LIMIT, validate_news_limit

CONTEXT_BUDGETS = (30000, 60000, 120000, 240000, 480000)


class DesktopRepository:
    def __init__(self, repository):
        self.repository = repository
        with repository._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS desktop_news_preferences (
                user_id TEXT PRIMARY KEY REFERENCES research_users(id), max_news INTEGER NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS article_ratings (
                user_id TEXT NOT NULL REFERENCES research_users(id),
                report_id TEXT NOT NULL REFERENCES research_reports(id),
                evidence_id TEXT NOT NULL, score INTEGER NOT NULL CHECK(score BETWEEN 1 AND 5),
                updated_at TEXT NOT NULL, PRIMARY KEY(user_id,report_id,evidence_id))''')
            db.execute('''CREATE TABLE IF NOT EXISTS desktop_dialogue (
                user_id TEXT PRIMARY KEY REFERENCES research_users(id),
                messages TEXT NOT NULL, updated_at TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS desktop_runtime_preferences (
                user_id TEXT PRIMARY KEY REFERENCES research_users(id),
                context_chars INTEGER NOT NULL CHECK(context_chars IN (30000,60000,120000,240000,480000)))''')

    def news_limit(self, user):
        with self.repository._connect() as db:
            self.repository._require_user(db, user)
            row = db.execute('SELECT max_news FROM desktop_news_preferences WHERE user_id=?', (user,)).fetchone()
        return validate_news_limit(row['max_news']) if row else DEFAULT_NEWS_LIMIT

    def set_news_limit(self, user, value):
        value = validate_news_limit(value)
        with self.repository._connect(write=True) as db:
            self.repository._require_user(db, user)
            db.execute('INSERT OR REPLACE INTO desktop_news_preferences VALUES (?,?)', (user, value))

    def context_budget(self, user):
        with self.repository._connect() as db:
            self.repository._require_user(db, user)
            row = db.execute('SELECT context_chars FROM desktop_runtime_preferences WHERE user_id=?', (user,)).fetchone()
            return row['context_chars'] if row else CONTEXT_BUDGETS[0]

    def set_context_budget(self, user, value):
        if type(value) is not int or value not in CONTEXT_BUDGETS:
            raise ValueError('请选择支持的上下文字符预算')
        with self.repository._connect(write=True) as db:
            self.repository._require_user(db, user)
            db.execute('''INSERT INTO desktop_runtime_preferences VALUES (?,?) ON CONFLICT(user_id)
                DO UPDATE SET context_chars=excluded.context_chars''', (user, value))

    def save_dialogue(self, user, messages):
        if not isinstance(messages, list) or len(messages) > 6 or len(messages) % 2:
            raise ValueError('对话记录格式无效')
        for index, message in enumerate(messages):
            if (not isinstance(message, dict) or set(message) != {'role', 'content'}
                    or message['role'] != ('user' if index % 2 == 0 else 'assistant')
                    or not isinstance(message['content'], str) or len(message['content']) > 100_000):
                raise ValueError('对话记录格式无效')
        with self.repository._connect(write=True) as db:
            self.repository._require_user(db, user)
            db.execute('''INSERT INTO desktop_dialogue VALUES (?,?,?) ON CONFLICT(user_id)
                DO UPDATE SET messages=excluded.messages,updated_at=excluded.updated_at''',
                (user, json.dumps(messages, ensure_ascii=False), _now()))

    def dialogue(self, user):
        with self.repository._connect() as db:
            self.repository._require_user(db, user)
            row = db.execute('SELECT messages FROM desktop_dialogue WHERE user_id=?', (user,)).fetchone()
            return json.loads(row['messages']) if row else []

    def rate(self, user, report_id, evidence_id, score):
        if type(score) is not int or not 1 <= score <= 5:
            raise ValueError('评分必须为 1–5')
        report = self.repository.report(user, report_id)['report']
        if evidence_id not in {r['id'] for r in report.get('knowledge_payload', {}).get('evidence', [])}:
            raise ValueError('这条信息不属于当前报告')
        with self.repository._connect(write=True) as db:
            db.execute('''INSERT INTO article_ratings VALUES (?,?,?,?,?)
                ON CONFLICT(user_id,report_id,evidence_id) DO UPDATE SET
                score=excluded.score, updated_at=excluded.updated_at''',
                (user, report_id, evidence_id, score, _now()))

    def ratings(self, user, report_id):
        self.repository.report(user, report_id)
        with self.repository._connect() as db:
            return {r['evidence_id']: r['score'] for r in db.execute(
                'SELECT evidence_id,score FROM article_ratings WHERE user_id=? AND report_id=?',
                (user, report_id))}
