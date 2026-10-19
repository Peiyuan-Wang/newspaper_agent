"""Windows-user protected local device login and explicit recovery dialogs."""
import json
import base64
import os
from pathlib import Path
from hashlib import sha256
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLineEdit, QPushButton, QLabel, QMessageBox, QInputDialog
from newspaper_agent.database.model_connections import WindowsSecretCodec
from newspaper_agent.database.account_security import AccountSecurity


class RememberedLogin:
    def __init__(self, repository, codec=None):
        self.security = AccountSecurity(repository)
        self.codec = codec or WindowsSecretCodec()
        self.scope = 'newspaper-login:' + str(repository.path.resolve()).casefold()
        self.path = repository.path.parent / ('desktop-login-' + sha256(self.scope.encode()).hexdigest()[:12] + '.json')

    def read(self):
        try:
            if self.path.stat().st_size > 16000:
                return {}
            data = json.loads(self.path.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, RecursionError):
            return {}

    def restore(self):
        data = self.read()
        try:
            token = self.codec.decrypt(base64.b64decode(data['token'], validate=True), self.scope)
            user = self.security.restore(token)
            return user if user == data.get('user') else None
        except (ValueError, KeyError, TypeError):
            return None  # Corrupt, expired, wrong Windows user: return to ordinary login.

    def forget(self):
        data = self.read()
        try:
            token = self.codec.decrypt(base64.b64decode(data['token'], validate=True), self.scope)
            self.security.revoke(token)
        except (ValueError, KeyError, TypeError):
            pass
        self.path.unlink(missing_ok=True)

    def save(self, user, password, keep):
        self.forget()
        data = {'user': user}
        token = None
        try:
            if keep:
                token = self.security.issue_device(user, password)
                data['token'] = base64.b64encode(self.codec.encrypt(token, self.scope)).decode('ascii')
            temporary = self.path.with_suffix('.tmp')
            try:
                temporary.write_text(json.dumps(data), encoding='utf-8')
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
        except (ValueError, OSError):
            if token:
                self.security.revoke(token)
            raise ValueError('未能保存本机登录；可以取消“保持登录”后重试，不会保存明文密码') from None


def recover_account(parent, repository, initial=''):
    dialog = QDialog(parent)
    dialog.setWindowTitle('恢复本机账号')
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel('用事先保存的一次性恢复码重置密码；本机恢复不使用邮箱。'))
    fields = []
    for title in ('账号', '恢复码', '新密码', '再次输入新密码'):
        field = QLineEdit()
        field.setPlaceholderText(title)
        field.setMaxLength(1024 if len(fields) >= 2 else 100)
        if fields:
            field.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(field)
        fields.append(field)
    fields[0].setText(initial)
    def submit():
        try:
            if fields[2].text() != fields[3].text():
                raise ValueError('两次输入的新密码不一致')
            AccountSecurity(repository).reset_password(fields[0].text().strip(), fields[1].text(), fields[2].text())
            QMessageBox.information(dialog, '密码已重置', '请用新密码登录。旧恢复码和保持登录已失效；登录后请生成新的恢复码。')
            dialog.accept()
        except ValueError as exc:
            QMessageBox.warning(dialog, '未能恢复', str(exc))
    action = QPushButton('重置密码')
    action.clicked.connect(submit)
    layout.addWidget(action)
    dialog.exec()


def generate_recovery(window):
    password, ok = QInputDialog.getText(window, '验证身份', '请输入当前账号密码；生成新恢复码会使旧码失效。', QLineEdit.EchoMode.Password)
    if not ok:
        return
    try:
        code = AccountSecurity(window.repo).recovery_code(window.user, password)
        dialog = QDialog(window)
        dialog.setWindowTitle('请离线保存恢复码')
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('仅此次显示。恢复码等同于重置密码权限，请勿发送给他人或模型。\n使用一次即失效；不会在数据库保存明文。'))
        value = QLineEdit(code)
        value.setReadOnly(True)
        value.setMinimumWidth(520)
        layout.addWidget(value)
        close = QPushButton('我已妥善保存')
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()
    except ValueError as exc:
        QMessageBox.warning(window, '无法生成恢复码', str(exc))


def build_account_security(window, root):
    from .app import disclosure, label, button
    _, body = disclosure(root, '账号与安全')
    body.addWidget(label('恢复码仅用于本机密码找回，不同步或备份新闻数据。邮箱服务尚未接入。'))
    body.addWidget(button('生成／更换恢复码', lambda: generate_recovery(window)))
    def forget():
        window.local(lambda: RememberedLogin(window.repo).forget())
    body.addWidget(button('取消记住登录', forget))
