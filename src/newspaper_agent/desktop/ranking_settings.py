"""Low-frequency per-account column weights, no model or authentication changes."""
from PySide6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QSpinBox, QCheckBox, QMessageBox
from newspaper_agent.database.news_feedback import NewsFeedback
from newspaper_agent.news_ranking import SECTIONS


def edit_ranking(window):
    from .app import label, button
    store = NewsFeedback(window.repo)
    prefs = store.preferences(window.user)
    dialog = QDialog(window)
    dialog.setWindowTitle('推荐指标权重')
    layout = QVBoxLayout(dialog)
    layout.addWidget(label('每栏合计100。可信度是来源与资料参考，重要性是材料估计，热度目前只用可观察报道覆盖。'))
    fields = {}
    for key, title in SECTIONS.items():
        layout.addWidget(label(title, 'cardTitle'))
        form = QFormLayout()
        fields[key] = []
        for name, value in zip(('关联性', '可信度', '重要性', '热度'), prefs['weights'][key]):
            spin = QSpinBox()
            spin.setRange(0, 100)
            spin.setSuffix(' %')
            spin.setValue(value)
            fields[key].append(spin)
            form.addRow(name, spin)
        layout.addLayout(form)
    learning = QCheckBox('使用本机赞踩及弱点击校正关联性（不改变长期领域）')
    learning.setChecked(prefs['learning'])
    layout.addWidget(learning)
    def save():
        try:
            store.configure(window.user, {k: [w.value() for w in rows] for k,rows in fields.items()}, learning.isChecked())
            dialog.accept()
        except ValueError as exc:
            QMessageBox.warning(dialog, '请检查权重', str(exc))
    layout.addWidget(button('保存 · 下次生成生效', save, True))
    def clear():
        if QMessageBox.question(dialog, '清除新闻反馈', '删除本账号的赞踩、原文打开及曝光记录？不删除新闻、历史评分或兴趣。') == QMessageBox.StandardButton.Yes:
            store.clear(window.user)
            window.select_report(window.reports.currentItem())
    layout.addWidget(button('清除本机新闻反馈', clear))
    dialog.exec()
