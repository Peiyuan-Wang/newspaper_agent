from __future__ import annotations

import logging

logger = logging.getLogger("newspaper_agent")

def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level)
