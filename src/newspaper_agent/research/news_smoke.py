"""One bounded public-feed probe per AI news source; no account or model call."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
from newspaper_agent.sources.catalog import SourceCatalog
from newspaper_agent.sources.contracts import SearchRequest
from newspaper_agent.sources.live_adapters import build_adapter
from newspaper_agent.sources.filtering import select_evidence


def main():
    path = Path(__file__).resolve().parents[3] / 'config/sources/live.yaml'
    sources = [s for s in SourceCatalog.load(path).sources if s.id in {'ai_openai', 'ai_huggingface', 'ai_techcrunch'}]
    request = SearchRequest('ai agent', ('news',), max_results=2, recent_days=30)
    def probe(source):
        try:
            candidates = build_adapter(source).search(request)
            chosen, _ = select_evidence(candidates, request.query, {}, recent_days=30)
            return {'source': source.id, 'status': 'ok', 'candidates': len(candidates),
                    'kept': len(chosen), 'titles': [r.title for r in chosen]}
        except Exception as error:
            return {'source': source.id, 'status': 'failed', 'code': getattr(error, 'code', 'source_unavailable')}
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(probe, sources))
    print(json.dumps({'actual_model_requests': 0, 'sources': results}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
