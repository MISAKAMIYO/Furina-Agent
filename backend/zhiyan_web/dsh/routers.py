"""
DSH (DeepSeek Harness) API 路由。

将 DSH 作为新增对话引擎接入 zhiyan 的聊天系统，复用 chat 的 SSE 事件格式：
  thinking / reasoning / tool_call / tool_result / chunk / done / error

接口:
- GET   /api/dsh/status            - 运行时状态（含配置/工具/会话目录/用户插件数）
- POST  /api/dsh/start             - 启动 runtime（幂等）
- POST  /api/dsh/stop              - 停止 runtime
- POST  /api/dsh/restart           - 重启 runtime（重新生成组合配置）
- POST  /api/dsh/chat/stream       - 发送消息（SSE 流式）
- GET   /api/dsh/sessions          - 列出持久化会话文件
- DELETE /api/dsh/sessions         - 清空全部会话
- DELETE /api/dsh/sessions/{name}  - 删除单个会话
- GET   /api/dsh/version           - 内核版本信息
- GET   /api/dsh/kernel/update/check   - 检查内核更新（npm registry 版本比对）
- POST  /api/dsh/kernel/update         - 启动内核更新任务（后台执行、失败回滚）
- GET   /api/dsh/kernel/update/status  - 内核更新任务进度与实时日志
- GET   /api/dsh/plugins           - 插件列表（内置行 + 用户插件）
- POST  /api/dsh/plugins/install   - 从 npm 安装 DSH 插件（npm install）
- POST  /api/dsh/plugins           - 新增/内联一个用户插件
- PATCH /api/dsh/plugins/{id}      - 更新（启用/配置/备注）
- DELETE /api/dsh/plugins/{id}     - 删除用户插件（可卸载 npm uninstall）
- GET   /api/dsh/skills            - 列出 DSH Skills
- GET   /api/dsh/storage           - 各目录占用大小
- GET   /api/dsh/logs              - 运行时内部日志（stderr 环形缓冲）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
import urllib.parse
from datetime import datetime

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse

# 复用主软件聊天历史（全局列表 + 自动保存），保证 DSH 对话进入主软件历史记录
from zhiyan_web.chat.routers.chat import (
    chat_history,
    auto_save_chat_history,
    get_chat_manager,
    _try_pipeline_command,
    build_ai_message,
)
from zhiyan_web.config import get_settings
from zhiyan_web.dsh.engine import (
    get_dsh_engine,
    new_session_id,
    get_dsh_settings,
    get_user_dsh_plugins,
    save_user_dsh_plugins,
    sanitize_plugin_row,
    list_builtin_plugins,
    DSH_CORE,
    DSH_RUNTIME,
    DSH_PROFILE,
    DSH_PLUGINS_FILE,
)
from zhiyan_web.dsh.updater import (
    check_update as kernel_check_update,
    get_kernel_updater,
    current_kernel_version,
    npm_registry,
)

router = APIRouter(prefix="/api/dsh", tags=["dsh"])
logger = logging.getLogger("ZhiYanWeb")


# ═══════════════════════════════════════════════════════════════════════════
# 进程 / 版本
# ═══════════════════════════════════════════════════════════════════════════
@router.get("/status")
async def dsh_status():
    engine = get_dsh_engine()
    st = engine.status()
    st["user_plugins"] = len(get_user_dsh_plugins())
    return st


@router.post("/start")
async def dsh_start():
    engine = get_dsh_engine()
    try:
        await engine.start()
        return {"ok": True, **engine.status()}
    except Exception as exc:
        logger.exception("[DSH] start 失败")
        return {"ok": False, "error": str(exc)}


@router.post("/stop")
async def dsh_stop():
    engine = get_dsh_engine()
    await engine.stop()
    return {"ok": True, "running": False}


@router.post("/restart")
async def dsh_restart():
    engine = get_dsh_engine()
    try:
        await engine.restart()
        return {"ok": True, **engine.status()}
    except Exception as exc:
        logger.exception("[DSH] restart 失败")
        return {"ok": False, "error": str(exc)}


@router.get("/version")
async def dsh_version():
    # 真实内核版本来自 @deepseek-ai/dsh（npm-core/package.json 只是本软件的封装壳）
    version = current_kernel_version()
    return {
        "dshCoreVersion": version,
        "profile": DSH_PROFILE,
        "node": os.getenv("NODE_VERSION", ""),
        "coreDir": DSH_CORE,
        "runtimeDir": DSH_RUNTIME,
        "pluginsFile": DSH_PLUGINS_FILE,
        "npm": bool(shutil.which("npm")),
        "registry": npm_registry(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 内核自主更新（@deepseek-ai 官方 npm 发行包）
# ═══════════════════════════════════════════════════════════════════════════
@router.get("/kernel/update/check")
async def dsh_kernel_update_check(channel: str = "latest"):
    """检查内核更新：本地版本 / 渠道目标版本 / 可选版本列表（联网查 npm registry）。"""
    return await asyncio.to_thread(kernel_check_update, channel)


@router.post("/kernel/update")
async def dsh_kernel_update(body: dict = Body(default={})):
    """启动内核更新任务（后台执行）：停内核 -> 备份 -> 改版本 -> 装依赖 -> 校验 -> 重启。

    body: {version?: 目标版本, channel?: latest|next|custom, restart?: 是否自动重启}
    未指定 version 时按 channel 解析目标版本，失败自动回滚。
    """
    target = str(body.get("version") or "").strip()
    channel = str(body.get("channel") or "latest").strip()
    restart = bool(body.get("restart", True))
    return await get_kernel_updater().start_update(target, channel, restart)


@router.get("/kernel/update/status")
async def dsh_kernel_update_status():
    """更新任务状态（含进度步骤与实时日志，供前端轮询）。"""
    return get_kernel_updater().status()


# ═══════════════════════════════════════════════════════════════════════════
# DSH 插件系统（cordis 插件，独立于 zhiyan 自身插件系统）
# ═══════════════════════════════════════════════════════════════════════════
@router.get("/plugins")
async def dsh_plugins_list():
    engine = get_dsh_engine()
    builtins = list_builtin_plugins()
    user = get_user_dsh_plugins()
    return {
        "builtins": builtins,
        "user": user,
        "running": engine.alive,
        "pluginsFile": DSH_PLUGINS_FILE,
    }


def _name_to_id(name: str) -> str:
    base = name.rstrip("/")
    return base.split("/")[-1] or base


async def _run_npm(args, timeout: int = 600) -> dict:
    """在内置 npm 发行包内核(dsh-suite/npm-core)下执行 npm 子命令（用于安装/卸载 DSH 插件）。

    与内核同构的 npm 平铺布局：安装/卸载走 npm install/uninstall，保持 node_modules
    可整体移动、免构建的特性；不引入 pnpm 虚拟存储以免打乱发行包布局。
    """
    exe = shutil.which("npm")
    if not exe:
        return {"ok": False, "code": 1, "out": "",
                "err": "未检测到 npm（Node.js 自带）。请先安装 Node.js，或手动把插件放进 DSH 内核 node_modules 后内联添加。"}
    # 避免 npm 在安装/卸载时执行第三方包的 install 脚本造成意外；内核插件以预编译产物为主
    proc = await asyncio.create_subprocess_exec(
        exe, *args, "--no-audit", "--no-fund", "--no-save",
        cwd=DSH_CORE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return {"ok": False, "code": 1, "out": "", "err": "npm 执行超时（%ss）" % timeout}
    text = ((out or b"").decode("utf-8", "replace")
            + "\n"
            + (err or b"").decode("utf-8", "replace"))
    tail = "\n".join([l for l in text.splitlines() if l.strip()][-6:])
    return {"ok": proc.returncode == 0, "code": proc.returncode or 0, "out": tail, "err": tail}


@router.post("/plugins/install")
async def dsh_plugin_install(body: dict = Body(...)):
    """从 npm 安装 DSH 插件：在内置内核下 npm install <spec>，并加入用户插件清单。"""
    spec = str(body.get("spec") or body.get("name") or "").strip()
    enabled = bool(body.get("enabled", True))
    config = body.get("config") if isinstance(body.get("config"), dict) else {}
    if not spec or any(c in spec for c in "&|;<>`\"' "):
        raise HTTPException(status_code=400, detail="非法插件包名（npm 包名@版本 或 link:路径）")
    res = await _run_npm(["install", spec])
    if not res["ok"]:
        return {"ok": False, "message": "npm install 失败：\n" + res["err"], "installed": False}
    name = spec if spec.startswith("link:") else spec
    row = sanitize_plugin_row({"id": _name_to_id(name), "name": name,
                               "enabled": enabled, "config": config})
    if row is None:
        return {"ok": False, "message": "插件定义非法"}
    rows = get_user_dsh_plugins()
    rows = [r for r in rows if r.get("id") != row["id"]]
    rows.append(row)
    save_user_dsh_plugins(rows)
    return {"ok": True, "installed": True, "message": f"插件 {spec} 已安装并启用。需重启 DSH 生效。",
            "row": row}


@router.post("/plugins")
async def dsh_plugin_add(body: dict = Body(...)):
    """新增一个用户 DSH 插件行（内联；若 install=True 先执行 npm install）。"""
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="缺少插件名")
    install = bool(body.get("install", False))
    config = body.get("config") if isinstance(body.get("config"), dict) else {}
    enabled = bool(body.get("enabled", True))
    note = str(body.get("note") or "")
    if install:
        if any(c in name for c in "&|;<>`\"' "):
            raise HTTPException(status_code=400, detail="非法插件包名")
        res = await _run_npm(["install", name])
        if not res["ok"]:
            return {"ok": False, "message": "npm install 失败：\n" + res["err"]}
    row = sanitize_plugin_row({
        "id": str(body.get("id") or "").strip() or _name_to_id(name),
        "name": name, "enabled": enabled, "config": config, "note": note})
    if row is None:
        raise HTTPException(status_code=400, detail="插件定义非法")
    rows = get_user_dsh_plugins()
    if any(r.get("id") == row["id"] for r in rows):
        raise HTTPException(status_code=409, detail=f"插件 id='{row['id']}' 已存在")
    rows.append(row)
    save_user_dsh_plugins(rows)
    return {"ok": True, "row": row,
            "message": "已添加用户插件：%s（需重启 DSH 生效）" % row["name"]}


@router.patch("/plugins/{plugin_id}")
async def dsh_plugin_update(plugin_id: str, body: dict = Body(...)):
    """更新用户插件（启用开关 / 配置 / 备注）。"""
    rows = get_user_dsh_plugins()
    target = next((r for r in rows if str(r.get("id")) == plugin_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="插件不存在")
    if "enabled" in body and isinstance(body.get("enabled"), bool):
        target["enabled"] = body["enabled"]
    if "config" in body and isinstance(body.get("config"), dict):
        target["config"] = body["config"]
    if "note" in body and isinstance(body.get("note"), str):
        target["note"] = body["note"]
    target = sanitize_plugin_row(target)
    rows = [sanitize_plugin_row(r) for r in rows]
    save_user_dsh_plugins([r for r in rows if r is not None])
    return {"ok": True, "row": target, "message": "已更新插件（需重启 DSH 生效）"}


@router.delete("/plugins/{plugin_id}")
async def dsh_plugin_delete(plugin_id: str, body: dict = Body(default={})):
    """删除用户插件；uninstall=True 时先 npm uninstall。"""
    rows = get_user_dsh_plugins()
    target = next((r for r in rows if str(r.get("id")) == plugin_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail="插件不存在")
    rows = [r for r in rows if str(r.get("id")) != plugin_id]
    save_user_dsh_plugins(rows)
    msg = f"已移除插件 {plugin_id}"
    if body and bool(body.get("uninstall", False)):
        name = str(target.get("name") or "")
        if name and not name.startswith("link:"):
            res = await _run_npm(["uninstall", name])
            msg += "；npm uninstall：" + ("成功" if res["ok"] else "失败\n" + res["err"])
    return {"ok": True, "message": msg}


# ═══════════════════════════════════════════════════════════════════════════
# Skills / Storage / Logs
# ═══════════════════════════════════════════════════════════════════════════
def _dsh_home_dir() -> str:
    return os.path.join(DSH_RUNTIME, "dsh_home")


def _read_skill_desc(md: str, limit: int = 2000) -> str:
    try:
        head = open(md, "r", encoding="utf-8").read()[:limit]
        import re
        m = re.search(r"^description:\s*(.+)$", head, re.MULTILINE)
        return (m.group(1).strip() if m else "")[:160]
    except Exception:
        return ""


@router.get("/skills")
async def dsh_skills():
    sources = [
        {"source": "运行时用户 Skills", "dir": os.path.join(_dsh_home_dir(), "skills")},
        {"source": "本体仓库 Skills (.agents/skills)", "dir": os.path.join(DSH_CORE, ".agents", "skills")},
    ]
    skills = []
    for src in sources:
        d = src["dir"]
        if not os.path.isdir(d):
            continue
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if not os.path.isdir(p):
                continue
            md = os.path.join(p, "SKILL.md")
            desc = _read_skill_desc(md) if os.path.exists(md) else ""
            skills.append({"name": name, "source": src["source"], "description": desc})
    return {"skills": skills, "count": len(skills)}


def _dir_size_mb(p: str, cap_bytes: int = 800 * 1024 * 1024) -> float:
    total = 0
    for root, dirs, files in os.walk(p):
        dirs[:] = [d for d in dirs if d not in ("node_modules", ".cache", "__pycache__")]
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except Exception:
                pass
            if total > cap_bytes:
                return round(cap_bytes / 1048576, 1)
    return round(total / 1048576, 1)


@router.get("/storage")
async def dsh_storage():
    home = _dsh_home_dir()
    cfg = get_dsh_settings()
    cwd = (cfg.get("cwd") or "").strip() or os.path.join(DSH_RUNTIME, "cwd")
    sess_root = (cfg.get("session_root") or "").strip() or os.path.join(DSH_RUNTIME, "sessions")
    folders = [
        {"key": "core", "label": "DSH 内核 (内置 npm-core)", "path": DSH_CORE, "exists": os.path.isdir(DSH_CORE)},
        {"key": "runtime", "label": "运行时数据 (dsh-runtime)", "path": DSH_RUNTIME, "exists": os.path.isdir(DSH_RUNTIME)},
        {"key": "home", "label": "DSH 主目录 (dsh_home)", "path": home, "exists": os.path.isdir(home)},
        {"key": "cwd", "label": "工作目录 cwd", "path": cwd, "exists": os.path.isdir(cwd)},
        {"key": "sessions", "label": "会话目录", "path": sess_root, "exists": os.path.isdir(sess_root)},
        {"key": "plugins", "label": "本机插件集", "path": DSH_RUNTIME, "exists": True},
    ]
    for f in folders:
        f["sizeMB"] = _dir_size_mb(f["path"]) if f["exists"] else 0
    return {"folders": folders}


@router.get("/logs")
async def dsh_logs():
    engine = get_dsh_engine()
    lines = engine.log_tail(300)
    return {"lines": lines, "running": engine.alive, "count": len(lines)}


# ═══════════════════════════════════════════════════════════════════════════
# 市场 / Tokens / 打开文件夹 / 更新
# ═══════════════════════════════════════════════════════════════════════════
async def _market_http(url: str, timeout: float = 15.0):
    """发起外部市场请求(优先走配置的 github_proxy 代理, 否则直连)。"""
    import httpx
    base = (get_settings().get("github_proxy") or "").strip() or ""
    full = (base.rstrip("/") + "/" + url) if base else url
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
        return await c.get(full)


@router.get("/market/search")
async def dsh_market_search(
    type: str = "plugin", q: str = "",
):
    """市场搜索:type=plugin 查 npm,type=skill 查 GitHub 仓库,type=skin 查 npm 皮肤包。"""
    q = str(q)[:60].strip()
    try:
        if type == "skill":
            return await _market_search_skill(q)
        if type == "skin":
            return await _market_search_npm("dsh " + (q or "skin theme"), is_skin=True)
        return await _market_search_npm("dsh " + q, is_skin=False)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[DSH] 市场搜索失败: %s", exc)
        return {"items": [], "message": f"市场搜索失败: {exc}"}


async def _market_search_npm(text: str, is_skin: bool):
    import httpx
    url = "https://registry.npmjs.org/-/v1/search?size=20&text=" + \
        urllib.parse.quote(text)
    base = (get_settings().get("github_proxy") or "").strip() or ""
    full = (base.rstrip("/") + "/" + url) if base else url
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
        r = await c.get(full)
        data = r.json()
    is_dsh = lambda pk: re.search(r"(dsh|deepseek)", (pk.get("name", "") + " " + (pk.get("description") or "") + " " + " ".join(pk.get("keywords") or [])), re.I)
    is_skin_pkg = lambda pk: re.search(r"(skin|theme|皮肤|换肤)", (pk.get("name", "") + " " + (pk.get("description") or "")), re.I)
    items = [
        {"name": o["package"]["name"], "version": o["package"].get("version", ""),
         "description": (o["package"].get("description") or "")[:140],
         "kind": "skin" if is_skin else "plugin", "stars": 0,
         "url": (o["package"].get("links") or {}).get("npm", "")}
        for o in (data.get("objects") or [])
        if is_dsh(o.get("package", {})) and (not is_skin or is_skin_pkg(o.get("package", {})))
    ][:16]
    return {"items": items, "message": ""}


async def _market_search_skill(q: str):
    import httpx
    url = "https://api.github.com/search/repositories?per_page=12&q=" + \
        urllib.parse.quote((q + " " if q else "") + "dsh skill in:name,description,topics")
    base = (get_settings().get("github_proxy") or "").strip() or ""
    full = (base.rstrip("/") + "/" + url) if base else url
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
        r = await c.get(full, headers={"user-agent": "zhiyan-dsh-launcher", "accept": "application/vnd.github+json"})
        data = r.json()
    items = [
        {"name": it.get("full_name", ""), "version": it.get("default_branch", ""),
         "description": (it.get("description") or "")[:140],
         "kind": "skill", "stars": it.get("stargazers_count", 0),
         "url": it.get("html_url", "")}
        for it in (data.get("items") or [])
    ][:12]
    return {"items": items, "message": ""}


@router.post("/market/install")
async def dsh_market_install(body: dict = Body(...)):
    """安装市场项:plugin=npm install;skill=GitHub zip 解压到用户 skills;skin=npm install。"""
    kind = str(body.get("kind") or "plugin")
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="缺少名称")
    if re.search(r"[&|;<>`\"' ]", name):
        raise HTTPException(status_code=400, detail="非法包名/仓库名")

    if kind == "skill":
        return await _install_market_skill(name)
    # plugin / skin → npm install + 写入用户插件清单
    res = await _run_npm(["install", name])
    if not res["ok"]:
        return {"ok": False, "message": "npm install 失败：\n" + res["err"]}
    row = sanitize_plugin_row({"id": _name_to_id(name), "name": name,
                               "enabled": True, "config": {},
                               "note": "来自插件市场"})
    if row is None:
        return {"ok": True, "installed": True, "message": "npm install 成功，但插件定义非法"}
    rows = get_user_dsh_plugins()
    rows = [r for r in rows if r.get("id") != row["id"]]
    rows.append(row)
    save_user_dsh_plugins(rows)
    return {"ok": True, "installed": True, "message": f"已安装 {kind}：{name}，需重启 DSH 生效"}


# 使用前置声明避免循环依赖(github_proxy 与 market 独立)
async def _github_fetch_bytes(url: str, timeout: float = 60.0):
    import httpx
    base = (get_settings().get("github_proxy") or "").strip() or ""
    full = (base.rstrip("/") + "/" + url) if base else url
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
        r = await c.get(full)
        r.raise_for_status()
        return r.content


async def _install_market_skill(spec: str):
    """owner/repo → codeload zip → 解压到 <runtime>/dsh_home/skills/<repo>。"""
    if not re.match(r"^[\w.-]+/[\w.-]+$", spec):
        raise HTTPException(status_code=400, detail="需要 owner/repo 形式，如 deepseek-ai/dsh-skill-xxx")
    dest = os.path.join(_dsh_home_dir(), "skills")
    os.makedirs(dest, exist_ok=True)
    import io, zipfile
    try:
        raw = await _github_fetch_bytes("https://codeload.github.com/" + spec + "/zip/refs/heads/HEAD")
        zi = zipfile.ZipFile(io.BytesIO(raw))
        members = zi.namelist()
        # 取 zip 根(通常是 repo-branch)目录下的内容
        if not members:
            return {"ok": False, "message": "下载内容为空"}
        root = members[0].split("/")[0] + "/"
        repo = spec.split("/")[-1]
        target = os.path.join(dest, repo)
        os.makedirs(target, exist_ok=True)
        # 安全解压：只取 zip 根名一致的条目
        prefix = root
        n = 0
        for m in members:
            if not m.startswith(prefix):
                continue
            rel = m[len(prefix):]
            if not rel or rel.endswith("/"):
                continue
            out = os.path.join(target, rel)
            out = os.path.abspath(out)
            if not out.startswith(os.path.abspath(target) + os.sep):
                continue
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "wb") as f:
                f.write(zi.read(m))
            n += 1
        return {"ok": True, "installed": True, "extracted": n,
                "message": f"Skill {repo} 已解压到用户 skills 目录（含 {n} 个文件）"}
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[DSH] skill 安装失败: %s", exc)
        return {"ok": False, "message": f"安装失败：{exc}"}


@router.get("/tokens")
async def dsh_tokens(range: str = "all"):
    """读取 cost-meter 账本做用量统计(range=all|30|7)，无账本返回空结构。"""
    ledger_path = os.path.join(DSH_RUNTIME, "dsh_home", "storages", "cost-meter", "ledger.json")
    days = []
    try:
        if os.path.exists(ledger_path):
            with open(ledger_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            days = [d for d in (data.get("days") or {}).values() if isinstance(d, dict) and d.get("date")]
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("[DSH] 读取账本失败: %s", exc)
    return _compute_tokens(days, range)


def _compute_tokens(days, range):
    import datetime
    day_key = lambda ms: datetime.datetime.fromtimestamp(ms).strftime("%Y-%m-%d") if isinstance(ms, (int, float)) else str(ms)
    today = datetime.date.today().isoformat()
    cutoff = ""
    if range in ("30", "7"):
        cutoff = (datetime.date.today() - datetime.timedelta(days=(29 if range == "30" else 6))).isoformat()
    days = [d for d in days if str(d.get("date", "")) >= cutoff]
    tok = lambda d: (d.get("input") or 0) + (d.get("output") or 0) + (d.get("cacheRead") or 0) + (d.get("cacheWrite") or 0) + (d.get("reasoning") or 0)
    totalTokens = sum(tok(d) for d in days)
    messages = sum(d.get("calls") or 0 for d in days)
    cost = round(sum(d.get("cost") or 0 for d in days) * 100) / 100
    active = set(d.get("date") for d in days if tok(d) > 0)
    # 按模型汇总
    by_model = {}
    for d in days:
        for pm, v in (d.get("byProviderModel") or {}).items():
            cur = by_model.setdefault(pm, {"calls": 0, "hit": 0, "miss": 0, "output": 0, "tokens": 0, "cost": 0})
            cur["calls"] += v.get("calls") or 0
            cur["hit"] += (v.get("cacheRead") or 0) + (v.get("cacheWrite") or 0)
            cur["miss"] += v.get("input") or 0
            cur["output"] += v.get("output") or 0
            cur["tokens"] += (v.get("input") or 0) + (v.get("output") or 0) + (v.get("cacheRead") or 0) + (v.get("cacheWrite") or 0) + (v.get("reasoning") or 0)
            cur["cost"] += v.get("cost") or 0
    models = [{"model": m, **v, "cost": round(v["cost"] * 10000) / 10000} for m, v in by_model.items()]
    models.sort(key=lambda x: x["tokens"], reverse=True)
    # 每日明细
    daily = sorted(days, key=lambda d: d.get("date", ""), reverse=True)[:60]
    return {
        "overview": {
            "messages": messages, "totalTokens": totalTokens, "totalCost": cost,
            "activeDays": len(active), "favoriteModel": models[0]["model"].split("/")[-1] if models else "—",
            "busiestDay": (max(days, key=tok, default={}).get("date") or "—"),
        },
        "models": models,
        "daily": daily,
    }


@router.post("/open")
async def dsh_open_folder(body: dict = Body(...)):
    """在系统文件管理器中打开指定目录。key ∈ core|runtime|home|cwd|sessions|plugins|skills"""
    key = str(body.get("key") or "").strip()
    folder = {
        "core": DSH_CORE,
        "runtime": DSH_RUNTIME,
        "home": _dsh_home_dir(),
        "cwd": (get_dsh_settings().get("cwd") or "").strip() or os.path.join(DSH_RUNTIME, "cwd"),
        "sessions": os.path.join(DSH_RUNTIME, "sessions"),
        "skillshome": os.path.join(_dsh_home_dir(), "skills"),
        "plugins": DSH_RUNTIME,
    }.get(key)
    if not folder:
        raise HTTPException(status_code=400, detail="未知目录 key")
    os.makedirs(folder, exist_ok=True)
    try:
        if os.name == "nt":
            import subprocess
            subprocess.Popen(["explorer.exe", os.path.normpath(folder)])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", folder])
        return {"ok": True, "message": "已打开目录：" + folder}
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "message": f"打开目录失败：{exc}"}


# ═══════════════════════════════════════════════════════════════════════════
# 会话
# ═══════════════════════════════════════════════════════════════════════════
@router.get("/sessions")
async def dsh_sessions():
    engine = get_dsh_engine()
    items = engine.list_sessions()
    for it in items:
        it["size_desc"] = _human_size(it.get("size", 0))
        it["mtime_desc"] = _ts_desc(it.get("mtime", 0))
    return {"sessions": items, "count": len(items), "root": engine._session_dir()}


@router.delete("/sessions")
async def dsh_sessions_clear():
    engine = get_dsh_engine()
    n = engine.clear_sessions()
    return {"ok": True, "deleted": n}


@router.delete("/sessions/{name}")
async def dsh_session_delete(name: str):
    engine = get_dsh_engine()
    ok = engine.delete_session(name)
    if not ok:
        raise HTTPException(status_code=404, detail="会话文件不存在")
    return {"ok": True}


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _ts_desc(ts: float) -> str:
    import datetime
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "--"


def _dsh_content_blocks(message: str):
    """把正文里的 [img]名称|url[/img] 哨兵转成 SDK 原生内容块（text + image）。

    图片无法解析时退化为纯文本块，保证请求不因附件失败。
    """
    blocks = [{"type": "text", "text": message}]
    if "[img]" not in (message or ""):
        return blocks
    try:
        from zhiyan_web.core.llm.providers import _IMG_SENTINEL, _image_url_to_data_url
    except Exception:
        return blocks
    for _name, url in _IMG_SENTINEL.findall(message):
        data_url = _image_url_to_data_url(url)
        if not data_url or not data_url.startswith("data:"):
            continue
        try:
            head, b64 = data_url[5:].split(";base64,", 1)
        except ValueError:
            continue
        if head and b64:
            blocks.append({"type": "image", "data": b64, "mimeType": head})
    return blocks


@router.post("/chat/stream")
async def dsh_chat_stream(
    body: dict = Body(...),
):
    message = str(body.get("message") or "").strip()
    session_id = str(body.get("session_id") or "") or new_session_id()
    if not message:
        return StreamingResponse(_sse([{"type": "error", "message": "消息不能为空"}]))

    engine = get_dsh_engine()
    content_blocks = _dsh_content_blocks(message)

    # 记录用户消息到主软件聊天历史
    user_msg = {"role": "user", "content": message, "time": datetime.now().isoformat()}
    chat_history.append(user_msg)
    auto_save_chat_history()

    started_at = time.monotonic()

    def _elapsed_ms() -> int:
        return int((time.monotonic() - started_at) * 1000)

    async def event_generator():
        full_content = ""
        try:
            # 命令消息（/ ! # 前缀）先走管道（插件 / miao-plugin），
            # 保证 # 开头的 miao 命令不会被 LLM 直接拦截。
            cm = get_chat_manager()
            handled, cmd_response = await _try_pipeline_command(cm, message)
            if handled:
                if cmd_response:
                    full_content += cmd_response
                    yield f"event: chunk\ndata: {json.dumps({'type': 'chunk', 'text': cmd_response}, ensure_ascii=False)}\n\n"
                elapsed = _elapsed_ms()
                yield f"event: done\ndata: {json.dumps({'type': 'done', 'content': full_content, 'think_ms': elapsed}, ensure_ascii=False)}\n\n"
                if full_content:
                    chat_history.append(build_ai_message(full_content, elapsed))
                    auto_save_chat_history()
                return
            # 非流式失败（如 runtime 未就绪）在生成器内捕获并输出 error 事件
            async for event in engine.prompt_stream(session_id, message,
                                                    content_blocks=content_blocks):
                event_type = event.get("type", "")
                if event_type == "done":
                    # 回传最终思考耗时，前端据此定格，与落盘数值保持一致
                    event["think_ms"] = _elapsed_ms()
                data = json.dumps(event, ensure_ascii=False)
                yield f"event: {event_type}\ndata: {data}\n\n"
                if event_type == "chunk":
                    full_content += event.get("text", "")
                elif event_type == "done":
                    # 保存最终回复到主软件聊天历史
                    done_content = event.get("content")
                    if done_content:
                        full_content = done_content
                    if full_content:
                        chat_history.append(build_ai_message(full_content, event.get("think_ms", 0)))
                        auto_save_chat_history()
        except asyncio.CancelledError:
            logger.info("[DSH] 流式输出被客户端终止")
            if full_content:
                full_content += "\n\n（生成已终止）"
                chat_history.append(build_ai_message(full_content, _elapsed_ms()))
                auto_save_chat_history()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _sse(events):
    for ev in events:
        yield f"event: {ev.get('type','')}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"