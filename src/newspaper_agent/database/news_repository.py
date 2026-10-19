"""Account-scoped article library, AI interest scores, and separate interpretations."""
from dataclasses import asdict
import json
from uuid import uuid4
from hashlib import sha256
from .research_repository import _json, _now


class NewsRepository:
    def __init__(self, repository):
        self.repo = repository
        with repository._connect(write=True) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS news_articles (
                user_id TEXT NOT NULL REFERENCES research_users(id), evidence_id TEXT NOT NULL,
                content TEXT NOT NULL, score INTEGER, score_version TEXT, score_state TEXT NOT NULL,
                updated_at TEXT NOT NULL, PRIMARY KEY(user_id,evidence_id));
                CREATE TABLE IF NOT EXISTS news_interpretations (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES research_users(id), evidence_id TEXT NOT NULL,
                version TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS news_interpretation_lookup ON news_interpretations(user_id,evidence_id,version);
                CREATE TABLE IF NOT EXISTS news_documents (
                user_id TEXT NOT NULL, evidence_id TEXT NOT NULL, version INTEGER NOT NULL,
                content TEXT NOT NULL, fetched_at TEXT NOT NULL, PRIMARY KEY(user_id,evidence_id));
                CREATE TABLE IF NOT EXISTS news_entities (
                user_id TEXT NOT NULL, evidence_id TEXT NOT NULL, name TEXT NOT NULL,
                kind TEXT NOT NULL, basis TEXT NOT NULL, PRIMARY KEY(user_id,evidence_id,name,kind));
                CREATE INDEX IF NOT EXISTS news_entity_browse ON news_entities(user_id,kind,name);
                CREATE TABLE IF NOT EXISTS news_event_links (
                user_id TEXT NOT NULL, evidence_id TEXT NOT NULL, event_id TEXT NOT NULL,
                content TEXT NOT NULL, PRIMARY KEY(user_id,evidence_id));
                CREATE INDEX IF NOT EXISTS news_event_browse ON news_event_links(user_id,event_id);''')
            db.execute('''CREATE TABLE IF NOT EXISTS news_reading_states (
                user_id TEXT NOT NULL,evidence_id TEXT NOT NULL,version TEXT NOT NULL,content TEXT NOT NULL,
                updated_at TEXT NOT NULL,PRIMARY KEY(user_id,evidence_id,version))''')

    def put_articles(self, user, records):
        with self.repo._connect(write=True) as db:
            self.repo._require_user(db, user)
            for record in records:
                if record.is_fixture:
                    raise ValueError('新闻库不能混入虚构资料')
                from newspaper_agent.sources.annotations import annotate_record
                record = annotate_record(record)
                old = db.execute('SELECT content FROM news_articles WHERE user_id=? AND evidence_id=?',
                                 (user,record.id)).fetchone()
                if old:
                    from newspaper_agent.sources.contracts import EvidenceRecord
                    from .news_feature_cache import feature_key
                    if feature_key(EvidenceRecord(**json.loads(old['content']))) != feature_key(record):
                        db.execute("UPDATE news_articles SET score=NULL,score_version=NULL,score_state='pending' WHERE user_id=? AND evidence_id=?",
                                   (user,record.id))
                        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='news_importance'").fetchone():
                            db.execute('DELETE FROM news_importance WHERE user_id=? AND evidence_id=?',(user,record.id))
                        db.execute('DELETE FROM news_documents WHERE user_id=? AND evidence_id=?',(user,record.id))
                db.execute('''INSERT INTO news_articles VALUES (?,?,?,NULL,NULL,'pending',?)
                    ON CONFLICT(user_id,evidence_id) DO UPDATE SET content=excluded.content,updated_at=excluded.updated_at''',
                    (user, record.id, _json(asdict(record), 50000), _now()))
                from newspaper_agent.sources.news_index import title_entities
                for entity in title_entities(record.title):
                    db.execute('INSERT OR IGNORE INTO news_entities VALUES (?,?,?,?,?)',
                               (user, record.id, entity['name'], entity['kind'], 'title'))

    def articles(self, user, ids):
        wanted = set(ids)
        with self.repo._connect() as db:
            self.repo._require_user(db, user)
            return {r['evidence_id']: dict(r) for r in db.execute(
                'SELECT evidence_id,score,score_version,score_state FROM news_articles WHERE user_id=?', (user,))
                if r['evidence_id'] in wanted}

    def score(self, user, scores, version, *, state='scored'):
        if state not in ('scored', 'failed') or any(v is not None and (type(v) is not int or not 0 <= v <= 100) for v in scores.values()):
            raise ValueError('兴趣分无效')
        if state == 'scored' and any(v is None for v in scores.values()):
            raise ValueError('未取得兴趣分不能标记已评分')
        with self.repo._connect(write=True) as db:
            self.repo._require_user(db, user)
            for identity, value in scores.items():
                db.execute('UPDATE news_articles SET score=?,score_version=?,score_state=?,updated_at=? WHERE user_id=? AND evidence_id=?',
                    (value, version, state, _now(), user, identity))

    def records(self, user, ids):
        from newspaper_agent.sources.contracts import EvidenceRecord
        wanted = set(ids)
        with self.repo._connect() as db:
            self.repo._require_user(db, user)
            return [EvidenceRecord(**json.loads(r['content'])) for r in db.execute(
                'SELECT evidence_id,content FROM news_articles WHERE user_id=?', (user,)) if r['evidence_id'] in wanted]

    def interpretation(self, user, evidence_id, version):
        with self.repo._connect() as db:
            self.repo._require_user(db, user)
            row = db.execute('''SELECT id,content FROM news_interpretations WHERE user_id=? AND evidence_id=? AND version=?
                ORDER BY created_at DESC LIMIT 1''', (user, evidence_id, version)).fetchone()
        return (row['id'], json.loads(row['content'])) if row else None

    def save_interpretation(self, user, evidence_id, version, content):
        identity = 'interpretation_' + uuid4().hex
        encoded = _json(content)
        with self.repo._connect(write=True) as db:
            self.repo._require_user(db, user)
            if db.execute('SELECT 1 FROM news_articles WHERE user_id=? AND evidence_id=?', (user, evidence_id)).fetchone() is None:
                raise ValueError('新闻不存在或不可访问')
            db.execute('INSERT INTO news_interpretations VALUES (?,?,?,?,?,?)',
                (identity, user, evidence_id, version, encoded, _now()))
        return identity

    def document(self, user, evidence_id, version):
        from datetime import datetime, timezone
        with self.repo._connect() as db:
            self.repo._require_user(db, user)
            row = db.execute('SELECT * FROM news_documents WHERE user_id=? AND evidence_id=? AND version=?',
                             (user, evidence_id, version)).fetchone()
        if not row:
            return None
        data = json.loads(row['content'])
        # Failures are retried after 15 minutes, successful immutable snapshots after one day.
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(row['fetched_at'])).total_seconds()
        return data if age < (86400 if data['status'] == 'extracted' else 900) else None

    def save_document(self, user, evidence_id, version, document):
        encoded = _json(document, 300000)
        with self.repo._connect(write=True) as db:
            self.repo._require_user(db, user)
            if not db.execute('SELECT 1 FROM news_articles WHERE user_id=? AND evidence_id=?', (user, evidence_id)).fetchone():
                raise ValueError('新闻不存在或不可访问')
            db.execute('INSERT OR REPLACE INTO news_documents VALUES (?,?,?,?,?)',
                       (user, evidence_id, version, encoded, _now()))

    def index_metadata(self, user, record, metadata):
        """Model data has already passed the evidence/schema gate; it is not fact verification."""
        if not metadata:
            return
        with self.repo._connect(write=True) as db:
            self.repo._require_user(db, user)
            if not db.execute('SELECT 1 FROM news_articles WHERE user_id=? AND evidence_id=?', (user, record.id)).fetchone():
                raise ValueError('新闻不存在或不可访问')
            db.execute("DELETE FROM news_entities WHERE user_id=? AND evidence_id=? AND basis='body'", (user, record.id))
            for entity in metadata.get('entities', []):
                db.execute('INSERT OR REPLACE INTO news_entities VALUES (?,?,?,?,?)',
                           (user, record.id, entity['name'], entity['kind'], 'body'))
            event = metadata.get('event')
            if event is None:
                db.execute('DELETE FROM news_event_links WHERE user_id=? AND evidence_id=?', (user, record.id))
            if event:
                event = dict(event)
                key = [event[k].casefold().strip() for k in ('subject', 'action', 'object')]
                # Unknown dates do not merge independent releases merely because the wording matches.
                key.append(event.get('occurred_at') or record.id)
                identity = 'event-' + sha256(_json(key).encode()).hexdigest()[:24]
                parent = event.get('continues')
                if parent:
                    candidate = db.execute('SELECT 1 FROM news_event_links WHERE user_id=? AND event_id=?', (user, parent)).fetchone()
                    if not candidate or parent == identity:
                        event['continues'] = None
                db.execute('INSERT OR REPLACE INTO news_event_links VALUES (?,?,?,?)',
                           (user, record.id, identity, _json(event)))

    def library(self, user, *, visible_sources=None, entity=None, kind=None, query='', limit=100, offset=0, ascending=False):
        """Paginated local view; never runs a model or downloads articles."""
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError('分页无效')
        with self.repo._connect() as db:
            self.repo._require_user(db, user)
            rows = db.execute('''SELECT a.*,e.event_id,e.content AS event_content FROM news_articles a
                LEFT JOIN news_event_links e ON a.user_id=e.user_id AND a.evidence_id=e.evidence_id
                WHERE a.user_id=?''', (user,)).fetchall()
            memberships = {}
            for e in db.execute('SELECT * FROM news_entities WHERE user_id=?', (user,)):
                memberships.setdefault(e['evidence_id'], []).append(dict(e))
        result = []
        for row in rows:
            record = json.loads(row['content'])
            if visible_sources is not None and record['source_id'] not in visible_sources:
                continue
            entities = memberships.get(row['evidence_id'], [])
            if entity and not any(e['name'] == entity and (not kind or e['kind'] == kind) for e in entities):
                continue
            if query and query.casefold() not in (record['title'] + ' ' + record['snippet']).casefold():
                continue
            event = json.loads(row['event_content']) if row['event_content'] else None
            result.append({'record': record, 'score': row['score'], 'score_state': row['score_state'],
                           'entities': entities, 'event_id': row['event_id'], 'event': event})
        result.sort(key=lambda r: (r['record'].get('published_at') or '', r['record']['id']), reverse=not ascending)
        return {'total': len(result), 'items': result[offset:offset + limit]}

    def entity_counts(self, user, visible_sources):
        with self.repo._connect() as db:
            self.repo._require_user(db, user)
            counts = {}
            for row in db.execute('''SELECT e.name,e.kind,a.content FROM news_entities e JOIN news_articles a
                ON a.user_id=e.user_id AND a.evidence_id=e.evidence_id WHERE e.user_id=?''', (user,)):
                if json.loads(row['content'])['source_id'] in visible_sources:
                    key = (row['kind'], row['name'])
                    counts[key] = counts.get(key, 0) + 1
        return sorted(({'kind': k, 'name': n, 'count': c} for (k, n), c in counts.items()),
                      key=lambda r: (-r['count'], r['name']))

    def event_candidates(self, user, record, visible_sources):
        from newspaper_agent.sources.news_index import title_entities
        names = {e['name'] for e in title_entities(record.title)}
        result = []
        with self.repo._connect() as db:
            self.repo._require_user(db, user)
            rows = db.execute('''SELECT e.event_id,e.content,a.content AS article FROM news_event_links e
                JOIN news_articles a ON a.user_id=e.user_id AND a.evidence_id=e.evidence_id
                WHERE e.user_id=? AND e.evidence_id<>? ORDER BY a.updated_at DESC LIMIT 100''', (user, record.id))
            seen = set()
            for row in rows:
                article = json.loads(row['article'])
                if article['source_id'] not in visible_sources or row['event_id'] in seen:
                    continue
                if not names or not names.intersection(e['name'] for e in title_entities(article['title'])):
                    continue
                event = json.loads(row['content'])
                result.append({'id': row['event_id'], **{k: event[k] for k in ('subject', 'action', 'object', 'occurred_at')}})
                seen.add(row['event_id'])
                if len(result) == 6:
                    break
        return result

    def backfill_titles(self, user, visible_sources):
        """Index old snapshots without rescoring, fetching, or modifying their reports."""
        from newspaper_agent.sources.news_index import title_entities
        with self.repo._connect(write=True) as db:
            self.repo._require_user(db, user)
            for row in db.execute('SELECT evidence_id,content FROM news_articles WHERE user_id=?', (user,)).fetchall():
                article = json.loads(row['content'])
                if article['source_id'] not in visible_sources:
                    continue
                for e in title_entities(article['title']):
                    db.execute('INSERT OR IGNORE INTO news_entities VALUES (?,?,?,?,?)',
                               (user, row['evidence_id'], e['name'], e['kind'], 'title'))

    def timeline(self, user, event_id, visible_sources):
        """Connected explicit continuations, never every event mentioning the same company."""
        with self.repo._connect() as db:
            self.repo._require_user(db, user)
            rows = db.execute('''SELECT e.*,a.content AS article FROM news_event_links e JOIN news_articles a
                ON a.user_id=e.user_id AND a.evidence_id=e.evidence_id WHERE e.user_id=?''', (user,)).fetchall()
        visible = []
        for row in rows:
            article = json.loads(row['article'])
            if article['source_id'] in visible_sources:
                visible.append({'event_id': row['event_id'], 'event': json.loads(row['content']), 'record': article})
        if not any(r['event_id'] == event_id for r in visible):
            return []
        connected = {event_id}
        for _ in range(30):
            old = len(connected)
            for row in visible:
                parent = row['event'].get('continues')
                if parent and (row['event_id'] in connected or parent in connected):
                    connected.update((row['event_id'], parent))
            if len(connected) == old:
                break
        selected = [r for r in visible if r['event_id'] in connected]
        selected.sort(key=lambda r: (r['event'].get('occurred_at') or r['record'].get('published_at') or '', r['record']['id']))
        return selected

    def reading_state(self, user, evidence_id, version):
        with self.repo._connect() as db:
            self.repo._require_user(db, user)
            row = db.execute('SELECT content FROM news_reading_states WHERE user_id=? AND evidence_id=? AND version=?',
                             (user, evidence_id, version)).fetchone()
        return json.loads(row['content']) if row else None

    def save_reading_state(self, user, evidence_id, version, state):
        with self.repo._connect(write=True) as db:
            self.repo._require_user(db, user)
            if not db.execute('SELECT 1 FROM news_articles WHERE user_id=? AND evidence_id=?', (user, evidence_id)).fetchone():
                raise ValueError('新闻不存在或不可访问')
            db.execute('INSERT OR REPLACE INTO news_reading_states VALUES (?,?,?,?,?)',
                       (user, evidence_id, version, _json(state, 100000), _now()))
