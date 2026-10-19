"""Compact visual editor for confirmed, account-private interest units."""
from copy import deepcopy
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QListWidget, QLineEdit, QComboBox, QSpinBox, QPushButton, QMessageBox, QWidget)

from newspaper_agent.preferences import validate_interest_units, DOMAINS, LEVELS


class InterestEditor(QDialog):
    def __init__(self, repository, user, parent=None):
        super().__init__(parent)
        self.repository, self.user = repository, user
        self.units = deepcopy(repository.profile(user).get('interest_units', []))
        self.setWindowTitle('细分兴趣与阶段目标')
        self.resize(900, 540)
        layout = QVBoxLayout(self)
        columns = QHBoxLayout()
        self.entries = QListWidget()
        self.entries.setMaximumWidth(230)
        columns.addWidget(self.entries)
        editor = QWidget()
        form = QFormLayout(editor)
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.addItem('')
        self.category.addItems(sorted({u.get('category') for u in self.units if u.get('category')}))
        self.category.lineEdit().setMaxLength(60)
        self.category.lineEdit().setPlaceholderText('例如：凝聚态理论、AI算法与求职')
        self.category.setToolTip('大类只负责分组。同类内相近细分主题可共享演化向量；留空保持独立。')
        form.addRow('兴趣大类', self.category)
        self.fields = {key: QLineEdit() for key in ('topic', 'question', 'purpose', 'methods', 'expires_on')}
        for key, title in (('topic', '关注主题'), ('question', '具体想解决的问题'), ('purpose', '用途'),
                           ('methods', '偏好的方法 · 分号分隔'), ('expires_on', '截止日期 · 留空为长期')):
            self.fields[key].setMaxLength(360 if key == 'methods' else 120 if key == 'topic' else 180)
            form.addRow(title, self.fields[key])
        self.fields['expires_on'].setPlaceholderText('YYYY-MM-DD')
        self.domain, self.level, self.priority = QComboBox(), QComboBox(), QSpinBox()
        for key, title in zip(DOMAINS, ('学术', '医学', '金融', '新闻', '通用')):
            self.domain.addItem(title, key)
        for key, title in zip(LEVELS, ('入门', '熟悉基础', '专业')):
            self.level.addItem(title, key)
        self.priority.setRange(1, 5)
        self.priority.setValue(3)
        form.addRow('领域', self.domain)
        form.addRow('该领域的知识水平', self.level)
        form.addRow('关注优先级 · 1低／5高', self.priority)
        columns.addWidget(editor, 1)
        layout.addLayout(columns, 1)
        row = QHBoxLayout()
        for title, callback in (('导入关注主题', self.import_topics), ('新建', self.new), ('加入／更新列表', self.apply),
                                ('移除', self.remove), ('取消', self.reject), ('保存全部', self.save)):
            b = QPushButton(title)
            b.clicked.connect(callback)
            row.addWidget(b)
        layout.addLayout(row)
        self.entries.currentRowChanged.connect(self.load)
        self.refresh()

    def refresh(self):
        self.entries.blockSignals(True)
        self.entries.clear()
        self.entries.addItems([(u.get('category') or '未分类') + ' / ' + u['topic'] +
                              (' · 至' + u['expires_on'] if u['expires_on'] else '') for u in self.units])
        self.entries.blockSignals(False)

    def new(self):
        self.entries.setCurrentRow(-1)
        for field in self.fields.values():
            field.clear()
        self.priority.setValue(3)
        self.category.setCurrentText('')

    def import_topics(self):
        profile = self.repository.profile(self.user)
        present = {u['topic'].casefold() for u in self.units}
        for topic in profile['interests']:
            if len(self.units) >= 12:
                break
            if topic.casefold() not in present:
                self.units.append({'topic': topic, 'category': '', 'domain': profile['domains'][0] if len(profile['domains']) == 1 else 'general',
                    'question': '', 'purpose': '', 'methods': [], 'knowledge_level': profile['knowledge_level'],
                    'priority': 3, 'expires_on': None})
                present.add(topic.casefold())
        self.refresh()
        if self.units:
            self.entries.setCurrentRow(0)

    def load(self, index):
        if 0 <= index < len(self.units):
            unit = self.units[index]
            self.category.setCurrentText(unit.get('category', ''))
            for key, field in self.fields.items():
                field.setText('; '.join(unit[key]) if key == 'methods' else unit[key] or '')
            self.domain.setCurrentIndex(self.domain.findData(unit['domain']))
            self.level.setCurrentIndex(self.level.findData(unit['knowledge_level']))
            self.priority.setValue(unit['priority'])

    def apply(self):
        try:
            unit = {k: f.text().strip() for k, f in self.fields.items()}
            unit.update(category=self.category.currentText().strip(),
                        methods=[m.strip() for m in unit['methods'].replace('；', ';').split(';') if m.strip()],
                        expires_on=unit['expires_on'] or None, domain=self.domain.currentData(),
                        knowledge_level=self.level.currentData(), priority=self.priority.value())
            rows = deepcopy(self.units)
            index = self.entries.currentRow()
            if index < 0:
                rows.append(unit)
                index = len(rows) - 1
            else:
                rows[index] = unit
            self.units = validate_interest_units(rows)
            self.refresh()
            self.entries.setCurrentRow(index)
            return True
        except ValueError as exc:
            QMessageBox.warning(self, '请检查兴趣设置', str(exc))
            return False

    def remove(self):
        index = self.entries.currentRow()
        if index >= 0:
            del self.units[index]
            self.refresh()
            self.new()

    def save(self):
        # Include the visible form so typing and pressing Save never silently loses it.
        if self.fields['topic'].text().strip() and not self.apply():
            return
        self.repository.update_profile(self.user, {'interest_units': self.units}, '用户在细分兴趣编辑器中明确保存')
        self.accept()


def edit_interest_units(window):
    if InterestEditor(window.repo, window.user, window).exec() == QDialog.DialogCode.Accepted:
        window.refresh_profile()
        window.refresh_events()
