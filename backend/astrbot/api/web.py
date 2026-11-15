"""AstrBot api.web 兼容模块。

提供插件 Web API handler 使用的：
- `request` 代理：读取当前请求的 body/json/form/files/query
- `json_response` / `error_response` / `file_response` / `stream_response`
- `PluginRequest` / `PluginUploadFile` / `PluginMultiDict`
- `bind_request_context`：由路由层把当前 FastAPI Request 绑定进上下文变量
"""
from __future__ import annotations

import contextvars
import json as _json
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse
from starlette.responses import StreamingResponse


class PluginMultiDict:
    """保留重复键的查询/表单读取器（对齐 AstrBot）。"""

    def __init__(self, pairs) -> None:
        self._pairs = pairs  # list[(key, value)]

    def get(self, key: str, default: Any = None, type=None):
        for item_key, item_value in reversed(self._pairs):
            if item_key != key:
                continue
            if type is None:
                return item_value
            try:
                return type(item_value)
            except (TypeError, ValueError):
                return default
        return default

    def getlist(self, key: str):
        return [v for k, v in self._pairs if k == key]

    def keys(self):
        return list(dict.fromkeys(k for k, _ in self._pairs))

    def values(self):
        return [self[k] for k in self.keys()]

    def items(self):
        return [(k, self[k]) for k in self.keys()]

    def __contains__(self, key: str) -> bool:
        return any(k == key for k, _ in self._pairs)

    def __getitem__(self, key: str):
        value = self.get(key)
        if value is None and key not in self:
            raise KeyError(key)
        return value

    def __bool__(self) -> bool:
        return bool(self._pairs)


class PluginUploadFile:
    """上传文件包装（对齐 AstrBot）。"""

    def __init__(self, upload_file) -> None:
        self._upload_file = upload_file
        self.filename = upload_file.filename
        self.content_type = upload_file.content_type
        self.headers = upload_file.headers
        self.content_length = None
        try:
            raw = self.headers.get("content-length")
            self.content_length = int(raw) if raw else None
        except (TypeError, ValueError):
            self.content_length = None

    async def save(self, destination) -> None:
        path = Path(destination)
        try:
            await self._upload_file.seek(0)
        except Exception:
            pass
        with path.open("wb") as out:
            while True:
                chunk = await self._upload_file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)

    async def read(self, size: int = -1) -> bytes:
        return await self._upload_file.read(size)

    async def write(self, data: bytes) -> None:
        await self._upload_file.write(data)

    async def seek(self, offset: int) -> None:
        await self._upload_file.seek(offset)

    async def close(self) -> None:
        await self._upload_file.close()

    def __getattr__(self, key: str):
        return getattr(self._upload_file, key)


class PluginRequest:
    """暴露给插件 Web handler 的请求对象。"""

    def __init__(self, request_, *, path_params=None, plugin_name=None, username=None) -> None:
        self._request = request_
        self.method = request_.method
        self.path = request_.url.path
        self.headers = request_.headers
        self.cookies = request_.cookies
        self.content_type = request_.headers.get("content-type")
        self.client_host = request_.client.host if request_.client else None
        self.path_params = path_params or {}
        self.plugin_name = plugin_name
        self.username = username
        try:
            self.query = PluginMultiDict(list(request_.query_params.multi_items()))
        except Exception:
            self.query = PluginMultiDict([])
        self._form_cache = None
        self._files_cache = None

    async def body(self) -> bytes:
        return await self._request.body()

    async def json(self, default: Any = None) -> Any:
        try:
            return await self._request.json()
        except Exception:
            return default

    async def _load_form_parts(self) -> None:
        if self._form_cache is not None and self._files_cache is not None:
            return
        form = await self._request.form()
        form_pairs, file_pairs = [], []
        for key, value in form.multi_items():
            if value.__class__.__name__ == "UploadFile":
                file_pairs.append((key, PluginUploadFile(value)))
            else:
                form_pairs.append((key, value))
        self._form_cache = PluginMultiDict(form_pairs)
        self._files_cache = PluginMultiDict(file_pairs)

    async def form(self) -> PluginMultiDict:
        await self._load_form_parts()
        return self._form_cache

    async def files(self) -> PluginMultiDict:
        await self._load_form_parts()
        return self._files_cache


_request_var: contextvars.ContextVar[PluginRequest] = contextvars.ContextVar(
    "astrbot_plugin_web_request"
)


class PluginRequestProxy:
    """模块级 request 代理，handler 内可用 await request.json() 等。"""

    def _get_current(self) -> PluginRequest:
        try:
            return _request_var.get()
        except LookupError as exc:
            raise RuntimeError(
                "astrbot.api.web.request 只能在插件 Web API handler 内使用。"
            ) from exc

    @property
    def method(self) -> str:
        return self._get_current().method

    @property
    def path(self) -> str:
        return self._get_current().path

    @property
    def headers(self):
        return self._get_current().headers

    @property
    def cookies(self):
        return self._get_current().cookies

    @property
    def content_type(self):
        return self._get_current().content_type

    @property
    def client_host(self):
        return self._get_current().client_host

    @property
    def path_params(self):
        return self._get_current().path_params

    @property
    def plugin_name(self):
        return self._get_current().plugin_name

    @property
    def username(self):
        return self._get_current().username

    @property
    def query(self) -> PluginMultiDict:
        return self._get_current().query

    async def body(self) -> bytes:
        return await self._get_current().body()

    async def json(self, default=None):
        return await self._get_current().json(default=default)

    async def form(self) -> PluginMultiDict:
        return await self._get_current().form()

    async def files(self) -> PluginMultiDict:
        return await self._get_current().files()

    def __getattr__(self, key: str):
        return getattr(self._get_current(), key)


request: PluginRequestProxy = PluginRequestProxy()


@contextmanager
def bind_request_context(request_: PluginRequest):
    token = _request_var.set(request_)
    try:
        yield request_
    finally:
        _request_var.reset(token)


def json_response(data: Any = None, *, status_code: int = 200, headers=None) -> JSONResponse:
    return JSONResponse(
        jsonable_encoder({} if data is None else data),
        status_code=status_code,
        headers=headers,
    )


def error_response(message: str, *, status_code: int = 400, data: Any = None, headers=None) -> JSONResponse:
    return json_response(
        {"status": "error", "message": message, "data": data},
        status_code=status_code,
        headers=headers,
    )


def file_response(path, *, filename=None, content_type=None, headers=None) -> FileResponse:
    return FileResponse(path, filename=filename, media_type=content_type, headers=headers)


def stream_response(content: Any, *, content_type: str = "text/event-stream",
                    status_code: int = 200, headers=None) -> StreamingResponse:
    return StreamingResponse(content, media_type=content_type, status_code=status_code, headers=headers)


__all__ = [
    "PluginMultiDict",
    "PluginRequest",
    "PluginRequestProxy",
    "PluginUploadFile",
    "bind_request_context",
    "error_response",
    "file_response",
    "json_response",
    "request",
    "stream_response",
]