"""
GitHub 加速代理模块（结合 astrbot2github 思路）

原项目 `astrbot2github` 用 Deno Deploy 提供 `/https://github.com/...` 前缀的
GitHub 资源代理，解决 AstrBot 插件市场 (market.json) 与插件 .zip 包下载时的
网络问题。此处用 Python (FastAPI) 实现等价能力，作为软件内置功能：

- GET /api/github-proxy/{url:path}   代理任意 GitHub 资源（转发方法/头/体，流式）
- GET /api/github-proxy/status      探测本代理是否可用（默认返回欢迎文本）

用法示例：
- 浏览器访问 `/api/github-proxy/https://github.com/xxx/yyy/archive/...`
- 配置“GitHub 加速地址”为 `http://<主机>:<端口>/api/github-proxy`，
  则上游 `zip_updator` 等会把完整 GitHub URL 拼在其后下载。

安全：仅允许代理到 github.com / raw.githubusercontent.com / codeload.github.com /
api.github.com / objects.githubusercontent.com / githubusercontent.com 域名，
且重定向的每一跳都做白名单校验，防止被滥用为开放代理。
"""
from __future__ import annotations

import logging
import urllib.parse
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

logger = logging.getLogger("ZhiYanWeb.GithubProxy")

router = APIRouter(prefix="/api/github-proxy", tags=["github-proxy"])

# 允许代理的目标域名后缀（白名单）
ALLOWED_HOST_SUFFIXES = (
    "github.com",
    "raw.githubusercontent.com",
    "codeload.github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "githubusercontent.com",
    "github.io",
    "gist.github.com",
)

# 请求超时（大文件 zip 可能较慢，给足时间流式转发）
_TIMEOUT = httpx.Timeout(120.0, connect=20.0)

# 最大跟随重定向次数
MAX_REDIRECTS = 5

# 不转发的 hop-by-hop 头
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host",
}


def get_proxy_base() -> str:
    """获取当前配置的 GitHub 加速地址（去掉末尾斜杠）；未配置返回空串"""
    from zhiyan_web.config import get_setting
    return (get_setting("github_proxy", "") or "").strip().rstrip("/")


def _is_allowed_host(host: str) -> bool:
    return any(host == s or host.endswith("." + s) for s in ALLOWED_HOST_SUFFIXES)


def _parse_target(raw: str) -> Optional[str]:
    """从路径中解析目标 URL"""
    raw = raw.strip()
    if not raw:
        return None
    # 兼容路径前缀直接带完整 url（astrbot2github 风格）
    if raw.startswith("http://") or raw.startswith("https://"):
        url = raw
    else:
        # 可能是 URL 编码的
        try:
            decoded = urllib.parse.unquote(raw)
        except Exception:
            decoded = raw
        if decoded.startswith("http://") or decoded.startswith("https://"):
            url = decoded
        else:
            return None
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host or not _is_allowed_host(host):
        return None
    return url


def _filter_headers(headers: dict) -> dict:
    """过滤掉 hop-by-hop 与敏感头"""
    out = {}
    for k, v in headers.items():
        kl = k.lower()
        if kl in HOP_BY_HOP or kl.startswith("x-forwarded"):
            continue
        out[k] = v
    return out


async def _proxy_fetch(
    method: str, url: str, headers: dict, body: Optional[bytes], timeout: httpx.Timeout
) -> httpx.Response:
    """发起代理请求，并在代理内部跟随重定向（每一跳都做白名单校验）

    这样 archive/raw 的 302 会继续走代理（保持加速），而不是把 Location 原样
    抛给客户端直连。
    """
    async with httpx.AsyncClient(timeout=timeout, verify=False, follow_redirects=False) as client:
        current = url
        cur_method = method
        cur_headers = dict(headers)
        cur_body = body
        for _ in range(MAX_REDIRECTS + 1):
            host = (urllib.parse.urlparse(current).hostname or "").lower()
            if not host or not _is_allowed_host(host):
                raise HTTPException(status_code=400, detail="代理目标不在白名单内")
            resp = await client.send(
                client.build_request(cur_method, current, headers=cur_headers, content=cur_body),
                stream=True,
            )
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("location")
                await resp.aclose()
                if not loc:
                    return resp
                current = urllib.parse.urljoin(current, loc)
                # 301/302/303 且非 GET/HEAD 时，按标准降级为 GET 并去掉请求体
                if cur_method not in ("GET", "HEAD") and resp.status_code in (301, 302, 303):
                    cur_method = "GET"
                    cur_body = None
                    cur_headers = {
                        k: v for k, v in cur_headers.items()
                        if k.lower() not in ("content-type", "content-length")
                    }
                continue
            return resp
    raise HTTPException(status_code=504, detail="重定向次数过多")


@router.get("/")
async def github_proxy_welcome():
    """根路径：提示信息（与 astrbot2github 一致）"""
    return Response(
        "此地址只用于为 Furina Agent Web 提供更快速的 GitHub 访问服务。\n"
        "用法: /api/github-proxy/<完整 GitHub URL>",
        media_type="text/plain; charset=utf-8",
    )


@router.get("/status")
async def github_proxy_status():
    """探测代理是否可用"""
    return {"status": "ok", "message": "GitHub 加速代理运行中"}


@router.api_route("/{url:path}", methods=["GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"])
async def github_proxy(url: str, request: Request):
    """
    代理任意 GitHub 资源（流式转发）

    - 路径: /api/github-proxy/https://github.com/xxx/yyy/archive/refs/heads/main.zip
    - 或查询参数形式: /api/github-proxy/proxy?url=https://github.com/...
    """
    # 支持 ?url= 查询参数形式
    q = request.query_params.get("url")
    target = _parse_target(q) if q else _parse_target(url)
    if not target:
        raise HTTPException(status_code=400, detail="不支持的代理目标，仅允许 GitHub 相关域名")

    # 处理 OPTIONS 预检
    if request.method == "OPTIONS":
        return Response(
            status_code=204,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, HEAD, OPTIONS, PATCH",
                "Access-Control-Allow-Headers": "Content-Type, Authorization, *",
            },
        )

    headers = _filter_headers(dict(request.headers))
    body = None
    if request.method in ("POST", "PUT", "PATCH"):
        body = await request.body()

    try:
        upstream = await _proxy_fetch(request.method, target, headers, body, _TIMEOUT)
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="代理请求超时")
    except httpx.RequestError as e:
        logger.warning(f"GitHub 代理请求失败 {target}: {e}")
        raise HTTPException(status_code=502, detail=f"代理请求失败: {str(e)}")

    # 转发响应（流式，避免大文件整体缓冲在内存）
    resp_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in HOP_BY_HOP and k.lower() not in {"content-encoding", "content-length"}
    }
    resp_headers["Access-Control-Allow-Origin"] = "*"

    async def _iter_body():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        except httpx.ReadError:
            # 上游连接中途被重置（如 GitHub 边缘节点限流），给客户端明确错误而非静默断连
            raise HTTPException(status_code=502, detail="上游连接中断")
        finally:
            await upstream.aclose()

    return StreamingResponse(
        _iter_body(),
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )


async def github_fetch(url: str, timeout: float = 120.0) -> Optional[httpx.Response]:
    """
    供软件内部模块调用：获取 GitHub 资源。

    配置了 github_proxy 时走该加速地址，否则直连（走系统网络）。
    失败返回 None（由调用方决定回退策略）。
    """
    proxy_base = get_proxy_base()
    target = f"{proxy_base}/{url}" if proxy_base else url
    try:
        async with httpx.AsyncClient(timeout=timeout, verify=False, follow_redirects=True) as client:
            return await client.get(target)
    except Exception as e:
        logger.warning(f"GitHub 资源获取失败({url}): {e}")
        return None
