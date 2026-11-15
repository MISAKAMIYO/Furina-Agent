"""
MCP（Model Context Protocol）服务注册表与客户端池

设计要点:
- 所有 MCP 连接会话运行在一个专用 daemon 线程自己的 asyncio 事件循环里，
  与 FastAPI 主事件循环完全隔离，避免请求生命周期影响长连接。
- 对外暴露同步方法（list_servers / add_server / ...），内部通过
  asyncio.run_coroutine_threadsafe 投递到专用循环执行。
- 连接成功后把远端工具以 mcp__{服务名}__{工具名} 命名空间前缀注册进
  zhiyan_web.core.func_tool_manager，供 Agent / LLM 调用；
  服务断开时逐个注销，避免悬空工具。
- 配置持久化到 data/mcp_server.json，保持 {"mcpServers": {...}} 顶层结构，
  并原样保留未知字段；写入采用「先写 .tmp 再 os.replace」的原子方式。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from contextlib import AsyncExitStack
from typing import Any

logger = logging.getLogger("ZhiYanWeb.MCP")

# ── 官方 MCP SDK 探测 ──────────────────────────────────────────────
SDK_AVAILABLE = False
_SDK_ERROR = ""
try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamablehttp_client

    SDK_AVAILABLE = True
except Exception as e:  # pragma: no cover - 仅在 SDK 缺失时触发
    SDK_AVAILABLE = False
    _SDK_ERROR = str(e)
    logger.error(f"官方 mcp SDK 不可用: {e}")


# 默认配置文件路径：backend/data/mcp_server.json
_DEFAULT_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "mcp_server.json",
)

# 连接 / 测试连接超时（秒）：npx 首次拉包可能较慢，给足时间
CONNECT_TIMEOUT = 90
CALL_TIMEOUT = 120


def _sanitize(segment: str) -> str:
    """把工具名/服务名中非 [A-Za-z0-9_-] 的字符替换为下划线"""
    return "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in str(segment))


def namespace_tool(server: str, tool: str) -> str:
    """生成带命名空间前缀的工具名，避免与原生 skills 等扩展互相覆盖"""
    return f"mcp__{_sanitize(server)}__{_sanitize(tool)}"


def infer_transport(cfg: dict) -> str:
    """推断传输方式，兼容缺省 transport 字段的历史配置"""
    t = cfg.get("transport")
    if t in ("stdio", "http", "sse"):
        return t
    if cfg.get("command"):
        return "stdio"
    if cfg.get("url"):
        if cfg.get("type") == "sse":
            return "sse"
        return "http"
    return "stdio"


def schema_to_func_args(schema: Any) -> list[dict]:
    """把 MCP 工具的 JSON Schema 转换为 func_tool_manager 需要的 func_args 列表"""
    if not isinstance(schema, dict):
        return []
    props = schema.get("properties")
    if not isinstance(props, dict):
        return []
    result: list[dict] = []
    for pname, pspec in props.items():
        if not isinstance(pspec, dict):
            result.append({"name": pname, "type": "string", "description": ""})
            continue
        ptype = pspec.get("type", "string")
        # 联合类型（如 ["string", "null"]）取第一个非 null 类型
        if isinstance(ptype, list):
            ptype = next((t for t in ptype if t != "null"), "string")
        result.append(
            {
                "name": pname,
                "type": ptype,
                "description": pspec.get("description", "") or "",
            }
        )
    return result


def _get_func_tool_manager():
    """获取全局 FunctionToolManager（优先 core 中初始化的实例）"""
    try:
        from zhiyan_web.core import func_tool_manager as core_ftm

        if core_ftm is not None:
            return core_ftm
    except Exception:
        pass
    try:
        from zhiyan_web.provider.func_tool_manager import get_func_tool_manager

        return get_func_tool_manager()
    except Exception:
        return None


class McpRuntime:
    """MCP 服务注册表 + 客户端池（线程安全，网络操作在专用事件循环线程执行）"""

    def __init__(self, config_path: str | None = None):
        self._config_path = os.path.abspath(config_path or _DEFAULT_CONFIG_PATH)
        self._raw: dict[str, Any] = {}
        self._servers: dict[str, dict] = {}
        self._rt: dict[str, dict] = {}
        self._lock = threading.RLock()

        # 专用事件循环线程
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_ready = threading.Event()
        self._loop_lock = threading.Lock()

        # 启动即加载配置（只读文件，不触发任何连接）
        self._safe_load()

    # ════════ 配置读写 ════════

    @property
    def config_path(self) -> str:
        return self._config_path

    def _safe_load(self) -> None:
        try:
            self._load_config()
        except Exception as e:
            logger.error(f"加载 MCP 配置失败: {e}")

    def _load_config(self) -> None:
        path = self._config_path
        raw: dict[str, Any] = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                raw = loaded
        servers = raw.get("mcpServers")
        if not isinstance(servers, dict):
            servers = {}
        with self._lock:
            self._raw = raw
            self._servers = servers

    def _save(self) -> str:
        """原子落盘，保留 mcpServers 之外的未知字段。返回错误串（成功为空）"""
        with self._lock:
            self._raw["mcpServers"] = self._servers
            data = self._raw
            path = self._config_path
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            return ""
        except Exception as e:
            logger.error(f"保存 MCP 配置失败: {e}")
            return str(e)

    # ════════ 专用事件循环 ════════

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._loop_lock:
            if self._loop is not None and self._thread is not None and self._thread.is_alive():
                return self._loop
            self._loop_ready.clear()
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop, name="McpRuntimeLoop", daemon=True
            )
            self._thread.start()
            self._loop_ready.wait(timeout=5)
            return self._loop

    def _run_loop(self) -> None:
        loop = self._loop
        assert loop is not None
        asyncio.set_event_loop(loop)
        self._loop_ready.set()
        loop.run_forever()

    def _run(self, coro, timeout: float = CONNECT_TIMEOUT):
        """在专用循环里同步执行协程并等待结果"""
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=timeout)

    @staticmethod
    def _fmt_err(e: Exception) -> str:
        return f"{type(e).__name__}: {e}" if str(e) else type(e).__name__

    # ════════ MCP 会话打开 ════════

    async def _open_session(self, stack: AsyncExitStack, cfg: dict):
        """根据配置建立传输并返回已握手前的 ClientSession（上下文挂到 stack）"""
        transport = infer_transport(cfg)
        if transport == "stdio":
            command = cfg.get("command")
            if not command:
                raise ValueError("stdio 服务缺少 command")
            env = cfg.get("env") or {}
            merged_env = None
            if env:
                merged_env = {**os.environ, **{k: str(v) for k, v in env.items()}}
            params = StdioServerParameters(
                command=command,
                args=list(cfg.get("args") or []),
                env=merged_env,
            )
            read, write = await stack.enter_async_context(stdio_client(params))
        elif transport == "sse":
            url = cfg.get("url")
            if not url:
                raise ValueError("sse 服务缺少 url")
            read, write = await stack.enter_async_context(
                sse_client(url, headers=cfg.get("headers") or None)
            )
        else:  # http (streamable)
            url = cfg.get("url")
            if not url:
                raise ValueError("http 服务缺少 url")
            read, write, _ = await stack.enter_async_context(
                streamablehttp_client(url, headers=cfg.get("headers") or None)
            )
        session = await stack.enter_async_context(ClientSession(read, write))
        return session

    @staticmethod
    def _tool_to_dict(tool: Any) -> dict:
        schema = getattr(tool, "inputSchema", None)
        if schema is None:
            schema = {}
        return {
            "name": getattr(tool, "name", ""),
            "description": getattr(tool, "description", "") or "",
            "parameters": schema,
        }

    # ════════ 连接 / 断开 ════════

    async def _disconnect_async(self, name: str) -> None:
        with self._lock:
            rt = self._rt.get(name)
        self._unregister_tools(name)
        if not rt:
            return
        # 通知生命周期任务退出（AsyncExitStack 必须在其进入时的同一 Task 中关闭）
        stop = rt.get("stop_event")
        if stop is not None:
            stop.set()
        task = rt.get("task")
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=20)
            except Exception:
                pass
        rt["task"] = None
        rt["connected"] = False
        rt["session"] = None
        rt["stack"] = None
        rt["tools"] = []

    def _disconnect(self, name: str) -> None:
        try:
            self._run(self._disconnect_async(name), timeout=30)
        except Exception as e:
            logger.warning(f"断开 MCP 服务失败 {name}: {e}")

    async def _lifecycle(
        self,
        name: str,
        cfg: dict,
        register: bool,
        stop_event: asyncio.Event,
        ready_event: asyncio.Event,
    ) -> None:
        """单个服务的完整会话生命周期

        在同一 Task 内进入/退出 AsyncExitStack，直到收到 stop_event 才关闭，
        避免 anyio cancel scope 跨 Task 退出的问题。
        """
        rt = self._rt.setdefault(name, {})
        stack = AsyncExitStack()
        try:
            session = await self._open_session(stack, cfg)
            await session.initialize()
            listed = await session.list_tools()
            tools = [self._tool_to_dict(t) for t in (listed.tools or [])]
            rt["session"] = session
            rt["stack"] = stack
            rt["tools"] = tools
            rt["connected"] = True
            rt["error"] = ""
            if register:
                rt["registered"] = self._register_tools(name, tools)
            logger.info(f"MCP 服务已连接: {name} (工具 {len(tools)} 个)")
            ready_event.set()
            await stop_event.wait()
        except Exception as e:
            rt["connected"] = False
            rt["error"] = self._fmt_err(e)
            logger.warning(f"MCP 服务连接失败 {name}: {rt['error']}")
            ready_event.set()
        finally:
            try:
                await stack.aclose()
            except Exception as e:
                logger.warning(f"关闭 MCP 会话失败 {name}: {e}")
            rt["session"] = None
            rt["stack"] = None
            rt["connected"] = False
            rt["tools"] = []

    async def _connect_async(self, name: str, register: bool = True) -> str:
        """连接服务并（可选）注册工具；返回错误串（成功为空）"""
        # 先断开同名旧连接，保证幂等
        await self._disconnect_async(name)

        with self._lock:
            cfg = self._servers.get(name)
        rt = self._rt.setdefault(name, {})
        rt.update(
            {
                "connected": False,
                "error": "",
                "tools": [],
                "registered": [],
                "session": None,
                "stack": None,
                "task": None,
                "stop_event": None,
            }
        )
        if not cfg:
            rt["error"] = "配置不存在"
            return rt["error"]

        stop_event = asyncio.Event()
        ready_event = asyncio.Event()
        rt["stop_event"] = stop_event
        task = asyncio.get_running_loop().create_task(
            self._lifecycle(name, cfg, register, stop_event, ready_event)
        )
        rt["task"] = task
        try:
            await asyncio.wait_for(ready_event.wait(), timeout=CONNECT_TIMEOUT)
        except asyncio.TimeoutError:
            stop_event.set()
            rt["connected"] = False
            rt["error"] = "连接超时"
            return rt["error"]
        return rt.get("error", "") or ""

    def _connect(self, name: str) -> str:
        try:
            return self._run(
                self._connect_async(name, register=True), timeout=CONNECT_TIMEOUT + 20
            ) or ""
        except Exception as e:
            msg = self._fmt_err(e)
            rt = self._rt.setdefault(name, {})
            rt["connected"] = False
            rt["error"] = msg
            return msg

    # ════════ 工具注册 / 注销 ════════

    def _register_tools(self, name: str, tools: list[dict]) -> list[str]:
        """把 MCP 工具注册进 FunctionToolManager，返回成功注册的工具名列表"""
        ftm = _get_func_tool_manager()
        if ftm is None:
            logger.warning(f"FunctionToolManager 不可用，跳过工具注册: {name}")
            return []
        registered: list[str] = []
        for tool in tools:
            try:
                fname = namespace_tool(name, tool.get("name", ""))
                func_args = schema_to_func_args(tool.get("parameters"))
                handler = self._make_handler(name, tool.get("name", ""))
                ftm.add_func(
                    name=fname,
                    func_args=func_args,
                    desc=tool.get("description", "") or "",
                    handler=handler,
                )
                registered.append(fname)
            except Exception as e:
                logger.warning(f"注册 MCP 工具失败 {name}/{tool.get('name')}: {e}")
        return registered

    def _unregister_tools(self, name: str) -> None:
        with self._lock:
            rt = self._rt.get(name)
        if not rt:
            return
        names = rt.get("registered") or []
        rt["registered"] = []
        if not names:
            return
        ftm = _get_func_tool_manager()
        if ftm is None:
            return
        for fname in names:
            try:
                ftm.remove_func(fname)
            except Exception:
                pass

    def _make_handler(self, server: str, tool: str):
        """生成 async handler：内部把调用投递到专用循环，不阻塞主事件循环"""

        async def _handler(**kwargs):
            try:
                loop = self._ensure_loop()
                future = asyncio.run_coroutine_threadsafe(
                    self._call_tool_async(server, tool, kwargs), loop
                )
                return await asyncio.wrap_future(future)
            except Exception as e:
                return {"error": f"MCP 工具调用失败 ({server}/{tool}): {e}"}

        return _handler

    async def _call_tool_async(self, server: str, tool: str, args: dict) -> Any:
        with self._lock:
            rt = self._rt.get(server)
            session = rt.get("session") if rt else None
            connected = bool(rt.get("connected")) if rt else False
        if session is None or not connected:
            return {"error": f"MCP 服务 {server} 未连接"}
        try:
            result = await session.call_tool(tool, arguments=args or {})
        except Exception as e:
            return {"error": f"MCP 工具调用异常 ({server}/{tool}): {e}"}

        texts: list[str] = []
        for block in (getattr(result, "content", None) or []):
            text = getattr(block, "text", None)
            texts.append(text if text is not None else str(block))
        text = "\n".join(texts)
        if getattr(result, "isError", False):
            return {"error": text or f"MCP 工具 {tool} 返回错误"}
        return text

    # ════════ 供 handler / 外部调用 ════════

    def call_tool_sync(self, server: str, tool: str, args: dict, timeout: float = CALL_TIMEOUT) -> Any:
        """同步调用 MCP 工具（在专用循环执行）"""
        return self._run(self._call_tool_async(server, tool, args), timeout=timeout)

    # ════════ 注册表 CRUD（同步，供路由经 to_thread 调用） ════════

    def _server_view(self, name: str, cfg: dict) -> dict:
        rt = self._rt.get(name, {})
        return {
            "name": name,
            "transport": infer_transport(cfg),
            "command": cfg.get("command", "") or "",
            "args": list(cfg.get("args") or []),
            "env": dict(cfg.get("env") or {}),
            "url": cfg.get("url", "") or "",
            "headers": dict(cfg.get("headers") or {}),
            "active": bool(cfg.get("active", True)),
            "description": cfg.get("description", "") or "",
            "connected": bool(rt.get("connected", False)),
            "tool_count": len(rt.get("tools") or []),
            "error": rt.get("error", "") or "",
        }

    def list_servers(self) -> dict:
        with self._lock:
            items = [self._server_view(n, c) for n, c in self._servers.items()]
        return {"servers": items, "count": len(items)}

    def _normalize_payload(self, payload: dict, base: dict | None = None):
        """把请求体规范化为服务配置；返回 (cfg, error)"""
        cfg = dict(base or {})
        payload = payload or {}
        if payload.get("transport"):
            t = payload["transport"]
            if t not in ("stdio", "http", "sse"):
                return None, f"不支持的 transport: {t}"
            cfg["transport"] = t
        for key in ("command", "url", "description"):
            if key in payload:
                cfg[key] = payload.get(key) or ""
        if "args" in payload:
            cfg["args"] = list(payload.get("args") or [])
        if "env" in payload:
            cfg["env"] = {k: str(v) for k, v in (payload.get("env") or {}).items()}
        if "headers" in payload:
            cfg["headers"] = {k: str(v) for k, v in (payload.get("headers") or {}).items()}
        if "active" in payload:
            cfg["active"] = bool(payload.get("active"))

        transport = infer_transport(cfg)
        if transport == "stdio":
            if not cfg.get("command"):
                return None, "stdio 服务必须提供 command"
        else:
            if not cfg.get("url"):
                return None, f"{transport} 服务必须提供 url"
        return cfg, ""

    def add_server(self, payload: dict) -> dict:
        payload = payload or {}
        name = (payload.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "name 不能为空"}
        with self._lock:
            if name in self._servers:
                return {"ok": False, "error": f"服务已存在: {name}"}
        cfg, err = self._normalize_payload(payload)
        if err:
            return {"ok": False, "error": err}
        if "active" not in payload:
            cfg["active"] = True

        with self._lock:
            self._servers[name] = cfg
        save_err = self._save()
        if save_err:
            with self._lock:
                self._servers.pop(name, None)
            return {"ok": False, "error": f"保存配置失败: {save_err}"}

        result: dict[str, Any] = {"ok": True, "name": name}
        if cfg.get("active", True):
            cerr = self._connect(name)
            if cerr:
                result["connect_error"] = cerr
        return result

    def update_server(self, name: str, payload: dict) -> dict:
        with self._lock:
            if name not in self._servers:
                return {"ok": False, "error": f"服务不存在: {name}"}
            base = dict(self._servers[name])
        cfg, err = self._normalize_payload(payload or {}, base=base)
        if err:
            return {"ok": False, "error": err}

        self._disconnect(name)
        with self._lock:
            self._servers[name] = cfg
        save_err = self._save()
        if save_err:
            return {"ok": False, "error": f"保存配置失败: {save_err}"}

        result: dict[str, Any] = {"ok": True}
        if cfg.get("active", True):
            cerr = self._connect(name)
            if cerr:
                result["connect_error"] = cerr
        return result

    def remove_server(self, name: str) -> dict:
        with self._lock:
            if name not in self._servers:
                return {"ok": False, "error": f"服务不存在: {name}"}
        self._disconnect(name)
        with self._lock:
            self._servers.pop(name, None)
            self._rt.pop(name, None)
        save_err = self._save()
        if save_err:
            return {"ok": False, "error": f"保存配置失败: {save_err}"}
        return {"ok": True}

    def toggle(self, name: str, active: bool) -> dict:
        with self._lock:
            if name not in self._servers:
                return {"ok": False, "error": f"服务不存在: {name}"}
            self._servers[name]["active"] = bool(active)
        save_err = self._save()
        if save_err:
            return {"ok": False, "error": f"保存配置失败: {save_err}"}

        if active:
            cerr = self._connect(name)
            result: dict[str, Any] = {"ok": True, "active": True}
            if cerr:
                result["error"] = cerr
            return result
        self._disconnect(name)
        return {"ok": True, "active": False}

    async def _probe_async(self, cfg: dict) -> dict:
        """临时连接、列出工具、断开（不注册），用于测试连接"""
        stack = AsyncExitStack()
        try:
            session = await self._open_session(stack, cfg)
            await session.initialize()
            listed = await session.list_tools()
            tools = [self._tool_to_dict(t) for t in (listed.tools or [])]
            return {"ok": True, "error": "", "tools": tools, "count": len(tools)}
        except Exception as e:
            return {"ok": False, "error": self._fmt_err(e), "tools": [], "count": 0}
        finally:
            try:
                await stack.aclose()
            except Exception:
                pass

    def test_server(self, name: str, payload: dict | None = None) -> dict:
        with self._lock:
            cfg = self._servers.get(name)
        if not cfg:
            # 支持在保存前用请求体临时测试
            cfg, err = self._normalize_payload(payload or {})
            if err:
                return {"ok": False, "error": err, "tools": [], "count": 0}
        try:
            return self._run(self._probe_async(dict(cfg)))
        except Exception as e:
            return {"ok": False, "error": self._fmt_err(e), "tools": [], "count": 0}

    def reload(self) -> dict:
        """断开全部后按配置文件重连所有 active 服务，并重新注册工具"""
        with self._lock:
            names = set(self._servers.keys()) | set(self._rt.keys())
        for name in names:
            self._disconnect(name)

        self._safe_load()

        errors: list[str] = []
        connected = 0
        tools = 0
        with self._lock:
            server_items = list(self._servers.items())
        for name, cfg in server_items:
            if not bool(cfg.get("active", True)):
                continue
            err = self._connect(name)
            if err:
                errors.append(f"{name}: {err}")
            else:
                connected += 1
                with self._lock:
                    tools += len(self._rt.get(name, {}).get("tools") or [])
        return {
            "ok": True,
            "connected": connected,
            "tools": tools,
            "errors": errors,
            "count": len(server_items),
        }

    # ════════ 状态快照 ════════

    def _collect_errors(self) -> list[str]:
        errors: list[str] = []
        with self._lock:
            for name in self._servers:
                err = (self._rt.get(name) or {}).get("error")
                if err:
                    errors.append(f"{name}: {err}")
        return errors

    def status(self) -> dict:
        with self._lock:
            servers = len(self._servers)
            active = sum(1 for c in self._servers.values() if c.get("active", True))
            connected = sum(
                1 for n in self._servers if (self._rt.get(n) or {}).get("connected")
            )
            tools = sum(len((self._rt.get(n) or {}).get("tools") or []) for n in self._servers)
        return {
            "ok": True,
            "sdk": SDK_AVAILABLE,
            "servers": servers,
            "active": active,
            "connected": connected,
            "tools": tools,
            "config_path": self._config_path,
            "errors": self._collect_errors(),
        }

    def overview(self) -> dict:
        """纯内存快照，不联网、不触发连接（供 /api/extensions/overview 使用）"""
        with self._lock:
            servers = len(self._servers)
            active = sum(1 for c in self._servers.values() if c.get("active", True))
            connected = sum(
                1 for n in self._servers if (self._rt.get(n) or {}).get("connected")
            )
            tools = sum(len((self._rt.get(n) or {}).get("tools") or []) for n in self._servers)
            errors = [
                f"{n}: {err}"
                for n in self._servers
                if (err := (self._rt.get(n) or {}).get("error"))
            ]
        return {
            "servers": servers,
            "active": active,
            "connected": connected,
            "tools": tools,
            "errors": errors,
        }

    def runtime_state(self) -> dict[str, Any]:
        """形如 {服务名: {config, connected, tool_count, error}} 的运行时状态"""
        with self._lock:
            result = {}
            for name, cfg in self._servers.items():
                rt = self._rt.get(name, {})
                result[name] = {
                    "config": dict(cfg),
                    "connected": bool(rt.get("connected", False)),
                    "tool_count": len(rt.get("tools") or []),
                    "error": rt.get("error", "") or "",
                }
        return result


# ── 模块级单例 ──────────────────────────────────────────────
_registry: McpRuntime | None = None
_registry_lock = threading.Lock()


def get_registry(config_path: str | None = None) -> McpRuntime:
    """获取 McpRuntime 单例（首次调用时创建）"""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = McpRuntime(config_path)
    return _registry
