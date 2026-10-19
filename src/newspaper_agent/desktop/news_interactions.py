"""Human-only card interactions; viewport impressions never imply article reading."""
import time
from uuid import uuid4
from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QHBoxLayout
from newspaper_agent.database.news_feedback import NewsFeedback


def interaction_row(window, body, record, state):
    from .app import button, label, open_link
    from PySide6.QtCore import QUrl
    store = NewsFeedback(window.repo)
    report_id, user = window.report_id, window.user
    row = QHBoxLayout()
    up = button('👍 有帮助', lambda: None)
    down = button('👎 没帮助', lambda: None)
    for b in (up, down):
        b.setCheckable(True)
    up.setChecked(state['vote'] == 1)
    down.setChecked(state['vote'] == -1)
    vote_state = [state['vote']]
    def vote(value):
        if window.busy:
            return
        new = 0 if vote_state[0] == value else value
        # UI only commits its toggle state after a successful account-checked write.
        try:
            store.vote(user, report_id, record['id'], new)
            vote_state[0] = new
            window.statusBar().showMessage('已撤回评价' if new == 0 else '已记录；下次更新简报用于关联性校正，不修改来源可信度')
        finally:
            up.setChecked(vote_state[0] == 1)
            down.setChecked(vote_state[0] == -1)
    up.clicked.connect(lambda: window.local(lambda: vote(1)))
    down.clicked.connect(lambda: window.local(lambda: vote(-1)))
    row.addWidget(up)
    row.addWidget(down)
    count = label(f"原文打开 {state['clicks']} 次", 'subtitle')
    def opened():
        if window.busy:
            return
        if open_link(QUrl(record['url'])) is True:
            def save_open():
                store.opened(user, report_id, record['id'])
                state['clicks'] = store.state(user, report_id)[record['id']]['clicks']
                count.setText(f"原文打开 {state['clicks']} 次")
            window.local(save_open)
        else:
            window.statusBar().showMessage('未能打开原文；未计作成功打开或已阅读')
    row.addWidget(button('查看原文 ↗', opened))
    row.addStretch()
    row.addWidget(count)
    body.addLayout(row)


class ExposureTracker(QObject):
    def __init__(self, window, cards, section):
        super().__init__(window)
        self.window, self.cards, self.section = window, cards, section
        self.report_id = window.report_id
        if not hasattr(window, 'news_view_session'):
            window.news_view_session = uuid4().hex
        self.session = window.news_view_session
        self.since, self.recorded = {}, set()
        self.timer = QTimer(self)
        self.timer.setInterval(750)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def stop(self):
        self.timer.stop()
        self.deleteLater()

    def tick(self):
        w = self.window
        if not w.isVisible() or not w.isActiveWindow() or w.stack.currentIndex() != 0 or w.reader.currentIndex() != 0:
            self.since.clear()
            return
        viewport = w.ranked_scroll.viewport()
        rect = viewport.rect()
        now = time.monotonic()
        for position, (identity, card) in enumerate(self.cards):
            if identity in self.recorded:
                continue
            top = card.mapTo(viewport, card.rect().topLeft())
            from PySide6.QtCore import QRect
            bounds = QRect(top, card.size())
            visible = bounds.intersected(rect)
            if visible.width()*visible.height() < .5*bounds.width()*bounds.height():
                self.since.pop(identity, None)
                continue
            self.since.setdefault(identity, now)
            if now - self.since[identity] >= 1:
                NewsFeedback(w.repo).impression(w.user, self.report_id, identity, self.section, position, self.session)
                self.recorded.add(identity)
