"""Explicit, small public-network smoke check. Never uses a model or API key."""
from dataclasses import asdict
import json
from pathlib import Path
import secrets
from tempfile import TemporaryDirectory

from newspaper_agent.database.research_repository import ResearchRepository
from newspaper_agent.sources.catalog import SourceCatalog
from newspaper_agent.sources.contracts import SearchRequest
from newspaper_agent.sources.live_adapters import build_adapter
from newspaper_agent.synthesis import SynthesisService


def main():
    root = Path(__file__).resolve().parents[3]
    catalog = SourceCatalog.load(root / "config/sources/live.yaml")
    # These three checks do not retry externally rate-limited arXiv/PubMed endpoints.
    cases = {"bbc_world": ("latest news", "news"), "fed_press": ("latest news", "finance"),
             "crossref": ("machine learning", "academic")}
    checks = []
    with TemporaryDirectory(prefix="newspaper-v1-smoke-") as directory:
        repository = ResearchRepository(Path(directory) / "research.sqlite3")
        repository.register("smoke", secrets.token_urlsafe(24))
        for source in catalog.sources:
            if source.id not in cases:
                continue
            query, domain = cases[source.id]
            try:
                records = build_adapter(source).search(SearchRequest(query, (domain,), max_results=1))
                report = SynthesisService(None).generate(query, records)
                report_id = repository.save_report("smoke", query, asdict(report))
                restored = repository.report("smoke", report_id)
                checks.append({"source": source.id, "status": "ok" if records else "empty",
                    "count": len(records), "content_level": records[0].content_level if records else None,
                    "url": records[0].url if records else None,
                    "report_round_trip": restored["report"]["markdown"] == report.markdown})
            except Exception as exc:
                checks.append({"source": source.id, "status": "failed", "error": getattr(exc, "code", "request_failed")})
    print(json.dumps({"model_requests": 0, "paid_requests": 0, "temporary_data_removed": True, "checks": checks}, ensure_ascii=False, indent=2))
    if not all(row["status"] == "ok" and row["report_round_trip"] for row in checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
