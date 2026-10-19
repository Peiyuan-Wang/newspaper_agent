"""Visual saved connections; no network traffic until an explicit test or task."""
import os
from PySide6.QtWidgets import QLineEdit, QComboBox, QHBoxLayout, QMessageBox
from newspaper_agent.database.model_connections import validate_connection
from newspaper_agent.model.client import OpenAICompatibleClient, OpenAICompatibleSettings
from newspaper_agent.model.settings import load_provider_settings

NEW_CONNECTION = '__new_connection__'


class ModelSettingsMixin:
    def connection_fields(self, body):
        from .app import label, button, disclosure
        self.connection_name = QLineEdit()
        self.connection_name.setPlaceholderText('例如：我的中转站 · 研究模型')
        self.connection_url = QLineEdit()
        self.connection_url.setPlaceholderText('https://服务商地址/v1')
        self.connection_model = QLineEdit()
        self.connection_model.setPlaceholderText('填写服务商提供的准确模型 ID')
        self.connection_homepage = QLineEdit()
        self.connection_homepage.setPlaceholderText('可选：服务商控制台或官网链接')
        self.connection_protocol = QComboBox()
        self.connection_protocol.addItem('Chat Completions · /chat/completions', 'chat')
        self.connection_protocol.addItem('Responses · /responses', 'responses')
        for title, widget in [('名称', self.connection_name), ('API 地址', self.connection_url),
                              ('模型', self.connection_model)]:
            row = QHBoxLayout()
            title_widget = label(title)
            title_widget.setMinimumWidth(100)
            row.addWidget(title_widget)
            row.addWidget(widget, 1)
            body.addLayout(row)
        self.connection_options_toggle, options = disclosure(body, '协议与服务商')
        for title, widget in [('协议', self.connection_protocol), ('服务商链接', self.connection_homepage)]:
            row = QHBoxLayout()
            row.addWidget(label(title))
            row.addWidget(widget, 1)
            options.addLayout(row)
        link = button('打开服务商网站', self.open_connection_homepage)
        link.setObjectName('quiet')
        options.addWidget(link)
        controls = QHBoxLayout()
        controls.addWidget(button('保存为新连接', lambda: self.save_connection(False)))
        self.update_connection_button = button('更新当前连接', lambda: self.save_connection(True))
        controls.addWidget(self.update_connection_button)
        self.delete_connection_button = button('删除当前连接', self.delete_connection)
        controls.addWidget(self.delete_connection_button)
        self.connection_save_controls = controls

    def save_current_connection(self):
        identity = self.provider.currentData()
        self.save_connection(bool(identity and identity != NEW_CONNECTION))

    def open_connection_homepage(self):
        from PySide6.QtCore import QUrl
        from .app import open_link
        def open_page():
            values = self.connection_values()
            if not values['homepage']:
                raise ValueError('请先填写服务商链接。')
            open_link(QUrl(values['homepage']))
        self.local(open_page)

    def connection_values(self):
        return validate_connection(dict(name=self.connection_name.text(), base_url=self.connection_url.text(),
            model=self.connection_model.text(), homepage=self.connection_homepage.text(),
            wire_api=self.connection_protocol.currentData()))

    def reload_connections(self, selected=None):
        self.provider.blockSignals(True)
        for index in range(self.provider.count() - 1, -1, -1):
            if self.provider.itemData(index):
                self.provider.removeItem(index)
        for index, record in enumerate(self.connections.list(self.user)):
            self.provider.insertItem(index, '已保存 · ' + record['name'], record['id'])
        self.provider.addItem('新建 API 连接 · 空白表单', NEW_CONNECTION)
        if selected:
            self.provider.setCurrentIndex(self.provider.findData(selected))
        elif self.connections.list(self.user):
            self.provider.setCurrentIndex(0)
        self.provider.blockSignals(False)
        self.provider_changed()

    def begin_connection(self):
        """Start a draft without reusing any preset or existing credential."""
        index = self.provider.findData(NEW_CONNECTION)
        if self.provider.currentIndex() == index:
            self.connection_changed()
        else:
            self.provider.setCurrentIndex(index)
        self.connection_name.setFocus()

    def connection_changed(self):
        self.model_key.clear()
        self.show_key.setChecked(False)
        identity = self.provider.currentData()
        saved = bool(identity and identity != NEW_CONNECTION)
        self.update_connection_button.setEnabled(saved)
        self.delete_connection_button.setEnabled(saved)
        self.connection_state.setText('密钥已保存' if saved else '填写连接信息后保存')
        if identity == NEW_CONNECTION:
            values = dict(name='', base_url='', model='', homepage='', wire_api='chat')
            self.model_key.setPlaceholderText('输入新服务的 API key；不会沿用任何已有连接的密钥')
        elif identity:
            values = self.connections.get(self.user, identity)
            self.model_key.setPlaceholderText('密钥已保存；留空不修改，输入新密钥后点“保存”')
        else:
            settings = load_provider_settings(self.config_path, self.provider.currentText())
            values = dict(name=settings.name, base_url=settings.base_url, model=settings.model, wire_api=settings.wire_api, homepage='')
            self.model_key.setPlaceholderText('输入密钥后可保存；原有环境变量也支持本次使用')
        for field, widget in [('name', self.connection_name), ('base_url', self.connection_url),
                              ('model', self.connection_model), ('homepage', self.connection_homepage)]:
            widget.setText(values[field])
        self.connection_protocol.setCurrentIndex(self.connection_protocol.findData(values['wire_api']))
        self.provider_info.setText('已保存 · 密钥已加密' if saved else '新建连接' if identity == NEW_CONNECTION else '预设连接')

    def save_connection(self, updating):
        def save():
            identity = self.provider.currentData() if updating else None
            if updating and (not identity or identity == NEW_CONNECTION):
                raise ValueError('请选择已保存的连接。')
            identity = self.connections.save(self.user, self.connection_values(), self.model_key.text().strip(), identity)
            self.reload_connections(identity)
            self.connection_state.setText('已保存 · 可启用')
        self.local(save)

    def delete_connection(self):
        identity = self.provider.currentData()
        if not identity or identity == NEW_CONNECTION:
            return
        if QMessageBox.question(self, '删除连接', '删除当前账号的这份连接和加密密钥？不能撤销；不会撤销服务商的密钥。') != QMessageBox.StandardButton.Yes:
            return
        self.connections.delete(self.user, identity)
        self.disable_model()
        self.reload_connections()
        self.connection_state.setText('连接与本机加密密钥已删除，无法撤销；服务商密钥仍有效。')

    def connection_client(self, *, max_output_tokens=None, gateway_recovery=True):
        values = self.connection_values()
        identity = self.provider.currentData()
        if identity == NEW_CONNECTION:
            raise ValueError('请先保存连接，再启用或测试。')
        if not identity:
            settings = load_provider_settings(self.config_path, self.provider.currentText())
            if (values['base_url'], values['model'], values['wire_api']) == (settings.base_url.rstrip('/'), settings.model, settings.wire_api):
                from . import app
                provider = self.apply_key()
                return app.create_research_model(provider, config_path=self.config_path,
                    request_timeout=None,
                    gateway_recovery=gateway_recovery,
                    max_output_tokens=max_output_tokens or self.output_budget.currentData())
            raise ValueError('请先保存修改后的连接，再启用或测试。')
        saved = self.connections.get(self.user, identity)
        if any(values[field] != saved[field] for field in values) or self.model_key.text():
            raise ValueError('配置或密钥已修改，请先保存，再启用或测试。')
        # Existing client captures this temporary environment value during construction.
        # No persistent environment variable or plaintext credential file is written.
        env = 'NEWSPAPER_CONNECTION_' + identity.upper()
        previous = os.environ.get(env)
        try:
            os.environ[env] = self.connections.key(self.user, identity)
            return OpenAICompatibleClient(OpenAICompatibleSettings(
                provider='saved_connection', base_url=saved['base_url'], model=saved['model'],
                api_key_env=env, wire_api=saved['wire_api'], temperature=None,
                max_tokens=max_output_tokens or self.output_budget.currentData(),
                timeout=None, max_attempts=2 if gateway_recovery else 1,
                retry_statuses=(502, 503, 504), retry_transport_errors=False,
                retry_transport_codes=('remote_protocol', 'read_error') if gateway_recovery else (),
                max_retry_delay=2))
        finally:
            if previous is None:
                os.environ.pop(env, None)
            else:
                os.environ[env] = previous
