from dataclasses import fields
from pathlib import Path
import yaml

from .contracts import SearchRequest, SourceDescriptor


class SourceCatalog:
    def __init__(self, sources):
        self.sources = tuple(sources)
        if len({s.id for s in self.sources}) != len(self.sources):
            raise ValueError("来源 id 重复")

    @classmethod
    def load(cls, path: Path):
        try:
            if path.stat().st_size > 128_000:
                raise ValueError
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or set(raw) != {"version", "sources"} or type(raw["version"]) is not int or raw["version"] != 1:
                raise ValueError
            if not isinstance(raw["sources"], list) or not 1 <= len(raw["sources"]) <= 100:
                raise ValueError
            sources = []
            allowed = {f.name for f in fields(SourceDescriptor)}
            required = {"id", "name", "domains", "kinds", "regions", "languages", "adapter", "status", "homepage"}
            for entry in raw["sources"]:
                if not isinstance(entry, dict) or not required <= set(entry) <= allowed:
                    raise ValueError
                values = dict(entry)
                for key in ("domains", "kinds", "regions", "languages", "capabilities", "filters", "topics"):
                    if key in values:
                        if not isinstance(values[key], list):
                            raise ValueError
                        values[key] = tuple(values[key])
                sources.append(SourceDescriptor(**values))
            return cls(sources)
        except (OSError, ValueError, TypeError, yaml.YAMLError):
            raise ValueError("来源目录无效；检查版本、分类、白名单适配器与状态") from None

    def route(self, request: SearchRequest, preferences=None):
        preferences = preferences or {}
        candidates, warnings = [], []
        for source in self.sources:
            if source.status != "enabled" or "search" not in source.capabilities:
                continue
            overlap = set(request.domains) & set(source.domains)
            fallback = not overlap and "general" in source.domains
            if not overlap and not fallback:
                continue
            unsupported = []
            for key, wanted, supported in (("kind", request.kind, source.kinds),
                                            ("region", request.region, source.regions),
                                            ("language", request.language, source.languages)):
                if wanted is not None and (key not in source.filters or wanted not in supported and "*" not in supported):
                    unsupported.append(key)
            if unsupported:
                warnings.append(f"{source.id}: unsupported_filter:{','.join(unsupported)}")
                continue
            pref = preferences.get(source.id, 0)
            pref = max(-2, min(2, pref)) if type(pref) is int else 0
            candidates.append(((-len(overlap), fallback, -pref, -int(source.role == "primary"), source.id), source))
        candidates.sort(key=lambda item: item[0])
        remaining = [item[1] for item in candidates]
        uncovered = set(request.domains)
        selected_list = []
        while remaining and len(selected_list) < request.max_sources:
            index = next((i for i, s in enumerate(remaining) if uncovered & set(s.domains)), 0)
            source = remaining.pop(index)
            selected_list.append(source)
            uncovered -= set(source.domains)
        selected = tuple(selected_list)
        if not selected:
            warnings.append("no_eligible_source")
        elif not any(set(request.domains) & set(s.domains) for s in selected):
            warnings.append("general_fallback: 未匹配已启用的专业来源，使用通用来源")
        missing = set(request.domains) - {d for s in selected for d in s.domains}
        if missing:
            warnings.append("uncovered_domains:" + ",".join(sorted(missing)))
        return selected, tuple(warnings)
