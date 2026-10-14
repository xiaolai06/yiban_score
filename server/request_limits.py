"""Bound actual incoming bytes, including requests without Content-Length."""
from __future__ import annotations

from starlette.responses import JSONResponse


class BodyLimitExceeded(Exception):
    pass


class RequestBodyLimit:
    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') not in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get('headers', []))
        length = headers.get(b'content-length')
        if length is not None:
            if not length.isdigit():
                await JSONResponse({'detail': '请求长度无效。'}, status_code=400)(scope, receive, send)
                return
            digits = length.lstrip(b'0') or b'0'
            maximum = str(self.max_bytes).encode('ascii')
            if len(digits) > len(maximum) or len(digits) == len(maximum) and digits > maximum:
                await JSONResponse({'detail': '上传内容超过允许大小。'}, status_code=413)(scope, receive, send)
                return
        count = 0
        exceeded = False
        replacement_sent = False

        async def bounded_receive():
            nonlocal count, exceeded
            message = await receive()
            if message['type'] == 'http.request':
                count += len(message.get('body', b''))
                if count > self.max_bytes:
                    exceeded = True
                    raise BodyLimitExceeded
            return message

        async def bounded_send(message):
            nonlocal replacement_sent
            # A body parser may translate the limit exception to HTTP 400.
            # Return the accurate 413 and do not leak a partial parser response.
            if exceeded:
                if not replacement_sent:
                    replacement_sent = True
                    await JSONResponse({'detail': '上传内容超过允许大小。'}, status_code=413)(scope, receive, send)
                return
            await send(message)

        try:
            await self.app(scope, bounded_receive, bounded_send)
        except BodyLimitExceeded:
            if not replacement_sent:
                await JSONResponse({'detail': '上传内容超过允许大小。'}, status_code=413)(scope, receive, send)
