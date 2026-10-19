from __future__ import annotations

import os

from newspaper_agent.model.settings import ProviderSettings, load_provider_settings


def database_url() -> str:
    return os.getenv("NEWSPAPER_AGENT_DATABASE_URL", "sqlite:///data/newspaper_agent.sqlite3")
