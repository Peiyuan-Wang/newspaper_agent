"""Background-only orchestration: cache encoding, replay feedback, match candidates."""
from datetime import datetime, timezone
from newspaper_agent.database.semantic_repository import SemanticRepository
from newspaper_agent.database.news_repository import NewsRepository
from newspaper_agent.database.news_feedback import NewsFeedback
from newspaper_agent.sources.annotations import content_tags
from newspaper_agent.semantic_profile import identity, seed_specs, mean, make_slots, evolve, match, POLICY
from newspaper_agent.news_ranking import article_key
from newspaper_agent.agent_core.runtime import RunCancelled

FEATURE_VERSION = 'title2-abstract600x200-tags15-v1'


class SemanticPersonalizer:
    def __init__(self, repository, user, encoder, *, control=None):
        self.repo, self.user, self.encoder, self.control = repository, user, encoder, control
        self.store = SemanticRepository(repository)
        self.version = encoder.version + ':' + FEATURE_VERSION

    def check(self):
        if self.control:
            self.control.check()

    def encode(self, texts):
        texts = list(dict.fromkeys(texts))
        cached = self.store.vectors(self.user, self.version, texts)
        missing = [t for t in texts if identity(t) not in cached]
        for start in range(0, len(missing), 32):
            self.check()
            batch = missing[start:start+32]
            vectors = self.encoder.encode(batch)
            if len(vectors) != len(batch) or any(len(v) != self.encoder.dimension for v in vectors):
                raise ValueError('语义编码维数或数量不符')
            values = {identity(t): v for t,v in zip(batch,vectors)}
            self.store.save_vectors(self.user, self.version, values)
            cached.update(values)
            if self.control:
                self.control.emit('semantic_encoding', count=min(start+32,len(missing)), total=len(missing))
        return {t: cached[identity(t)] for t in texts}

    def documents(self, records, source_map):
        cached = self.store.documents(self.user, self.version, [r.id for r in records])
        inputs, texts = {}, []
        for record in records:
            tags = list(dict.fromkeys(t['label'] for t in content_tags(record)
                                      if t['facet'] != 'contribution_mention'))[:4]
            pieces = [record.title[:200]] + [record.snippet[i:i+200] for i in range(0,min(600,len(record.snippet)),200)]
            pieces = [t for t in pieces if t.strip()]
            source = source_map.get(record.source_id)
            domains = list(source.domains) if source else []
            digest = identity([pieces,tags,domains])
            if record.id not in cached or cached[record.id].get('digest') != digest:
                inputs[record.id] = pieces,tags,domains,digest
                texts.extend(pieces+tags)
        encoded = self.encode(texts)
        values = {}
        for eid,(pieces,tags,domains,digest) in inputs.items():
            body = mean([encoded[t] for t in pieces], [2.]+[1.]*(len(pieces)-1))
            vector = mean([body,mean([encoded[t] for t in tags])], [.85,.15]) if tags else body
            values[eid] = {'vector': vector, 'domains': domains, 'digest': digest,
                           'tags': [{'label': t, 'vector': encoded[t]} for t in tags]}
        self.store.save_documents(self.user, self.version, values)
        cached.update(values)
        return cached

    def build(self, profile, records, source_map, *, now=None):
        self.check()
        specs = seed_specs(profile)
        # Encode only fine-grained descriptions; category strings are never encoded.
        parts = {s['id']: [s['text'][i:i+200] for i in range(0,len(s['text']),200)] for s in specs}
        encoded = self.encode([p for group in parts.values() for p in group])
        vectors = {key: mean([encoded[p] for p in group]) for key,group in parts.items()}
        slots = make_slots(specs, vectors)
        feedback = self.store.feedback(self.user)
        learning = NewsFeedback(self.repo).preferences(self.user)['learning']
        if not learning:
            feedback = []
        existing = {r.id for r in records}
        old_ids = {r['evidence_id'] for r in feedback} - existing
        older = NewsRepository(self.repo).records(self.user, old_ids)
        older = [r for r in older if r.source_id in source_map]
        docs = self.documents([*records,*older], source_map)
        visible_ids = existing | {r.id for r in older}
        observations = [{**r, **docs[r['evidence_id']]} for r in feedback
                        if r['evidence_id'] in docs and r['evidence_id'] in visible_ids]
        evolved, ambiguous = evolve(slots, observations, now=now)
        now = now or datetime.now(timezone.utc)
        state = {'policy': POLICY, 'encoder': self.version, 'dimension': self.encoder.dimension,
                 'profile_key': identity(specs), 'feedback_key': identity(feedback), 'slots': evolved,
                 'feedback_count': len(observations), 'ambiguous_feedback': ambiguous,
                 'unavailable_feedback': len(feedback)-len(observations), 'learning': learning,
                 'rebuilt_at': now.isoformat()}
        self.check()
        self.store.save_state(self.user,state)
        matches = {r.id: match(evolved,docs[r.id]) for r in records}
        return state, matches, feedback


def semantic_adjustments(session, records, profile, source_map, scores):
    """Never silently replace real embeddings with hashing or generated numbers."""
    store = SemanticRepository(session.repository)
    if not store.enabled(session.user):
        return None, {'status': 'disabled'}, {}
    if not seed_specs(profile):
        return None, {'status': 'no_confirmed_interests'}, {}
    try:
        encoder = getattr(session, 'text_encoder', None)
        if encoder is None:
            from newspaper_agent.model.embeddings import model_ready, local_encoder
            directory = str(session.repository.path.parent)
            if not model_ready(directory):
                return None, {'status': 'model_not_ready'}, {}
            encoder = local_encoder(directory)
        engine = SemanticPersonalizer(session.repository, session.user, encoder, control=session.control)
        state, matches, feedback = engine.build(profile, records, source_map)
        votes = {r['key']: r['kind'] for r in feedback}
        adjustments = {}
        for record in records:
            matched, base = matches[record.id], scores.get(record.id)
            if matched is None or base is None:
                adjustments[record.id] = 0.
                continue
            direct = {'up': 4., 'down': -4.}.get(votes.get(article_key(record)),0.)
            # Conservative hybrid; cosine score is an engineering scale, not a calibrated probability.
            adjustments[record.id] = max(-20., min(20., .25*(matched['score']-base)+direct))
        summary = {k: state[k] for k in ('policy','encoder','dimension','profile_key','feedback_key',
                                        'feedback_count','ambiguous_feedback','unavailable_feedback','learning')}
        summary.update(status='active', slots=len(state['slots']), categories=len({s['category'] for s in state['slots']}))
        return adjustments, summary, matches
    except RunCancelled:
        raise
    except (ValueError, RuntimeError, OSError, ImportError):
        return None, {'status': 'semantic_unavailable'}, {}
