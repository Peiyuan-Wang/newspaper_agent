"""A message is an independent widget: cursor position cannot reorder messages."""
import re
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QScrollArea, QWidget, QVBoxLayout, QFrame, QLabel, QTextBrowser
from .rich_text import RichBrowser


class MessageBody(RichBrowser):
    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self.fit_height)

    def fit_height(self):
        self.document().setTextWidth(max(100, self.viewport().width()))
        height = min(760, max(42, int(self.document().size().height()) + 24))
        if self.height() != height:
            self.setFixedHeight(height)


class ChatView(QScrollArea):
    def __init__(self, open_link, parent=None):
        super().__init__(parent)
        self.open_link = open_link
        self.setWidgetResizable(True)
        self.content = QWidget()
        self.layout = QVBoxLayout(self.content)
        self.layout.setContentsMargins(4, 4, 10, 8)
        self.layout.setSpacing(16)
        self.layout.addStretch()
        self.setWidget(self.content)
        self.messages = []
        self.follow_tail = True
        self.verticalScrollBar().valueChanged.connect(self.scroll_changed)

    def scroll_changed(self, value):
        self.follow_tail = value >= self.verticalScrollBar().maximum() - 40

    def add_message(self, role, text, *, markdown=False):
        card = QFrame()
        card.setObjectName('userMessage' if role == 'user' else 'card')
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        name = QLabel({'user': '你', 'assistant': '知闻', 'error': '这次没有完成', 'system': '会话提示'}.get(role, role))
        name.setObjectName('eyebrow')
        layout.addWidget(name)
        body = MessageBody()
        body.setObjectName('messageBody')
        body.setOpenLinks(False)
        body.anchorClicked.connect(self.open_link)
        if markdown:
            # Pure chat may not pass through the evidence renderer. Do not load
            # model-supplied HTML or images (including local-file image links).
            text = re.sub(r'<[^>]*>', '', text)
            text = re.sub(r'!\[([^\]]*)\]\([^)]*\)', r'\1', text)
            body.setMarkdown(text)
        else:
            body.setPlainText(text)
        layout.addWidget(body)
        self.layout.insertWidget(self.layout.count() - 1, card)
        self.messages.append({'role': role, 'text': text, 'body': body})
        QTimer.singleShot(0, body.fit_height)
        if self.follow_tail:
            QTimer.singleShot(0, lambda: self.verticalScrollBar().setValue(self.verticalScrollBar().maximum()))
        return body

    def clear(self):
        for _ in range(self.layout.count() - 1):
            item = self.layout.takeAt(0)
            item.widget().hide()
            item.widget().deleteLater()
        self.messages.clear()
        self.follow_tail = True

    def remove_message(self, body):
        for message in self.messages[:]:
            if message['body'] is body:
                card = body.parentWidget()
                self.layout.removeWidget(card)
                card.hide()
                card.deleteLater()
                self.messages.remove(message)
                return

    def setPlainText(self, text):
        self.clear()
        self.add_message('system', text)

    def toPlainText(self):
        return '\n\n'.join(m['text'] for m in self.messages)
