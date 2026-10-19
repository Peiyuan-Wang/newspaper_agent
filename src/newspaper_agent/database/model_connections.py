"""Account-scoped connection metadata and Windows-user encrypted credentials."""
import ctypes
from ctypes import wintypes
import os
from urllib.parse import urlsplit
from uuid import uuid4


class WindowsSecretCodec:
    def _crypt(self, data, entropy, decrypt=False):
        if os.name != 'nt':
            raise ValueError('持久保存密钥需要 Windows DPAPI；不会退回明文保存。')
        class Blob(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
        raw, salt = ctypes.create_string_buffer(data), ctypes.create_string_buffer(entropy)
        incoming = Blob(len(data), ctypes.cast(raw, ctypes.POINTER(ctypes.c_ubyte)))
        extra = Blob(len(entropy), ctypes.cast(salt, ctypes.POINTER(ctypes.c_ubyte)))
        outgoing = Blob()
        library = ctypes.WinDLL('crypt32', use_last_error=True)
        function = library.CryptUnprotectData if decrypt else library.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        function.restype = wintypes.BOOL
        if not function(ctypes.byref(incoming), None, ctypes.byref(extra), None, None, 1, ctypes.byref(outgoing)):
            raise ValueError('Windows 无法解密或保存此密钥；请在原 Windows 账号使用，或重新输入密钥。')
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        try:
            return ctypes.string_at(outgoing.data, outgoing.size)
        finally:
            kernel.LocalFree(outgoing.data)

    def encrypt(self, key, scope):
        return self._crypt(key.encode('utf-8'), scope.encode('utf-8'))

    def decrypt(self, data, scope):
        return self._crypt(data, scope.encode('utf-8'), True).decode('utf-8')


def validate_connection(values):
    result = {}
    for field, maximum in [('name', 80), ('base_url', 1000), ('model', 200), ('homepage', 1000), ('wire_api', 20)]:
        value = values.get(field, '')
        if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
            raise ValueError('连接配置格式无效')
        result[field] = value.strip()
    if not result['name'] or not result['model']:
        raise ValueError('请填写连接名称和模型 ID。')
    if result['wire_api'] not in ('chat', 'responses'):
        raise ValueError('请选择 Chat Completions 或 Responses 协议。')
    for field in ('base_url', 'homepage'):
        value = result[field]
        if field == 'homepage' and not value:
            continue
        try:
            url = urlsplit(value)
            _ = url.port
            valid = (bool(url.hostname) and not url.username and not url.password
                     and not url.query and not url.fragment
                     and not any(c.isspace() or c in '\\<>\"\'' for c in value)
                     and (url.scheme == 'https' or (url.scheme == 'http' and url.hostname in ('localhost', '127.0.0.1', '::1'))))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('请填写无凭证、无查询参数的 HTTPS 网址；HTTP 仅支持本机地址。')
    base = result['base_url'].rstrip('/')
    for suffix, protocol in [('/chat/completions', 'chat'), ('/responses', 'responses')]:
        if base.endswith(suffix):
            if result['wire_api'] != protocol:
                raise ValueError('接口网址与所选协议不一致。')
            base = base[:-len(suffix)]
    result['base_url'] = base
    return result


class ModelConnectionRepository:
    def __init__(self, repository, codec=None):
        self.repo, self.codec = repository, codec or WindowsSecretCodec()
        with repository._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS model_connections (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES research_users(id),
                name TEXT NOT NULL, base_url TEXT NOT NULL, model TEXT NOT NULL,
                homepage TEXT NOT NULL, wire_api TEXT NOT NULL, secret BLOB NOT NULL,
                selected INTEGER NOT NULL DEFAULT 0)''')

    def list(self, user):
        with self.repo._connect() as db:
            self.repo._require_user(db, user)
            return [dict(r) for r in db.execute('''SELECT id,name,base_url,model,homepage,wire_api,selected
                FROM model_connections WHERE user_id=? ORDER BY selected DESC,name,id''', (user,))]

    def get(self, user, identity):
        for record in self.list(user):
            if record['id'] == identity:
                return record
        raise ValueError('此连接不属于当前账号或已被删除。')

    def save(self, user, values, key='', identity=None):
        values = validate_connection(values)
        if not isinstance(key, str) or len(key) > 4096 or any(c.isspace() or ord(c) < 32 for c in key):
            raise ValueError('密钥中不能包含空白或换行，且不能超过4096字符。')
        with self.repo._connect(write=True) as db:
            self.repo._require_user(db, user)
            old = db.execute('SELECT * FROM model_connections WHERE id=? AND user_id=?', (identity, user)).fetchone() if identity else None
            if identity and not old:
                raise ValueError('此连接不属于当前账号或已被删除。')
            if not key and (not old or old['base_url'] != values['base_url']):
                raise ValueError('新建连接或更改 API 地址时必须重新输入密钥，防止旧密钥发往其他服务。')
            identity = identity or uuid4().hex
            secret = self.codec.encrypt(key, f'newspaper:{user}:{identity}') if key else old['secret']
            db.execute('''INSERT INTO model_connections VALUES (?,?,?,?,?,?,?,?,0)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name,base_url=excluded.base_url,
                model=excluded.model,homepage=excluded.homepage,wire_api=excluded.wire_api,secret=excluded.secret''',
                (identity, user, values['name'], values['base_url'], values['model'], values['homepage'], values['wire_api'], secret))
            db.execute('UPDATE model_connections SET selected=(id=?) WHERE user_id=?', (identity, user))
        return identity

    def key(self, user, identity):
        with self.repo._connect() as db:
            self.repo._require_user(db, user)
            row = db.execute('SELECT secret FROM model_connections WHERE user_id=? AND id=?', (user, identity)).fetchone()
            if not row:
                raise ValueError('此连接不属于当前账号或已被删除。')
            return self.codec.decrypt(row['secret'], f'newspaper:{user}:{identity}')

    def delete(self, user, identity):
        self.get(user, identity)
        with self.repo._connect(write=True) as db:
            db.execute('DELETE FROM model_connections WHERE user_id=? AND id=?', (user, identity))
