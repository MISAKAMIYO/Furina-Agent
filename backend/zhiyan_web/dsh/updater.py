"""DSH 内核（@deepseek-ai 官方 npm 发行包）自主更新。

软件内置的 DSH 内核位于 dsh-suite/npm-core，本质是官方 npm 发行包（免构建、
可整体移动）。本模块把「升级内核」这件事从手工改 package.json + 跑 npm 变成
一条可自助完成、失败可回滚的链路：

  检查更新 -> 读 npm registry 的 dist-tags 与版本列表，与本地内核版本比较
  执行更新 -> 停止内核 -> 备份 package.json -> 改写 @deepseek-ai/dsh* 版本
             -> npm install -> 扫描并补齐缺失依赖（最多 3 轮）
             -> dump-config 校验 -> 重启内核
             任一步失败则回滚 package.json 并重装回旧版本

两个必须遵守的约束（沿用既有内核方案，改动前务必先读）：

1. 本 monorepo 的 @deepseek-ai/dsh* 全部同版本号发布，因此批量改写这些包的
   版本号即可锁定整套内核；而 @deepseek-ai/cordis、cosmokit、cordis-plugin-*
   等有独立版本号，必须保持不动。
2. 依赖必须显式平铺在 npm-core/package.json 里。dsh-tools 等包一旦出现多份物理
   副本，Symbol 身份不相等，工具调度服务（TOOL_RUNTIME_SCHEDULER）查找会失败，
   表现为工具调用报 TOOL_OUTCOME_UNKNOWN。故安装后用 _scan_deps.mjs 扫描根部
   无法解析的 @deepseek-ai 包并补进 dependencies 再装一轮。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from zhiyan_web.dsh import engine as engine_mod

logger = logging.getLogger("ZhiYanWeb")

# 内核发行包名（npm 上真正的主体包；npm-core/package.json 只是本软件的封装壳）
DIST_PACKAGE = "@deepseek-ai/dsh"

# npm install 上限：全量 600+ 包，给足 30 分钟
NPM_INSTALL_TIMEOUT = 1800
# 改用例：registry 查询超时
REGISTRY_TIMEOUT = 30

_CREATE_NO_WINDOW = 0x08000000
_SPAWN_KW: Dict[str, Any] = {}
if os.name == "nt":  # 避免弹出系统控制台黑框
    _SPAWN_KW["creationflags"] = _CREATE_NO_WINDOW

_registry_cache = ""


# ═══════════════════════════════════════════════════════════════════════════
# 版本号工具（支持 prerelease：0.2.0-rc.2 < 0.2.0-rc.10 < 0.2.0）
# ═══════════════════════════════════════════════════════════════════════════
def _split_version(v: str):
    """把版本串拆成 (数字段, 预发布段) 以便比较。预发布段内数字按数值比大小。"""
    raw = (v or "").strip().lstrip("v").split("+")[0]
    core, _, pre = raw.partition("-")
    nums: List[int] = []
    for seg in core.split("."):
        nums.append(int(seg) if seg.isdigit() else 0)
    while len(nums) < 3:
        nums.append(0)
    pres = []
    if pre:
        for seg in re.split(r"[.\-]", pre):
            pres.append((0, int(seg), "") if seg.isdigit() else (1, 0, seg))
    return nums, pres


def cmp_version(a: str, b: str) -> int:
    """语义化比较：a>b 返回 1，a<b 返回 -1，相等返回 0（release 大于同号 prerelease）。"""
    an, ap = _split_version(a)
    bn, bp = _split_version(b)
    if an != bn:
        return -1 if an < bn else 1
    if not ap and not bp:
        return 0
    if not ap:
        return 1
    if not bp:
        return -1
    if ap == bp:
        return 0
    return -1 if ap < bp else 1


def _sort_desc(versions: List[str]) -> List[str]:
    import functools
    return sorted(set(v for v in versions if v),
                  key=functools.cmp_to_key(lambda x, y: -cmp_version(x, y)))


# ═══════════════════════════════════════════════════════════════════════════
# 路径与 registry
# ═══════════════════════════════════════════════════════════════════════════
def _core_dir() -> str:
    """当前内核目录（每次重新解析，支持设置里改 kernel_dir）。"""
    engine_mod._init_dsh_paths()
    return engine_mod.DSH_CORE


def _bin_path() -> str:
    return os.path.join(_core_dir(), "node_modules", "@deepseek-ai", "dsh", "lib", "bin.js")


def current_kernel_version() -> str:
    """本地真实内核版本（读 node_modules 里 @deepseek-ai/dsh 的 package.json）。"""
    core = _core_dir()
    if not core:
        return ""
    pkg = os.path.join(core, "node_modules", "@deepseek-ai", "dsh", "package.json")
    try:
        with open(pkg, "r", encoding="utf-8") as f:
            return str(json.load(f).get("version") or "")
    except Exception:
        return ""


def _npm_cmd() -> List[str]:
    """npm 的可执行命令前缀。

    Windows 上 npm 实际是 npm.cmd，CreateProcess（asyncio.create_subprocess_exec）
    不会按 PATHEXT 解析 .cmd，直接传 "npm" 会抛 WinError 2。因此：
    1) 优先用 node + npm-cli.js（最稳，不依赖 cmd 解析）；
    2) 退化为 cmd /c npm(.cmd)；
    3) 非 Windows 直接用 npm。
    """
    node = shutil.which("node")
    npm = shutil.which("npm")
    if node and npm:
        cli = os.path.join(os.path.dirname(npm), "node_modules", "npm", "bin", "npm-cli.js")
        if os.path.isfile(cli):
            return [node, cli]
    if os.name == "nt":
        return [os.environ.get("COMSPEC") or "cmd.exe", "/c", npm or "npm"]
    return [npm or "npm"]


def npm_registry() -> str:
    """npm registry 地址：优先读本机 npm 配置（兼容国内镜像），失败回落官方源。"""
    global _registry_cache
    if _registry_cache:
        return _registry_cache
    reg = ""
    exe = shutil.which("npm")
    if exe:
        try:
            out = subprocess.run(_npm_cmd() + ["config", "get", "registry"],
                                 capture_output=True, text=True, timeout=15,
                                 **_SPAWN_KW)
            reg = (out.stdout or "").strip()
        except Exception:
            reg = ""
    if not reg.startswith("http"):
        reg = "https://registry.npmjs.org"
    _registry_cache = reg.rstrip("/")
    return _registry_cache


def _fetch_packument(name: str) -> Dict[str, Any]:
    """同步拉取 npm 包元数据（调用方自行放到线程里，避免阻塞事件循环）。"""
    url = npm_registry() + "/" + urllib.parse.quote(name, safe="@")
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.npm.install-v1+json, application/json",
        "User-Agent": "zhiyan-dsh-updater",
    })
    with urllib.request.urlopen(req, timeout=REGISTRY_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def check_update(channel: str = "latest") -> Dict[str, Any]:
    """检查内核更新：返回本地版本、渠道目标版本与可选版本列表。"""
    cur = current_kernel_version()
    info: Dict[str, Any] = {
        "current": cur,
        "channel": channel or "latest",
        "registry": npm_registry(),
        "latest": "",
        "target": "",
        "available": False,
        "tags": {},
        "versions": [],
        "error": "",
    }
    try:
        doc = _fetch_packument(DIST_PACKAGE)
    except Exception as exc:
        info["error"] = "读取 npm 版本信息失败：%s" % exc
        return info

    tags = doc.get("dist-tags") or {}
    info["tags"] = {str(k): str(v) for k, v in tags.items()}
    info["latest"] = str(tags.get("latest") or "")

    versions = _sort_desc(list((doc.get("versions") or {}).keys()))
    info["versions"] = versions[:60]

    tag = info["channel"]
    target = ""
    if tag in tags:
        target = str(tags[tag])
    elif tag and tag not in ("custom", ""):
        # 渠道不存在（如某些版本没有 next/rc tag）时回落 latest
        target = info["latest"]
    else:
        target = info["latest"]
    info["target"] = target
    if target and cur:
        info["available"] = cmp_version(target, cur) > 0
    elif target and not cur:
        info["available"] = True
    return info


# ═══════════════════════════════════════════════════════════════════════════
# package.json 改写
# ═══════════════════════════════════════════════════════════════════════════
def _pkg_path() -> str:
    return os.path.join(_core_dir(), "package.json")


def _load_pkg() -> Dict[str, Any]:
    with open(_pkg_path(), "r", encoding="utf-8") as f:
        return json.load(f)


def _save_pkg(data: Dict[str, Any]) -> None:
    p = _pkg_path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, p)


def _is_dsh_pkg(name: str) -> bool:
    """是否属于同版本号发布的 DSH 内核主体包（含 dsh 与全部 dsh-*）。"""
    return name == "@deepseek-ai/dsh" or name.startswith("@deepseek-ai/dsh-")


def rewrite_versions(target: str) -> Dict[str, Any]:
    """把 package.json 中所有 DSH 主体包版本改写为 target，并同步 allowScripts 键。

    返回 {changed, total, allow} 供日志展示。
    """
    data = _load_pkg()
    deps = data.get("dependencies") or {}
    changed = 0
    total = 0
    for name in list(deps.keys()):
        if not _is_dsh_pkg(name):
            continue
        total += 1
        if str(deps.get(name)) != target:
            deps[name] = target
            changed += 1
    data["dependencies"] = deps

    # allowScripts 的键含版本号（如 @deepseek-ai/dsh-subprocess-local@0.2.0-rc.2），
    # 版本变了旧键就失效，需一并改写，否则该包的 install 脚本不再被批准执行。
    allow = data.get("allowScripts")
    if isinstance(allow, dict):
        for key in list(allow.keys()):
            if "@" not in key:
                continue
            pkg_name = key.rsplit("@", 1)[0]
            if _is_dsh_pkg(pkg_name):
                allow.pop(key)
                allow["%s@%s" % (pkg_name, target)] = True
        data["allowScripts"] = allow

    _save_pkg(data)
    return {"changed": changed, "total": total, "target": target}


def add_missing_deps(names: List[str], target: str) -> List[str]:
    """把缺失的 @deepseek-ai 包补进 dependencies。

    dsh* 主体包统一用 target 版本；其余（cordis 系等独立版本包）按 target 不存在
    时回落该包自身的 latest，避免写死一个不存在的版本导致安装失败。
    返回实际新增的包名。
    """
    data = _load_pkg()
    deps = data.get("dependencies") or {}
    added: List[str] = []
    for name in names:
        if name in deps:
            continue
        ver = target
        if not _is_dsh_pkg(name):
            ver = target
            try:
                doc = _fetch_packument(name)
                vs = doc.get("versions") or {}
                if target in vs:
                    ver = target
                else:
                    ver = str((doc.get("dist-tags") or {}).get("latest") or target)
            except Exception:
                ver = target
        deps[name] = ver
        added.append("%s@%s" % (name, ver))
    if added:
        data["dependencies"] = deps
        _save_pkg(data)
    return added


# ═══════════════════════════════════════════════════════════════════════════
# 子进程执行（npm / node 扫描，输出实时进日志）
# ═══════════════════════════════════════════════════════════════════════════
async def _run_stream(cmd: List[str], cwd: str, sink, timeout: int) -> int:
    """执行命令并把 stdout/stderr 逐行喂给 sink，返回退出码（超时 124，启动失败 127）。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **_SPAWN_KW,
        )
    except (FileNotFoundError, OSError) as exc:
        sink("[启动失败] 无法执行 %s：%s（请确认 Node.js / npm 已安装并加入 PATH）"
             % (cmd[0] if cmd else "?", exc))
        return 127

    async def pump() -> None:
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                sink(line)

    try:
        await asyncio.wait_for(pump(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        sink("[超时] 命令执行超过 %ss，已终止" % timeout)
        return 124
    await proc.wait()
    return proc.returncode or 0


async def _scan_missing(sink) -> List[str]:
    """跑 _scan_deps.mjs，返回根部无法解析的 @deepseek-ai 包名。"""
    core = _core_dir()
    script = os.path.join(core, "_scan_deps.mjs")
    node = shutil.which("node")
    if not script or not os.path.isfile(script) or not node:
        return []
    lines: List[str] = []
    code = await _run_stream([node, script], core, lines.append, 300)
    if code != 0:
        return []
    missing: List[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith("@deepseek-ai/") or s.startswith("@"):
            missing.append(s)
    if missing:
        sink("扫描到缺失依赖 %d 个：%s" % (len(missing), ", ".join(missing)))
    return missing


# ═══════════════════════════════════════════════════════════════════════════
# 更新状态机
# ═══════════════════════════════════════════════════════════════════════════
class KernelUpdater:
    """内核更新任务的状态机（单例，同一时刻只允许一个更新任务）。"""

    def __init__(self) -> None:
        self.state = "idle"          # idle | running | done | failed
        self.step = ""
        self.message = ""
        self.error = ""
        self.frm = ""
        self.to = ""
        self.target_version = ""
        self.started_at = 0.0
        self.finished_at = 0.0
        self.log: List[str] = []
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    # ── 日志 ────────────────────────────────────────────────────────
    def _log(self, line: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log.append("[%s] %s" % (stamp, line))
        if len(self.log) > 800:
            del self.log[:len(self.log) - 800]
        logger.info("[DSH-UP] %s", line)

    def _mark(self, step: str, message: str = "") -> None:
        self.step = step
        if message:
            self.message = message
            self._log(message)

    # ── 对外状态 ────────────────────────────────────────────────────
    def status(self) -> Dict[str, Any]:
        return {
            "state": self.state,
            "step": self.step,
            "message": self.message,
            "error": self.error,
            "from": self.frm,
            "to": self.to,
            "target": self.target_version,
            "current": current_kernel_version(),
            "running": self.state == "running",
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed": (time.time() - self.started_at) if self.started_at and self.state == "running"
                       else (self.finished_at - self.started_at if self.finished_at and self.started_at else 0),
            "registry": npm_registry(),
            "log": self.log[-200:],
        }

    # ── 启动任务 ────────────────────────────────────────────────────
    async def start_update(self, target: str, channel: str = "latest",
                           restart: bool = True) -> Dict[str, Any]:
        if self.state == "running":
            return {"ok": False, "error": "已有更新任务在进行中", **self.status()}

        core = _core_dir()
        if not core or not os.path.isfile(_pkg_path()):
            return {"ok": False, "error": "未找到 DSH 内核 npm-core 目录，无法更新"}
        if shutil.which("node") is None or shutil.which("npm") is None:
            return {"ok": False, "error": "未检测到 Node.js / npm，无法更新内核"}

        # 未指定具体版本时按渠道解析目标版本
        if not target:
            info = await asyncio.to_thread(check_update, channel)
            if info.get("error"):
                return {"ok": False, "error": info["error"]}
            target = info.get("target") or info.get("latest") or ""
        if not target:
            return {"ok": False, "error": "无法确定目标版本，请先检查更新或手动指定版本"}

        self.state = "running"
        self.step = "准备"
        self.message = ""
        self.error = ""
        self.frm = current_kernel_version()
        self.to = ""
        self.target_version = target
        self.started_at = time.time()
        self.finished_at = 0.0
        self.log = []
        self._log("开始更新内核：%s -> %s（registry=%s）" % (self.frm or "未知", target, npm_registry()))

        self._task = asyncio.create_task(self._run(target, restart))
        return {"ok": True, **self.status()}

    # ── 主流程 ──────────────────────────────────────────────────────
    async def _run(self, target: str, restart: bool) -> None:
        core = _core_dir()
        pkg = _pkg_path()
        backup = pkg + ".update-bak"
        engine = engine_mod.get_dsh_engine()
        was_running = engine.alive
        try:
            # 1) 停止内核（node_modules 被占用时 npm 可能写不进去）
            self._mark("停止内核", "停止当前 DSH 内核进程…")
            try:
                await engine.stop()
            except Exception as exc:
                self._log("停止内核时出现异常（忽略）：%s" % exc)

            # 2) 备份 package.json
            self._mark("备份配置", "备份 package.json -> %s" % os.path.basename(backup))
            shutil.copy2(pkg, backup)

            # 3) 改写版本号
            self._mark("改写依赖", "把 DSH 主体包版本改写为 %s…" % target)
            info = rewrite_versions(target)
            self._log("共 %d 个 DSH 包，改写 %d 个版本号" % (info["total"], info["changed"]))

            # 4) 安装 + 5) 补齐缺失依赖（最多 3 轮）
            for attempt in range(1, 4):
                self._mark("安装依赖", "npm install（第 %d 轮）…" % attempt)
                code = await _run_stream(
                    _npm_cmd() + ["install", "--no-audit", "--no-fund"],
                    core, self._log, NPM_INSTALL_TIMEOUT)
                if code != 0:
                    raise RuntimeError("npm install 失败（退出码 %s），详见日志" % code)
                missing = await _scan_missing(self._log)
                if not missing:
                    break
                if attempt == 3:
                    raise RuntimeError("仍有 %d 个依赖无法解析：%s" % (len(missing), ", ".join(missing)))
                added = await asyncio.to_thread(add_missing_deps, missing, target)
                self._log("补齐依赖：%s" % (", ".join(added) or "无新增"))
                if not added:
                    raise RuntimeError("缺失依赖 %s 无法自动补全" % ", ".join(missing))

            # 6) 校验：用新内核合成配置，退出码 0 即视为可用
            self._mark("校验内核", "运行 --dump-config 校验新内核…")
            patch = engine_mod._write_generated_config(engine_mod.get_dsh_settings())
            code = await _run_stream(
                ["node", _bin_path(), "--profile", engine_mod.DSH_PROFILE,
                 "--patch", patch, "--dump-config"],
                core, self._log, 180)
            if code != 0:
                raise RuntimeError("新内核 --dump-config 校验失败（退出码 %s）" % code)

            new_ver = current_kernel_version()
            self.to = new_ver
            self._log("内核已更新到 %s" % (new_ver or target))

            # 7) 重启内核
            if restart and was_running:
                self._mark("重启内核", "重新启动 DSH 内核…")
                try:
                    await engine.start()
                    self._log("DSH 内核已重启，model=%s" % engine.model)
                except Exception as exc:
                    self._log("内核重启失败（可稍后在状态页手动启动）：%s" % exc)
            elif restart:
                self._log("更新前内核未运行，跳过自动重启")

            self.state = "done"
            self.step = "完成"
            self.message = "内核已更新到 %s" % (new_ver or target)
            self._log("更新完成 ✓")

        except Exception as exc:
            self.state = "failed"
            self.step = "失败"
            self.error = str(exc)
            self._log("更新失败：%s" % exc)
            await self._rollback(pkg, backup, core, engine)

        finally:
            self.finished_at = time.time()
            try:
                if os.path.isfile(backup):
                    os.remove(backup)
            except Exception:
                pass

    async def _rollback(self, pkg: str, backup: str, core: str, engine) -> None:
        """回滚 package.json 并重装回旧版本，尽量让内核恢复可用。"""
        self._mark("回滚", "回滚 package.json 并重装旧版本…")
        try:
            if os.path.isfile(backup):
                shutil.copy2(backup, pkg)
                self._log("已还原 package.json")
        except Exception as exc:
            self._log("还原 package.json 失败：%s" % exc)
            return
        try:
            code = await _run_stream(
                _npm_cmd() + ["install", "--no-audit", "--no-fund"],
                core, self._log, NPM_INSTALL_TIMEOUT)
            self._log("旧版本重装%s（退出码 %s）" % ("成功" if code == 0 else "失败", code))
        except Exception as exc:
            self._log("旧版本重装异常：%s" % exc)
        # 尽力把内核恢复运行
        try:
            await engine.start()
            self._log("已恢复内核运行（版本 %s）" % current_kernel_version())
        except Exception as exc:
            self._log("内核恢复启动失败：%s（请在状态页手动启动）" % exc)


_updater: Optional[KernelUpdater] = None


def get_kernel_updater() -> KernelUpdater:
    global _updater
    if _updater is None:
        _updater = KernelUpdater()
    return _updater
