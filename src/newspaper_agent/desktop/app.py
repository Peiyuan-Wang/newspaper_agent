"""Qt presentation: all network/model operations run outside the UI thread."""
from datetime import datetime
from pathlib import Path
import sys
import os
import yaml
import time
import json

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot, Qt, QUrl, QTimer
from PySide6.QtGui import QDesktopServices, QFontDatabase
from PySide6.QtWidgets import (QApplication, QMainWindow, QDialog, QWidget, QFrame,
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit, QPlainTextEdit,
    QTextBrowser, QComboBox, QCheckBox, QListWidget, QListWidgetItem,
    QStackedWidget, QScrollArea, QSplitter, QMessageBox, QGridLayout, QInputDialog)

from newspaper_agent.database.research_repository import ResearchRepository
from newspaper_agent.database.desktop_repository import DesktopRepository, CONTEXT_BUDGETS
from newspaper_agent.database.model_connections import ModelConnectionRepository
from .model_settings import ModelSettingsMixin
from newspaper_agent.research.service import ResearchSources
from newspaper_agent.research.session import ResearchSession
from newspaper_agent.research.model_runtime import create_research_model
from newspaper_agent.model.settings import load_provider_settings
from newspaper_agent.agent_core.messages import Message
from .theme import STYLE
from .chat_view import ChatView
from .rich_text import RichBrowser
from newspaper_agent.agent_core.runtime import RunControl, RunCancelled
from newspaper_agent.model.client import ModelCommunicationError
from newspaper_agent.synthesis.rendering import excerpt, render
from newspaper_agent.sources.filtering import select_evidence, select_report_evidence
from newspaper_agent.sources.contracts import EvidenceRecord
from newspaper_agent.sources.query import plan_query
from newspaper_agent.synthesis.editorial import category, policy

ROOT = Path(__file__).resolve().parents[3]
DOMAINS = {'news': '新闻与社会', 'academic': '学术研究', 'medical': '医学与生命科学',
           'finance': '金融与经济', 'general': '通用探索'}
KINDS = {'missed': '漏掉了重要信息', 'irrelevant': '推荐与我无关', 'useful': '这类内容很有用',
         'fact_error': '内容可能有误', 'too_long': '内容太长', 'unclear': '解释不够清楚',
         'not_new': '重复或没有新信息', 'too_shallow': '解读太浅', 'too_hard': '解读太难',
         'source_quality': '来源质量存疑', 'not_now': '今天暂时不想看'}


def label(text, name=None):
    w = QLabel(text)
    w.setTextFormat(Qt.TextFormat.PlainText)
    w.setWordWrap(True)
    if name:
        w.setObjectName(name)
    return w


def button(text, action, primary=False):
    w = QPushButton(text)
    w.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        w.setObjectName('primary')
    w.clicked.connect(action)
    return w


def box():
    w = QFrame()
    w.setObjectName('card')
    layout = QVBoxLayout(w)
    layout.setContentsMargins(22, 20, 22, 20)
    layout.setSpacing(12)
    layout.setAlignment(Qt.AlignmentFlag.AlignTop)
    return w, layout


def disclosure(layout, title):
    """Low-frequency controls stay reachable without occupying the default view."""
    toggle = button('+ ' + title, lambda: None)
    toggle.setObjectName('quiet')
    toggle.setProperty('sectionTitle', title)
    toggle.setCheckable(True)
    panel = QWidget()
    body = QVBoxLayout(panel)
    body.setContentsMargins(0, 6, 0, 6)
    body.setSpacing(10)
    panel.hide()
    toggle.toggled.connect(panel.setVisible)
    toggle.toggled.connect(lambda opened: toggle.setText(
        ('− ' if opened else '+ ') + toggle.property('sectionTitle')))
    layout.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)
    layout.addWidget(panel)
    return toggle, body


def card_scroll(card):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    card.setMaximumWidth(860)
    content = QWidget()
    content.setObjectName('canvas')
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(card, 0, Qt.AlignmentFlag.AlignTop)
    layout.addStretch()
    scroll.setWidget(content)
    return scroll


def browser():
    w = RichBrowser()
    w.setOpenLinks(False)
    w.anchorClicked.connect(open_link)
    return w


def open_link(url):
    if url.scheme() in ('https', 'http') and not url.userInfo():
        return QDesktopServices.openUrl(url)
    return False


class Signals(QObject):
    finished = Signal(object, str)
    progress = Signal(object)


class Job(QRunnable):
    def __init__(self, action):
        super().__init__()
        self.action, self.signals = action, Signals()

    def run(self):
        try:
            result, error = self.action(), ''
        except (ValueError, RunCancelled, ModelCommunicationError) as exc:
            result, error = None, str(exc)
        except Exception:
            # Do not expose network URLs, request bodies or credentials in dialogs.
            result, error = None, '操作未完成。请检查网络与配置后重试；未展示底层敏感请求。'
        self.signals.finished.emit(result, error)


class Login(QDialog):
    def __init__(self, repository):
        super().__init__()
        self.repository, self.user = repository, None
        from .account_access import RememberedLogin
        self.remembered = RememberedLogin(repository)
        self.setWindowTitle('知闻 · 登录')
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(42, 38, 42, 38)
        layout.setSpacing(18)
        layout.addWidget(label('KNOWLEDGE DESK', 'eyebrow'))
        layout.addWidget(label('知闻，与你的领域同行。', 'title'))
        layout.addWidget(label('登录本机账号，保存自己的兴趣、评分与研究记录。\n首次使用请创建账号；首个账号管理共享来源。', 'subtitle'))
        self.account, self.password = QLineEdit(), QLineEdit()
        self.account.setMaxLength(64)
        self.password.setMaxLength(1024)
        self.account.setPlaceholderText('账号 · 小写英文、数字或下划线')
        self.password.setPlaceholderText('本机账号密码 · 至少 8 位，不是 API key')
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.account)
        layout.addWidget(self.password)
        remembered_user = self.remembered.read().get('user', '')
        self.account.setText(remembered_user if isinstance(remembered_user, str) else '')
        self.keep_login = QCheckBox('保持登录30天 · 仅用于自己的电脑')
        layout.addWidget(self.keep_login)
        layout.addWidget(button('登录工作台', lambda: self.submit(False), True))
        layout.addWidget(button('创建本机账号', lambda: self.submit(True)))
        from .account_access import recover_account
        layout.addWidget(button('忘记密码 · 使用恢复码', lambda: recover_account(self, repository, self.account.text())))
        self.password.returnPressed.connect(lambda: self.submit(False))

    def submit(self, register):
        try:
            user = self.account.text().strip()
            if register:
                self.repository.register(user, self.password.text())
            if not self.repository.authenticate(user, self.password.text()):
                raise ValueError('账号或密码不正确')
            self.user = user
            self.remembered.save(user, self.password.text(), self.keep_login.isChecked())
            self.password.clear()
            self.accept()
        except ValueError as exc:
            QMessageBox.warning(self, '未能登录', str(exc))


class Window(ModelSettingsMixin, QMainWindow):
    def __init__(self, repository, user, session=None):
        super().__init__()
        self.repo, self.user = repository, user
        self.ratings = DesktopRepository(repository)
        self.connections = ModelConnectionRepository(repository)
        self.config_path = ROOT / 'model_config.yaml'
        self.session = session or ResearchSession(repository, user,
            ResearchSources(repository, user, catalog_path=ROOT / 'config/sources/live.yaml'))
        self.busy, self.report_id = False, None
        self.original_key_env = {}
        self.chat_pending = False
        self.request_diagnostics = {}
        self.last_error_body = None
        self.failure_callback = None
        self.control = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.setWindowTitle('知闻 · 个人信息与研究工作台')
        self.resize(1380, 900)
        self.setMinimumSize(1080, 720)
        self.central = QWidget()
        self.setCentralWidget(self.central)
        shell = QHBoxLayout(self.central)
        shell.setContentsMargins(0, 0, 0, 0)
        sidebar = QWidget()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(190)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(22, 34, 22, 24)
        nav.addWidget(label('知闻', 'brand'))
        nav.addWidget(label('个人信息助理', 'sideNote'))
        nav.addSpacing(24)
        self.stack = QStackedWidget()
        self.nav_buttons = []
        names = ['今日简报', '研究对话', '兴趣与领域', '反馈与改进', '信息来源', '连接设置', '新闻库']
        for index, text in enumerate(names):
            b = button(text, lambda checked=False, i=index: self.navigate(i))
            b.setObjectName('nav')
            b.setCheckable(True)
            self.nav_buttons.append(b)
            nav.addWidget(b)
        nav.addStretch()
        self.model_badge = label('模型未启用', 'sideNote')
        nav.addWidget(self.model_badge)
        nav.addWidget(label(user, 'sideNote'))
        shell.addWidget(sidebar)
        shell.addWidget(self.stack, 1)
        self.build_news()
        self.build_chat()
        self.build_profile()
        self.build_feedback()
        self.build_sources()
        self.build_settings()
        self.page('新闻库', '按公司、模型和研究方向浏览本机历史；同一事件的报道归组。')
        from .library_view import LibraryView
        self.library_view = LibraryView(self)
        self.stack.widget(6).layout().addWidget(self.library_view)
        self.refresh_reports()
        self.refresh_profile()
        self.refresh_events()
        self.refresh_sources()
        if session is None:
            previous = self.ratings.dialogue(self.user)
            if previous:
                self.chat_view.clear()
                for message in previous:
                    self.session.history.append(Message(message['role'], message['content']))
                    self.chat_view.add_message(message['role'], message['content'], markdown=message['role'] == 'assistant')
                self.chat_status.setText('已恢复上次完成的对话 · 启用模型后可继续追问')
        self.navigate(0)
        self.global_stop = button('停止任务', self.stop_job)
        self.global_stop.setEnabled(False)
        self.global_stop.hide()
        self.statusBar().addPermanentWidget(self.global_stop)
        self.progress_timer = QTimer(self)
        self.progress_timer.setInterval(500)
        self.progress_timer.timeout.connect(self.update_elapsed)
        self.statusBar().showMessage('就绪')

    def page(self, title, subtitle):
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(14)
        title = {'我的领域简报': '今日简报', '让信息，与你有关。': '兴趣与领域',
            '你的一句反馈，让下一次更准确。': '反馈与改进',
            '连接你的模型，保留你的选择。': '模型连接',
            '好的答案，从好的来源开始。': '信息来源'}.get(title, title)
        layout.addWidget(label(title, 'title'))
        content.setToolTip(subtitle)
        self.stack.addWidget(content)
        return layout

    def navigate(self, index):
        if index == 6:
            self.library_view.refresh()
        self.stack.setCurrentIndex(index)
        for i, b in enumerate(self.nav_buttons):
            b.setChecked(i == index)

    def run_job(self, action, done=None, failed=None):
        if self.busy:
            return
        self.busy = True
        self.disabled_buttons = [b for b in self.central.findChildren(QPushButton)
            if b.isEnabled() and b.objectName() != 'nav' and b is not self.stop_button]
        for b in self.disabled_buttons:
            b.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.global_stop.setEnabled(True)
        self.global_stop.show()
        self.phase = '正在准备任务'
        self.job_started = time.monotonic()
        self.progress_timer.start()
        self.update_elapsed()
        self.callback = done
        self.failure_callback = failed
        self.job = Job(action)
        self.control = RunControl(self.job.signals.progress.emit, max_seconds=None)
        self.session.set_control(self.control)
        self.session.fast_mode = self.fast_mode.isChecked()
        self.job.signals.progress.connect(self.on_progress)
        self.job.signals.finished.connect(self.job_finished)
        self.pool.start(self.job)

    @Slot(object, str)
    def job_finished(self, result, error):
        self.busy = False
        was_chat = self.chat_pending
        self.chat_pending = False
        self.progress_timer.stop()
        for b in self.disabled_buttons:
            try:
                b.setEnabled(True)
            except RuntimeError:
                pass  # A report refresh may have replaced the old article widgets.
        self.stop_button.setEnabled(False)
        self.global_stop.setEnabled(False)
        self.global_stop.hide()
        self.session.set_control(None)
        if error:
            if self.connection_state.text() == '正在测试连接…':
                self.connection_state.setText('测试未通过，请检查服务地址、密钥与网络后重试。')
            self.statusBar().showMessage('操作未完成 · 请检查提示后重试')
            if self.failure_callback:
                self.failure_callback(error)
            elif self.control and self.control.cancelled.is_set():
                if was_chat:
                    self.chat_status.setText('任务已停止')
                self.statusBar().showMessage(error)
            else:
                QMessageBox.warning(self, '需要留意', error)
        else:
            self.statusBar().showMessage('已完成')
            if self.callback:
                self.callback(result)

    def stop_job(self):
        if self.busy and self.control:
            self.control.cancel()
            self.phase = '停止中 · 等待当前网络请求收尾，不再发起后续请求'
            self.stop_button.setEnabled(False)
            self.global_stop.setEnabled(False)
            self.update_elapsed()

    @Slot(object)
    def on_progress(self, event):
        if not self.busy:
            return
        names = {'model_start': '模型正在分析问题', 'tool_start': '正在使用工具',
            'model_receiving': '正在接收模型响应',
            'synthesis_validate': '正在生成解读并校验引用',
            'synthesis_repair': '校验未通过，正在修正解读',
            'interest_scoring': 'AI正在为新闻库评估兴趣分',
            'semantic_encoding': '正在本机计算兴趣与新闻向量',
            'news_interpreting': 'AI正在解读排名靠前的新闻',
            'article_fetch': '正在获取入选新闻正文', 'article_reading': '正在分段阅读正文',
            'tool_end': '工具处理完成', 'source_start': '正在读取来源', 'source_end': '来源返回',
            'source_failed': '来源暂时不可用', 'filter_end': '正在筛选相关资料',
            'synthesis_start': '正在整理重点与引用', 'saving': '正在保存简报', 'complete': '回答已完成'}
        detail = event.get('name', '')
        detail = {'research_search': '检索资料', 'read_evidence': '读取证据',
                  'record_feedback': '记录反馈', 'discover_sources': '发现来源'}.get(detail, detail)
        count = f" · {event['count']} 条" if 'count' in event else ''
        self.phase = names.get(event['type'], '正在处理') + (' · ' + detail if detail else '') + count
        if event['type'] == 'model_retry':
            reason = f"服务暂时不可用（{event['status']}）" if event.get('status') else '模型响应连接中断'
            self.phase = f"{reason}，正在重试 {event['attempt']}/{event['total']}"
        if event['type'] == 'synthesis_repair':
            self.phase += f" {event['attempt']}/{event['total']}"
        if event['type'] in ('interest_scoring', 'news_interpreting', 'semantic_encoding'):
            self.phase = names[event['type']] + f" {event['count']}/{event['total']}"
        self.update_elapsed()

    def update_elapsed(self):
        if self.busy:
            text = f'{self.phase}  /  已用 {int(time.monotonic() - self.job_started)} 秒'
            self.statusBar().showMessage(text)
            if self.chat_pending:
                self.chat_status.setText(text)

    def local(self, action):
        if self.busy:
            return None
        try:
            result = action()
            self.statusBar().showMessage('已保存')
            return result
        except ValueError as exc:
            QMessageBox.warning(self, '请检查输入', str(exc))

    def build_news(self):
        layout = self.page('我的领域简报',
            f'{datetime.now():%Y 年 %m 月 %d 日}   /   手动更新简报 · 新闻日期以原文为准，不保证当日发布')
        controls, row = box()
        line = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText('今天想了解什么？例如 robotics、Federal Reserve')
        self.domain = QComboBox()
        self.domain.addItem('按兴趣自动选择', None)
        for key, name in DOMAINS.items():
            self.domain.addItem(name, key)
        line.addWidget(self.query, 1)
        line.addWidget(self.domain)
        self.news_mode = QComboBox()
        self.news_mode.addItem('订阅简报', 'subscriptions')
        self.news_mode.addItem('主动检索', 'search')
        self.news_mode.setToolTip('先由AI分批评分新闻库，再按所选数量解读；首次新闻多时请求较多。已有分数按档案版本复用，无模型时只保存待评分新闻。')
        line.addWidget(self.news_mode)
        self.period = QComboBox()
        for title, value in [('时间自动识别', None), ('今天 · 香港时间', 'today'), ('过去 24 小时', '24h'),
                             ('近 7 天', 7), ('近 30 天', 30), ('近 90 天', 90)]:
            self.period.addItem(title, value)
        self.period.setCurrentIndex(self.period.findData(policy()['default_recent_days']))
        line.addWidget(self.period)
        self.news_limit = self.ratings.news_limit(self.user)
        self.news_limit_button = button(f'最多 {self.news_limit} 条', self.choose_news_limit)
        self.news_limit_button.setToolTip('输入最多展示并解读的新闻数量（1—100），不足时不凑数；更多解读可能增加费用。')
        line.addWidget(self.news_limit_button)
        line.addWidget(button('更新简报  →', self.search, True))
        row.addLayout(line)
        row.addWidget(label('全库分批评分，再按所选数量解读。更多条数可能增加等待与费用；历史评分可复用。', 'subtitle'))
        self.query.setToolTip('英文来源建议用英文关键词；不输入则按首个兴趣检索。')
        layout.addWidget(controls)
        split = QSplitter()
        self.reports = QListWidget()
        self.reports.setMinimumWidth(200)
        self.reports.currentItemChanged.connect(self.select_report)
        split.addWidget(self.reports)
        self.reader = QStackedWidget()
        self.summary = browser()
        self.summary.setMarkdown('## 从一个关注点开始\n\n输入主题，生成你的第一份简报。')
        self.brief_stack = QStackedWidget()
        self.brief_stack.addWidget(self.summary)
        self.ranked_scroll = QScrollArea()
        self.ranked_scroll.setWidgetResizable(True)
        self.brief_stack.addWidget(self.ranked_scroll)
        self.reader.addWidget(self.brief_stack)
        self.article_scroll = QScrollArea()
        self.article_scroll.setWidgetResizable(True)
        self.reader.addWidget(self.article_scroll)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.news_section = QComboBox()
        from newspaper_agent.news_ranking import SECTIONS
        section_row = QHBoxLayout()
        for key, title in SECTIONS.items():
            self.news_section.addItem(title, key)
            b = button(title, lambda checked=False, value=key: self.news_section.setCurrentIndex(self.news_section.findData(value)))
            b.setCheckable(True)
            b.setObjectName('tab')
            b.setChecked(key == 'personal')
            self.news_section.currentIndexChanged.connect(lambda index, w=b, value=key: w.setChecked(self.news_section.itemData(index) == value))
            section_row.addWidget(b)
        self.news_section.currentIndexChanged.connect(lambda index: self.select_report(self.reports.currentItem()))
        from .ranking_settings import edit_ranking
        section_row.addWidget(button('指标权重', lambda: edit_ranking(self)))
        right_layout.addLayout(section_row)
        tabs = QHBoxLayout()
        for title, index in [('简报', 0), ('历史评分', 1)]:
            tab = button(title, lambda checked=False, i=index: self.reader.setCurrentIndex(i))
            tab.setObjectName('tab')
            tab.setCheckable(True)
            tab.setChecked(index == 0)
            self.reader.currentChanged.connect(lambda current, w=tab, i=index: w.setChecked(current == i))
            tabs.addWidget(tab)
        tabs.addStretch()
        organize = button('AI 重新整理', self.resynthesize_report)
        organize.setObjectName('quiet')
        organize.setToolTip('复用已保存新闻与评分，补读或复用正文并重新解读；可能计费，不重新搜索，不覆盖原报告。')
        tabs.addWidget(organize)
        feedback = button('反馈', lambda: self.navigate(3))
        feedback.setObjectName('quiet')
        tabs.addWidget(feedback)
        right_layout.addLayout(tabs)
        filters = QHBoxLayout()
        self.edition_category = QComboBox()
        self.edition_category.addItem('全部栏目', None)
        for item in policy()['categories']:
            self.edition_category.addItem(item['label'], item['id'])
        self.reading_style = QComboBox()
        for title, value in [('标准阅读', 'standard'), ('只看要点', 'brief'), ('展开证据', 'expert')]:
            self.reading_style.addItem(title, value)
        self.edition_order = QComboBox()
        self.edition_order.addItems(['相关性优先', '最新在前'])
        for widget in (self.edition_category, self.reading_style, self.edition_order):
            filters.addWidget(widget)
            widget.currentIndexChanged.connect(lambda index: self.select_report(self.reports.currentItem()))
        self.news_filters_toggle, filter_body = disclosure(right_layout, '筛选与阅读选项')
        filter_body.addLayout(filters)
        self.result_status = label('尚无简报 · 输入问题开始检索', 'subtitle')
        metadata = QHBoxLayout()
        metadata.addWidget(self.result_status, 1)
        self.search_details_toggle = QPushButton('检索详情 · 展开')
        self.search_details_toggle.setCheckable(True)
        self.search_details_toggle.setObjectName('quiet')
        self.search_details = QPlainTextEdit()
        self.search_details.setReadOnly(True)
        self.search_details.setMaximumHeight(150)
        self.search_details.hide()
        self.search_details_toggle.toggled.connect(self.search_details.setVisible)
        self.search_details_toggle.toggled.connect(lambda opened: self.search_details_toggle.setText(
            '检索详情 · 收起' if opened else '检索详情 · 展开'))
        metadata.addWidget(self.search_details_toggle)
        right_layout.addLayout(metadata)
        right_layout.addWidget(self.search_details)
        right_layout.addWidget(self.reader, 1)
        split.addWidget(right)
        split.setSizes([230, 800])
        layout.addWidget(split, 1)

    def choose_news_limit(self):
        value, ok = QInputDialog.getInt(self, '简报数量', '最多展示并解读多少条新闻？（1—100）', self.news_limit, 1, 100)
        if ok:
            self.ratings.set_news_limit(self.user, value)
            self.news_limit = value
            self.news_limit_button.setText(f'最多 {value} 条')

    def confirm_news_cost(self):
        return self.news_limit <= 10 or QMessageBox.question(self, '确认解读数量',
            f'本次最多解读{self.news_limit}条，可能显著增加模型调用、等待与费用。是否继续？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def search(self):
        if not self.confirm_news_cost():
            return
        query = self.query.text().strip() or 'latest news'
        domain = self.domain.currentData()
        period = self.period.currentData()
        recent_days = period if type(period) is int else None
        time_scope = period if isinstance(period, str) else None
        mode = self.news_mode.currentData()
        limit = self.news_limit
        self.run_job(lambda: self.session.search(query, [domain] if domain else None, recent_days=recent_days,
                                               time_scope=time_scope, mode=mode, max_news=limit), self.search_done)

    def search_done(self, result):
        self.refresh_reports(result.get('report_id'))
        self.reader.setCurrentIndex(0)

    def resynthesize_report(self):
        if self.report_id and self.confirm_news_cost():
            limit = self.news_limit
            self.run_job(lambda: self.session.resynthesize(self.report_id, max_news=limit), self.search_done)

    def show_request_details(self):
        client = getattr(self.session.model, 'client', None)
        diagnostic = self.request_diagnostics or getattr(client, 'last_diagnostic', {})
        safe = {k: v for k, v in diagnostic.items() if k in (
            'code', 'wire_api', 'tools', 'request_characters', 'http_status', 'elapsed_seconds', 'attempts')}
        safe['active_context_characters'] = getattr(self.session.model, 'max_context_chars', None)
        settings = getattr(client, 'settings', None)
        safe['active_wait_seconds'] = getattr(settings, 'timeout', None)
        safe['wait_policy'] = '不限时' if settings is not None and settings.timeout is None else '有限等待'
        safe['active_output_budget'] = getattr(settings, 'max_tokens', None)
        QMessageBox.information(self, '请求诊断', '最后一次HTTP请求（预算拦截可能未发起新请求）：\n'
            + json.dumps(safe, ensure_ascii=False, indent=2) + '\n不含密钥、消息正文或响应正文。')

    def refresh_reports(self, selected=None):
        self.reports.clear()
        for r in self.repo.reports(self.user, limit=100):
            title = '我的关注 · 自动检索' if r['question'] == 'latest news' else r['question'][:55]
            item = QListWidgetItem(f"{r['created_at'][:16].replace('T', ' ')}\n{title}")
            item.setData(Qt.ItemDataRole.UserRole, r['id'])
            self.reports.addItem(item)
            if r['id'] == selected:
                self.reports.setCurrentItem(item)
        if self.reports.currentRow() < 0 and self.reports.count():
            self.reports.setCurrentRow(0)

    def select_report(self, item, previous=None):
        if item is None:
            return
        self.report_id = item.data(Qt.ItemDataRole.UserRole)
        saved = self.repo.report(self.user, self.report_id)
        report = saved['report']
        payload = report.get('knowledge_payload', {})
        if payload.get('format') == 'ranked_news_v1':
            from .news_view import show_ranked_report
            show_ranked_report(self, saved)
            return
        self.brief_stack.setCurrentIndex(0)
        raw_evidence = payload.get('evidence', [])
        # Recheck old reports for display, keeping their saved snapshots and ratings
        # intact. Their original collection date is the recency reference.
        records = [EvidenceRecord(**r) for r in raw_evidence]
        audit = report.get('search_audit', {})
        chosen, decisions = select_report_evidence(records, saved['question'], self.repo.profile(self.user),
            audit=audit, limit=8,
            now=datetime.fromisoformat(saved['created_at'])) if records else ((), ())
        evidence = [r for r in raw_evidence if r['id'] in {r.id for r in chosen}]
        hidden = len(raw_evidence) - len(evidence)
        if self.edition_category.currentData():
            evidence = [r for r in evidence if category(r) == self.edition_category.currentData()]
        if self.edition_order.currentIndex() == 1:
            evidence.sort(key=lambda r: r.get('published_at') or '', reverse=True)
        if payload and 'claims' in payload:
            visible_ids = {r['id'] for r in evidence}
            display = {**payload, 'evidence': evidence,
                'source_conditions': [c for c in payload.get('source_conditions', []) if c['evidence_id'] in visible_ids],
                'claims': [c for c in payload['claims'] if all(ref['evidence_id'] in visible_ids for ref in c['evidence_refs'])]}
            self.summary.setMarkdown(render(display, style=self.reading_style.currentData()))
        else:
            self.summary.setMarkdown(report.get('markdown', '没有可展示的正文'))
        plan = plan_query(saved['question'])
        failures = audit.get('failures', [])
        self.result_status.setText(f'有效结果 {len(evidence)} 条  ·  {saved["created_at"][:10]}  ·  '
            + ('AI 解读' if payload.get('claims') else '来源速览'))
        details = [f'主题检索词：{audit.get("query") or plan.query}',
            '时间范围：' + ({'today': '今天（香港时间）', '24h': '过去24小时'}.get(audit.get('time_scope'))
                or f'{audit.get("recent_days") or plan.recent_days or "不限"} 天'),
            f'已隐藏 {hidden} 条无关或过期资料；{len(failures)} 个来源请求失败。']
        details.extend(str(w) for w in audit.get('warnings', []))
        details.extend(str(f) for f in failures)
        diagnostic = report.get('metrics', {}).get('model_diagnostic')
        if diagnostic:
            details.append('上次AI整理请求：' + json.dumps(diagnostic, ensure_ascii=False))
        validation_attempts = payload.get('validation_attempts', [])
        if validation_attempts:
            if not validation_attempts[-1].get('passed'):
                self.result_status.setText(self.result_status.text() + ' · AI校验未通过，草稿已保留')
            details.append(f'AI校验：共{len(validation_attempts)}轮；' +
                ('已通过格式与引用检查（不代表事实核验）' if validation_attempts[-1].get('passed') else '仍未通过，草稿已保留'))
            for entry in validation_attempts:
                if not entry.get('passed'):
                    details.append(f"第{entry['attempt']}轮：{entry.get('error', '校验失败')} · {entry.get('location', '')}")
        if 'synthesis_unavailable' in report.get('issues', []):
            details.append('上次AI综合未完成；仍可阅读来源摘录，可点击“AI重新整理”。')
        self.search_details.setPlainText('\n'.join(details))
        self.search_details_toggle.setChecked(False)
        self.search_details.hide()
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setSpacing(16)
        scores = self.ratings.ratings(self.user, self.report_id)
        if not evidence:
            layout.addWidget(label('这份报告没有可评分的来源。请查看简报中的覆盖限制与失败提示。'))
        for record in evidence:
            card, body = box()
            body.addWidget(label(f"{record.get('publisher', record.get('source_id', '来源'))}  ·  {(record.get('published_at') or '发布日期未知')[:10]}", 'eyebrow'))
            body.addWidget(label(next(c['label'] for c in policy()['categories'] if c['id'] == category(record)), 'subtitle'))
            claim = next((c['statement'] for c in payload.get('claims', [])
                if any(ref['evidence_id'] == record['id'] for ref in c['evidence_refs'])), None)
            body.addWidget(label(claim or record['title'], 'cardTitle'))
            if claim:
                body.addWidget(label('原文标题：' + record['title'], 'subtitle'))
            body.addWidget(label('摘要摘录  /  ' + excerpt(record)))
            selection = next((r.payload.get('selection', {}) for r in chosen if r.id == record['id']), {})
            matched = selection.get('query_matches', [])
            if matched:
                body.addWidget(label('匹配主题：' + '、'.join(matched), 'subtitle'))
            full = browser()
            full.setPlainText(record.get('snippet', ''))
            full.setMaximumHeight(190)
            full.hide()
            body.addWidget(full)
            actions = QHBoxLayout()
            actions.addWidget(button('查看原文 ↗', lambda checked=False, u=record['url']: open_link(QUrl(u))))
            actions.addWidget(button('展开原始片段', lambda checked=False, w=full: w.setVisible(not w.isVisible())))
            actions.addStretch()
            actions.addWidget(label('对我有用程度'))
            score = QComboBox()
            score.addItem('未评分', None)
            score.model().item(0).setEnabled(False)
            for n, name in enumerate(['无关', '用处较少', '一般', '有用', '非常重要'], 1):
                score.addItem(f'{n} ★  {name}', n)
            score.setCurrentIndex(scores.get(record['id'], 0))
            score.currentIndexChanged.connect(lambda index, w=score, eid=record['id'], rid=self.report_id:
                self.local(lambda: self.ratings.rate(self.user, rid, eid, w.currentData())) if w.currentData() else None)
            actions.addWidget(score)
            body.addLayout(actions)
            layout.addWidget(card)
        layout.addStretch()
        self.article_scroll.setWidget(container)

    def build_profile(self):
        layout = self.page('让信息，与你有关。', '描述专业细分方向与兴趣关键词；明确的偏好比泛泛的“科技”更有帮助。')
        card, body = box()
        body.addWidget(label('我的领域', 'cardTitle'))
        row = QGridLayout()
        self.domain_checks = {}
        for index, (key, name) in enumerate(DOMAINS.items()):
            check = QCheckBox(name)
            self.domain_checks[key] = check
            row.addWidget(check, index // 3, index % 3)
        body.addLayout(row)
        body.addWidget(label('关注主题'))
        self.interests = QPlainTextEdit()
        self.interests.setMaximumHeight(110)
        self.interests.setPlaceholderText('例如：\nembodied AI\n机器人强化学习\n半导体产业政策')
        body.addWidget(self.interests)
        from .interest_editor import edit_interest_units
        body.addWidget(button('细分兴趣与阶段目标', lambda: edit_interest_units(self)))
        from .semantic_profile import show_semantic_profile
        body.addWidget(button('兴趣分类与语义画像', lambda: show_semantic_profile(self)))
        body.addWidget(label('排除关键词'))
        self.interests.setToolTip('每行一个兴趣或专业方向，越具体越有帮助。')
        self.excluded = QPlainTextEdit()
        self.excluded.setToolTip('每行一项；命中后会过滤，请谨慎使用。')
        self.excluded.setMaximumHeight(100)
        body.addWidget(self.excluded)
        row = QHBoxLayout()
        row.addWidget(label('我希望阅读'))
        self.style = QComboBox()
        for name, key in [('简短要点', 'brief'), ('标准解读', 'standard'), ('专业详解', 'expert')]:
            self.style.addItem(name, key)
        row.addWidget(self.style)
        row.addStretch()
        row.addWidget(button('保存', self.save_profile, True))
        body.addLayout(row)
        root_body = body
        from .account_access import build_account_security
        build_account_security(self, root_body)
        from .exploration_settings import build_exploration
        build_exploration(self, root_body)
        self.profile_preferences_toggle, body = disclosure(root_body, '阅读与表达偏好')
        body.addWidget(label('阅读用途'))
        self.purpose = QLineEdit()
        self.purpose.setMaxLength(300)
        self.purpose.setPlaceholderText('例如：学习Agent架构；判断哪些工具值得实际尝试')
        body.addWidget(self.purpose)
        choices = QHBoxLayout()
        self.knowledge_level = QComboBox()
        for title, value in [('入门 · 解释术语', 'beginner'), ('熟悉基础', 'intermediate'), ('专业 · 保留细节', 'expert')]:
            self.knowledge_level.addItem(title, value)
        self.output_format = QComboBox()
        for title, value in [('重点卡片', 'cards'), ('对比表格', 'table'), ('系统提纲', 'outline')]:
            self.output_format.addItem(title, value)
        choices.addWidget(self.knowledge_level)
        choices.addWidget(self.output_format)
        body.addLayout(choices)
        body.addWidget(label('表达规则 · 每行一项'))
        self.expression_rules = QPlainTextEdit()
        self.expression_rules.setMaximumHeight(85)
        self.expression_rules.setPlaceholderText('先讲具体变化，再讲对我的意义\n涉及机制时按输入、处理、输出讲解\n少用宣传式语言')
        body.addWidget(self.expression_rules)
        body.addWidget(label('分领域阅读偏好 · 每行一项'))
        self.domain_reading_rules = QPlainTextEdit()
        self.domain_reading_rules.setMaximumHeight(85)
        self.domain_reading_rules.setPlaceholderText('凝聚态：专业，优先群论与投影表示构造\nAI：入门，先说具体变化并解释必要术语')
        body.addWidget(self.domain_reading_rules)
        body.addWidget(label('我的术语约定 · 每行“术语 = 含义/希望如何解释”'))
        self.glossary = QPlainTextEdit()
        self.glossary.setMaximumHeight(85)
        body.addWidget(self.glossary)
        interview = button('聊聊我的兴趣', self.start_profile_interview)
        interview.setObjectName('quiet')
        root_body.addWidget(interview, 0, Qt.AlignmentFlag.AlignLeft)
        self.profile_proposals_toggle, body = disclosure(root_body, '待确认建议 · 0')
        self.proposal_list = QListWidget()
        self.proposal_list.setMaximumHeight(140)
        self.proposal_list.currentItemChanged.connect(self.show_proposal)
        body.addWidget(self.proposal_list)
        self.proposal_details = browser()
        self.proposal_details.setMaximumHeight(180)
        body.addWidget(self.proposal_details)
        review = QHBoxLayout()
        review.addWidget(button('确认选中建议', lambda: self.resolve_profile_proposal(True), True))
        review.addWidget(button('拒绝选中建议', lambda: self.resolve_profile_proposal(False)))
        body.addLayout(review)
        layout.addWidget(card_scroll(card), 1)

    def start_profile_interview(self):
        self.navigate(1)
        self.dialogue_mode.setCurrentIndex(0)
        self.chat_input.setPlainText('请帮我确认我的兴趣大类、细分兴趣和阅读偏好。先问我最多3个关键问题，弄清大类边界、具体问题、阅读用途、偏好方法、各领域知识水平和阶段目标。将细分主题整理为interest_units，同一大类填写相同category，保留其他原字段；分领域阅读约定写入domain_reading_rules。作为待确认档案建议，不直接把猜测写入长期档案。')

    def show_proposal(self, item, previous=None):
        if not item:
            self.proposal_details.setPlainText('暂时没有待确认建议。可以点击上方按钮开始兴趣访谈。')
            return
        proposal = next(p for p in self.pending_proposals if p['id'] == item.data(Qt.ItemDataRole.UserRole))
        names = {'interests': '关注主题', 'excluded': '排除词', 'domains': '领域', 'style': '详略', 'language': '语言',
            'purpose': '阅读用途', 'knowledge_level': '知识水平', 'output_format': '输出结构',
            'expression_rules': '表达规则', 'glossary': '术语约定', 'domain_reading_rules': '分领域阅读偏好',
            'interest_units': '细分兴趣与阶段目标'}
        choices = {'domains': DOMAINS, 'knowledge_level': {'beginner': '入门', 'intermediate': '熟悉基础', 'expert': '专业'},
            'output_format': {'cards': '重点卡片', 'table': '对比表格', 'outline': '系统提纲'},
            'style': {'brief': '简短要点', 'standard': '标准解读', 'expert': '专业详解'}, 'language': {'zh': '中文', 'en': '英文'}}
        def value(key, v):
            if key == 'interest_units':
                return '\n'.join(f"{u.get('category') or '未分类'} / {u['topic']}｜{u['question']}｜{u['purpose']}｜方法：{'、'.join(u['methods'])}｜"
                                 f"{u['knowledge_level']}｜优先级{u['priority']}｜{u['expires_on'] or '长期'}" for u in v)
            mapping = choices.get(key, {})
            return '、'.join(mapping.get(s, s) for s in v) if isinstance(v, list) else mapping.get(v, str(v))
        current = self.repo.profile(self.user)
        lines = ['根据你的原话：' + proposal['user_quote'], '建议理由：' + proposal['reason'], '待确认的变化：']
        lines.extend(names[k] + '：' + value(k, current.get(k, '')) + ' → ' + value(k, v) for k, v in proposal['changes'].items())
        if current['version'] != proposal['base_version']:
            lines.append('档案已更新，这份旧建议不能直接应用；请拒绝后重新生成。')
        self.proposal_details.setPlainText('\n\n'.join(lines))

    def resolve_profile_proposal(self, accept):
        item = self.proposal_list.currentItem()
        if item:
            self.local(lambda: self.repo.resolve_proposal(self.user, item.data(Qt.ItemDataRole.UserRole), accept=accept))
            self.refresh_profile()
            self.refresh_events()

    def refresh_profile(self):
        profile = self.repo.profile(self.user)
        self.interests.setPlainText('\n'.join(profile['interests']))
        self.excluded.setPlainText('\n'.join(profile['excluded']))
        for key, check in self.domain_checks.items():
            check.setChecked(key in profile['domains'])
        self.style.setCurrentIndex(self.style.findData(profile['style']))
        self.purpose.setText(profile['purpose'])
        self.knowledge_level.setCurrentIndex(self.knowledge_level.findData(profile['knowledge_level']))
        self.output_format.setCurrentIndex(self.output_format.findData(profile['output_format']))
        self.expression_rules.setPlainText('\n'.join(profile['expression_rules']))
        self.domain_reading_rules.setPlainText('\n'.join(profile['domain_reading_rules']))
        self.glossary.setPlainText('\n'.join(profile['glossary']))
        self.pending_proposals = self.repo.proposals(self.user)
        self.profile_proposals_toggle.setProperty('sectionTitle', f'待确认建议 · {len(self.pending_proposals)}')
        self.profile_proposals_toggle.setText(('− ' if self.profile_proposals_toggle.isChecked() else '+ ')
            + self.profile_proposals_toggle.property('sectionTitle'))
        self.proposal_list.clear()
        for p in self.pending_proposals:
            item = QListWidgetItem('待确认 · ' + p['reason'][:100])
            item.setData(Qt.ItemDataRole.UserRole, p['id'])
            self.proposal_list.addItem(item)
        if self.proposal_list.count():
            self.proposal_list.setCurrentRow(0)
        else:
            self.show_proposal(None)

    def save_profile(self):
        changes = {'interests': [v.strip() for v in self.interests.toPlainText().splitlines() if v.strip()],
                   'excluded': [v.strip() for v in self.excluded.toPlainText().splitlines() if v.strip()],
                   'domains': [k for k, v in self.domain_checks.items() if v.isChecked()],
                   'style': self.style.currentData(), 'purpose': self.purpose.text(),
                   'knowledge_level': self.knowledge_level.currentData(), 'output_format': self.output_format.currentData(),
                   'expression_rules': [s.strip() for s in self.expression_rules.toPlainText().splitlines() if s.strip()],
                   'domain_reading_rules': [s.strip() for s in self.domain_reading_rules.toPlainText().splitlines() if s.strip()],
                   'glossary': [s.strip() for s in self.glossary.toPlainText().splitlines() if s.strip()]}
        self.local(lambda: self.repo.update_profile(self.user, changes, '桌面兴趣档案更新'))
        self.refresh_events()

    def build_feedback(self):
        layout = self.page('你的一句反馈，让下一次更准确。', '漏掉了什么、哪些内容无关、哪里难理解，都可以在这里告诉我。')
        card, body = box()
        self.feedback_kind = QComboBox()
        for key, name in KINDS.items():
            self.feedback_kind.addItem(name, key)
        body.addWidget(self.feedback_kind)
        self.feedback_text = QPlainTextEdit()
        self.feedback_text.setPlaceholderText('例如：我想看机器人商业落地，而不是只有算法论文。\n请补充具体事件、标题或公开链接，方便后续补查。')
        self.feedback_text.setMaximumHeight(140)
        body.addWidget(self.feedback_text)
        body.addWidget(button('记录反馈', self.save_feedback, True))
        discuss = button('讨论这条反馈', self.discuss_feedback)
        discuss.setObjectName('quiet')
        discuss.setToolTip('填入对话草稿，由你确认发送；不会自动修改兴趣。')
        body.addWidget(discuss)
        self.feedback_text.setToolTip('反馈不会直接修改兴趣；需要讨论和补查时，可在研究对话中继续。')
        layout.addWidget(card)
        self.events = QListWidget()
        layout.addWidget(self.events, 1)
        layout.addWidget(button('撤销选中的反馈 / 偏好修改', self.undo_event))

    def save_feedback(self):
        detail = self.feedback_text.toPlainText().strip()
        result = self.local(lambda: self.repo.feedback(self.user, self.feedback_kind.currentData(), detail))
        if result:
            self.feedback_text.clear()
        self.refresh_events()

    def refresh_events(self):
        self.events.clear()
        for e in self.repo.events(self.user):
            item = QListWidgetItem(f"{'已记录' if e['active'] else '已撤销'} · {KINDS.get(e['kind'], '偏好调整')}\n{e['detail'][:160]}")
            item.setData(Qt.ItemDataRole.UserRole, e['id'])
            item.setData(Qt.ItemDataRole.UserRole + 1, e['detail'])
            self.events.addItem(item)

    def discuss_feedback(self):
        detail = self.feedback_text.toPlainText().strip()
        item = self.events.currentItem()
        if not detail and item:
            detail = item.data(Qt.ItemDataRole.UserRole + 1) or ''
        if not detail:
            QMessageBox.information(self, '选择反馈', '请填写反馈，或选中一条已有记录。')
            return
        self.navigate(1)
        self.dialogue_mode.setCurrentIndex(0)
        self.chat_input.setPlainText('请分析这条反馈属于兴趣理解、筛选规则还是来源缺口；需要修改档案时生成待确认建议：\n' + detail)

    def undo_event(self):
        item = self.events.currentItem()
        if item:
            self.local(lambda: self.repo.undo(self.user, item.data(Qt.ItemDataRole.UserRole)))
            self.refresh_events()
            self.refresh_profile()

    def build_chat(self):
        layout = self.page('研究对话', '提出问题、追问遗漏、修正兴趣。执行进度会显示在下方，等待时可以继续阅读或准备下一条消息。')
        self.chat_view = ChatView(open_link)
        self.chat_view.setPlainText('先去「连接设置」启用模型，然后在这里提问、追问或讨论兴趣。')
        layout.addWidget(self.chat_view, 1)
        self.chat_status = label('就绪 · 可以开始新对话', 'subtitle')
        layout.addWidget(self.chat_status)
        self.chat_input = QPlainTextEdit()
        self.chat_input.setPlaceholderText('输入你的问题或反馈…')
        self.chat_input.setMaximumHeight(110)
        layout.addWidget(self.chat_input)
        row = QHBoxLayout()
        self.dialogue_mode = QComboBox()
        for title, mode in [('自动', 'auto'), ('仅对话', 'plain'), ('整理兴趣建议', 'profile')]:
            self.dialogue_mode.addItem(title, mode)
        self.dialogue_mode.setToolTip('仅对话不搜索或写档案；整理兴趣建议需输入完整描述，只生成候选，不自动生效。')
        row.addWidget(self.dialogue_mode)
        details = button('请求详情', self.show_request_details)
        details.setObjectName('quiet')
        row.addWidget(details)
        row.addWidget(button('新对话', self.reset_chat))
        self.retry_button = button('重试上一条', self.retry_chat)
        self.retry_button.setEnabled(False)
        self.retry_button.hide()
        row.addWidget(self.retry_button)
        self.stop_button = button('停止当前任务', self.stop_job)
        self.stop_button.setEnabled(False)
        self.stop_button.hide()  # One global task-stop entry, visible only while busy.
        row.addWidget(self.stop_button)
        row.addStretch()
        row.addWidget(button('发送  →', self.send_chat, True))
        layout.addLayout(row)

    def reset_chat(self):
        if self.busy:
            return
        self.session.reset()
        self.ratings.save_dialogue(self.user, [])
        self.chat_view.clear()
        self.last_error_body = None
        self.retry_button.setEnabled(False)
        self.retry_button.hide()
        self.chat_status.setText('新对话已开始')

    def send_chat(self, *, retry=False):
        if self.busy:
            return
        text = self.chat_input.toPlainText().strip()
        if self.session.model is None:
            QMessageBox.information(self, '尚未连接模型', '请在连接设置启用模型；今日简报不需要模型也能搜索。')
            return
        if not text:
            return
        if retry:
            self.last_chat_mode = self.dialogue_mode.currentData()
            if self.last_error_body is not None:
                self.chat_view.remove_message(self.last_error_body)
                self.last_error_body = None
        else:
            self.chat_view.add_message('user', text)
            self.last_chat_mode = self.dialogue_mode.currentData()
        self.chat_input.clear()
        self.last_chat_text = text
        self.retry_button.setEnabled(False)
        self.retry_button.hide()
        self.chat_pending = True
        mode = self.last_chat_mode
        def action():
            if mode == 'profile':
                return self.session.propose_profile(text)
            if mode == 'plain':
                return self.session.chat(text, allow_tools=False)
            return self.session.chat(text)
        self.run_job(action, self.chat_done, self.chat_failed)

    def chat_done(self, result):
        self.request_diagnostics = dict(getattr(getattr(self.session.model, 'client', None), 'last_diagnostic', {}))
        self.ratings.save_dialogue(self.user, [{'role': m.role, 'content': m.content} for m in self.session.history])
        self.chat_view.add_message('assistant', result['markdown'], markdown=True)
        if any(a.get('type') == 'profile_proposal' for a in result.get('actions', [])):
            self.chat_view.add_message('system', 'AI提出了待确认档案建议，尚未生效。请到“兴趣与领域”确认，或在只有一项建议时说“确认档案建议”。')
        self.chat_status.setText('回答完成 · 可继续追问，或在简报中查看来源')
        if 'synthesis_unavailable' in result.get('issues', []) or 'model_unavailable' in result.get('issues', []):
            self.chat_status.setText('模型综合未完成 · 当前显示来源摘录，可稍后重试')
        self.refresh_reports(result.get('report_id'))
        self.refresh_profile()
        self.refresh_events()
        self.refresh_sources()

    def chat_failed(self, error):
        self.request_diagnostics = dict(getattr(getattr(self.session.model, 'client', None), 'last_diagnostic', {}))
        self.last_error_body = self.chat_view.add_message('error', error + '\n可重试；当前草稿保留。')
        self.chat_status.setText('本轮未完成 · 可以重试')
        self.retry_button.setEnabled(True)
        self.retry_button.show()

    def retry_chat(self):
        if not self.busy and getattr(self, 'last_chat_text', ''):
            draft = self.chat_input.toPlainText()
            self.chat_input.setPlainText(self.last_chat_text)
            self.send_chat(retry=True)
            self.chat_input.setPlainText(draft)

    def build_sources(self):
        # Domain filter includes universal sources; it does not grant visibility to private feeds.
        layout = self.page('好的答案，从好的来源开始。', '内置专业来源 + 个人私密来源 + 经审核的共享来源。不会自动执行下载的代码。')
        self.source_filter = QComboBox()
        self.source_filter.addItem('全部来源', None)
        for key, name in DOMAINS.items():
            self.source_filter.addItem(name + ' + 通用来源', key)
        self.source_filter.currentIndexChanged.connect(lambda: self.refresh_sources())
        layout.addWidget(self.source_filter)
        self.source_list = QListWidget()
        layout.addWidget(self.source_list, 1)
        self.auto_collect = QCheckBox('应用运行时每30分钟采集订阅 · 不调用模型')
        self.auto_collect.setToolTip('默认关闭；开启时立即采集，之后每30分钟采集当前账号可见来源。忙碌时跳过，不排队；关闭应用后停止。')
        self.subscription_timer = QTimer(self)
        self.subscription_timer.setInterval(30 * 60 * 1000)
        self.subscription_timer.timeout.connect(self.collect_feed_pool)
        self.auto_collect.toggled.connect(self.toggle_feed_collection)
        layout.addWidget(self.auto_collect)
        card, body = box()
        self.source_url = QLineEdit()
        self.source_url.setPlaceholderText('公开网站或 RSS 链接 · https://…（不支持令牌、内网或登录网站）')
        body.addWidget(self.source_url)
        self.source_domain = QComboBox()
        for key, name in DOMAINS.items():
            self.source_domain.addItem(name, key)
        body.addWidget(self.source_domain)
        body.addWidget(button('发现来源', self.discover, True))
        self.source_management_toggle, management = disclosure(body, '管理选中来源')
        row = QHBoxLayout()
        for text, action in [('验证', 'validate'), ('启用', 'enabled'), ('停用', 'suspended'), ('贡献共享', 'share')]:
            row.addWidget(button(text, lambda checked=False, a=action: self.source_action(a)))
        if self.repo.is_admin(self.user):
            row.addWidget(button('发布共享', lambda: self.source_action('publish')))
        management.addLayout(row)
        layout.addWidget(card)

    def toggle_feed_collection(self, enabled):
        if enabled:
            self.subscription_timer.start()
            self.collect_feed_pool()
        else:
            self.subscription_timer.stop()

    def collect_feed_pool(self):
        if self.busy or not self.auto_collect.isChecked():
            return
        def action():
            # Independent retrieval context: prefetch must not overwrite chat evidence.
            sources = ResearchSources(self.repo, self.user, ROOT / 'config/sources/live.yaml')
            sources.control = self.control
            sources.collect_news('latest news')
            return sources.health
        def done(health):
            self.session.sources.health.update(health)
            self.refresh_sources()
        self.run_job(action, done)

    def refresh_sources(self):
        self.source_list.clear()
        wanted = self.source_filter.currentData()
        for s in self.session.sources.base.sources:
            if wanted and wanted not in s.domains and 'general' not in s.domains:
                continue
            if s.id.startswith('source_'):
                continue
            health = self.session.sources.health.get(s.id, {})
            status = {'ok': '本次运行检索成功', 'failed': '本次运行检索失败'}.get(health.get('status'), '已接入 · 尚未检测')
            if s.status != 'enabled':
                status = '暂停 · 待复核'
            elif s.adapter == 'tavily' and not os.getenv('TAVILY_API_KEY'):
                status = '待配置搜索 Key'
            info = (' · ' + health.get('checked_at', '')[:16].replace('T', ' ')
                    + (' · ' + health['error'] if health.get('error') else '')) if health else ''
            method = '订阅采集' if s.adapter == 'rss' else '主动检索'
            participation = ' · 可跨领域参与' if 'general' in s.domains else ''
            self.source_list.addItem(f'{status}    {s.name}\n{method}{participation} · 领域：{", ".join(DOMAINS.get(d, d) for d in s.domains)}{info}')
        for s in self.repo.list_sources(self.user, include_pending=True):
            if wanted and s['domain'] not in (wanted, 'general'):
                continue
            status = {'enabled': '已启用', 'suspended': '已停用', 'discovered': '待验证',
                'validated': '已验证', 'reviewed': '已审核', 'rejected': '已拒绝', 'archived': '已归档'}.get(s['status'], s['status'])
            item = QListWidgetItem(f"{'个人' if s['scope'] == 'private' else '共享'} · {status}    {s['name']}\n{s['url']}")
            item.setData(Qt.ItemDataRole.UserRole, s['id'])
            self.source_list.addItem(item)

    def discover(self):
        url, domain = self.source_url.text().strip(), self.source_domain.currentData()
        if not url:
            QMessageBox.information(self, '填写网站', '请输入希望发现来源的公开网站地址。')
            return
        def action():
            self.session.sources.begin_turn()
            return self.session.sources.discover('latest news', domain, website=url)
        self.run_job(action, lambda result: self.discovery_done(result))

    def discovery_done(self, result):
        self.refresh_sources()
        QMessageBox.information(self, '发现完成', f"发现 {len(result.get('candidates', []))} 个候选。请选中后验证，再启用。\n没有候选时，请检查网址是否提供 RSS/Atom。")

    def source_action(self, action):
        item = self.source_list.currentItem()
        sid = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not sid:
            QMessageBox.information(self, '请选择来源', '请选择个人或共享候选；内置来源由目录配置维护。')
            return
        if action in ('share', 'publish') and QMessageBox.question(self, '确认共享',
                '将公开此来源的名称、网址和领域（不含私人兴趣与报告），是否继续？') != QMessageBox.StandardButton.Yes:
            return
        def task():
            if action == 'validate':
                return self.session.sources.validate(sid)
            if action == 'share':
                return self.repo.share_source(self.user, sid)
            if action == 'publish':
                return self.repo.publish_source(self.user, sid)
            return self.repo.set_source_status(self.user, sid, action)
        self.run_job(task, lambda result: self.refresh_sources())

    def build_settings(self):
        layout = self.page('连接你的模型，保留你的选择。', '建立自己的连接档案，记住地址、模型与密钥；无需编辑配置文件。')
        card, body = box()
        self.provider = QComboBox()
        config = yaml.safe_load((ROOT / 'model_config.yaml').read_text(encoding='utf-8'))
        for name in config.get('providers', {}):
            self.provider.addItem(name)
        connection_picker = QHBoxLayout()
        connection_picker.addWidget(self.provider, 1)
        self.new_connection_button = button('+ 新建 API 连接', self.begin_connection)
        connection_picker.addWidget(self.new_connection_button)
        body.addLayout(connection_picker)
        self.connection_fields(body)
        self.provider_info = label('', 'subtitle')
        self.provider_info.hide()
        body.addWidget(self.provider_info)
        key_row = QHBoxLayout()
        self.model_key = QLineEdit()
        self.model_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.model_key.setMaxLength(4096)
        self.model_key.setPlaceholderText('粘贴所选服务的 API key；已配置环境变量时可留空')
        key_row.addWidget(self.model_key, 1)
        self.show_key = QCheckBox('显示密钥')
        self.show_key.toggled.connect(lambda shown: self.model_key.setEchoMode(
            QLineEdit.EchoMode.Normal if shown else QLineEdit.EchoMode.Password))
        key_row.addWidget(self.show_key)
        body.addLayout(key_row)
        self.model_key.setToolTip('密钥按账号加密保存，不回显旧密钥。测试会发一次小请求，可能计费。')
        controls = QHBoxLayout()
        self.save_connection_button = button('保存', self.save_current_connection, True)
        controls.addWidget(self.save_connection_button)
        controls.addWidget(button('启用', self.enable_model))
        test = button('测试连接', self.test_model_connection)
        test.setObjectName('quiet')
        test.setToolTip('向当前服务发送一次小请求，最多64输出token，可能计费。')
        controls.addWidget(test)
        controls.addStretch()
        body.addLayout(controls)
        self.connection_state = label('尚未测试连接', 'subtitle')
        body.addWidget(self.connection_state)
        self.connection_management_toggle, management = disclosure(body, '连接管理')
        management.addLayout(self.connection_save_controls)
        management.addWidget(button('关闭模型', self.disable_model))
        self.request_options_toggle, body = disclosure(body, '高级设置')
        self.fast_mode = QCheckBox('快速研究模式 · 收到资料后直接整理，减少一次模型请求')
        self.fast_mode.setChecked(True)
        body.addWidget(self.fast_mode)
        limits = QHBoxLayout()
        wait_policy = label('模型等待不限时', 'subtitle')
        wait_policy.setToolTip('不设置客户端等待时限；服务商仍可能主动超时或断开。停止任务会在网络返回后收尾。502/503/504最多重试一次，可能增加费用。')
        body.addWidget(wait_policy)
        limits.addWidget(label('单次输出额度'))
        self.output_budget = QComboBox()
        for n in (1800, 4096, 8192, 16384):
            self.output_budget.addItem(str(n), n)
        limits.addWidget(self.output_budget)
        body.addLayout(limits)
        context_row = QHBoxLayout()
        context_row.addWidget(label('上下文上限'))
        self.context_budget = QComboBox()
        for value in CONTEXT_BUDGETS:
            self.context_budget.addItem(f'{value // 10000:g} 万字符', value)
        self.context_budget.setCurrentIndex(self.context_budget.findData(self.ratings.context_budget(self.user)))
        self.context_budget.setToolTip('每次模型调用的消息、工具说明及工具结果的序列化字符预算，不是 token 上限。选择后重新启用生效并记住；更高额度可能增加费用。')
        context_row.addWidget(self.context_budget, 1)
        body.addLayout(context_row)
        body.addWidget(label('字符 ≠ token。提高上限后重新启用；仍受模型和中转站容量限制。', 'subtitle'))
        body.addWidget(label('推理模型可选90秒；输出额度含推理token，提高可能增加费用。修改后重新启用。', 'subtitle'))
        self.test_tools = QCheckBox('连接测试包含工具往返 · 最多2次请求，可能计费')
        self.test_tools.setToolTip('只发送固定测试文本和虚构echo工具，不发送兴趣档案、历史或报告。')
        body.addWidget(self.test_tools)
        body.addWidget(label('通用搜索', 'cardTitle'))
        body.addWidget(label('Tavily 使用 TAVILY_API_KEY 环境变量。未配置时仍可检索已接入来源。', 'subtitle'))
        layout.addWidget(card_scroll(card), 1)
        self.provider.currentIndexChanged.connect(self.provider_changed)
        self.reload_connections()

    def provider_changed(self):
        self.connection_changed()

    def apply_key(self):
        settings = load_provider_settings(ROOT / 'model_config.yaml', self.provider.currentText())
        key = self.model_key.text().strip()
        if key:
            if any(c.isspace() or ord(c) < 32 for c in key):
                raise ValueError('密钥中不能包含换行或空白字符，请重新粘贴完整密钥。')
            if settings.api_key_env not in self.original_key_env:
                self.original_key_env[settings.api_key_env] = os.environ.get(settings.api_key_env)
            os.environ[settings.api_key_env] = key
            self.model_key.clear()
            self.show_key.setChecked(False)
        if not os.getenv(settings.api_key_env):
            raise ValueError('请先填入所选服务的 API key。')
        return self.provider.currentText()

    def enable_model(self):
        def create():
            model = self.connection_client()
            provider = self.provider.currentText()
            history = self.session.history.copy()
            context_chars = self.context_budget.currentData()
            self.ratings.set_context_budget(self.user, context_chars)
            self.session = ResearchSession(self.repo, self.user, self.session.sources, model=model,
                max_context_chars=context_chars)
            self.session.history = history
            self.model_badge.setText(f'已启用 · {self.connection_model.text()}')
            if len(self.chat_view.messages) == 1 and self.chat_view.messages[0]['text'].startswith('先去'):
                self.chat_view.clear()
            self.chat_view.add_message('system', f'{provider} 已启用，可以开始对话。')
            self.connection_state.setText('已启用 · 可以开始对话')
        self.local(create)

    def test_model_connection(self):
        tool_probe = self.test_tools.isChecked()
        client = self.local(lambda: self.connection_client(max_output_tokens=self.output_budget.currentData() if tool_probe else 64,
                                                         gateway_recovery=False))
        if client is None:
            return
        provider = self.provider.currentText()
        self.connection_state.setText('正在测试连接…')
        def action():
            from newspaper_agent.research.connection_probe import probe_connection
            if hasattr(client, 'control'):
                client.control = self.control
            probe_connection(client, tools=tool_probe)
            return provider
        def done(name):
            self.request_diagnostics = dict(getattr(client, 'last_diagnostic', {}))
            self.connection_state.setText(f'{name} ' + ('工具回环连接成功' if tool_probe else '基础连接成功 · 尚未验证工具往返'))
        def failed(error):
            self.request_diagnostics = dict(getattr(client, 'last_diagnostic', {}))
            self.connection_state.setText(error)
        self.run_job(action, done, failed)

    def disable_model(self):
        history = self.session.history.copy()
        self.session = ResearchSession(self.repo, self.user, self.session.sources)
        self.session.history = history
        self.model_badge.setText('模型未启用')
        self.chat_view.add_message('system', '模型已关闭，仍可在简报页检索公开来源。')
        self.statusBar().showMessage('模型已关闭 · 公开来源检索仍可用')

    def closeEvent(self, event):
        if self.busy:
            QMessageBox.information(self, '任务仍在进行', '请等待当前请求完成后关闭，避免中断正在保存的数据。')
            event.ignore()
        else:
            self.model_key.clear()
            for name, previous in self.original_key_env.items():
                if previous is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = previous
            self.original_key_env.clear()
            event.accept()


def main():
    app = QApplication(sys.argv)
    # Explicit loading also supports Qt's offscreen font database on Windows.
    font = Path('C:/Windows/Fonts/msyh.ttc')
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    repo = ResearchRepository(ROOT / 'data/research.sqlite3')
    login = Login(repo)
    restored_user = login.remembered.restore()
    if not restored_user and login.exec() != QDialog.DialogCode.Accepted:
        return
    window = Window(repo, restored_user or login.user)
    window.show()
    app.exec()
