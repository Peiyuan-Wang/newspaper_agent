"""Local entity browse and conservative event timelines; no network/model calls on open."""
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
                              QLineEdit, QComboBox, QSplitter, QScrollArea)
from newspaper_agent.database.news_repository import NewsRepository


class LibraryView(QWidget):
    def __init__(self, window):
        super().__init__()
        from .app import label, button
        self.window, self.store, self.offset = window, NewsRepository(window.repo), 0
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText('搜索历史标题和摘要')
        self.kind = QComboBox()
        for text, value in [('全部专题', None), ('公司', 'company'), ('模型', 'model'), ('研究方向', 'topic')]:
            self.kind.addItem(text, value)
        self.order = QComboBox()
        self.order.addItems(['最新在前', '最早在前'])
        controls.addWidget(self.query, 1)
        controls.addWidget(self.kind)
        controls.addWidget(self.order)
        controls.addWidget(button('查找', self.search))
        layout.addLayout(controls)
        split = QSplitter()
        self.entities = QListWidget()
        self.entities.setMinimumWidth(180)
        self.entities.setMaximumWidth(280)
        self.entities.currentItemChanged.connect(lambda *_: self.search())
        split.addWidget(self.entities)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        split.addWidget(self.scroll)
        layout.addWidget(split, 1)
        bottom = QHBoxLayout()
        self.status = label('', 'subtitle')
        bottom.addWidget(self.status, 1)
        self.previous = button('上一页', lambda: self.move(-1))
        self.next = button('下一页', lambda: self.move(1))
        bottom.addWidget(self.previous)
        bottom.addWidget(self.next)
        layout.addLayout(bottom)
        self.kind.currentIndexChanged.connect(lambda *_: self.refresh())
        self.order.currentIndexChanged.connect(lambda *_: self.search())
        self.query.returnPressed.connect(self.search)

    def visible(self):
        return {s.id for s in self.window.session.sources.descriptors()}

    def refresh(self):
        visible = self.visible()
        self.store.backfill_titles(self.window.user, visible)
        previous = self.entities.currentItem().data(Qt.ItemDataRole.UserRole) if self.entities.currentItem() else None
        self.entities.blockSignals(True)
        self.entities.clear()
        all_item = QListWidgetItem('全部新闻')
        all_item.setData(Qt.ItemDataRole.UserRole, None)
        self.entities.addItem(all_item)
        current = 0
        for e in self.store.entity_counts(self.window.user, visible):
            if self.kind.currentData() and e['kind'] != self.kind.currentData():
                continue
            item = QListWidgetItem(f"{e['name']}  {e['count']}")
            data = (e['name'], e['kind'])
            item.setData(Qt.ItemDataRole.UserRole, data)
            self.entities.addItem(item)
            if data == previous:
                current = self.entities.count() - 1
        self.entities.setCurrentRow(current)
        self.entities.blockSignals(False)
        self.search()

    def search(self):
        self.offset = 0
        self.show_page()

    def move(self, direction):
        self.offset = max(0, self.offset + 50 * direction)
        self.show_page()

    def show_page(self):
        from .app import label, box, button, open_link, disclosure
        selected = self.entities.currentItem()
        entity = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        result = self.store.library(self.window.user, visible_sources=self.visible(),
            entity=entity[0] if entity else None, kind=entity[1] if entity else None,
            query=self.query.text().strip(), limit=50, offset=self.offset,
            ascending=self.order.currentIndex() == 1)
        rows = result['items']
        container = QWidget()
        layout = QVBoxLayout(container)
        groups = {}
        for row in rows:
            key = row['event_id'] or row['record']['id']
            groups.setdefault(key, []).append(row)
        self.displayed_groups = len(groups)
        for identity, reports in groups.items():
            row, record = reports[0], reports[0]['record']
            card, body = box()
            event = row['event']
            title = f"{event['subject']} {event['action']} {event['object']}" if event else record['title']
            body.addWidget(label(title, 'cardTitle'))
            if event and event.get('occurred_at'):
                date_text = '事件日期 ' + event['occurred_at']
            else:
                date_text = '报道日期 ' + (record.get('published_at') or '未知')[:10]
            score_text = str(row['score']) + '/100' if row['score_state'] == 'scored' else '待评分'
            body.addWidget(label(f"{date_text} · {record['publisher']} · 历史兴趣分 {score_text}", 'eyebrow'))
            entities = row['entities']
            if entity and any(e['name'] == entity[0] and e['basis'] == 'title' for e in entities):
                body.addWidget(label('标题关联，尚未按正文确认主体', 'subtitle'))
            if event:
                body.addWidget(label('事件归属为AI提取，尚未经人工核对', 'subtitle'))
                if event.get('continues'):
                    body.addWidget(label('已有相关前序进展', 'subtitle'))
                body.addWidget(button('查看进展时间线', lambda checked=False, eid=identity: self.show_timeline(eid)))
            _, detail = disclosure(body, f'报道与证据 · {len(reports)}')
            for report in reports:
                article = report['record']
                detail.addWidget(label((article.get('published_at') or '日期未知')[:10] + ' · ' + article['title']))
                if report['event']:
                    detail.addWidget(label(report['event']['quote'], 'subtitle'))
                detail.addWidget(button('查看原文 ↗', lambda checked=False, u=article['url']: open_link(QUrl(u))))
            layout.addWidget(card)
        if not groups:
            layout.addWidget(label('没有匹配的历史新闻。更新简报后，采集结果会进入这里。'))
        layout.addStretch()
        self.scroll.setWidget(container)
        self.previous.setEnabled(self.offset > 0)
        self.next.setEnabled(self.offset + len(rows) < result['total'])
        self.status.setText(f"本机已收录 {result['total']} 篇 · 本页 {len(groups)} 个事件或报道 · 不代表全网覆盖")

    def show_timeline(self, event_id):
        from .app import label, box, button, open_link
        dialog = QDialog(self)
        dialog.setWindowTitle('事件进展与报道')
        dialog.resize(760, 650)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label('事件进展', 'title'))
        layout.addWidget(label('按时间从早到晚；未知发生日期时标为报道日期。事件关系为AI提取。', 'subtitle'))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        for row in self.store.timeline(self.window.user, event_id, self.visible()):
            event, record = row['event'], row['record']
            card, inner = box()
            date_text = ('发生 ' + event['occurred_at'] if event.get('occurred_at') else
                         '报道 ' + (record.get('published_at') or '日期未知')[:10])
            inner.addWidget(label(date_text, 'eyebrow'))
            inner.addWidget(label(f"{event['subject']} {event['action']} {event['object']}", 'cardTitle'))
            inner.addWidget(label(record['title']))
            inner.addWidget(label(event['quote'], 'subtitle'))
            inner.addWidget(button('原文 ↗', lambda checked=False, u=record['url']: open_link(QUrl(u))))
            body.addWidget(card)
        body.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        self.timeline_dialog = dialog
        dialog.show()
