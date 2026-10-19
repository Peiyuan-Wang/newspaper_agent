"""Private recommendation decisions and explicit, replaceable feedback; no raw reasoning."""
import json
from .research_repository import _now
from newspaper_agent.recommendation import DEFAULTS, validate_settings, POLICY_VERSION


class RecommendationRepository:
    def __init__(self, repository):
        self.repo = repository
        with repository._connect(write=True) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS recommendation_settings (
                user_id TEXT PRIMARY KEY, content TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS recommendation_decisions (
                user_id TEXT NOT NULL, report_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
                content TEXT NOT NULL, displayed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(user_id,report_id,evidence_id));
                CREATE TABLE IF NOT EXISTS recommendation_feedback (
                user_id TEXT NOT NULL, report_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
                reward REAL NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY(user_id,report_id,evidence_id));''')

    def settings(self, user):
        with self.repo._connect() as db:
            self.repo._require_user(db, user)
            row = db.execute('SELECT content FROM recommendation_settings WHERE user_id=?', (user,)).fetchone()
        return validate_settings(json.loads(row['content'])) if row else dict(DEFAULTS)

    def configure(self, user, settings):
        clean = validate_settings(settings)
        with self.repo._connect(write=True) as db:
            self.repo._require_user(db, user)
            db.execute('INSERT OR REPLACE INTO recommendation_settings VALUES (?,?)', (user, json.dumps(clean)))

    def record_plan(self, user, report_id):
        report = self.repo.report(user, report_id)['report']
        payload = report.get('knowledge_payload', {})
        if not payload.get('exploration', {}).get('enabled'):
            return
        valid = {r['id'] for r in payload.get('evidence', [])}
        with self.repo._connect(write=True) as db:
            for item in payload.get('ranked_items', []):
                decision = item.get('recommendation')
                if item['evidence_id'] in valid and decision and decision.get('policy') == POLICY_VERSION:
                    db.execute('INSERT OR IGNORE INTO recommendation_decisions VALUES (?,?,?,?,0)',
                        (user, report_id, item['evidence_id'], json.dumps(decision, ensure_ascii=False)))

    def mark_displayed(self, user, report_id, ids):
        self.repo.report(user, report_id)  # Cross-account report IDs cannot be inspected or modified.
        with self.repo._connect(write=True) as db:
            for identity in set(ids):
                db.execute('UPDATE recommendation_decisions SET displayed=1 WHERE user_id=? AND report_id=? AND evidence_id=?',
                           (user, report_id, identity))

    def feedback(self, user, report_id, evidence_id, kind):
        if kind not in ('useful', 'irrelevant', 'clear'):
            raise ValueError('拓展反馈类型无效')
        if not self.settings(user)['enabled'] and kind != 'clear':
            raise ValueError('请先开启兴趣拓展；关闭时不学习反馈')
        self.repo.report(user, report_id)
        with self.repo._connect(write=True) as db:
            row = db.execute('SELECT content,displayed FROM recommendation_decisions WHERE user_id=? AND report_id=? AND evidence_id=?',
                             (user, report_id, evidence_id)).fetchone()
            if not row or not row['displayed'] or json.loads(row['content']).get('role') != 'explore':
                raise ValueError('只能评价本账号已展示的拓展推荐')
            if kind == 'clear':
                db.execute('DELETE FROM recommendation_feedback WHERE user_id=? AND report_id=? AND evidence_id=?',
                           (user, report_id, evidence_id))
            else:
                db.execute('INSERT OR REPLACE INTO recommendation_feedback VALUES (?,?,?,?,?)',
                           (user, report_id, evidence_id, float(kind == 'useful'), _now()))

    def feedback_kind(self, user, report_id, evidence_id):
        self.repo.report(user, report_id)
        with self.repo._connect() as db:
            row = db.execute('SELECT reward FROM recommendation_feedback WHERE user_id=? AND report_id=? AND evidence_id=?',
                             (user, report_id, evidence_id)).fetchone()
        return 'clear' if row is None else 'useful' if row['reward'] == 1 else 'irrelevant'

    def learned(self, user):
        with self.repo._connect() as db:
            self.repo._require_user(db, user)
            rows = db.execute('''SELECT d.content,f.reward,f.evidence_id FROM recommendation_feedback f
                JOIN recommendation_decisions d USING(user_id,report_id,evidence_id)
                WHERE f.user_id=? AND d.displayed=1 ORDER BY f.updated_at DESC LIMIT 5000''', (user,)).fetchall()
        result, seen = {}, set()
        for row in rows:
            item = json.loads(row['content'])
            if item.get('policy') != POLICY_VERSION or item.get('role') != 'explore':
                continue
            key = item['context'], item['tag']
            sample = (*key, row['evidence_id'])
            if sample in seen:
                continue  # Re-generated reports must not amplify the same article's feedback.
            seen.add(sample)
            stat = result.setdefault(key, {'sum': 0., 'count': 0})
            stat['sum'] += row['reward']
            stat['count'] += 1
        return result

    def reset(self, user):
        with self.repo._connect(write=True) as db:
            self.repo._require_user(db, user)
            db.execute('DELETE FROM recommendation_feedback WHERE user_id=?', (user,))
