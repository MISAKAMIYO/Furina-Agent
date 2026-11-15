"""
DSH (DeepSeek Harness) engine bridge.

Spawns the DSH JSON-RPC runtime as a subprocess and exposes an asyncio
request/notification bridge so zhiyan's chat layer can stream DSH agent turns
over the standard SSE event format (thinking/tool_call/chunk/…/done).

Protocol summary (newline-delimited JSON over stdio):
  client -> server  : {"jsonrpc":"2.0","id":N,"method":"initialize"|"session/prompt"|"shutdown","params":...}
  server -> client  : {"jsonrpc":"2.0","id":N,"result":...|"error":...}   (replies)
                      {"jsonrpc":"2.0","method":"session.event","params":{sessionId,event}}   (notifications)
                      {"jsonrpc":"2.0","method":"session.status","params":{sessionId,status}}
                      {"jsonrpc":"2.0","method":"subagent.started|subagent.finished","params":...}
"""
from __future__ import annotations

import asyncio
import glob
import json
import logging
import os
import shutil
import subprocess
import threading
import uuid
from typing import Any, Dict, Optional, List

from zhiyan_web.config import PROJECT_ROOT, get_settings

logger = logging.getLogger("ZhiYanWeb")

# ═══ DSH 内核(DeepSeek Harness)定位 ═══════════════════════════════════
# 内核 = 软件内置的官方 npm 发行包(免构建、可移动):
#   <软件根>/dsh-suite/npm-core
# 其中 node_modules 为 npm 平铺物理目录 + 相对链接,移动/打包软件后依然可用,
# 不需要 pnpm 源码仓库的 junction 重建。Node.js 由用户手动安装,软件只负责
# 定位内置内核、检查依赖完整性并做连通性控制。
DSH_CORE = ""
DSH_RUNTIME = ""
DSH_BIN = ""
DSH_NODE_MODULES = ""


def _locate_core_dir() -> str:
    """定位内置 npm 发行包内核(dsh-suite/npm-core)。

    优先级: settings.json -> dsh -> kernel_dir >> 软件内置 dsh-suite/npm-core。
    返回合法内核目录;找不到时返回空串(由调用方给出可读错误)。
    """
    def _valid(p: str) -> bool:
        return bool(p) and os.path.isfile(os.path.join(p, "package.json"))

    cfg = get_settings().get("dsh") or {}
    kd = (cfg.get("kernel_dir") or "").strip()
    # 1) 显式配置,直接作为内核根目录
    if kd and _valid(kd.rstrip("/\\")):
        return os.path.normpath(kd.rstrip("/\\"))
    # 2) 软件内置 npm 发行包内核(相对路径读取,随软件整体移动)
    builtin = os.path.join(PROJECT_ROOT, "dsh-suite", "npm-core")
    if _valid(builtin):
        return os.path.normpath(builtin)
    return ""


def _init_dsh_paths() -> None:
    """按当前配置(重新)解析 DSH 内核与运行时路径,写入模块级常量。"""
    global DSH_CORE, DSH_RUNTIME, DSH_BIN, DSH_NODE_MODULES, DSH_GENERATED_CONFIG
    core = _locate_core_dir()
    if not core:
        # 不到合法内核时暂用空占位,start() 会给出可读错误
        core = ""
    DSH_CORE = core
    # 运行时数据(会话/凭证/控制台)落在 dsh-suite/dsh-runtime 独立目录,与软件包解耦
    DSH_RUNTIME = os.path.join(os.path.dirname(core), "dsh-runtime")
    # 0.2 唯一入口是 CLI：node <core>/node_modules/@deepseek-ai/dsh/lib/bin.js
    #   --profile sdk [--patch <覆盖层>...]
    DSH_BIN = os.path.join(core, "node_modules", "@deepseek-ai",
                           "dsh", "lib", "bin.js")
    DSH_NODE_MODULES = os.path.join(core, "node_modules")
    # 生成式覆盖层(patch)：叠加在 sdk profile(dsh-base + dsh-sdk-app)之上，
    # 只声明需要覆盖/停用/insert 的行，随 settings.json -> "dsh" 动态生成。
    DSH_GENERATED_CONFIG = os.path.join(core, "zhiyan.cordis.generated.yml")


_init_dsh_paths()

DEFAULT_DEEPSEEK_BASE = "https://api.deepseek.com"

# 0.2 的 DeepSeek 适配器(dsh-llm-deepseek-api-key)走的是 Messages 协议
# （Anthropic 风格），不是 OpenAI 的 /chat/completions。它会在 baseURL 之后
# 追加 /v1/messages，官方端点因此是 .../anthropic（再由插件补 /v1）。
# 直接沿用主软件的 OpenAI 端点会打到 .../v1/messages 上得到 404。
DEFAULT_DEEPSEEK_MESSAGES_BASE = "https://api.deepseek.com/anthropic"

# npm 发行包内核的 node_modules 为平铺物理目录 + 相对链接，移动/打包后无需重建，
# 因此不再需要 pnpm 源码仓库的 workspace junction 自愈逻辑。

# 生成式覆盖层（由 settings.json -> "dsh" 动态生成，工具开关/参数随保存生效）。
# 0.2 起不再生成完整组合树，而是生成 patch 覆盖层，叠加在 sdk profile 之上。
# 路径由 _init_dsh_paths 解析到内核目录内(见上)。

# 面板可用的 DSH 工具能力
DSH_TOOL_OPTIONS = ["bash", "fs", "subagent", "todo"]

# 启动的 profile：sdk = dsh-base + dsh-sdk-app，提供 bash/fs/subagent/todo 等完整工具集，
# 并以 stdio JSON-RPC 对外握手（协议与 0.1 一致）。
DSH_PROFILE = "sdk"

# 0.1 组合中不存在、且对 headless JSON-RPC 桥无意义的可选工具行：
# 统一停用，保证模型侧工具面与本软件面板开关一致，并避免交互式工具
# （ask_user_question / 计划模式）因无人应答而阻塞会话。
DSH_EXTRA_ROWS_OFF = [
    "tool-jobs", "tool-skill", "tool-goal", "tool-web",
    "tool-workflow", "tool-ralph", "plan-mode", "user-questions",
]

# 默认上下文窗口（tokens），供 llm-deepseek 适配器与 DSH_CONTEXT_WINDOW 使用。
DSH_CONTEXT_WINDOW_DEFAULT = 1000000

DSH_DEFAULTS = {
    "api_key": "",
    "api_url": "",
    "model": "",
    # 0 = 不向内核传 maxTokens，沿用内核自带的单次输出上限（llm-deepseek 默认 256K）。
    # 早期默认 4096 太小：带 thinking 的 agent 任务会因单步输出被截断而
    # turn/end reason=max-tokens，表现为思考链断在半句、工具循环做不完。
    "max_tokens": 0,
    "cwd": "",
    "system_prompt": "",
    "thinking": True,
    "reasoning_effort": "max",
    "tools": list(DSH_TOOL_OPTIONS),
    "bash_timeout": 60000,
    "session_root": "",
    "kernel_dir": "",
    # 权限模式：danger-full-access（默认，无人应答审批）或 workspace-write
    # （越权操作在没有审批应答者时会被拒绝，属于"工作区白名单"模式）。
    "permission_mode": "danger-full-access",
    # 是否把主引擎已配置的 MCP 服务同步注入 DSH 内核（stdio / streamable-http）
    "mcp_bridge": True,
}

DSH_PERMISSION_MODES = ("danger-full-access", "workspace-write")


def _permission_mode(cfg: Optional[Dict[str, Any]] = None) -> str:
    """读取合法的 DSH 权限模式，非法值回退到 danger-full-access。"""
    c = cfg if cfg is not None else get_dsh_settings()
    mode = str(c.get("permission_mode") or "").strip()
    return mode if mode in DSH_PERMISSION_MODES else "danger-full-access"


def get_dsh_settings(s=None) -> Dict[str, Any]:
    """返回合并默认值后的 DSH 配置字典（应用 settings.json -> 'dsh' 覆盖）。"""
    s = s if s is not None else get_settings()
    cfg = dict(DSH_DEFAULTS)
    raw = s.get("dsh") or {}
    if isinstance(raw, dict):
        cfg.update(raw)
    return cfg


# ═══════════════════════════════════════════════════════════════════════════
# DSH 插件系统（cordis 插件，独立于 zhiyan 自身的 star/技能插件系统）
# 清单单独存于 dsh-runtime/dsh-plugins.json（内核同级独立 runtime），与 settings.json 完全分离。
# ═══════════════════════════════════════════════════════════════════════════
DSH_PLUGINS_FILE = os.path.join(DSH_RUNTIME, "dsh-plugins.json")
_plugins_lock = threading.Lock()


def get_user_dsh_plugins() -> List[Dict[str, Any]]:
    """读取用户自定义 DSH 插件清单（失败/缺省返回空表）。"""
    with _plugins_lock:
        try:
            if os.path.exists(DSH_PLUGINS_FILE):
                with open(DSH_PLUGINS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[DSH] 读取插件清单失败: %s", exc)
    return []


def save_user_dsh_plugins(rows: List[Dict[str, Any]]) -> None:
    """写入用户自定义 DSH 插件清单。"""
    with _plugins_lock:
        os.makedirs(DSH_RUNTIME, exist_ok=True)
        tmp = DSH_PLUGINS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DSH_PLUGINS_FILE)


def sanitize_plugin_row(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """校验/规整一行插件定义；非法时返回 None。"""
    if not isinstance(raw, dict):
        return None
    row_id = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    if not row_id:
        row_id = name.rstrip("/").split("/")[-1] or name
    cfg = raw.get("config")
    if not isinstance(cfg, dict):
        cfg = {}
    return {
        "id": row_id,
        "name": name,
        "enabled": bool(raw.get("enabled", True)),
        "config": cfg,
        "note": str(raw.get("note") or "").strip(),
    }


_BUILTIN_HINT = {
    "system-prompt": "系统提示词（人格 / 工作目录提示）",
    "llm-deepseek": "DeepSeek LLM 适配（端点/密钥/上下文窗口）",
    "session-persistence-jsonl": "JSONL 会话持久化",
    "sandbox-policy": "沙箱与工作目录策略",
    "tool-pwsh": "pwsh 工具（Windows 模型侧 Shell）",
    "tool-bash": "bash 工具（非 Windows 模型侧 Shell）",
    "pwsh-sandbox": "PowerShell 执行器沙箱（超时）",
    "bash-sandbox": "bash 执行器沙箱（超时）",
    "tool-fs": "fs 工具（模型侧调用）",
    "tool-fs-search": "fs 检索工具（glob/grep）",
    "fs-observation-policy": "文件系统观察策略",
    "tool-todo": "todo 工具",
    "tool-subagent": "subagent 工具（spawn）",
    "tool-subagent-fork": "subagent_fork 工具（fork）",
    "tool-subagent-control": "子智能体控制（send_message）",
    "tool-subagent-list-agents": "子智能体列表",
    "tool-jobs": "后台作业工具（已停用）",
    "tool-skill": "skills 工具（已停用）",
    "tool-goal": "goal 工具（已停用）",
    "tool-web": "web 工具（已停用）",
    "tool-workflow": "workflow 工具（已停用）",
    "tool-ralph": "ralph 工具（已停用）",
    "plan-mode": "计划模式（已停用）",
    "user-questions": "交互式提问（已停用）",
}


def list_builtin_plugins(cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """当前覆盖层生成的内置 cordis 行（只读，供管理页展示）。"""
    if cfg is None:
        cfg = get_dsh_settings()
    rows = []
    for p in _build_patch(cfg):
        rid = p.get("id")
        if not rid:
            continue
        rows.append({
            "id": rid,
            "builtin": True,
            "enabled": not bool(p.get("disabled", False)),
            "hint": _BUILTIN_HINT.get(rid, ""),
        })
    return rows


def _to_messages_base(url: str) -> str:
    """把用户填的端点规范化为 Messages 兼容根。

    0.1 内核用 OpenAI 协议,设置里普遍存的是 https://api.deepseek.com(或 /v1)。
    0.2 的 DeepSeek 适配器改走 Messages 协议,同一个域名的正确根是
    https://api.deepseek.com/anthropic。仅对已知的官方 OpenAI 根做映射,其它
    自定义端点原样保留(由用户自行保证 Messages 兼容)。
    """
    u = (url or "").strip().rstrip("/")
    if not u:
        return ""
    low = u.lower()
    for suffix in ("https://api.deepseek.com", "http://api.deepseek.com"):
        if low == suffix or low == suffix + "/v1":
            return DEFAULT_DEEPSEEK_MESSAGES_BASE
    return u


def _resolve_llm_credentials():
    """解析 DSH 使用的 Messages 端点与密钥（patch 与运行时环境共用）。

    端点规则:仅当设置里显式填了 DSH 专属 api_url 时才用它(并规范化为 Messages
    根);否则一律回落 DeepSeek 官方 Messages 端点,不沿用主软件的 OpenAI 端点
    (协议不同,会 404)。
    密钥规则:DSH 专属 api_key > openrouter 端点/供应商时取 openrouter 密钥 >
    全局 api_key。
    """
    s = get_settings()
    provider = (s.get("provider") or "deepseek").lower()
    dsh_cfg = s.get("dsh") or {}
    dsh_key = (dsh_cfg.get("api_key") or "").strip()
    dsh_url = (dsh_cfg.get("api_url") or "").strip()
    base_url = _to_messages_base(dsh_url) or DEFAULT_DEEPSEEK_MESSAGES_BASE
    base_url = base_url.rstrip("/")
    if dsh_key:
        api_key = dsh_key
    elif provider == "openrouter" or "openrouter" in (s.get("api_url") or ""):
        api_key = s.get("openrouter_api_key") or s.get("api_key") or ""
    else:
        api_key = s.get("api_key") or ""
    return base_url, api_key


def _safe_plugin_id(text: str, limit: int = 40) -> str:
    """把任意名称转成 cordis 行 id 可用的字符集。"""
    return ("".join(ch if (ch.isalnum() or ch in "-_") else "_"
                    for ch in str(text)) or "plugin")[:limit]


def _mcp_plugin_rows() -> List[Dict[str, Any]]:
    """把主引擎已启用且已配置的 MCP 服务映射成 dsh-mcp-client 插件行。

    - stdio -> transport: stdio（command / args / env）
    - http  -> transport: streamable-http（url / headers）
    - sse   -> 内核不支持，跳过并记日志
    """
    try:
        from zhiyan_web.mcp.manager import get_registry
        data = get_registry().list_servers() or {}
    except Exception as e:
        logger.debug("[DSH] 读取 MCP 服务配置失败: %s", e)
        return []
    rows: List[Dict[str, Any]] = []
    for srv in (data.get("servers") or []):
        if not isinstance(srv, dict) or not srv.get("active", True):
            continue
        name = str(srv.get("name") or "").strip()
        if not name:
            continue
        transport = str(srv.get("transport") or "stdio")
        cfg: Dict[str, Any] = {}
        if transport == "stdio":
            command = str(srv.get("command") or "").strip()
            if not command:
                continue
            cfg = {"transport": "stdio", "command": command,
                   "args": [str(a) for a in (srv.get("args") or [])]}
            if srv.get("env"):
                cfg["env"] = {str(k): str(v) for k, v in (srv.get("env") or {}).items()}
        elif transport in ("http", "streamable-http"):
            url = str(srv.get("url") or "").strip()
            if not url:
                continue
            cfg = {"transport": "streamable-http", "url": url}
            if srv.get("headers"):
                cfg["headers"] = {str(k): str(v) for k, v in (srv.get("headers") or {}).items()}
        else:
            logger.info("[DSH] 跳过 SSE 类型的 MCP 服务 %s（内核仅支持 stdio / streamable-http）", name)
            continue
        rows.append({
            "id": "mcp-" + _safe_plugin_id(name),
            "name": "@deepseek-ai/dsh-mcp-client",
            "config": cfg,
        })
    return rows


def _build_patch(cfg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """按配置生成 0.2 的 patch 覆盖层（决定 DSH 暴露哪些工具能力）。

    覆盖层叠加在 sdk profile(dsh-base + dsh-sdk-app) 之上，按 id 覆盖已有行：
    - 只声明 disabled 的覆盖仅切换开关，保留该行原有 config；
    - 声明 config 的覆盖会整体替换该行 config，故必须写全所需字段。
    """
    tools = set(cfg.get("tools") or DSH_TOOL_OPTIONS)
    nt = os.name == "nt"
    timeout_ms = int(cfg.get("bash_timeout") or 60000)
    persona = (cfg.get("system_prompt") or "").strip() or "You are a helpful assistant."
    if nt:
        # Windows 上模型侧 Shell 工具名为 pwsh，提示其使用 PowerShell 语法
        persona += ("\n\nEnvironment note: this host runs on Windows and has no bash binary. "
                    "Shell commands run through the 'pwsh' tool; use PowerShell-compatible syntax: "
                    "ls/pwd/cat/cp/mv/rm/mkdir/echo exist as aliases; chain with ';', "
                    "pipe with '|', and quote inline strings with single quotes.")
    cwd = (cfg.get("cwd") or "").strip() or env_dsh_cwd()
    sess_root = os.path.normpath(
        (cfg.get("session_root") or "").strip() or os.path.join(DSH_RUNTIME, "sessions"))
    base_url, _api_key = _resolve_llm_credentials()

    patch: List[Dict[str, Any]] = []
    # 人格 / 工作目录提示（模板变量 {{cwd}} 由 system-prompt 插件替换）
    patch.append({"id": "system-prompt", "config": {
        "personaPrefix": persona,
        "personaSuffix": "Your working directory is {{cwd}}.",
    }})
    # JSONL 会话持久化落盘到受控目录。
    # compression 必须与已有会话存档一致(zstd)：持久化后端对同一 root 只接受
    # 一种压缩模式,混用会直接报错拒绝加载,故不可改为 none。
    patch.append({"id": "session-persistence-jsonl", "config": {
        "root": sess_root, "compression": "zstd"}})
    # 沙箱：headless JSON-RPC 桥默认无人应答审批，故默认 danger-full-access；
    # 设置里可改为 workspace-write（越权操作会被拒绝，等价于工作区白名单）。
    patch.append({"id": "sandbox-policy", "config": {
        "mode": _permission_mode(cfg), "workspaceRoot": cwd}})
    # LLM 适配器:端点显式写入；密钥走环境变量(apiKeyEnv)，不落盘到生成文件。
    # 不设置 reasoningEffort,避免 DeepSeek 公网 API 因 thinking 字段返回 400。
    patch.append({"id": "llm-deepseek", "config": {
        "apiKeyEnv": "DEEPSEEK_API_KEY",
        "baseURL": base_url,
        "defaultContextWindow": DSH_CONTEXT_WINDOW_DEFAULT,
        "streamIdleTimeoutMs": 172800000,
    }})
    # 四个核心工具组:按面板开关启用/停用（Windows 用 pwsh，其它平台用 bash）
    bash_on = "bash" in tools
    patch.append({"id": "tool-pwsh", "disabled": (not bash_on) or (not nt)})
    patch.append({"id": "tool-bash", "disabled": (not bash_on) or nt})
    if bash_on:
        patch.append({"id": "pwsh-sandbox" if nt else "bash-sandbox",
                      "config": {"timeoutMs": timeout_ms}})
    fs_on = "fs" in tools
    patch.append({"id": "tool-fs", "disabled": not fs_on})
    patch.append({"id": "tool-fs-search", "disabled": not fs_on})
    patch.append({"id": "fs-observation-policy", "disabled": not fs_on})
    patch.append({"id": "tool-todo", "disabled": "todo" not in tools})
    sub_on = "subagent" in tools
    for rid in ("tool-subagent", "tool-subagent-fork",
                "tool-subagent-control", "tool-subagent-list-agents"):
        patch.append({"id": rid, "disabled": not sub_on})
    # 非核心可选工具行统一停用（0.1 组合中不存在 / 桥无法应答）
    for rid in DSH_EXTRA_ROWS_OFF:
        patch.append({"id": rid, "disabled": True})
    # 追加启用的用户插件（id 与内置行冲突则跳过）
    used = {str(p.get("id")) for p in patch if p.get("id")}
    inserts: List[Dict[str, Any]] = []
    for row in get_user_dsh_plugins():
        row = sanitize_plugin_row(row)
        if not row or not row["enabled"] or row["id"] in used:
            continue
        inserts.append({"id": row["id"], "name": row["name"],
                        "config": row["config"] or {}})
        used.add(row["id"])
    # 主引擎的 MCP 服务同步注入内核（stdio / streamable-http）
    if cfg.get("mcp_bridge", True):
        for row in _mcp_plugin_rows():
            if row["id"] in used:
                continue
            inserts.append(row)
            used.add(row["id"])
    if inserts:
        patch.append({"insert": inserts})
    # 会话续聊：停用官方 sdk-jsonrpc-server，改为插入自带实现。
    # patch 只能按 id 替换 config / 切换 disabled，不能改 name，
    # 因此必须「停用 + 新 id 插入」，而不是覆盖 name。
    if _resume_plugin_path():
        patch.append({"id": "sdk-jsonrpc-server", "disabled": True})
        patch.append({"insert": [{
            "id": "zhiyan-sdk-jsonrpc-server",
            "name": "./zhiyan-sdk-resume.mjs",
            "inject": ["sdkAppStartup", "loader"],
            "config": {"maxTokensAsSuccess": True},
        }]})
    return patch


# ── 会话续聊插件（写入内核根目录，随每次启动重建） ──────────────────
SDK_RESUME_PLUGIN_JS = r'''// @zhiyan/dsh-sdk-resume
// 在官方 SDK JSON-RPC 服务之上增加「按 sessionId 恢复持久化会话」。
// 官方实现只调用 agents.create()，重启后同一 id 会撞上已存在的会话文件；
// 这里改为先 agents.resume()，只有历史不存在时才回退到新建。
import { HarnessSdkJsonRpcServer } from '@deepseek-ai/dsh-sdk-jsonrpc-server';
import { JsonRpcLineTransport } from '@deepseek-ai/dsh-sdk-protocol';
import Schema from '@deepseek-ai/schemastery';


class ResumeSdkJsonRpcServer extends HarnessSdkJsonRpcServer {
    async getOrCreateSession(sessionId) {
        const existing = this.sessions.get(sessionId);
        if (existing) return existing;
        const pending = this.sessionCreations.get(sessionId);
        if (pending) return pending;
        if (this.ctx.get('sessionPersistence') === undefined) {
            return super.getOrCreateSession(sessionId);
        }
        const creation = (async () => {
            try {
                const handle = await this.ctx.agents.resume({
                    resumeSessionId: sessionId,
                    agentOptions: {
                        provider: this.provider,
                        model: this.model,
                        ...(this.reasoningEffort === undefined ? {} : { reasoningEffort: this.reasoningEffort }),
                        ...(this.maxTokens === undefined ? {} : { maxTokens: this.maxTokens }),
                    },
                });
                const rec = { handle };
                this.sessions.set(sessionId, rec);
                return rec;
            } catch (error) {
                console.warn('[zhiyan-sdk-resume] 恢复会话失败，改为新建:',
                    (error && error.message) || error);
                return super.getOrCreateSession(sessionId);
            }
        })();
        this.sessionCreations.set(sessionId, creation);
        creation.then(
            () => this.sessionCreations.delete(sessionId),
            () => this.sessionCreations.delete(sessionId),
        );
        return creation;
    }
}

export const name = 'sdk-jsonrpc-server';
export const inject = ['agents'];
export const Config = Schema.object({
    maxTokensAsSuccess: Schema.boolean().default(true),
});

export function apply(ctx, config) {
    const rootFiber = ctx.root.fiber;
    const input = config.input ?? process.stdin;
    const output = config.output ?? process.stdout;
    const exit = config.exit ?? ((code) => { process.exit(code); });
    const transport = new JsonRpcLineTransport(input, output);
    const server = new ResumeSdkJsonRpcServer(ctx, transport, {
        maxTokensAsSuccess: config.maxTokensAsSuccess,
    });
    let exitTask;
    const disposeAndExit = () => {
        exitTask ??= (async () => {
            await Promise.allSettled([Promise.resolve().then(() => transport.flush())]);
            await Promise.allSettled([Promise.resolve().then(() => rootFiber.dispose())]);
            exit(0);
        })();
        return exitTask;
    };
    transport.onRequest(async (method, params) => {
        if (method === 'initialize') await ctx.get('loader')?.await();
        const result = await server.handleRequest(method, params);
        if (method === 'shutdown') {
            setImmediate(() => { disposeAndExit(); });
        }
        return result;
    });
    ctx.effect(() => {
        transport.start();
        return async () => {
            await server.shutdown();
            transport.close();
        };
    }, 'jsonrpc.serve');
}
'''


def _resume_plugin_path() -> str:
    """返回续聊插件文件；内核或插件缺失时返回空串。"""
    if not DSH_CORE:
        return ""
    entry = os.path.join(DSH_CORE, "zhiyan-sdk-resume.mjs")
    return entry if os.path.isfile(entry) else ""


def _ensure_resume_plugin() -> str:
    """把续聊插件写到内核根目录（相对路径加载，绕开包名解析），
    每次启动覆盖，内核升级后自动重建。"""
    if not DSH_CORE:
        return ""
    entry = os.path.join(DSH_CORE, "zhiyan-sdk-resume.mjs")
    try:
        with open(entry, "w", encoding="utf-8") as f:
            f.write(SDK_RESUME_PLUGIN_JS)
        return entry
    except Exception as e:
        logger.warning("[DSH] 写入会话续聊插件失败: %s", e)
        return ""


def _write_generated_config(cfg: Dict[str, Any]) -> str:
    """把 patch 覆盖层写入生成文件，返回文件路径。

    0.2 起该文件不再是完整组合树，而是叠加在 sdk profile 之上的 --patch 覆盖层。
    """
    import yaml
    _ensure_resume_plugin()
    patch = _build_patch(cfg)
    os.makedirs(DSH_RUNTIME, exist_ok=True)
    text = yaml.safe_dump(patch, allow_unicode=True,
                          default_flow_style=False, sort_keys=False)
    with open(DSH_GENERATED_CONFIG, "w", encoding="utf-8") as f:
        f.write("# Generated by ZhiYan DSH engine from settings.json -> \"dsh\" + runtime/dsh-plugins.json. Do not edit.\n")
        f.write("# Applied as a --patch overlay on top of the sdk profile (dsh-base + dsh-sdk-app).\n")
        f.write(text)
    return DSH_GENERATED_CONFIG


def _resolve_runtime_env() -> Dict[str, str]:
    """Build the child environment: isolate user-local dirs + feed credentials."""
    env = dict(os.environ)
    os.makedirs(DSH_RUNTIME, exist_ok=True)
    home = os.path.join(DSH_RUNTIME, "dsh_home")
    os.makedirs(home, exist_ok=True)
    env["DSH_HOME"] = home
    env["LOCALAPPDATA"] = home
    env["APPDATA"] = home
    env["NODE_PATH"] = DSH_NODE_MODULES
    cfg = get_dsh_settings()
    cwd = (cfg.get("cwd") or "").strip() or os.path.join(DSH_RUNTIME, "cwd")
    sess_root = (cfg.get("session_root") or "").strip() or os.path.join(DSH_RUNTIME, "sessions")
    os.makedirs(cwd, exist_ok=True)
    os.makedirs(sess_root, exist_ok=True)
    env["DSH_CWD"] = cwd
    env["DSH_SESSION_ROOT"] = sess_root
    # 上下文窗口与 max-tokens 成功判定（0.2 profile 会用它们初始化适配器）
    env["DSH_CONTEXT_WINDOW"] = str(DSH_CONTEXT_WINDOW_DEFAULT)
    env["DSH_MAX_TOKENS_AS_SUCCESS"] = "true"
    # 权限模式：同时驱动 sandbox-policy.mode 与 approval.policy，二者必须匹配
    # permission-presets 中的同一条预设，否则 permission 插件会因「无匹配预设」
    # 拒绝激活。默认 danger-full-access（无人应答审批）；workspace-write 下
    # 越权操作因没有应答者而 fail-closed（拒绝），可作为工作区白名单模式。
    env["DSH_PERMISSION_MODE"] = _permission_mode(cfg)
    if (cfg.get("system_prompt") or "").strip():
        env["DSH_SYSTEM_PROMPT"] = cfg["system_prompt"].strip()

    # ── 安全管控说明 ───────────────────────────────────────────────
    # DSH 的 shell 工具经 `pwsh -NoLogo -NoProfile -NonInteractive -Command …`
    # 执行（-NoProfile 禁用 PowerShell profile 注入），子进程由 node
    # `spawn(shell:false)` 拉起（无法执行 .cmd/.ps1 包装器），且 PowerShell 的
    # rm/del 是优先级高于 PATH 的内置 cmdlet —— 因此「用 PATH 垫片把 dsh 内
    # rm/del 重定向进回收站」在 shell 层不可实现，相关 shim 方案从未生效，已移除。
    # 实际生效的安全边界由以下已隔离项保证：
    #   1) 环境隔离：上文将 DSH_HOME/APPDATA/LOCALAPPDATA 指到 dsh-runtime/dsh_home；
    #   2) 工具白名单：仅覆盖层启用的 pwsh/fs/subagent/todo 可被模型调用；
    #   3) 受控工作目录：cwd、workspaceRoot 与会话持久化目录固定在 dsh-runtime 内。

    # 凭证/端点沿用 zhiyan 设置（与 patch 生成共用同一套解析，保证一致）
    base_url, api_key = _resolve_llm_credentials()
    if base_url:
        env["DEEPSEEK_BASE_URL"] = base_url
    env["DEEPSEEK_API_KEY"] = api_key
    return env


class DshEngine:
    """One managed DSH JSON-RPC subprocess with request/reply + event fan-out."""

    def __init__(self) -> None:
        self.proc: Optional[asyncio.subprocess.Process] = None
        self._reader_task: Optional[asyncio.Task] = None
        self._pending: Dict[int, asyncio.Future] = {}
        self._seq = 0
        self._subs: Dict[str, set] = {}
        self._started = False
        self._start_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self.model = "deepseek-flash"
        self.last_error: str = ""
        # 工具 callId -> 工具名（0.2 的 tool/result 只带 callId，需回查名字）
        self._tool_names: Dict[str, str] = {}
        # 运行日志环形缓冲（供管理页"内部日志"读取）
        self.stderr_log: List[str] = []
        self._stderr_log_limit = 500

    def log_tail(self, limit: int = 300) -> List[str]:
        return list(self.stderr_log[-limit:])

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self) -> None:
        """Start the runtime and complete the initialize handshake (idempotent)."""
        if self._started and self.alive:
            return
        async with self._start_lock:
            if self._started and self.alive:
                return
            # 环境预检(软件只负责内嵌内核与连通性控制,Node.js/依赖由用户手动准备)
            _init_dsh_paths()  # 重新解析内核目录(支持在设置里改 kernel_dir)
            if shutil.which("node") is None:
                raise RuntimeError(
                    "未检测到 Node.js。请先手动安装 Node.js(>=22.19)后再启动 DSH 内核；"
                    "软件仅负责内嵌内核与连通性控制,不代为安装运行环境。"
                )
            if not os.path.isfile(os.path.join(DSH_CORE, "package.json")):
                raise RuntimeError(
                    "未找到 DSH 内核。请确认软件内置 dsh-suite/npm-core 目录存在,"
                    "或在 DSH 设置里指定 kernel_dir。"
                )
            if not os.path.isfile(DSH_BIN):
                raise RuntimeError(
                    f"DSH 内核依赖未安装：请在 {DSH_CORE} 下执行一次 "
                    "`npm install` 后再启动（npm 发行包为免构建方案,安装后可整体移动）。"
                )
            # 由 settings.json -> "dsh" 动态生成 patch 覆盖层（工具开关/参数随保存生效）
            cfg = get_dsh_settings()
            patch_path = _write_generated_config(cfg)

            env = _resolve_runtime_env()
            s = get_settings()
            # 默认模型用 DeepSeek 现行名 deepseek-flash（= V4.1-Flash）；
            # 旧名 deepseek-chat / deepseek-reasoner 已于 2026-07-24 弃用。
            self.model = (cfg.get("model") or s.get("model") or "deepseek-flash")
            work_dir = (cfg.get("cwd") or "").strip() or env_dsh_cwd()
            os.makedirs(work_dir, exist_ok=True)

            # 0.2 唯一入口：CLI 选定 profile，再叠加生成的 --patch 覆盖层
            cmd = ["node", DSH_BIN, "--profile", DSH_PROFILE,
                   "--patch", patch_path]
            logger.info("[DSH] 启动 runtime: %s (cwd=%s)", " ".join(cmd), work_dir)
            self.proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=work_dir,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self.last_error = ""
            self._started = True
            self._reader_task = asyncio.create_task(self._reader_loop())
            # Kick a stderr pump so logs don't fill the pipe if buffered.
            self._stderr_task = asyncio.create_task(self._stderr_pump())

            try:
                await self._initialize()
            except Exception:
                # Best-effort teardown on handshake failure.
                await self.stop()
                raise
            logger.info("[DSH] initialize 完成，model=%s", self.model)

    async def _initialize(self) -> None:
        cfg = get_dsh_settings()
        cwd = (cfg.get("cwd") or "").strip() or env_dsh_cwd()
        provider = "deepseek-official"  # the only adapter the composition owns
        model = cfg.get("model") or self.model
        try:
            max_tokens = int(cfg.get("max_tokens") or 0)
        except (TypeError, ValueError):
            max_tokens = 0
        params: Dict[str, Any] = {"cwd": cwd, "provider": provider, "model": model}
        # 只有显式给了正数才封顶；否则沿用内核默认上限（避免 4096 这类过小值截断思考）
        if max_tokens > 0:
            params["maxTokens"] = max_tokens
        result = await self._request("initialize", params, timeout=180)
        if not (isinstance(result, dict) and result.get("serverInfo")):
            raise RuntimeError(f"DSH initialize 返回异常: {result!r}")
        self.model = model

    async def restart(self) -> None:
        """重启 runtime（重新生成组合配置并完整握手）。"""
        await self.stop()
        await self.start()

    def status(self) -> Dict[str, Any]:
        """当前运行时状态（供管理面板展示）。"""
        cfg = get_dsh_settings()
        return {
            "available": True,
            "running": self.alive,
            "model": self.model if self.alive else (cfg.get("model") or ""),
            "pid": self.proc.pid if (self.proc and self.proc.returncode is None) else None,
            "last_error": self.last_error,
            "cwd": (cfg.get("cwd") or "").strip() or env_dsh_cwd(),
            "session_root": (cfg.get("session_root") or "").strip()
                            or os.path.join(DSH_RUNTIME, "sessions"),
            "config_path": DSH_GENERATED_CONFIG,
            "kernel_dir": DSH_CORE,
            "profile": DSH_PROFILE,
            "node_detected": shutil.which("node") is not None,
            "enabled_tools": list(cfg.get("tools") or DSH_TOOL_OPTIONS),
            "thinking": bool(cfg.get("thinking", True)),
            "reasoning_effort": str(cfg.get("reasoning_effort") or "max"),
            "permission_mode": _permission_mode(cfg),
            "system_prompt": cfg.get("system_prompt") or "",
            "max_tokens": cfg.get("max_tokens"),
            "bash_timeout": int(cfg.get("bash_timeout") or 60000),
        }

    def _session_dir(self) -> str:
        cfg = get_dsh_settings()
        root = (cfg.get("session_root") or "").strip() or os.path.join(DSH_RUNTIME, "sessions")
        return os.path.normpath(root)

    def list_sessions(self) -> List[Dict[str, Any]]:
        """列出 DSH 持久化会话文件（按修改时间倒序）。

        持久化插件按 <root>/<project-key>/<session-id>/<日志文件> 的嵌套结构落盘。
        日志文件名随会话格式版本变化（0.1 为 session.jsonl[.zstd]，0.2 为
        session.v4.jsonl.zstd），故按 session*.jsonl* 通配匹配，兼容各版本。
        """
        root = self._session_dir()
        items: List[Dict[str, Any]] = []
        if not os.path.isdir(root):
            return items
        for project in os.listdir(root):
            pdir = os.path.join(root, project)
            if not os.path.isdir(pdir):
                continue
            for name in os.listdir(pdir):
                sdir = os.path.join(pdir, name)
                if not os.path.isdir(sdir):
                    continue
                matches = sorted(glob.glob(os.path.join(sdir, "session*.jsonl*")))
                if not matches:
                    continue
                logf = matches[0]
                st = os.stat(logf)
                items.append({
                    "name": name,
                    "project": project,
                    "path": logf,
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                })
        items.sort(key=lambda i: i.get("mtime", 0), reverse=True)
        return items

    def delete_session(self, name: str) -> bool:
        """删除单个会话（按 name 递归定位嵌套会话目录，防目录穿越）。"""
        root = self._session_dir()
        root = os.path.normpath(root)
        for item in self.list_sessions():
            if item.get("name") != name:
                continue
            sdir = os.path.dirname(item.get("path") or "")
            sdir = os.path.normpath(sdir)
            if os.path.commonpath([root, sdir]) != root:
                continue
            try:
                shutil.rmtree(sdir)
                return True
            except Exception:
                return False
        return False

    def clear_sessions(self) -> int:
        """清空所有会话目录，返回删除数量。"""
        root = self._session_dir()
        n = 0
        if os.path.isdir(root):
            for project in os.listdir(root):
                pdir = os.path.join(root, project)
                if not os.path.isdir(pdir):
                    continue
                for name in os.listdir(pdir):
                    sdir = os.path.join(pdir, name)
                    if os.path.isdir(sdir):
                        try:
                            shutil.rmtree(sdir)
                            n += 1
                        except Exception:
                            pass
        return n

    async def _reader_loop(self) -> None:
        assert self.proc and self.proc.stdout
        while True:
            try:
                raw = await self.proc.stdout.readline()
            except Exception:
                break
            if not raw:
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("[DSH] 非 JSON 输出: %s", line[:200])
                continue
            try:
                self._dispatch(msg)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("[DSH] 分发失败: %s", exc)

    async def _stderr_pump(self) -> None:
        assert self.proc and self.proc.stderr
        while True:
            try:
                raw = await self.proc.stderr.readline()
            except Exception:
                break
            if not raw:
                break
            text = raw.decode("utf-8", "replace").rstrip()
            if text:
                self.stderr_log.append(text)
                if len(self.stderr_log) > self._stderr_log_limit:
                    self.stderr_log = self.stderr_log[-self._stderr_log_limit:]
                logger.info("[DSH stderr] %s", text[:400])

    def _dispatch(self, msg: Dict[str, Any]) -> None:
        rid = msg.get("id")
        if rid is not None:
            fut = self._pending.pop(rid, None)
            if fut is None:
                return
            if "error" in msg:
                err = msg["error"]
                if not fut.done():
                    fut.set_exception(RuntimeError(
                        err.get("message") if isinstance(err, dict) else str(err)))
            else:
                if not fut.done():
                    fut.set_result(msg.get("result"))
            return
        method = msg.get("method")
        if method in ("session.event", "session.status"):
            params = msg.get("params") or {}
            session_id = str(params.get("sessionId") or "")
            if session_id:
                self._publish(session_id, {"method": method, **params})
        elif method in ("subagent.started", "subagent.finished"):
            logger.debug("[DSH] 通知 %s: %s", method, str(msg.get("params"))[:200])
        else:
            logger.debug("[DSH] 未处理通知: %s", str(msg)[:200])

    def _subscribe(self, session_id: str):
        q: asyncio.Queue = asyncio.Queue()
        self._subs.setdefault(session_id, set()).add(q)
        return q

    def _unsubscribe(self, session_id: str, q: asyncio.Queue) -> None:
        s = self._subs.get(session_id)
        if s:
            s.discard(q)
            if not s:
                self._subs.pop(session_id, None)

    def _publish(self, session_id: str, payload: Dict[str, Any]) -> None:
        for q in list(self._subs.get(session_id, ())):
            try:
                q.put_nowait(payload)
            except Exception:
                pass

    async def _request(self, method: str, params: Any = None, timeout: float = 120) -> Any:
        if not self.alive:
            raise RuntimeError("DSH runtime 已停止")
        self._seq += 1
        rid = self._seq
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[rid] = fut
        msg: Dict[str, Any] = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        try:
            async with self._write_lock:
                data = (json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
                assert self.proc and self.proc.stdin
                self.proc.stdin.write(data)
                await self.proc.stdin.drain()
            return await asyncio.wait_for(fut, timeout=timeout)
        finally:
            self._pending.pop(rid, None)

    def _remember_tool(self, call_id: str, name: str) -> None:
        """登记 callId -> 工具名（带上限，避免长进程内无限增长）。"""
        if not call_id or not name:
            return
        if len(self._tool_names) > 1000:
            for k in list(self._tool_names)[:500]:
                self._tool_names.pop(k, None)
        self._tool_names[call_id] = name

    def _map_events(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        """把一条 DSH 通知映射为零到多条 zhiyan SSE 事件。

        0.2 的事件形态与 0.1 不同:助手输出不再逐 token 推送 assistant/chunk,
        而是每个 step 给一条完整的 assistant/message(内含 reasoning / text /
        tool-call 三类内容块);工具结果放在 tool/result.message 里。
        """
        if payload.get("method") != "session.event":
            return []
        event = payload.get("event") or {}
        etype = event.get("type")
        data = event.get("data") or {}
        out: List[Dict[str, Any]] = []

        if etype == "assistant/message":
            blocks = ((data.get("message") or {}).get("content")) or []
            for blk in blocks:
                if not isinstance(blk, dict):
                    continue
                btype = str(blk.get("type") or "")
                if btype == "reasoning":
                    txt = str(blk.get("text") or "")
                    if txt:
                        out.append({"type": "reasoning", "content": txt})
                elif btype == "text":
                    txt = str(blk.get("text") or "")
                    if txt:
                        out.append({"type": "chunk", "text": txt})
                elif btype == "tool-call":
                    cid = str(blk.get("id") or "")
                    name = str(blk.get("name") or "")
                    self._remember_tool(cid, name)
                    raw = blk.get("arguments")
                    try:
                        args = json.loads(raw) if isinstance(raw, str) else (raw or {})
                    except Exception:
                        args = raw or {}
                    out.append({"type": "tool_call", "name": name, "args": args})
            return out

        if etype == "tool/call":
            # 仅登记 callId -> 工具名（工具卡片由 assistant/message 负责发出）
            self._remember_tool(str(data.get("callId") or ""), str(data.get("name") or ""))
            return []

        if etype == "tool/result":
            msg = data.get("message") or {}
            cid = str(msg.get("toolCallId") or "")
            content = msg.get("content")
            if isinstance(content, list):
                preview = "".join(
                    str(b.get("text", "")) for b in content
                    if isinstance(b, dict) and str(b.get("type")) == "text")
            else:
                preview = str(content or "")
            if not preview:
                preview = str(msg.get("text") or "")
            return [{"type": "tool_result",
                     "name": self._tool_names.get(cid, ""),
                     "success": not bool(msg.get("isError")),
                     "preview": preview[:300]}]

        if etype in ("step/start", "turn/start"):
            return [{"type": "thinking", "message": "正在思考..."}]

        if etype == "turn/end":
            reason = data.get("reason") or {}
            kind = str(reason.get("kind") or "")
            if kind == "error":
                err = reason.get("error") or {}
                detail = err.get("message") if isinstance(err, dict) else str(err)
                return [{"type": "error", "message": f"DSH 对话失败：{detail}"}]
            if kind == "max-tokens":
                # 单步输出撞到上限：思考/回复会被截在半句。必须显式告知，
                # 否则前端只看到「思考突然停住、任务没做完」而无任何解释。
                return [{
                    "type": "engine_notice", "level": "warn",
                    "title": "输出已达上限被截断",
                    "message": "本轮模型单次输出撞到 max_tokens 上限，思考链与回复在这里被截断。"
                               "请在 DSH 设置里把「每次回答最大 Tokens」调大（留 0 表示不限制），"
                               "然后重启 DSH 内核。",
                }]
            return []
        return []

    async def prompt_stream(self, session_id: str, text: str,
                            content_blocks: Optional[List[Dict[str, Any]]] = None):
        """Stream one DSH turn as zhiyan SSE events (async generator).

        DSH 0.2 每个 step 推送一条完整的 assistant/message（含 reasoning / text /
        tool-call 内容块），因此这里按 step 粒度即时转发，不再攒到整轮结束。
        reasoning 走 thinking 展示，text 直接作为正文 chunk 推送。

        content_blocks 可传入 SDK 原生内容块（text / image），用于图片多模态。
        """
        if not self.alive:
            await self.start()
        q = self._subscribe(session_id)
        collected: list = []        # 已推给前端的正文，用于收尾 done
        try:
            await self._request("session/prompt", {
                "sessionId": session_id,
                "contentBlocks": content_blocks or [{"type": "text", "text": text}],
            }, timeout=120)
            yield {"type": "thinking", "message": "正在思考..."}
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=300)
                except asyncio.TimeoutError:
                    yield {"type": "error", "message": "DSH 响应超时"}
                    break
                if payload.get("method") == "session.status":
                    if payload.get("status") == "idle":
                        break
                    continue
                for mapped in self._map_events(payload):
                    if mapped["type"] == "chunk":
                        collected.append(mapped["text"])
                    # 事件即刻转发，不再等整轮结束——避免"输出完才模拟流式"
                    yield mapped
        except Exception as exc:
            logger.warning("[DSH] prompt 流异常: %s", exc)
            yield {"type": "error", "message": f"DSH 对话失败：{exc}"}
        finally:
            self._unsubscribe(session_id, q)
            yield {"type": "done", "content": "".join(collected).strip()}

    async def stop(self) -> None:
        if self.proc is not None and self.alive:
            try:
                await asyncio.wait_for(
                    self._request("shutdown", None, timeout=10), timeout=12)
            except Exception:
                pass
        if self.proc is not None and self.proc.stdin:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
        if self.proc is not None and self.proc.returncode is None:
            try:
                self.proc.kill()
                await asyncio.wait_for(self.proc.wait(), timeout=5)
            except Exception:
                pass
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await asyncio.gather(self._reader_task, return_exceptions=True)
            except Exception:
                pass
        self._reader_task = None
        self._pending.clear()
        self._started = False


def env_dsh_cwd() -> str:
    os.makedirs(DSH_RUNTIME, exist_ok=True)
    cwd = os.path.join(DSH_RUNTIME, "cwd")
    os.makedirs(cwd, exist_ok=True)
    return cwd


# Singleton
_engine: Optional[DshEngine] = None


def get_dsh_engine() -> DshEngine:
    global _engine
    if _engine is None:
        _engine = DshEngine()
    return _engine


def new_session_id() -> str:
    return "zhiyan_" + uuid.uuid4().hex[:12]