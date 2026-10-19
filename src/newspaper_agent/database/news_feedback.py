"""Per-account reversible card votes, outbound opens and viewport impressions."""
import json
from .research_repository import _now
from newspaper_agent.news_ranking import article_key, tag_keys, DEFAULT_WEIGHTS, validate_weights
from newspaper_agent.sources.contracts import EvidenceRecord
from newspaper_agent.preferences import confirmed_topics, active_interest_units
from newspaper_agent.recommendation import concepts


class NewsFeedback:
    def __init__(self, repository):
        self.repo = repository
        with repository._connect(write=True) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS news_votes (
                user_id TEXT NOT NULL, article_key TEXT NOT NULL, evidence_id TEXT NOT NULL,
                vote INTEGER NOT NULL, tags TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id,article_key));
                CREATE TABLE IF NOT EXISTS news_opens (
                user_id TEXT NOT NULL, article_key TEXT NOT NULL, evidence_id TEXT NOT NULL,
                clicks INTEGER NOT NULL, tags TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id,article_key));
                CREATE TABLE IF NOT EXISTS news_impressions (
                user_id TEXT NOT NULL, report_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
                section TEXT NOT NULL, position INTEGER NOT NULL, session TEXT NOT NULL,
                seen_at TEXT NOT NULL, PRIMARY KEY(user_id,report_id,evidence_id,section,session));
                CREATE TABLE IF NOT EXISTS news_ranking_preferences (
                user_id TEXT PRIMARY KEY, weights TEXT NOT NULL, learning INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS news_importance (
                user_id TEXT NOT NULL, evidence_id TEXT NOT NULL, version TEXT NOT NULL,
                content TEXT NOT NULL, PRIMARY KEY(user_id,evidence_id));''')

    def _record(self, user, report_id, evidence_id):
        report = self.repo.report(user, report_id)['report']
        for row in report.get('knowledge_payload', {}).get('evidence', []):
            if row['id'] == evidence_id:
                return EvidenceRecord(**row)
        raise ValueError('新闻不属于当前账号的报告')

    def preferences(self, user):
        with self.repo._connect() as db:
            self.repo._require_user(db, user)
            row = db.execute('SELECT * FROM news_ranking_preferences WHERE user_id=?', (user,)).fetchone()
        return {'weights': validate_weights(json.loads(row['weights'])) if row else {k: list(v) for k,v in DEFAULT_WEIGHTS.items()},
                'learning': bool(row['learning']) if row else True}

    def configure(self, user, weights, learning):
        weights = validate_weights(weights)
        if type(learning) is not bool:
            raise ValueError('反馈学习设置无效')
        with self.repo._connect(write=True) as db:
            self.repo._require_user(db, user)
            db.execute('INSERT OR REPLACE INTO news_ranking_preferences VALUES (?,?,?)', (user, json.dumps(weights), int(learning)))

    def vote(self, user, report_id, evidence_id, value):
        if type(value) is not int or value not in (-1, 0, 1):
            raise ValueError('请选择有帮助、没帮助或撤回')
        record = self._record(user, report_id, evidence_id)
        with self.repo._connect(write=True) as db:
            if value == 0:
                db.execute('DELETE FROM news_votes WHERE user_id=? AND article_key=?', (user, article_key(record)))
            else:
                db.execute('INSERT OR REPLACE INTO news_votes VALUES (?,?,?,?,?,?)',
                    (user, article_key(record), evidence_id, value, json.dumps(tag_keys(record)), _now()))

    def opened(self, user, report_id, evidence_id):
        record = self._record(user, report_id, evidence_id)
        with self.repo._connect(write=True) as db:
            db.execute('''INSERT INTO news_opens VALUES (?,?,?,1,?,?) ON CONFLICT(user_id,article_key)
                DO UPDATE SET clicks=MIN(clicks+1,1000000)''',
                (user, article_key(record), evidence_id, json.dumps(tag_keys(record)), _now()))

    def state(self, user, report_id):
        report = self.repo.report(user, report_id)['report']
        records = [EvidenceRecord(**r) for r in report.get('knowledge_payload', {}).get('evidence', [])]
        with self.repo._connect() as db:
            votes = {r['article_key']: r['vote'] for r in db.execute('SELECT article_key,vote FROM news_votes WHERE user_id=?', (user,))}
            clicks = {r['article_key']: r['clicks'] for r in db.execute('SELECT article_key,clicks FROM news_opens WHERE user_id=?', (user,))}
        return {r.id: {'vote': votes.get(article_key(r), 0), 'clicks': clicks.get(article_key(r), 0)} for r in records}

    def impression(self, user, report_id, evidence_id, section, position, session):
        if section not in ('personal', 'major', 'hot') or type(position) is not int or position < 0 or not isinstance(session, str) or len(session) > 64:
            raise ValueError('曝光记录无效')
        self._record(user, report_id, evidence_id)
        with self.repo._connect(write=True) as db:
            db.execute('INSERT OR IGNORE INTO news_impressions VALUES (?,?,?,?,?,?,?)',
                       (user, report_id, evidence_id, section, position, session, _now()))

    def adjustments(self, user, records, profile):
        if not self.preferences(user)['learning']:
            return {}
        allowed = concepts(' '.join(confirmed_topics(profile) + [method for unit in active_interest_units(profile)
                                                               for method in unit['methods']]))
        with self.repo._connect() as db:
            votes = {r['article_key']: dict(r) for r in db.execute('SELECT * FROM news_votes WHERE user_id=? ORDER BY updated_at DESC LIMIT 5000', (user,))}
            opens = {r['article_key']: dict(r) for r in db.execute('SELECT * FROM news_opens WHERE user_id=? ORDER BY updated_at DESC LIMIT 5000', (user,))}
        stats = {}
        # Explicit vote supersedes opens; frequency never increases learning weight.
        for key in votes.keys() | opens.keys():
            row = votes.get(key) or opens[key]
            reward = (1 if row['vote'] == 1 else -.3) if key in votes else .1
            for tag in set(json.loads(row['tags'])) & allowed:
                total, count = stats.get(tag, (0., 0))
                stats[tag] = total + reward, count + 1
        result = {}
        for record in records:
            values = [15 * total/(5+count) for tag, (total,count) in stats.items() if tag in tag_keys(record)]
            key = article_key(record)
            direct = (4 if votes[key]['vote'] == 1 else -4) if key in votes else 0
            result[record.id] = max(-15, min(15, (sum(values)/len(values) if values else 0) + direct))
        return result

    def clear(self, user):
        with self.repo._connect(write=True) as db:
            self.repo._require_user(db, user)
            for table in ('news_votes', 'news_opens', 'news_impressions'):
                db.execute(f'DELETE FROM {table} WHERE user_id=?', (user,))
        from .semantic_repository import SemanticRepository
        SemanticRepository(self.repo).clear_state(user)

    def importance(self, user, ids, version):
        wanted = set(ids)
        with self.repo._connect() as db:
            self.repo._require_user(db, user)
            return {r['evidence_id']: json.loads(r['content']) for r in db.execute(
                'SELECT evidence_id,content FROM news_importance WHERE user_id=? AND version=?', (user,version)) if r['evidence_id'] in wanted}

    def save_importance(self, user, version, values):
        with self.repo._connect(write=True) as db:
            self.repo._require_user(db, user)
            for identity, value in values.items():
                db.execute('INSERT OR REPLACE INTO news_importance VALUES (?,?,?,?)', (user, identity, version, json.dumps(value)))
