"""Presentation-neutral progress and cooperative cancellation boundary."""
from threading import Event
from time import monotonic


class RunCancelled(RuntimeError):
    pass


class RunControl:
    def __init__(self, notify=None, *, max_seconds=150):
        self.cancelled = Event()
        self.notify = notify
        self.started = monotonic()
        self.max_seconds = max_seconds

    def cancel(self):
        self.cancelled.set()

    def check(self):
        if self.cancelled.is_set():
            raise RunCancelled('已停止本轮任务；已完成的反馈或来源修改仍保留，可在对应页面检查。')
        if self.max_seconds is not None and monotonic() - self.started > self.max_seconds:
            raise RunCancelled('本轮达到时间上限，请缩小问题后重试。')

    def emit(self, kind, **data):
        self.check()
        if self.notify:
            self.notify({'type': kind, **data})
