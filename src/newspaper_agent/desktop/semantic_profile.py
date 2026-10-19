"""Visual hierarchy, model preparation and background profile rebuild."""
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem, QCheckBox
from newspaper_agent.database.semantic_repository import SemanticRepository
from newspaper_agent.database.news_feedback import NewsFeedback
from newspaper_agent.semantic_profile import identity, seed_specs
from newspaper_agent.model.embeddings import model_ready, download_model, local_encoder


class SemanticProfileDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        from .app import label, button
        self.window, self.repo, self.user = window, window.repo, window.user
        self.store = SemanticRepository(self.repo)
        self.working = False
        self.setWindowTitle('兴趣分类与语义画像')
        self.resize(860, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(label('大类由你确定，细分兴趣随反馈逐步调整。', 'cardTitle'))
        layout.addWidget(label('同类相近主题可共享向量。赞踩在下次生成简报时生效，也可以立即重建；分类名称不会被学习改写。', 'subtitle'))
        self.enabled = QCheckBox('使用本地语义画像参与关联性排序')
        self.enabled.setChecked(self.store.enabled(self.user))
        self.enabled.toggled.connect(lambda value: self.store.configure(self.user,value))
        layout.addWidget(self.enabled)
        self.status = label('', 'subtitle')
        layout.addWidget(self.status)
        self.tree = QTreeWidget()
        self.tree.setStyleSheet('QTreeWidget {background:white; border:1px solid #e7eae9; border-radius:8px; padding:8px;} QTreeWidget::item {padding:9px 4px;} QHeaderView::section {background:#f0f4f1; border:0; padding:9px; color:#59736b;}')
        self.tree.setUniformRowHeights(True)
        self.tree.setHeaderLabels(['兴趣大类 / 细分兴趣', '关联反馈数', '向量偏移'])
        self.tree.setColumnWidth(0, 470)
        layout.addWidget(self.tree,1)
        row = QHBoxLayout()
        self.edit = button('编辑分类与兴趣', self.edit_categories)
        self.prepare = button('准备本地语义模型', self.prepare_model)
        self.rebuild = button('按当前反馈重建画像', self.rebuild_profile, True)
        for b in (self.edit,self.prepare,self.rebuild):
            row.addWidget(b)
        layout.addLayout(row)
        self.close_button = button('关闭', self.reject)
        layout.addWidget(self.close_button)
        self.refresh()

    def refresh(self):
        ready = model_ready(self.repo.path.parent)
        self.prepare.setEnabled(not ready)
        self.rebuild.setEnabled(ready)
        state = self.store.state(self.user)
        specs = seed_specs(self.repo.profile(self.user))
        feedback = self.store.feedback(self.user) if NewsFeedback(self.repo).preferences(self.user)['learning'] else []
        stale = state and (state['profile_key'] != identity(specs) or state['feedback_key'] != identity(feedback))
        text = '模型已就绪 · 本地计算，不使用聊天API额度。' if ready else '首次准备需下载约220MB公开模型，之后离线计算。'
        self.status.setText(text + (' 画像待重建。' if stale else '') +
            (f" 已有{len(state['slots'])}个兴趣向量；{state['ambiguous_feedback']}条反馈未能可靠归类，暂未学习。"
             if state and not stale else ''))
        self.tree.clear()
        categories = {}
        rows = state['slots'] if state and not stale else [
            {'category': s['category'], 'topics': [s['topic']], 'observations': 0, 'angle': 0} for s in specs]
        for slot in rows:
            name = slot['category']
            if name not in categories:
                categories[name] = QTreeWidgetItem(self.tree,[name,'固定分类',''])
            QTreeWidgetItem(categories[name], [' / '.join(slot['topics']),
                str(slot['observations']) if state and not stale else '待建立',
                f"{slot['angle']:.1f}° / 上限25°" if state and not stale else ''])
        self.tree.expandAll()
        if not specs:
            self.status.setText(text + ' 请先添加细分兴趣并设置大类。')

    def edit_categories(self):
        from .interest_editor import edit_interest_units
        edit_interest_units(self.window)
        self.refresh()

    def launch(self, action):
        if self.window.busy:
            return
        self.working = True
        for widget in (self.edit,self.prepare,self.rebuild,self.enabled):
            widget.setEnabled(False)
        self.close_button.setText('停止当前任务')
        self.status.setText('正在后台处理…')
        def done(result):
            self.finish()
        def failed(error):
            self.finish()
            self.status.setText('未完成，请检查模型文件或重试准备；已有画像与新闻仍保留。')
        self.window.run_job(action, done, failed)

    def finish(self):
        self.working = False
        self.edit.setEnabled(True)
        self.enabled.setEnabled(True)
        self.close_button.setText('关闭')
        self.refresh()

    def prepare_model(self):
        self.launch(lambda: download_model(self.repo.path.parent))

    def rebuild_profile(self):
        def action():
            from newspaper_agent.research.semantic_personalization import SemanticPersonalizer
            encoder = local_encoder(str(self.repo.path.parent))
            engine = SemanticPersonalizer(self.repo,self.user,encoder,control=self.window.control)
            sources = {s.id:s for s in self.window.session.sources.descriptors()}
            return engine.build(self.repo.profile(self.user),[],sources)[0]
        self.launch(action)

    def reject(self):
        if self.working:
            self.window.stop_job()
            self.status.setText('已请求停止，等待当前计算或下载返回。')
            return
        super().reject()


def show_semantic_profile(window):
    window.semantic_dialog = SemanticProfileDialog(window)
    window.semantic_dialog.show()
