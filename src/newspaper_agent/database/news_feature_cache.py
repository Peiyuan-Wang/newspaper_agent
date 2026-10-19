"""Private application K/V and content-addressed decisions, not transformer tensors."""
import json
from newspaper_agent.semantic_profile import identity
from newspaper_agent.news_ranking import article_key
from newspaper_agent.sources.annotations import TAG_VERSION
from .research_repository import _json, _now


def feature(record):
    return {'title': record.title, 'snippet': record.snippet[:600], 'published_at': record.published_at,
            'mentioned_tags': [t['label'] for t in record.payload.get('content_tags', [])]}


def feature_key(record):
    return identity([record.source_id, article_key(record), feature(record), TAG_VERSION])


class NewsFeatureCache:
    def __init__(self, repository):
        self.repo = repository
        with repository._connect(write=True) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS news_feature_kv (
                user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id,key));
                CREATE TABLE IF NOT EXISTS news_score_cache (
                user_id TEXT NOT NULL, key TEXT NOT NULL, version TEXT NOT NULL,
                score INTEGER NOT NULL, importance_version TEXT NOT NULL, importance TEXT NOT NULL,
                PRIMARY KEY(user_id,key,version));''')

    def prepare(self, user, records):
        keys = {r.id: feature_key(r) for r in records}
        with self.repo._connect(write=True) as db:
            self.repo._require_user(db,user)
            for r in records:
                db.execute('INSERT OR IGNORE INTO news_feature_kv VALUES (?,?,?,?)',
                           (user,keys[r.id],_json(feature(r)),_now()))
        return keys

    def decisions(self, user, keys, version, importance_version):
        wanted, found = set(keys), {}
        with self.repo._connect() as db:
            self.repo._require_user(db,user)
            for row in db.execute('SELECT * FROM news_score_cache WHERE user_id=? AND version=? AND importance_version=?',
                                  (user,version,importance_version)):
                if row['key'] in wanted:
                    found[row['key']] = (row['score'],json.loads(row['importance']))
        return found

    def save(self, user, values, version, importance_version):
        with self.repo._connect(write=True) as db:
            self.repo._require_user(db,user)
            for key,(score,importance) in values.items():
                if type(score) is not int or not 0 <= score <= 100:
                    raise ValueError('无效评分缓存')
                db.execute('INSERT OR REPLACE INTO news_score_cache VALUES (?,?,?,?,?,?)',
                           (user,key,version,score,importance_version,_json(importance)))
