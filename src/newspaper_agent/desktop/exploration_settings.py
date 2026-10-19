"""Opt-in local interest exploration, separate from language-model temperature."""
from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QFormLayout, QMessageBox
from newspaper_agent.database.recommendation_repository import RecommendationRepository


def build_exploration(window, root):
    from .app import disclosure, label, button
    _, body = disclosure(root, '兴趣拓展 · 可选')
    body.addWidget(label('仅在已确认兴趣附近探索；只学习你对拓展条目的明确评价，不改变领域和原始兴趣分。'))
    body.addWidget(label('当前邻域为有限概念词表，并非通用语义向量。明确主题检索不启用探索。'))
    values = RecommendationRepository(window.repo).settings(window.user)
    window.exploration_enabled = QCheckBox('开启兴趣拓展与反馈学习（仅本机账号）')
    window.exploration_enabled.setChecked(values['enabled'])
    body.addWidget(window.exploration_enabled)
    form = QFormLayout()
    window.exploration_fields = {}
    for key, title, low, high in (
            ('epsilon', '探索比例 · 最多30%', 0, .3), ('temperature', '推荐温度 · 越高越分散', .02, 2),
            ('radius', '邻域范围 · 0仅同标签／0.5起含相邻概念', 0, 1),
            ('learning_rate', '反馈敏感度 · 越高越快调整', .01, .3)):
        spin = QDoubleSpinBox()
        spin.setDecimals(2)
        spin.setRange(low, high)
        spin.setSingleStep(.05 if key == 'radius' else .01)
        spin.setValue(values[key])
        window.exploration_fields[key] = spin
        form.addRow(title, spin)
    body.addLayout(form)
    body.addWidget(button('保存拓展设置', lambda: window.local(lambda:
        RecommendationRepository(window.repo).configure(window.user, {
            'enabled': window.exploration_enabled.isChecked(),
            **{k: w.value() for k, w in window.exploration_fields.items()}}))))
    def reset():
        if QMessageBox.question(window, '清除拓展学习', '清除本账号所有拓展反馈学习？不删除新闻、档案或人工评分。') == QMessageBox.StandardButton.Yes:
            window.local(lambda: RecommendationRepository(window.repo).reset(window.user))
    body.addWidget(button('清除拓展学习', reset))
