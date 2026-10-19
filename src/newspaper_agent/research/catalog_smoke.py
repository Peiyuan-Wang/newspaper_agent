"""Explicit public RSS probes; no database, profile, model, or paid search traffic."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import argparse
import json
from newspaper_agent.sources.catalog import SourceCatalog
from newspaper_agent.sources.live_adapters import build_adapter


AIHOT_SOURCE_IDS = frozenset({
    'ai_openai', 'ai_huggingface', 'ai_techcrunch', 'ai_deepmind',
    'ai_google_research', 'ai_microsoft_research', 'ai_nvidia', 'ai_aws_ml',
    'ai_github', 'ai_mistral', 'ai_bair', 'ai_verge', 'ai_ars',
    'ai_mit_review', 'ai_decoder', 'ai_simon', 'ai_import', 'ai_latent',
})

EXPANDED_SOURCE_IDS = frozenset({
    'hn_community', 'hf_forum', 'nist_news', 'quanta_science', 'aps_physics', 'aps_prl',
    'arxiv_condmat_feed', 'arxiv_ai_feed', 'arxiv_ml_feed', 'arxiv_cl_feed', 'arxiv_math_feed',
    'physorg_physics', 'sciencedaily_physics', 'sspai_news', 'ithome_news', 'solidot_news',
    'kr36_news', 'infoq_news', 'nasa_news', 'who_news', 'ieee_ai',
})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--aihot', action='store_true', help='Probe the 18 public AIHOT RSS endpoints, including suspended entries')
    parser.add_argument('--expanded', action='store_true', help='Probe the 21 additional public endpoints, including suspended entries')
    args = parser.parse_args(argv)
    catalog = SourceCatalog.load(Path(__file__).resolve().parents[3] / 'config/sources/live.yaml')
    ids = {'mit_ai', 'mit_physics', 'nature_physics', 'nature_medicine', 'nih_nibib',
           'ecb_press', 'bbc_technology', 'bbc_science', 'nasa_jpl'}
    if args.aihot:
        ids = AIHOT_SOURCE_IDS
    if args.expanded:
        ids = EXPANDED_SOURCE_IDS
    def probe(source):
        try:
            return {'id': source.id, 'status': 'ok', 'count': len(build_adapter(source).collect())}
        except Exception as exc:
            return {'id': source.id, 'status': 'failed', 'error': getattr(exc, 'code', 'feed_unavailable')}
    with ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(probe, [s for s in catalog.sources if s.id in ids]):
            print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
