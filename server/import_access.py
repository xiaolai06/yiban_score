"""Password gate for import operations; public read pages remain independent."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import os
import secrets
import time
from collections import OrderedDict, deque
from pathlib import Path
from threading import Lock

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

SESSION_SECONDS = 8 * 60 * 60
ROUNDS = 600_000


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, ROUNDS)
    return f'pbkdf2_sha256${ROUNDS}${salt.hex()}${digest.hex()}'


def hash_parts(value):
    algorithm, rounds, salt, digest = value.split('$')
    if algorithm != 'pbkdf2_sha256' or not ROUNDS <= int(rounds) <= 2_000_000:
        raise ValueError('Invalid password algorithm')
    salt, digest = bytes.fromhex(salt), bytes.fromhex(digest)
    if len(salt) != 16 or len(digest) != 32:
        raise ValueError('Invalid password hash')
    return int(rounds), salt, digest


def protected_import_path(path: str, method: str) -> bool:
    path = path.rstrip('/')
    if path.startswith('/api/students/') and method in {'PATCH', 'PUT', 'DELETE'}:
        return True
    if path == '/api/imports' or path.startswith('/api/imports/'):
        return True
    if path == '/api/removals' or path.startswith('/api/removals/'):
        return True
    if path in {'/api/scores/analyze', '/api/scores/preview', '/api/scores/commit'}:
        return True
    if path.startswith(('/api/scores/uploads/', '/api/scores/previews/')):
        return True
    return method not in {'GET', 'HEAD', 'OPTIONS'} and path.startswith('/api/scores/batches/')


class UnlockInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    password: str = Field(min_length=1, max_length=256, strict=True)


class ImportAccess:
    def __init__(self, config_path: str | None, *, secure: bool):
        self.enabled = bool(config_path)
        self.secure = secure
        self.cookie = '__Host-student-import' if secure else 'student-import'
        self.parts = None
        if self.enabled:
            try:
                config = json.loads(Path(config_path).read_text(encoding='utf-8'))
                self.parts = hash_parts(config['password_hash'])
            except (OSError, ValueError, TypeError, KeyError) as error:
                raise RuntimeError('导入密码配置无法读取；服务已拒绝启动，避免导入入口失去保护。') from error
        self.lock = Lock()
        self.sessions = OrderedDict()
        self.attempts = {}
        self.global_attempts = deque()

    def _prune(self, now):
        for key, expiry in list(self.sessions.items()):
            if expiry <= now:
                del self.sessions[key]
        for address, times in list(self.attempts.items()):
            while times and times[0] <= now - 300:
                times.popleft()
            if not times:
                del self.attempts[address]
        while self.global_attempts and self.global_attempts[0] <= now - 300:
            self.global_attempts.popleft()

    def authorized(self, request: Request) -> bool:
        if not self.enabled:
            return True
        token = request.cookies.get(self.cookie, '')
        if not token or len(token) > 128:
            return False
        key = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            return self.sessions.get(key, 0) > time.time()

    def status(self, request):
        return {'enabled': self.enabled, 'unlocked': self.authorized(request)}

    def unlock(self, request, response, password):
        if not self.enabled:
            return self.status(request)
        address = request.client.host if request.client else 'unknown'
        now = time.time()
        with self.lock:
            self._prune(now)
            attempts = self.attempts.setdefault(address, deque())
            if len(attempts) >= 5 or len(self.global_attempts) >= 60:
                raise HTTPException(429, '尝试次数过多，请 5 分钟后重试。', headers={'Retry-After': '300'})
            attempts.append(now)
            self.global_attempts.append(now)
        rounds, salt, digest = self.parts
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, rounds)
        if not hmac.compare_digest(actual, digest):
            raise HTTPException(401, '操作密码不正确，请重新输入。')
        token = secrets.token_urlsafe(32)
        key = hashlib.sha256(token.encode()).hexdigest()
        with self.lock:
            self.attempts.pop(address, None)
            # Re-authenticating replaces this browser's previous session.
            previous = request.cookies.get(self.cookie, '')
            self.sessions.pop(hashlib.sha256(previous.encode()).hexdigest(), None)
            while len(self.sessions) >= 256:
                self.sessions.popitem(last=False)
            self.sessions[key] = time.time() + SESSION_SECONDS
        response.set_cookie(self.cookie, token, max_age=SESSION_SECONDS, path='/',
                            httponly=True, secure=self.secure, samesite='strict')
        return {'enabled': True, 'unlocked': True}

    def logout(self, request, response):
        token = request.cookies.get(self.cookie, '')
        with self.lock:
            self.sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)
        response.delete_cookie(self.cookie, path='/', secure=self.secure,
                               httponly=True, samesite='strict')
        return {'enabled': self.enabled, 'unlocked': not self.enabled}


def install_access_routes(app, access):
    @app.get('/api/import-access')
    def status(request: Request):
        return access.status(request)

    @app.post('/api/import-access/unlock')
    def unlock(body: UnlockInput, request: Request, response: Response):
        return access.unlock(request, response, body.password)

    @app.post('/api/import-access/lock')
    def logout(request: Request, response: Response):
        return access.logout(request, response)


def main():
    parser = argparse.ArgumentParser(description='设置导入专用密码（不修改学生数据）')
    parser.add_argument('file', type=Path, help='密码配置保存路径')
    args = parser.parse_args()
    password = getpass.getpass('新导入密码（至少12位）: ')
    if len(password) < 12 or len(password) > 256:
        raise SystemExit('密码须为 12–256 位。')
    if password != getpass.getpass('再次输入: '):
        raise SystemExit('两次输入不一致。')
    args.file.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids replacing an existing configuration accidentally.
    fd = os.open(args.file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        json.dump({'password_hash': password_hash(password)}, out)
    print('已创建密码配置。请给服务账户读取权限，配置 STUDENT_IMPORT_AUTH_FILE 后重启服务。')


if __name__ == '__main__':
    main()
