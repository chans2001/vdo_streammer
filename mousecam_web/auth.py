import hashlib
import hmac
import secrets
import threading
import time
from collections import deque

from .service import ServiceError


class Auth:
    """Expiring opaque bearer sessions. No ambient cookies or persistent browser secrets."""
    def __init__(self, config):
        self.code_hash = hashlib.sha256(config.access_code.encode()).digest()
        self.ttl = config.session_seconds
        self.lock = threading.Lock()
        self.sessions = {}
        self.attempts = {}
        self.global_attempts = deque(maxlen=100)

    def login(self, code, ip):
        now = time.monotonic()
        with self.lock:
            self.attempts = {key: times for key, times in self.attempts.items() if times and times[-1] > now - 60}
            self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
            attempts = self.attempts.setdefault(ip, deque(maxlen=10))
            while attempts and attempts[0] <= now - 60:
                attempts.popleft()
            while self.global_attempts and self.global_attempts[0] <= now - 60:
                self.global_attempts.popleft()
            if len(attempts) >= 10 or len(self.global_attempts) >= 100:
                raise ServiceError('Too many login attempts. Wait one minute.', 429)
            attempts.append(now)
            self.global_attempts.append(now)
            if not isinstance(code, str) or not hmac.compare_digest(hashlib.sha256(code.encode()).digest(), self.code_hash):
                raise ServiceError('Incorrect access code.', 401)
            if len(self.sessions) >= 100:
                raise ServiceError('Too many active sessions. Try again later.', 429)
            token = secrets.token_urlsafe(32)
            self.sessions[hashlib.sha256(token.encode()).digest()] = now + self.ttl
            return dict(token=token, expires_in=self.ttl)

    def valid(self, token):
        key = hashlib.sha256(token.encode()).digest()
        with self.lock:
            expiry = self.sessions.get(key, 0)
            if expiry <= time.monotonic():
                self.sessions.pop(key, None)
                return False
            return True

    def logout(self, token):
        with self.lock:
            self.sessions.pop(hashlib.sha256(token.encode()).digest(), None)
