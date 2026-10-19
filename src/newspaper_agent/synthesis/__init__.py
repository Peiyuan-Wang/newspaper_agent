"""可替换的知识综合层；不负责检索、用户认证或持久化。"""

from .contracts import Report
from .service import SynthesisService

__all__ = ["Report", "SynthesisService"]
