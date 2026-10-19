"""Account-private embedding cache and materialized multi-interest state."""
import json
from .research_repository import _json, _now
from newspaper_agent.semantic_profile import identity, unit


class SemanticRepository:
    def __init__(self, repository):
        self.repo = repository
        with repository._connect(write=True) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS semantic_settings (
                user_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS semantic_text_vectors (
                user_id TEXT NOT NULL, model TEXT NOT NULL, digest TEXT NOT NULL, vector TEXT NOT NULL,
                PRIMARY KEY(user_id,model,digest));
                CREATE TABLE IF NOT EXISTS semantic_documents (
                user_id TEXT NOT NULL, model TEXT NOT NULL, evidence_id TEXT NOT NULL,
                content TEXT NOT NULL, PRIMARY KEY(user_id,model,evidence_id));
                CREATE TABLE IF NOT EXISTS semantic_profiles (
                user_id TEXT PRIMARY KEY, content TEXT NOT NULL, updated_at TEXT NOT NULL);''')

    def enabled(self, user):
        with self.repo._connect() as db:
            self.repo._require_user(db, user)
            row = db.execute('SELECT enabled FROM semantic_settings WHERE user_id=?', (user,)).fetchone()
        return bool(row[0]) if row else True

    def configure(self, user, enabled):
        if type(enabled) is not bool:
            raise ValueError('画像开关无效')
        with self.repo._connect(write=True) as db:
            self.repo._require_user(db, user)
            db.execute('INSERT OR REPLACE INTO semantic_settings VALUES (?,?)', (user,int(enabled)))

    def vectors(self, user, model, texts):
        wanted = {identity(t) for t in texts}
        with self.repo._connect() as db:
            self.repo._require_user(db, user)
            rows = db.execute('SELECT digest,vector FROM semantic_text_vectors WHERE user_id=? AND model=?', (user,model))
            return {r['digest']: json.loads(r['vector']) for r in rows if r['digest'] in wanted}

    def save_vectors(self, user, model, values):
        with self.repo._connect(write=True) as db:
            self.repo._require_user(db, user)
            for digest, vector in values.items():
                db.execute('INSERT OR REPLACE INTO semantic_text_vectors VALUES (?,?,?,?)',
                           (user,model,digest,_json(unit(vector),100000)))

    def documents(self, user, model, ids):
        wanted = set(ids)
        with self.repo._connect() as db:
            self.repo._require_user(db, user)
            return {r['evidence_id']: json.loads(r['content']) for r in db.execute(
                'SELECT evidence_id,content FROM semantic_documents WHERE user_id=? AND model=?', (user,model))
                    if r['evidence_id'] in wanted}

    def save_documents(self, user, model, values):
        with self.repo._connect(write=True) as db:
            self.repo._require_user(db,user)
            for eid, value in values.items():
                unit(value['vector'])
                db.execute('INSERT OR REPLACE INTO semantic_documents VALUES (?,?,?,?)', (user,model,eid,_json(value,150000)))

    def state(self, user):
        with self.repo._connect() as db:
            self.repo._require_user(db,user)
            row = db.execute('SELECT * FROM semantic_profiles WHERE user_id=?', (user,)).fetchone()
        return {**json.loads(row['content']), 'updated_at': row['updated_at']} if row else None

    def save_state(self, user, value):
        with self.repo._connect(write=True) as db:
            self.repo._require_user(db,user)
            db.execute('INSERT OR REPLACE INTO semantic_profiles VALUES (?,?,?)', (user,_json(value),_now()))

    def clear_state(self, user):
        with self.repo._connect(write=True) as db:
            self.repo._require_user(db,user)
            db.execute('DELETE FROM semantic_profiles WHERE user_id=?', (user,))

    def feedback(self, user):
        from .news_feedback import NewsFeedback
        NewsFeedback(self.repo)
        with self.repo._connect() as db:
            self.repo._require_user(db,user)
            votes = {r['article_key']: dict(r) for r in db.execute(
                'SELECT * FROM news_votes WHERE user_id=? ORDER BY updated_at DESC LIMIT 5000',(user,))}
            opens = {r['article_key']: dict(r) for r in db.execute(
                'SELECT * FROM news_opens WHERE user_id=? ORDER BY updated_at DESC LIMIT 5000',(user,))}
        rows = []
        for key in votes.keys() | opens.keys():
            row = votes.get(key) or opens[key]
            rows.append({'key': key, 'evidence_id': row['evidence_id'], 'at': row['updated_at'],
                         'kind': ('up' if row['vote'] == 1 else 'down') if key in votes else 'open'})
        return sorted(rows, key=lambda r: (r['at'], r['key']), reverse=True)[:1000]
