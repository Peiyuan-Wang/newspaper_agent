"""Account-scoped feed pool. Reuse existing database transaction boundary."""
from dataclasses import asdict
import json
from newspaper_agent.sources.contracts import EvidenceRecord


class FeedRepository:
    def __init__(self, repository):
        self.repository = repository
        with repository._connect(write=True) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS subscription_feeds (
                user_id TEXT NOT NULL REFERENCES research_users(id), source_id TEXT NOT NULL,
                endpoint TEXT NOT NULL, content TEXT NOT NULL, checked_at REAL NOT NULL,
                PRIMARY KEY(user_id, source_id))''')
            db.execute('''CREATE TABLE IF NOT EXISTS subscription_checks (
                user_id TEXT NOT NULL, source_id TEXT NOT NULL, endpoint TEXT NOT NULL,
                attempted_at REAL NOT NULL, PRIMARY KEY(user_id,source_id))''')

    def attempts(self, user, sources):
        endpoints = {s.id: s.homepage for s in sources}
        with self.repository._connect() as db:
            self.repository._require_user(db, user)
            result = {r['source_id']: r['checked_at'] for r in db.execute(
                'SELECT source_id,endpoint,checked_at FROM subscription_feeds WHERE user_id=?', (user,))
                if endpoints.get(r['source_id']) == r['endpoint']}
            for row in db.execute('SELECT * FROM subscription_checks WHERE user_id=?', (user,)):
                if endpoints.get(row['source_id']) == row['endpoint']:
                    result[row['source_id']] = max(result.get(row['source_id'], 0), row['attempted_at'])
        return result

    def record_attempt(self, user, source, when):
        with self.repository._connect(write=True) as db:
            self.repository._require_user(db, user)
            db.execute('INSERT OR REPLACE INTO subscription_checks VALUES (?,?,?,?)',
                       (user, source.id, source.homepage, when))

    def load(self, user, source):
        with self.repository._connect() as db:
            self.repository._require_user(db, user)
            row = db.execute('SELECT content,checked_at FROM subscription_feeds WHERE user_id=? AND source_id=? AND endpoint=?',
                             (user, source.id, source.homepage)).fetchone()
        if row is None:
            return (), None
        return tuple(EvidenceRecord(**r) for r in json.loads(row['content'])), row['checked_at']

    def save(self, user, source, records, checked_at):
        previous, _ = self.load(user, source)
        # Deduplicate by URL, replacing a previous snapshot if its text changed.
        merged = {r.url: r for r in previous if r.source_id == source.id}
        for record in records[:200]:
            if record.source_id != source.id or record.is_fixture:
                raise ValueError('订阅资料身份不一致')
            merged.pop(record.url, None)
            merged[record.url] = record
        kept = tuple(merged.values())[-200:]
        content = json.dumps([asdict(r) for r in kept], ensure_ascii=False)
        with self.repository._connect(write=True) as db:
            self.repository._require_user(db, user)
            db.execute('INSERT OR REPLACE INTO subscription_feeds VALUES (?,?,?,?,?)',
                       (user, source.id, source.homepage, content, checked_at))
        return kept
