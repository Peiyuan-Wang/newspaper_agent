"""Local recovery codes and revocable remembered-device tokens; no mail or cloud."""
import hashlib
import secrets
import time
from .research_repository import _ITERATIONS
from newspaper_agent.sources.contracts import identifier


def digest(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


class AccountSecurity:
    def __init__(self, repository):
        self.repo = repository
        with repository._connect(write=True) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS account_recovery (
                user_id TEXT PRIMARY KEY REFERENCES research_users(id), code_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS remembered_devices (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES research_users(id), expires_at REAL NOT NULL);''')

    def _verify(self, db, user, password):
        try:
            identifier(user)
        except ValueError:
            raise ValueError('账号或密码不正确') from None
        if not isinstance(password, str) or len(password) > 1024:
            raise ValueError('账号或密码不正确')
        row = db.execute('SELECT salt,password_hash,iterations FROM research_users WHERE id=?', (user,)).fetchone()
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), row['salt'] if row else bytes(16),
                                     row['iterations'] if row else _ITERATIONS)
        if not secrets.compare_digest(actual, row['password_hash'] if row else bytes(32)) or row is None:
            raise ValueError('账号或密码不正确')

    def issue_device(self, user, password):
        token = secrets.token_urlsafe(32)
        with self.repo._connect(write=True) as db:
            self._verify(db, user, password)  # Verification and issue serialize with password reset.
            db.execute('DELETE FROM remembered_devices WHERE expires_at<=?', (time.time(),))
            db.execute('INSERT INTO remembered_devices VALUES (?,?,?)', (digest(token), user, time.time() + 30 * 86400))
        return token

    def restore(self, token):
        if not isinstance(token, str) or not 20 <= len(token) <= 100:
            return None
        with self.repo._connect() as db:
            row = db.execute('SELECT user_id FROM remembered_devices WHERE token_hash=? AND expires_at>?',
                             (digest(token), time.time())).fetchone()
        return row['user_id'] if row else None

    def revoke(self, token):
        with self.repo._connect(write=True) as db:
            db.execute('DELETE FROM remembered_devices WHERE token_hash=?', (digest(token),))

    def recovery_code(self, user, password):
        code = secrets.token_hex(24).upper()
        with self.repo._connect(write=True) as db:
            self._verify(db, user, password)
            db.execute('INSERT OR REPLACE INTO account_recovery VALUES (?,?)', (user, digest(code)))
        return '-'.join(code[i:i+8] for i in range(0, len(code), 8))

    def reset_password(self, user, code, password):
        if not isinstance(password, str) or not 8 <= len(password) <= 1024 or '\x00' in password:
            raise ValueError('密码需要8到1024个字符')
        normalized = code.replace('-', '').replace(' ', '').upper() if isinstance(code, str) else ''
        supplied = digest(normalized) if len(normalized) <= 100 else digest('invalid')
        salt = secrets.token_bytes(16)
        password_hash = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, _ITERATIONS)
        with self.repo._connect(write=True) as db:
            row = db.execute('SELECT code_hash FROM account_recovery WHERE user_id=?', (user,)).fetchone()
            if not secrets.compare_digest(supplied, row['code_hash'] if row else '0' * 64):
                raise ValueError('账号或恢复码不正确；请使用事先保存的恢复码')
            db.execute('UPDATE research_users SET salt=?,password_hash=?,iterations=? WHERE id=?',
                       (salt, password_hash, _ITERATIONS, user))
            db.execute('DELETE FROM account_recovery WHERE user_id=?', (user,))
            db.execute('DELETE FROM remembered_devices WHERE user_id=?', (user,))
