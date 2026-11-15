"""
BetterGI 托管服务

已接入能力：
- 自动探测 G:\\BetterGI 等常见安装路径
- 按 BetterGI 官方命令行参数启动：startOneDragon（可选配置名）、--startGroups、--TaskProgress
- 接收 BetterGI 通知回调（webhook），跟踪一条龙/配置组的开始、结束、错误
- 进程存活监测与异常重启
- 任务完成后可选自动关机（默认关闭，需要显式开启）

预留能力：
- 图像识别兜底（接口占位，未实现真实动作）
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
import time
import zipfile
from collections import deque
from datetime import datetime, timedelta
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger("ZhiYanWeb.bettergi")

from .errors import (  # noqa: E402  （避免与 service 内部工具函数命名冲突，放在 logger 之后）
    ERROR_NAMES,
    DailyErrorCounter,
    in_active_window,
    normalize_action_map,
)

# 配置默认值（settings.json 中 bettergi 缺失的键用这里的值兜底）
DEFAULT_BETTERGI_CONFIG: Dict[str, Any] = {
    "enabled": False,            # 总开关：关闭后不监控、不允许启动脚本
    "auto_monitor": False,       # 后端启动时自动开始监控
    "bgi_path": "",              # BetterGI.exe 绝对路径（留空自动探测）
    "launch_mode": "onedragon",  # normal / onedragon / groups / task_progress
    "onedragon_config": "",      # 一条龙配置名（留空使用 BetterGI 当前选中配置）
    "group_names": "",           # 配置组名，多个用英文逗号分隔
    "process_name": "BetterGI.exe",
    "check_interval": 30,        # 轮询间隔（秒）
    "auto_launch": True,         # 监控发现进程未启动时自动拉起
    "auto_restart": True,        # 进程异常退出时自动重启
    "max_restarts": 2,           # 连续异常重启上限，超过后进入 alert
    "restart_reset_after": 3600, # 稳定运行超过该秒数后清零重启计数
    "notify_qq": False,          # 是否通过 QQ 平台推送事件
    "notify_qq_task_events": True,  # 任务开始/结束/错误也推送到 QQ（不受告警冷却限制）
    "notify_target": "",         # 推送目标：group:群号 或 private:QQ号
    "notify_cooldown": 300,      # 同类告警最小推送间隔（秒）
    "notify_transport": "webhook",  # webhook / websocket（BetterGI 推送事件的方式）
    "auto_setup_notify": True,      # 后端启动时自动把通知端点同步到当前端口
    "webhook_events": "dragon.start,dragon.end,group.start,group.end,task.error,task.cancel,daily.reward",
    "auto_shutdown": False,      # 任务完成后自动关机（默认关闭）
    "shutdown_delay": 60,        # 关机倒计时（秒），可被 shutdown /a 取消
    "image_fallback": False,     # 卡住时启用图像识别兜底
    "vision_stall_timeout": 600, # 任务无事件多久后触发图像兜底（秒）
    "vision_max_attempts": 3,    # 单次卡住最多尝试次数
    "vision_ai_enabled": True,   # OCR 规则未命中时交给 AI 决策
    "vision_rules": "",          # 自定义规则 JSON；留空使用内置默认规则
    "overlay_enabled": False,    # 识别后在屏幕上叠加红框（调试用，默认关闭）
    "overlay_duration": 3000,    # 红框显示时长（毫秒）
    "overlay_scale": 0,          # 坐标缩放，0 表示自动（高 DPI 屏偏移时手动指定，如 1.25）
    "health_check": False,       # 卡死/任务出错时执行健康检查与自愈链（默认关闭）
    "retry_max": 2,              # 任务级重试上限（区别于进程级 max_restarts）
    "retry_interval": 60,        # 两次自愈之间的最小间隔（秒）
    "network_recover": False,    # 自愈链是否刷新 DNS（默认关闭）
    "notify_screenshot": True,   # 自愈时截图并推送 QQ
    "game_launcher": "",         # 游戏启动器路径（留空则跳过「拉起游戏」）
    "auto_relaunch_after_task": False,  # 任务结束后自动续跑（默认关闭）
    "relaunch_modes": "done",    # 触发续跑的结果：done / error / canceled，逗号分隔
    "relaunch_delay": 60,        # 续跑前等待（秒）
    "relaunch_max": 3,           # 连续续跑上限，防止失败后死循环
    "error_guard": False,        # 异常判定与自动处理（默认关闭）
    "monitor_mode": "loose",     # 监测模式：strict / loose
    "strict_miss_limit": 1,      # 严格模式连续无响应上限
    "loose_miss_limit": 25,      # 宽松模式连续无响应上限
    "daily_error_limit": 3,      # 同一自然日 BetterGI 报错上限
    "error_action_map": {},      # 异常类型 -> 动作（留空用默认表）
    "active_window_start": "",   # 规定时间段开始 HH:MM
    "active_window_end": "",     # 规定时间段结束 HH:MM
    "game_alive_shutdown_hours": 12,  # 原神进程存活上限（小时），超时关机
    "update_flow_game": "",      # 原神更新步骤定义（JSON，留空用内置）
    "update_flow_bgi": "",       # BetterGI 更新步骤定义（JSON，留空用内置）
    "update_launch_onedragon": True,  # 更新完成后是否启动预设一条龙
    "boot_task_plan": False,     # 开机后执行更新任务计划（默认关闭）
    "boot_task_delay": 60,       # 开机后延迟多久开始检查（秒）
    "boot_plan_targets": "game,bgi",  # 开机计划执行哪些流程，逗号分隔
    "schedule_enabled": False,   # 启用定时任务调度
    "schedule_kill_running": False,  # 到点时若 BetterGI 已在运行，是否先结束再按任务模式启动
    "schedules": [],             # 定时任务列表
    "auto_start_on_boot": False, # 开机自启（写入当前用户 Run 键）
}

# BetterGI 的启动参数（依据官方 CommandLineOptions.cs）
LAUNCH_MODES = ("normal", "onedragon", "groups", "task_progress")

# 无需轮询即可识别的任务类事件
_TASK_START_EVENTS = {"dragon.start", "group.start", "leyline.start"}
_TASK_END_EVENTS = {"dragon.end", "group.end", "leyline.end"}
_TASK_ERROR_EVENTS = {"task.error", "js.error", "album.error"}


def _split_args(raw: str) -> List[str]:
    """拆分启动参数（Windows 下按非 POSIX 规则处理引号）"""
    if not raw:
        return []
    try:
        return shlex.split(raw, posix=False)
    except Exception:
        return raw.split()


def _split_names(raw: str) -> List[str]:
    """拆分逗号/中文逗号分隔的配置组名"""
    if not raw:
        return []
    return [x.strip() for x in str(raw).replace("，", ",").split(",") if x.strip()]


def _is_success(result: Any) -> bool:
    """判断 BetterGI 通知里的 result 是否为成功"""
    text = str(result or "").strip().lower()
    if not text:
        return False
    if any(k in text for k in ("fail", "error", "失败", "异常", "cancel")):
        return False
    return any(k in text for k in ("success", "ok", "完成", "成功"))


def _is_admin() -> bool:
    """当前进程是否具备管理员权限"""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _pid_is_elevated(pid: int):
    """目标进程是否以更高权限运行（UIPI 会拦我们的输入）：True/False/None"""
    if not pid:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        advapi32 = ctypes.windll.advapi32
        kernel32.OpenProcess.restype = wintypes.HANDLE
        h_proc = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h_proc:
            return None
        token = wintypes.HANDLE()
        ok = advapi32.OpenProcessToken(h_proc, 0x0008, ctypes.byref(token))  # TOKEN_QUERY
        kernel32.CloseHandle(h_proc)
        if not ok:
            return True
        try:
            kernel32.CloseHandle(token)
        except Exception:
            pass
        return False
    except Exception:
        return None


class BetterGiManager:
    """BetterGI 进程监测、任务事件与重启管理器"""

    def __init__(self):
        self._config: Dict[str, Any] = dict(DEFAULT_BETTERGI_CONFIG)
        self._state = "idle"
        self._task_state = "idle"  # idle / running / done / error / canceled
        self._monitor_task: Optional[asyncio.Task] = None
        self._restarts = 0
        self._launched_at: Optional[float] = None
        self._last_seen_at: Optional[float] = None
        self._last_error = ""
        self._last_notify_at = 0.0
        self._last_event: Dict[str, Any] = {}
        self._last_task_result = ""
        self._task_started_at: Optional[float] = None
        self._shutdown_pending = False
        self._vision_attempts = 0
        self._last_recovery: Dict[str, Any] = {}
        self._last_health: Dict[str, Any] = {}
        self._heal_retries = 0
        self._last_heal_at = 0.0
        self._relaunch_task: Optional[asyncio.Task] = None
        self._relaunch_count = 0
        self._relaunch_due = 0.0
        self._relaunching = False
        self._launch_lock: Optional[asyncio.Lock] = None
        self._error_counter = None
        self._miss_streak = 0
        self._miss_ref = 0.0
        self._last_error: Dict[str, Any] = {}
        self._error_cooldowns: Dict[str, float] = {}
        self._updating = False
        self._update_result: Dict[str, Any] = {}
        self._boot_plan_task: Optional[asyncio.Task] = None
        self._last_log_mtime = 0.0
        self._schedule_task: Optional[asyncio.Task] = None
        self._schedule_state: Dict[str, str] = {}
        self._load_schedule_state()
        self._events: Deque[Dict[str, Any]] = deque(maxlen=100)
        self.reload()

    # ---------- 配置 ----------

    def reload(self) -> Dict[str, Any]:
        """从全局配置重新读取 bettergi 段"""
        try:
            from zhiyan_web.config import get_settings
            raw = get_settings().get("bettergi") or {}
        except Exception:
            raw = {}
        merged = dict(DEFAULT_BETTERGI_CONFIG)
        if isinstance(raw, dict):
            merged.update({k: v for k, v in raw.items() if v is not None})
        self._config = merged
        return self.get_config()

    def get_config(self) -> Dict[str, Any]:
        return dict(self._config)

    def update_config(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """保存配置（写入 settings.json 并立即生效）"""
        from zhiyan_web.config import get_settings, update_settings

        current = dict(get_settings().get("bettergi") or {})
        clean = {k: v for k, v in (patch or {}).items() if v is not None}
        current.update(clean)
        update_settings({"bettergi": current})
        self.reload()
        # 关掉续跑开关后，立即撤销已排队的续跑
        if not self._config.get("auto_relaunch_after_task"):
            self.cancel_relaunch("续跑开关已关闭")
        self._emit("info", "配置已更新")
        return self.get_config()

    # ---------- 路径与探测 ----------

    @staticmethod
    def _default_candidates() -> List[str]:
        """常见安装路径候选"""
        candidates = [
            r"G:\BetterGI\BetterGI.exe",
            r"D:\BetterGI\BetterGI.exe",
            r"C:\BetterGI\BetterGI.exe",
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "BetterGI", "BetterGI.exe"),
            os.path.join(os.environ.get("ProgramFiles", ""), "BetterGI", "BetterGI.exe"),
            os.path.join(os.environ.get("USERPROFILE", ""), "BetterGI", "BetterGI.exe"),
        ]
        env_path = os.environ.get("BETTERGI_PATH", "").strip()
        if env_path:
            candidates.insert(0, env_path)
        return [p for p in candidates if p]

    def detect(self) -> Dict[str, Any]:
        """探测 BetterGI 安装位置"""
        current = (self._config.get("bgi_path") or "").strip()
        found = current if current and os.path.isfile(current) else ""
        candidates = []
        for path in self._default_candidates():
            exists = os.path.isfile(path)
            candidates.append({"path": path, "exists": exists})
            if exists and not found:
                found = path
        return {"found": found, "candidates": candidates, "user_dir": self.get_user_dir(found) if found else ""}

    def get_user_dir(self, exe_path: Optional[str] = None) -> str:
        """BetterGI 的 User 目录（配置文件所在目录）"""
        configured = (self._config.get("bgi_user_dir") or "").strip()
        if configured:
            return configured
        exe_path = (exe_path or self._config.get("bgi_path") or "").strip()
        if not exe_path:
            exe_path = self.detect().get("found") or ""
        if not exe_path:
            return ""
        return os.path.join(os.path.dirname(exe_path), "User")

    # ---------- 事件日志 ----------

    def _emit(self, level: str, message: str) -> None:
        item = {"ts": time.time(), "level": level, "message": message}
        self._events.append(item)
        log_fn = getattr(logger, level, logger.info)
        log_fn(f"[bettergi] {message}")

    def get_events(self, limit: int = 30) -> List[Dict[str, Any]]:
        events = list(self._events)
        return events[-limit:][::-1]

    def clear_events(self) -> None:
        self._events.clear()

    # ---------- 进程 ----------

    def _iter_processes(self) -> List[Dict[str, Any]]:
        """按进程名匹配 BetterGI 进程"""
        try:
            import psutil
        except Exception:
            return []
        target = (self._config.get("process_name") or "BetterGI.exe").strip().lower()
        found: List[Dict[str, Any]] = []
        for proc in psutil.process_iter(["pid", "name", "exe", "create_time"]):
            try:
                name = (proc.info.get("name") or "").lower()
                if not name:
                    continue
                if name == target or name.startswith(target.rsplit(".", 1)[0]):
                    found.append(
                        {
                            "pid": proc.info.get("pid"),
                            "name": proc.info.get("name"),
                            "exe": proc.info.get("exe") or "",
                            "create_time": proc.info.get("create_time"),
                        }
                    )
            except Exception:
                continue
        return found

    def is_running(self) -> bool:
        return bool(self._iter_processes())

    def build_args(self, mode: str, config_name: str = "", group_names: Any = None) -> List[str]:
        """按 BetterGI 官方参数规则生成启动参数"""
        mode = (mode or self._config.get("launch_mode") or "normal").strip().lower()
        if mode not in LAUNCH_MODES:
            mode = "normal"
        if mode == "onedragon":
            name = (config_name or self._config.get("onedragon_config") or "").strip()
            return ["startOneDragon"] + ([name] if name else [])
        if mode == "groups":
            names = group_names if isinstance(group_names, (list, tuple)) else _split_names(
                group_names or self._config.get("group_names") or ""
            )
            names = [str(n).strip() for n in names if str(n).strip()]
            return ["--startGroups"] + names
        if mode == "task_progress":
            names = group_names if isinstance(group_names, (list, tuple)) else _split_names(
                group_names or self._config.get("group_names") or ""
            )
            names = [str(n).strip() for n in names if str(n).strip()]
            return ["--TaskProgress"] + names
        return []

    def launch(
        self,
        mode: Optional[str] = None,
        config_name: Optional[str] = None,
        group_names: Optional[Any] = None,
        force_args: bool = False,
    ) -> Dict[str, Any]:
        """启动 BetterGI；mode 不为 normal 时使用命令行任务参数"""
        if not self._config.get("enabled"):
            return {"ok": False, "message": "托管总开关未启用"}

        # 非续跑来源的启动视为新链路，续跑计数归零
        if not self._relaunching:
            self._relaunch_count = 0

        args = self.build_args(mode or self._config.get("launch_mode") or "normal", config_name or "", group_names)
        running = self.is_running()
        if running and args and not force_args:
            return {
                "ok": False,
                "message": "BetterGI 已在运行，命令行任务参数无法作用到现有实例；请先结束进程再按任务模式启动",
            }
        if running:
            return {"ok": False, "message": "BetterGI 已在运行"}

        path = (self._config.get("bgi_path") or "").strip()
        if not path or not os.path.isfile(path):
            detected = self.detect().get("found")
            path = detected or path
        if not path:
            self._emit("error", "未配置 BetterGI.exe 路径")
            return {"ok": False, "message": "未配置 BetterGI.exe 路径"}
        if not os.path.isfile(path):
            self._emit("error", f"BetterGI.exe 路径不存在: {path}")
            return {"ok": False, "message": f"路径不存在: {path}"}

        cmd = [path] + args
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=os.path.dirname(path) or None,
                close_fds=True,
            )
        except Exception as e:
            self._last_error = str(e)
            self._emit("error", f"启动 BetterGI 失败: {e}")
            return {"ok": False, "message": f"启动失败: {e}"}

        self._launched_at = time.time()
        self._state = "starting"
        mode_label = {"onedragon": "一条龙", "groups": "配置组", "task_progress": "任务进度"}.get(
            (mode or "").strip().lower(), "普通启动"
        )
        self._emit("info", f"已启动 BetterGI（{mode_label}，pid={proc.pid}，参数={args or '无'}）")
        return {"ok": True, "message": f"已启动（{mode_label}）", "pid": proc.pid, "args": args}

    def kill(self) -> Dict[str, Any]:
        """结束所有 BetterGI 进程"""
        procs = self._iter_processes()
        if not procs:
            return {"ok": False, "message": "BetterGI 未运行"}

        # 手动结束进程时，同时取消待执行的续跑（续跑自身的重启不取消）
        if self._relaunch_task is not None and not self._relaunching:
            self.cancel_relaunch("检测到手动结束进程")

        try:
            import psutil
        except Exception:
            return {"ok": False, "message": "缺少 psutil，无法结束进程"}

        killed = 0
        for info in procs:
            try:
                p = psutil.Process(info["pid"])
                p.terminate()
                try:
                    p.wait(timeout=5)
                except psutil.TimeoutExpired:
                    p.kill()
                killed += 1
            except Exception:
                continue

        self._emit("warning", f"已结束 BetterGI 进程 {killed} 个")
        self._record_exit_reason("manual")
        return {"ok": True, "message": f"已结束 {killed} 个进程", "count": killed}

    # ---------- 通知回调 ----------

    def get_notify_config(self) -> Dict[str, Any]:
        """读取 BetterGI 当前的通知配置摘要"""
        user_dir = self.get_user_dir()
        config_path = os.path.join(user_dir, "config.json") if user_dir else ""
        result: Dict[str, Any] = {"user_dir": user_dir, "config_path": config_path, "exists": bool(config_path and os.path.isfile(config_path))}
        if not result["exists"]:
            return result
        try:
            with open(config_path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            nc = data.get("notificationConfig") or {}
            result.update(
                {
                    "webhook_enabled": bool(nc.get("webhookEnabled")),
                    "webhook_endpoint": nc.get("webhookEndpoint") or "",
                    "webhook_send_to": nc.get("webhookSendTo") or "",
                    "websocket_enabled": bool(nc.get("webSocketNotificationEnabled")),
                    "websocket_endpoint": nc.get("webSocketEndpoint") or "",
                    "event_subscribe": nc.get("notificationEventSubscribe") or "",
                }
            )
        except Exception as e:
            result["error"] = str(e)
        return result

    def configure_notifications(
        self,
        base_url: str,
        event_codes: Optional[str] = None,
        transport: str = "webhook",
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """把 BetterGI 通知写入 User/config.json（修改前自动备份）

        transport:
        - webhook：HTTP POST 到 /api/bettergi/event（实现简单，兼容性最好）
        - websocket：BetterGI 主动连接 /api/bettergi/ws 推送同一份 JSON（更实时，单条后断开）
        """
        base_url = (base_url or "").strip().rstrip("/")
        if not base_url:
            return {"ok": False, "message": "缺少后端地址，无法生成通知端点"}

        transport = (transport or self._config.get("notify_transport") or "webhook").strip().lower()
        if transport not in ("webhook", "websocket"):
            transport = "webhook"

        user_dir = self.get_user_dir()
        config_path = os.path.join(user_dir, "config.json") if user_dir else ""
        if not config_path or not os.path.isfile(config_path):
            return {"ok": False, "message": f"未找到 BetterGI 配置文件：{config_path or '（未探测到安装路径）'}"}

        events = (event_codes or self._config.get("webhook_events") or "").strip()
        if transport == "websocket":
            endpoint = base_url.replace("https://", "wss://").replace("http://", "ws://") + "/api/bettergi/ws"
            changes = {
                "webSocketNotificationEnabled": True,
                "webSocketEndpoint": endpoint,
                "webhookEnabled": False,
                "webhookEndpoint": "",
                "notificationEventSubscribe": events,
            }
        else:
            endpoint = f"{base_url}/api/bettergi/event"
            changes = {
                "webhookEnabled": True,
                "webhookEndpoint": endpoint,
                "webhookSendTo": "BetterGI 托管",
                "webSocketNotificationEnabled": False,
                "webSocketEndpoint": "",
                "notificationEventSubscribe": events,
            }

        try:
            with open(config_path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            nc = data.get("notificationConfig")
            if not isinstance(nc, dict):
                nc = {}
                data["notificationConfig"] = nc
            nc.update(changes)
            if dry_run:
                return {
                    "ok": True,
                    "message": "仅预览，未写入",
                    "transport": transport,
                    "config_path": config_path,
                    "changes": changes,
                }

            backup = config_path + ".zhiyan-bak-" + time.strftime("%Y%m%d%H%M%S")
            shutil.copy2(config_path, backup)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            return {"ok": False, "message": f"写入通知配置失败: {e}"}

        self._emit("info", f"已写入 BetterGI 通知配置（{transport}）：{endpoint}")
        return {
            "ok": True,
            "message": "已写入通知配置，重启 BetterGI 后生效",
            "transport": transport,
            "config_path": config_path,
            "backup": backup,
            "changes": changes,
        }

    def sync_notification_endpoint(self, base_url: str, transport: Optional[str] = None) -> Dict[str, Any]:
        """启动时把通知端点同步到当前后端端口（仅在配置变化时写盘）"""
        transport = (transport or self._config.get("notify_transport") or "webhook").strip().lower()
        if transport not in ("webhook", "websocket"):
            transport = "webhook"
        base_url = (base_url or "").strip().rstrip("/")
        if not base_url:
            return {"ok": False, "changed": False, "message": "缺少后端地址"}

        current = self.get_notify_config()
        if not current.get("exists"):
            return {"ok": False, "changed": False, "message": "未找到 BetterGI 配置文件"}

        events = (self._config.get("webhook_events") or "").strip()
        if transport == "websocket":
            endpoint = base_url.replace("https://", "wss://").replace("http://", "ws://") + "/api/bettergi/ws"
            enabled = bool(current.get("websocket_enabled"))
            current_endpoint = current.get("websocket_endpoint") or ""
        else:
            endpoint = f"{base_url}/api/bettergi/event"
            enabled = bool(current.get("webhook_enabled"))
            current_endpoint = current.get("webhook_endpoint") or ""

        if (
            enabled
            and current_endpoint == endpoint
            and (current.get("event_subscribe") or "").strip() == events
        ):
            return {"ok": True, "changed": False, "message": "通知配置已是最新", "endpoint": endpoint}

        result = self.configure_notifications(base_url, event_codes=events, transport=transport)
        result["changed"] = bool(result.get("ok"))
        return result

    def handle_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """处理 BetterGI 推送的事件"""
        payload = payload or {}
        event = str(payload.get("event") or payload.get("event_code") or "").strip().lower()
        result = payload.get("result") or ""
        message = str(payload.get("message") or "").strip()
        data = payload.get("data")

        self._last_event = {
            "ts": time.time(),
            "event": event,
            "result": str(result),
            "message": message,
            "has_data": bool(data),
        }

        if not event:
            self._emit("warning", "收到未标注事件名的 BetterGI 回调")
            return {"ok": True, "ignored": True}

        if event in _TASK_START_EVENTS:
            self._task_state = "running"
            self._task_started_at = time.time()
            self._last_task_result = ""
            self._vision_attempts = 0
            self._heal_retries = 0
            self.cancel_relaunch("检测到新任务开始")
            self._emit("info", f"任务开始：{event}" + (f" - {message}" if message else ""))
            self._notify_task_event(f"BetterGI 任务开始：{event}")
            return {"ok": True, "task_state": self._task_state}

        if event in _TASK_END_EVENTS:
            self._task_state = "done" if _is_success(result) else "error"
            self._last_task_result = str(result)
            self._vision_attempts = 0
            self._heal_retries = 0
            level = "info" if self._task_state == "done" else "warning"
            self._emit(level, f"任务结束：{event}，结果={result or '未知'}" + (f" - {message}" if message else ""))
            if self._task_state == "done":
                self._notify_task_event(f"BetterGI 任务完成：{event}（{result or 'success'}）")
                self._record_exit_reason("done")
                self._maybe_shutdown(event, result)
            else:
                self._record_exit_reason("task_error")
                asyncio.create_task(self._notify(f"BetterGI 任务结束但结果异常：{event} / {result}"))
            self._maybe_relaunch(self._task_state, event, result)
            return {"ok": True, "task_state": self._task_state}

        if event in _TASK_ERROR_EVENTS:
            self._task_state = "error"
            self._last_task_result = str(result)
            self._record_exit_reason("task_error")
            self._emit("error", f"任务错误：{event}" + (f" - {message}" if message else ""))
            asyncio.create_task(self._notify(f"BetterGI 任务错误：{event} {message}"))
            if self._config.get("health_check"):
                asyncio.create_task(self.run_health_chain(f"任务错误：{event} {message}".strip()))
            if self._config.get("error_guard"):
                asyncio.create_task(self._handle_bgi_error(f"{event} {message}".strip()))
            self._maybe_relaunch("error", event, result)
            return {"ok": True, "task_state": self._task_state}

        if event == "task.cancel":
            self._task_state = "canceled"
            self._record_exit_reason("canceled")
            self._emit("warning", f"任务已取消：{event}" + (f" - {message}" if message else ""))
            self._notify_task_event(f"BetterGI 任务已取消：{event}")
            self._maybe_relaunch("canceled", event, result)
            return {"ok": True, "task_state": self._task_state}

        self._emit("info", f"事件：{event}" + (f" - {message}" if message else ""))
        return {"ok": True, "task_state": self._task_state}

    # ---------- 监控 ----------

    def _running_monitor(self) -> bool:
        return self._monitor_task is not None and not self._monitor_task.done()

    async def start_monitor(self) -> Dict[str, Any]:
        """开始监控（重复调用无副作用）"""
        if not self._config.get("enabled"):
            return {"ok": False, "message": "请先启用托管总开关"}
        if self._running_monitor():
            return {"ok": True, "message": "监控已在运行"}
        self.cancel_relaunch("重新开始监控")
        self._relaunch_count = 0
        self._state = "monitoring"
        self._monitor_task = asyncio.get_running_loop().create_task(self._watch_loop())
        self._emit("info", "已开始监控 BetterGI")
        return {"ok": True, "message": "已开始监控"}

    async def stop_monitor(self) -> Dict[str, Any]:
        """停止监控"""
        self.cancel_relaunch("停止监控")
        task = self._monitor_task
        self._monitor_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._state = "stopped"
        self._emit("info", "已停止监控")
        return {"ok": True, "message": "已停止监控"}

    async def _watch_loop(self) -> None:
        try:
            while True:
                try:
                    await self._tick()
                    await self._guard_tick(time.time(), self.is_running())
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._last_error = str(e)
                    self._emit("error", f"监控循环异常: {e}")
                await asyncio.sleep(self._effective_interval())
        except asyncio.CancelledError:
            raise

    def _effective_interval(self) -> int:
        """实际轮询间隔：严格模式自动收紧到 15 秒以内"""
        base = max(5, int(self._config.get("check_interval") or 30))
        if str(self._config.get("monitor_mode") or "").lower() == "strict":
            return min(base, 15)
        return base

    async def _tick(self) -> None:
        """单次监测：进程存在性 + 保守重启策略"""
        procs = self._iter_processes()
        now = time.time()

        if procs:
            if self._state != "running":
                self._state = "running"
                self._launched_at = self._launched_at or (procs[0].get("create_time") or now)
                self._emit("info", f"检测到 BetterGI 正在运行 (pid={procs[0].get('pid')})")
            self._last_seen_at = now
            log_info = self.log_status()
            if log_info.get("available"):
                self._last_log_mtime = float(log_info.get("mtime") or 0)

            if self._restarts and self._launched_at and (now - self._launched_at) >= float(
                self._config.get("restart_reset_after") or 3600
            ):
                self._emit("info", "运行稳定，重启计数已清零")
                self._restarts = 0

            await self._maybe_vision_recover(now)
            return

        # 进程不存在
        if self._state == "starting":
            if self._launched_at and (now - self._launched_at) < 60:
                return
            self._state = "alert"
            msg = "BetterGI 启动超时（60 秒内未检测到进程）"
            self._emit("error", msg)
            await self._notify(msg)
            return

        if self._state in ("idle", "monitoring"):
            if self._config.get("auto_launch") and self._detect_or_config_path():
                result = self.launch(mode="normal")
                if not result.get("ok"):
                    self._state = "monitoring"
            return

        # 之前是 running，现在消失 → 异常
        self._emit("warning", "BetterGI 进程已退出")
        if not self._config.get("auto_restart"):
            self._state = "alert"
            await self._notify("BetterGI 进程已退出，自动重启未开启。")
            return

        self._restarts += 1
        max_restarts = max(1, int(self._config.get("max_restarts") or 2))
        if self._restarts > max_restarts:
            self._state = "alert"
            msg = f"BetterGI 连续异常 {self._restarts} 次，已达上限，请人工处理。"
            self._emit("error", msg)
            await self._notify(msg)
            return

        self._state = "restarting"
        self._emit("warning", f"第 {self._restarts} 次尝试重启 BetterGI")
        result = self.launch(mode="normal")
        if not result.get("ok"):
            self._emit("error", f"重启失败: {result.get('message')}")
            self._state = "alert"
            await self._notify(f"BetterGI 重启失败：{result.get('message')}")

    def _detect_or_config_path(self) -> str:
        """返回可用的 BetterGI 路径（配置优先，其次自动探测）"""
        path = (self._config.get("bgi_path") or "").strip()
        if path and os.path.isfile(path):
            return path
        return self.detect().get("found") or ""

    # ---------- 日志进度与更新维护 ----------

    def _bgi_root(self) -> str:
        path = self._detect_or_config_path()
        return os.path.dirname(path) if path else ""

    def log_status(self) -> Dict[str, Any]:
        """BetterGI 日志最新状态（用于判断是否真的卡住）"""
        root = self._bgi_root()
        if not root:
            return {"available": False, "path": "", "size": 0, "mtime": 0}
        log_dir = os.path.join(root, "log")
        try:
            files = [
                os.path.join(log_dir, n)
                for n in os.listdir(log_dir)
                if n.endswith(".log")
            ]
            if not files:
                return {"available": False, "path": "", "size": 0, "mtime": 0}
            latest = max(files, key=lambda p: os.path.getmtime(p))
            return {
                "available": True,
                "path": latest,
                "size": os.path.getsize(latest),
                "mtime": os.path.getmtime(latest),
            }
        except Exception:
            return {"available": False, "path": "", "size": 0, "mtime": 0}

    def _backups_dir(self) -> str:
        return os.path.join(self._bgi_data_dir(), "backups")

    def list_backups(self) -> List[Dict[str, Any]]:
        folder = self._backups_dir()
        if not os.path.isdir(folder):
            return []
        items = []
        for name in os.listdir(folder):
            if not name.endswith(".zip"):
                continue
            path = os.path.join(folder, name)
            try:
                items.append(
                    {
                        "name": name,
                        "path": path,
                        "size": os.path.getsize(path),
                        "mtime": os.path.getmtime(path),
                    }
                )
            except Exception:
                continue
        return sorted(items, key=lambda x: x["mtime"], reverse=True)

    def update_backup(self) -> Dict[str, Any]:
        """备份 BetterGI 的 User 配置（不含 Cache）"""
        root = self._bgi_root()
        if not root:
            return {"ok": False, "message": "未探测到 BetterGI 安装路径"}
        user_dir = self.get_user_dir()
        if not os.path.isdir(user_dir):
            return {"ok": False, "message": f"User 目录不存在: {user_dir}"}

        folder = self._backups_dir()
        os.makedirs(folder, exist_ok=True)
        name = "bgi-backup-" + time.strftime("%Y%m%d-%H%M%S") + ".zip"
        path = os.path.join(folder, name)
        skip_dirs = {"Cache"}
        added = 0
        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                for base, dirs, files in os.walk(user_dir):
                    dirs[:] = [d for d in dirs if d not in skip_dirs]
                    for fn in files:
                        if ".zhiyan-bak" in fn:
                            continue
                        full = os.path.join(base, fn)
                        rel = os.path.relpath(full, root)
                        try:
                            zf.write(full, rel)
                            added += 1
                        except Exception:
                            continue
        except Exception as e:
            return {"ok": False, "message": f"备份失败: {e}"}

        self._emit("info", f"已备份 BetterGI 配置 {added} 个文件 -> {name}")
        return {"ok": True, "message": f"备份完成（{added} 个文件）", "path": path, "name": name}

    def _safe_backup_path(self, backup_path: str) -> str:
        folder = self._backups_dir()
        name = os.path.basename(str(backup_path or ""))
        if not name or not name.endswith(".zip"):
            return ""
        path = os.path.join(folder, name)
        return path if os.path.isfile(path) else ""

    def update_restore(self, backup_path: str = "", mode: str = "config") -> Dict[str, Any]:
        """恢复备份（config=仅配置，full=User 下全部）"""
        root = self._bgi_root()
        if not root:
            return {"ok": False, "message": "未探测到 BetterGI 安装路径"}

        if backup_path:
            path = self._safe_backup_path(backup_path)
        else:
            items = self.list_backups()
            path = items[0]["path"] if items else ""
        if not path:
            return {"ok": False, "message": "没有可用备份"}

        # 恢复前先备份当前配置，避免误覆盖
        safety = self.update_backup()
        if not safety.get("ok"):
            return {"ok": False, "message": f"恢复前备份失败，已中止: {safety.get('message')}"}

        mode = (mode or "config").strip().lower()
        restored = 0
        try:
            with zipfile.ZipFile(path, "r") as zf:
                for info in zf.infolist():
                    rel = info.filename
                    if mode == "config" and not rel.replace("\\", "/").startswith("User/config.json"):
                        continue
                    target = os.path.join(root, rel)
                    if not os.path.isabs(target):
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    with zf.open(info, "r") as src, open(target, "wb") as dst:
                        dst.write(src.read())
                    restored += 1
        except Exception as e:
            return {"ok": False, "message": f"恢复失败: {e}"}

        self._emit("warning", f"已恢复 BetterGI 备份（{mode}，{restored} 个文件），请重启 BetterGI")
        return {"ok": True, "message": f"恢复完成（{restored} 个文件），请重启 BetterGI", "backup": path}

    async def restart_bgi(self) -> Dict[str, Any]:
        """重启 BetterGI（普通模式）"""
        if self.is_running():
            self.kill()
            await asyncio.sleep(5)
        result = self.launch(mode="normal", force_args=True)
        return result

    def update_status(self) -> Dict[str, Any]:
        root = self._bgi_root()
        config_path = os.path.join(self.get_user_dir(), "config.json") if self.get_user_dir() else ""
        return {
            "bgi_root": root,
            "config_path": config_path,
            "config_exists": bool(config_path and os.path.isfile(config_path)),
            "log": self.log_status(),
            "backups": self.list_backups()[:10],
            "backup_dir": self._backups_dir(),
        }

    # ---------- 通知 ----------

    def _notify_task_event(self, text: str) -> None:
        """任务类事件推送（不受告警冷却限制）"""
        if not (self._config.get("notify_qq") or self._config.get("notify_official")):
            return
        if not self._config.get("notify_qq_task_events"):
            return
        try:
            asyncio.get_running_loop().create_task(self._notify(text, cooldown=False))
        except RuntimeError:
            pass

    async def _notify(self, text: str, cooldown: bool = True) -> None:
        """通过 QQ（NapCat）和/或 QQ 开放平台官方机器人推送告警/事件"""
        use_napcat = bool(self._config.get("notify_qq"))
        use_official = bool(self._config.get("notify_official"))
        if not (use_napcat or use_official):
            return
        cooldown_s = float(self._config.get("notify_cooldown") or 300)
        if cooldown and time.time() - self._last_notify_at < cooldown_s:
            return

        sent_any = False

        if use_napcat:
            target = str(self._config.get("notify_target") or "").strip()
            if not target:
                self._emit("warning", "未配置 QQ 通知目标，跳过 NapCat 推送")
            else:
                try:
                    from zhiyan_web.platform.manager import get_qq_platform_manager

                    mgr = get_qq_platform_manager()
                    if not mgr.get_status().get("running"):
                        self._emit("warning", "QQ 平台未连接，跳过 NapCat 推送")
                    else:
                        client = getattr(mgr, "client", None)
                        kind, _, rid = target.partition(":")
                        rid = rid.strip()
                        if client is None:
                            pass
                        elif not rid.isdigit():
                            self._emit("warning", f"QQ 通知目标格式不正确: {target}")
                        elif kind == "group":
                            await client.send_group_msg(int(rid), text, timeout=10)
                            sent_any = True
                        elif kind == "private":
                            await client.send_private_msg(int(rid), text, timeout=10)
                            sent_any = True
                        else:
                            self._emit("warning", f"QQ 通知目标需为 group:群号 或 private:QQ号，当前: {target}")
                except Exception as e:
                    self._emit("warning", f"NapCat 推送失败: {e}")

        if use_official:
            try:
                from zhiyan_web.platform.qq_official import send_text

                result = await send_text(text)
                if result.get("ok"):
                    sent_any = True
                    self._emit("info", "已推送 QQ 官方机器人")
                else:
                    self._emit("warning", f"官方机器人推送失败: {result.get('message')}")
            except Exception as e:
                self._emit("warning", f"官方机器人推送异常: {e}")

        if sent_any:
            self._last_notify_at = time.time()
            self._emit("info", "已推送 QQ 告警/事件")

    # ---------- 自动关机 ----------

    def _maybe_shutdown(self, event: str, result: Any) -> None:
        """任务成功结束时按配置自动关机（默认关闭）"""
        if self._updating:
            self._emit("info", "更新流程进行中，跳过自动关机")
            return
        if not self._config.get("auto_shutdown"):
            return
        if self._shutdown_pending:
            return
        if not _is_success(result):
            self._emit("warning", "任务结果非成功，跳过自动关机")
            return
        delay = max(0, int(self._config.get("shutdown_delay") or 60))
        try:
            subprocess.Popen(["shutdown", "/s", "/t", str(delay)])
        except Exception as e:
            self._emit("error", f"发起自动关机失败: {e}")
            return
        self._shutdown_pending = True
        self._emit("warning", f"任务完成，将在 {delay} 秒后自动关机（可取消）")
        asyncio.create_task(self._notify(f"BetterGI 任务完成（{event}），{delay} 秒后自动关机"))

    def cancel_shutdown(self) -> Dict[str, Any]:
        """取消待执行的关机"""
        try:
            subprocess.run(["shutdown", "/a"], capture_output=True, timeout=10)
        except Exception as e:
            return {"ok": False, "message": f"取消关机失败: {e}"}
        self._shutdown_pending = False
        self._emit("info", "已取消自动关机")
        return {"ok": True, "message": "已取消自动关机"}

    # ---------- 定时任务 ----------

    def _bgi_data_dir(self) -> str:
        try:
            from zhiyan_web.config import DATA_DIR
            base = DATA_DIR
        except Exception:
            base = os.path.join(os.getcwd(), "data")
        return os.path.join(base, "bettergi")

    def _schedule_state_path(self) -> str:
        return os.path.join(self._bgi_data_dir(), "schedule_state.json")

    def _load_schedule_state(self) -> None:
        path = self._schedule_state_path()
        if not os.path.isfile(path):
            self._schedule_state = {}
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._schedule_state = dict(data) if isinstance(data, dict) else {}
        except Exception:
            self._schedule_state = {}

    def _save_schedule_state(self) -> None:
        path = self._schedule_state_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._schedule_state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[bettergi] 保存定时任务状态失败: {e}")

    def get_schedules(self) -> List[Dict[str, Any]]:
        raw = self._config.get("schedules")
        return [dict(x) for x in raw] if isinstance(raw, list) else []

    def _scheduler_running(self) -> bool:
        return self._schedule_task is not None and not self._schedule_task.done()

    async def start_scheduler(self) -> Dict[str, Any]:
        if not self._config.get("schedule_enabled"):
            return {"ok": False, "message": "请先启用定时任务"}
        if self._scheduler_running():
            return {"ok": True, "message": "定时任务已在运行"}
        self._schedule_task = asyncio.get_running_loop().create_task(self._schedule_loop())
        self._emit("info", "定时任务调度已启动")
        return {"ok": True, "message": "定时任务调度已启动"}

    async def stop_scheduler(self) -> Dict[str, Any]:
        task = self._schedule_task
        self._schedule_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
        self._emit("info", "定时任务调度已停止")
        return {"ok": True, "message": "定时任务调度已停止"}

    async def _schedule_loop(self) -> None:
        while True:
            try:
                self._check_schedules()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._emit("error", f"定时任务检查异常: {e}")
            await asyncio.sleep(20)

    @staticmethod
    def _parse_schedule_time(text: str) -> Optional[tuple]:
        try:
            hour, minute = str(text or "").strip().split(":")
            hour, minute = int(hour), int(minute)
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return hour, minute
        except Exception:
            return None
        return None

    def _check_schedules(self) -> None:
        if not self._config.get("schedule_enabled"):
            return
        now = datetime.now()
        for item in self.get_schedules():
            if not item.get("enabled", True):
                continue
            parsed = self._parse_schedule_time(item.get("time") or "")
            if not parsed:
                continue
            hour, minute = parsed
            days = item.get("days")
            if isinstance(days, list) and days:
                try:
                    if now.weekday() not in [int(d) for d in days]:
                        continue
                except Exception:
                    pass
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            delta = (now - target).total_seconds()
            if delta < -30 or delta > 120:
                continue
            run_key = now.strftime("%Y-%m-%d") + " %02d:%02d" % (hour, minute)
            sid = str(item.get("id") or "%02d:%02d-%s" % (hour, minute, item.get("mode") or "onedragon"))
            if self._schedule_state.get(sid) == run_key:
                continue
            self._schedule_state[sid] = run_key
            self._save_schedule_state()
            self._emit("info", f"定时任务触发: {sid}")
            asyncio.get_running_loop().create_task(self._run_schedule(item))

    async def _run_schedule(self, item: Dict[str, Any]) -> None:
        mode = str(item.get("mode") or "onedragon").strip().lower()
        config_name = str(item.get("config_name") or "")
        group_names = item.get("group_names") or ""
        # 与自动续跑共用互斥锁，避免同一时刻有两个启动来源
        async with self._get_launch_lock():
            if self.is_running():
                if not self._config.get("schedule_kill_running"):
                    self._emit("warning", "定时任务到点，但 BetterGI 已在运行，跳过本次启动")
                    return
                self._emit("warning", "定时任务到点，先结束现有 BetterGI 进程")
                self.kill()
                await asyncio.sleep(5)
            result = self.launch(mode=mode, config_name=config_name, group_names=group_names, force_args=True)
        if result.get("ok"):
            self._emit("info", "定时任务已启动: %s" % (result.get("message") or mode))
        else:
            self._emit("error", "定时任务启动失败: %s" % (result.get("message") or ""))
            await self._notify("BetterGI 定时任务启动失败：%s" % (result.get("message") or ""))

    def schedule_status(self) -> Dict[str, Any]:
        now = datetime.now()
        next_runs = []
        for item in self.get_schedules():
            if not item.get("enabled", True):
                continue
            nxt = self._next_run_time(item, now)
            if nxt:
                next_runs.append({
                    "id": str(item.get("id") or item.get("time") or ""),
                    "time": item.get("time") or "",
                    "mode": item.get("mode") or "onedragon",
                    "next": nxt.strftime("%Y-%m-%d %H:%M"),
                })
        next_run = min((x["next"] for x in next_runs), default="")
        return {
            "enabled": bool(self._config.get("schedule_enabled")),
            "running": self._scheduler_running(),
            "kill_running": bool(self._config.get("schedule_kill_running")),
            "schedules": self.get_schedules(),
            "state": dict(self._schedule_state),
            "next_runs": next_runs,
            "next_run": next_run,
        }

    def _next_run_time(self, item: Dict[str, Any], now: Optional[datetime] = None) -> Optional[datetime]:
        """计算该任务下一次触发时间"""
        now = now or datetime.now()
        parsed = self._parse_schedule_time(item.get("time") or "")
        if not parsed:
            return None
        hour, minute = parsed
        days = item.get("days")
        day_list = None
        if isinstance(days, list) and days:
            try:
                day_list = [int(d) for d in days]
            except Exception:
                day_list = None
        for offset in range(0, 8):
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(days=offset)
            if day_list is not None and candidate.weekday() not in day_list:
                continue
            if candidate > now:
                return candidate
        return None

    # ---------- 开机自启 ----------

    _AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
    _AUTOSTART_NAME = "ZhiYanAgent"

    def _autostart_command(self) -> str:
        if getattr(sys, "frozen", False):
            return '"%s"' % sys.executable
        try:
            from zhiyan_web.config import PROJECT_ROOT
            root = PROJECT_ROOT
        except Exception:
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        desktop = os.path.join(root, "desktop.py")
        python_exe = sys.executable or "python"
        pythonw = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
        if os.path.isfile(pythonw):
            python_exe = pythonw
        return '"%s" "%s"' % (python_exe, desktop)

    def get_autostart(self) -> Dict[str, Any]:
        result = {"enabled": False, "command": "", "name": self._AUTOSTART_NAME}
        if sys.platform != "win32":
            return result
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._AUTOSTART_KEY) as key:
                try:
                    value, _ = winreg.QueryValueEx(key, self._AUTOSTART_NAME)
                    result["enabled"] = True
                    result["command"] = str(value)
                except FileNotFoundError:
                    pass
        except Exception as e:
            result["error"] = str(e)
        return result

    def set_autostart(self, enabled: bool, command: Optional[str] = None) -> Dict[str, Any]:
        if sys.platform != "win32":
            return {"ok": False, "message": "仅支持 Windows"}
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, self._AUTOSTART_KEY, 0, winreg.KEY_ALL_ACCESS
            ) as key:
                if enabled:
                    winreg.SetValueEx(
                        key, self._AUTOSTART_NAME, 0, winreg.REG_SZ, command or self._autostart_command()
                    )
                else:
                    try:
                        winreg.DeleteValue(key, self._AUTOSTART_NAME)
                    except FileNotFoundError:
                        pass
        except Exception as e:
            return {"ok": False, "message": f"写入开机自启失败: {e}"}
        current = self.get_autostart()
        message = "已开启开机自启" if enabled else "已关闭开机自启"
        self._emit("info", message)
        return {"ok": True, "message": message, "autostart": current}

    # ---------- 图像识别兜底 ----------

    def _vision_save_dir(self) -> str:
        try:
            from zhiyan_web.config import DATA_DIR
            return os.path.join(DATA_DIR, "bettergi", "vision")
        except Exception:
            return os.path.join(os.getcwd(), "data", "bettergi", "vision")

    def vision_status(self) -> Dict[str, Any]:
        from .overlay import overlay_status
        from .vision import engine_status
        return {
            "engine": engine_status(),
            "attempts": self._vision_attempts,
            "max_attempts": int(self._config.get("vision_max_attempts") or 3),
            "stall_timeout": int(self._config.get("vision_stall_timeout") or 600),
            "ai_enabled": bool(self._config.get("vision_ai_enabled")),
            "last_recovery": self._last_recovery,
            "save_dir": self._vision_save_dir(),
            "overlay": {
                "enabled": bool(self._config.get("overlay_enabled")),
                "duration": int(self._config.get("overlay_duration") or 3000),
                "scale": float(self._config.get("overlay_scale") or 0),
                "status": overlay_status(),
            },
        }

    def show_overlay(self, boxes: List[Dict[str, Any]]) -> Dict[str, Any]:
        """把识别框画到屏幕上（受 overlay_enabled 控制）"""
        if not self._config.get("overlay_enabled"):
            return {"ok": False, "message": "叠加层未启用"}
        if not boxes:
            return {"ok": False, "message": "没有可绘制的识别框"}
        from .overlay import show_boxes

        return show_boxes(
            boxes,
            duration_ms=int(self._config.get("overlay_duration") or 3000),
            scale=float(self._config.get("overlay_scale") or 0),
        )

    def overlay_preview(self) -> Dict[str, Any]:
        """画三个示例框，验证叠加层是否正常"""
        from .overlay import show_boxes, sample_boxes

        return show_boxes(
            sample_boxes(),
            duration_ms=max(3000, int(self._config.get("overlay_duration") or 3000)),
            scale=float(self._config.get("overlay_scale") or 0),
        )

    async def vision_recover_once(self) -> Dict[str, Any]:
        """手动执行一次图像兜底"""
        from .vision import load_rules, recover_once

        rules = load_rules(self._config.get("vision_rules"))
        ai_enabled = bool(self._config.get("vision_ai_enabled"))
        result = await recover_once(self._vision_save_dir(), rules, ai_enabled)
        if self._config.get("overlay_enabled"):
            await asyncio.to_thread(self.show_overlay, result.get("boxes") or [])
        self._last_recovery = {"ts": time.time(), **result}
        level = "info" if result.get("success") else "warning"
        self._emit(level, "[图像兜底] " + (result.get("message") or "未执行"))
        return result

    async def _maybe_vision_recover(self, now: float) -> None:
        """任务长时间没有事件时自动触发图像兜底"""
        if not self._config.get("image_fallback"):
            return
        if self._task_state != "running":
            return

        ref = max(
            float(self._task_started_at or 0),
            float((self._last_event or {}).get("ts") or 0),
            float(self._launched_at or 0),
            float(self._last_log_mtime or 0),
        )
        stall = max(60, int(self._config.get("vision_stall_timeout") or 600))
        if not ref or (now - ref) < stall:
            return

        max_attempts = max(1, int(self._config.get("vision_max_attempts") or 3))
        if self._vision_attempts >= max_attempts:
            if self._state != "alert":
                self._state = "alert"
                msg = f"任务疑似卡死，图像兜底已尝试 {self._vision_attempts} 次仍未恢复"
                self._emit("error", msg)
                await self._notify(msg)
            if self._config.get("health_check"):
                await self.run_health_chain("任务卡死且图像兜底无果")
            return

        self._vision_attempts += 1
        self._emit(
            "warning",
            f"任务 {stall} 秒无事件，执行第 {self._vision_attempts}/{max_attempts} 次图像兜底",
        )
        result = await self.vision_recover_once()
        if not result.get("success"):
            self._emit("warning", "[图像兜底] 本次未恢复，等待下一轮或人工处理")

    # ---------- 健康检查与自愈 ----------

    def health_status(self) -> Dict[str, Any]:
        """健康检查配置与最近一次结果"""
        return {
            "enabled": bool(self._config.get("health_check")),
            "retry_max": max(1, int(self._config.get("retry_max") or 2)),
            "retry_interval": max(10, int(self._config.get("retry_interval") or 60)),
            "network_recover": bool(self._config.get("network_recover")),
            "notify_screenshot": bool(self._config.get("notify_screenshot")),
            "game_launcher": str(self._config.get("game_launcher") or ""),
            "retries": self._heal_retries,
            "last_heal_at": self._last_heal_at,
            "last": self._last_health,
        }

    async def health_check(self) -> Dict[str, Any]:
        """执行一次健康检查（只读，不触发任何自愈动作）"""
        from .health import run_checks, summarize

        log_info = self.log_status()
        checks = await asyncio.to_thread(
            run_checks,
            float(log_info.get("mtime") or 0),
            self._stall_seconds(),
            int(self._config.get("vision_stall_timeout") or 600),
            str(self._config.get("process_name") or "BetterGI.exe"),
        )
        summary = summarize(checks)
        self._last_health = summary
        if summary.get("ok"):
            self._emit("info", "健康检查通过")
        else:
            self._emit("warning", "健康检查异常: " + "、".join(summary.get("problem_names") or []))
        return summary

    def _launch_game(self) -> Dict[str, Any]:
        """拉起游戏启动器（需要在配置里给出 game_launcher 路径）"""
        launcher = str(self._config.get("game_launcher") or "").strip()
        if not launcher:
            return {"ok": False, "message": "未配置游戏启动器路径，跳过拉起游戏"}
        if not os.path.isfile(launcher):
            return {"ok": False, "message": f"游戏启动器不存在: {launcher}"}
        try:
            subprocess.Popen([launcher], cwd=os.path.dirname(launcher) or None)
        except Exception as e:
            return {"ok": False, "message": f"拉起游戏失败: {e}"}
        return {"ok": True, "message": f"已拉起游戏启动器: {launcher}"}

    async def _notify_screenshot(self, text: str, path: str) -> Dict[str, Any]:
        """把截图推送到 QQ（NapCat 走媒体接口，官方机器人走富媒体接口）"""
        if not path or not os.path.isfile(path):
            return {"ok": False, "message": "截图文件不存在"}
        sent = False

        if self._config.get("notify_qq"):
            target = str(self._config.get("notify_target") or "").strip()
            kind, _, rid = target.partition(":")
            rid = rid.strip()
            if not rid.isdigit():
                self._emit("warning", f"QQ 通知目标格式不正确: {target or '（空）'}")
            else:
                try:
                    from zhiyan_web.platform.manager import get_qq_platform_manager

                    client = getattr(get_qq_platform_manager(), "client", None)
                    if client is None:
                        self._emit("warning", "QQ 平台未连接，跳过截图推送")
                    elif kind == "group":
                        await client.send_group_media(int(rid), "image", path, timeout=30)
                        sent = True
                    elif kind == "private":
                        await client.send_private_media(int(rid), "image", path, timeout=30)
                        sent = True
                except Exception as e:
                    self._emit("warning", f"NapCat 截图推送失败: {e}")

        if self._config.get("notify_official"):
            try:
                from zhiyan_web.platform.qq_official import send_image

                result = await send_image(path, text=text[:200])
                if result.get("ok"):
                    sent = True
                else:
                    self._emit("warning", f"官方机器人截图推送失败: {result.get('message')}")
            except Exception as e:
                self._emit("warning", f"官方机器人截图推送异常: {e}")

        return {"ok": sent, "message": "截图已推送" if sent else "截图未推送"}

    def _capture_for_notify(self) -> str:
        """截图并返回本地路径（失败返回空串）"""
        try:
            from .vision import capture_window

            # 通知需要真实文件上传，这里显式要求落盘（唯一的落盘场景）
            shot = capture_window(self._vision_save_dir(), None, "game", True)
            return shot.get("path") if shot.get("success") else ""
        except Exception as e:
            logger.warning(f"异常截图失败: {e}")
            return ""

    async def run_health_chain(self, reason: str = "") -> Dict[str, Any]:
        """按顺序执行自愈：重启 BetterGI → 拉起游戏 → 关无响应窗口 → 网络恢复 → 放弃"""
        if not self._config.get("health_check"):
            return {"ok": False, "message": "健康自愈未启用", "steps": []}

        summary = await self.health_check()
        problems = set(summary.get("problems") or [])
        if not problems:
            return {"ok": True, "message": "检查项全部正常", "checks": summary.get("checks"), "steps": []}

        retry_max = max(1, int(self._config.get("retry_max") or 2))
        interval = max(10, int(self._config.get("retry_interval") or 60))
        now = time.time()
        wait = int(interval - (now - self._last_heal_at)) if self._last_heal_at else 0
        if wait > 0:
            return {
                "ok": False,
                "message": f"自愈冷却中，还需 {wait} 秒",
                "checks": summary.get("checks"),
                "steps": [],
            }
        if self._heal_retries >= retry_max:
            msg = f"健康自愈已尝试 {self._heal_retries} 次仍未恢复，放弃自动处理，请人工介入"
            self._emit("error", msg)
            await self._notify(msg)
            return {"ok": False, "message": msg, "checks": summary.get("checks"), "steps": [], "gave_up": True}

        self._heal_retries += 1
        self._last_heal_at = now
        self._emit(
            "warning",
            f"触发健康自愈（第 {self._heal_retries}/{retry_max} 次）：{reason or '健康检查异常'} -> "
            + "、".join(sorted(problems)),
        )

        steps: List[Dict[str, Any]] = []

        # 1 重启 BetterGI
        if "bettergi" in problems:
            result = await self.restart_bgi()
            steps.append({"step": "restart_bgi", "ok": bool(result.get("ok")), "message": result.get("message", "")})

        # 2 拉起游戏
        if "game" in problems:
            result = await asyncio.to_thread(self._launch_game)
            steps.append({"step": "launch_game", "ok": bool(result.get("ok")), "message": result.get("message", "")})

        # 3 关闭无响应窗口
        if "window" in problems:
            from .health import close_hung_windows

            hung: List[int] = []
            for item in summary.get("checks") or []:
                if item.get("key") == "window":
                    hung = list(item.get("hung") or [])
            result = await asyncio.to_thread(close_hung_windows, hung)
            steps.append({"step": "close_hung_window", "ok": bool(result.get("ok")), "message": result.get("message", "")})

        # 4 网络恢复（默认关闭，只有显式开启才刷新 DNS）
        if "network" in problems:
            if self._config.get("network_recover"):
                from .health import flush_dns

                result = await asyncio.to_thread(flush_dns)
                steps.append({"step": "network_recover", "ok": bool(result.get("ok")), "message": result.get("message", "")})
            else:
                steps.append({"step": "network_recover", "ok": False, "message": "网络恢复未启用，已跳过"})

        # 5 结果推送（文本 + 截图）
        detail = "；".join(f"{s['step']}: {s['message']}" for s in steps) or "无可用自愈动作"
        text = f"BetterGI 健康自愈：{reason or '检查异常'}\n问题: {'、'.join(summary.get('problem_names') or [])}\n处理: {detail}"
        await self._notify(text)
        shot_path = await asyncio.to_thread(self._capture_for_notify) if self._config.get("notify_screenshot") else ""
        if shot_path:
            await self._notify_screenshot(text, shot_path)

        ok = any(s.get("ok") for s in steps)
        return {
            "ok": ok,
            "message": detail,
            "checks": summary.get("checks"),
            "steps": steps,
            "screenshot": shot_path,
            "retries": self._heal_retries,
        }

    # ---------- 自动续跑 ----------

    def _get_launch_lock(self) -> asyncio.Lock:
        """启动来源互斥锁（定时任务与自动续跑共用）"""
        if self._launch_lock is None:
            self._launch_lock = asyncio.Lock()
        return self._launch_lock

    def _relaunch_modes(self) -> List[str]:
        raw = str(self._config.get("relaunch_modes") or "done").replace("，", ",")
        return [x.strip().lower() for x in raw.split(",") if x.strip()]

    def relaunch_status(self) -> Dict[str, Any]:
        pending = self._relaunch_task is not None and not self._relaunch_task.done()
        due_in = max(0, int(self._relaunch_due - time.time())) if pending else 0
        return {
            "enabled": bool(self._config.get("auto_relaunch_after_task")),
            "modes": self._relaunch_modes(),
            "delay": max(0, int(self._config.get("relaunch_delay") or 60)),
            "max": max(1, int(self._config.get("relaunch_max") or 3)),
            "count": self._relaunch_count,
            "pending": pending,
            "due_in": due_in,
        }

    def cancel_relaunch(self, reason: str = "") -> Dict[str, Any]:
        """取消待执行的续跑"""
        task = self._relaunch_task
        self._relaunch_task = None
        self._relaunch_due = 0.0
        if task is not None and not task.done():
            task.cancel()
            self._emit("info", "已取消待执行的续跑" + (f"（{reason}）" if reason else ""))
            return {"ok": True, "message": "已取消待执行的续跑"}
        return {"ok": False, "message": "当前没有待执行的续跑"}

    def _maybe_relaunch(self, outcome: str, event: str, result: Any) -> None:
        """任务结束后按配置安排续跑（默认关闭）"""
        if not self._config.get("auto_relaunch_after_task"):
            return
        if self._updating:
            self._emit("info", "更新流程进行中，跳过自动续跑")
            return
        if not self._config.get("enabled"):
            return
        if self._shutdown_pending:
            self._emit("info", "自动关机已排队，跳过续跑")
            return
        modes = self._relaunch_modes()
        if outcome not in modes:
            return

        max_times = max(1, int(self._config.get("relaunch_max") or 3))
        if self._relaunch_count >= max_times:
            msg = f"任务已结束（{outcome}），连续续跑已达上限 {max_times} 次，不再自动启动"
            self._emit("warning", msg)
            try:
                asyncio.get_running_loop().create_task(self._notify(msg))
            except RuntimeError:
                pass
            return

        delay = max(0, int(self._config.get("relaunch_delay") or 60))
        self.cancel_relaunch("重新安排续跑")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._emit("warning", "没有可用的事件循环，跳过续跑调度")
            return
        self._relaunch_due = time.time() + delay
        self._relaunch_task = loop.create_task(self._relaunch_runner(delay, outcome, event))
        self._emit(
            "info",
            f"任务已结束（{outcome}），{delay} 秒后自动续跑（第 {self._relaunch_count + 1}/{max_times} 次）",
        )

    async def _relaunch_runner(self, delay: int, outcome: str, event: str) -> None:
        try:
            await asyncio.sleep(delay)
            self._relaunch_task = None
            self._relaunch_due = 0.0

            lock = self._get_launch_lock()
            if lock.locked():
                self._emit("warning", "定时任务正在启动，跳过本次续跑")
                return

            async with lock:
                self._relaunch_count += 1
                self._relaunching = True
                try:
                    if self.is_running():
                        self._emit("info", "续跑前先结束现有 BetterGI 进程")
                        self.kill()
                        await asyncio.sleep(5)
                    mode = str(self._config.get("launch_mode") or "onedragon")
                    result = self.launch(mode=mode, force_args=True)
                finally:
                    self._relaunching = False

            # 结果处理放在锁外，避免推送耗时阻塞定时任务
            if result.get("ok"):
                self._emit("info", f"已自动续跑（第 {self._relaunch_count}/{self._relaunch_max()} 次）：{result.get('message') or mode}")
            else:
                msg = f"自动续跑失败：{result.get('message') or ''}"
                self._emit("error", msg)
                await self._notify(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._emit("error", f"自动续跑异常: {e}")

    def _relaunch_max(self) -> int:
        return max(1, int(self._config.get("relaunch_max") or 3))

    # ---------- 异常判定与处理（R9 / R10） ----------

    def _error_counter_obj(self):
        """按自然日累计的错误计数器（落盘，跨重启保留）"""
        if self._error_counter is None:
            self._error_counter = DailyErrorCounter(os.path.join(self._bgi_data_dir(), "error_count.json"))
        return self._error_counter

    def _miss_limit(self) -> int:
        mode = str(self._config.get("monitor_mode") or "loose").lower()
        key = "strict_miss_limit" if mode == "strict" else "loose_miss_limit"
        default = 1 if mode == "strict" else 25
        return max(1, int(self._config.get(key) or default))

    def error_status(self) -> Dict[str, Any]:
        start = str(self._config.get("active_window_start") or "")
        end = str(self._config.get("active_window_end") or "")
        return {
            "enabled": bool(self._config.get("error_guard")),
            "mode": str(self._config.get("monitor_mode") or "loose").lower(),
            "miss_limit": self._miss_limit(),
            "miss_streak": self._miss_streak,
            "daily_limit": max(1, int(self._config.get("daily_error_limit") or 3)),
            "daily": self._error_counter_obj().status(),
            "action_map": normalize_action_map(self._config.get("error_action_map")),
            "active_window": {
                "start": start,
                "end": end,
                "inside": in_active_window(start, end),
            },
            "game_alive_shutdown_hours": float(self._config.get("game_alive_shutdown_hours") or 12),
            "last_error": self._last_error,
        }

    def reset_error_counters(self) -> Dict[str, Any]:
        """清空当日计数与连续无响应计数"""
        self._error_counter_obj().reset()
        self._miss_streak = 0
        self._miss_ref = 0.0
        self._last_error = {}
        self._error_cooldowns.clear()
        self._emit("info", "异常计数已重置")
        return self.error_status()

    def _activity_ref(self) -> float:
        """最近一次活动信号（事件或日志更新）"""
        return max(
            float((self._last_event or {}).get("ts") or 0),
            float(self._last_log_mtime or 0),
        )

    async def _guard_tick(self, now: float, bgi_alive: bool) -> None:
        """每轮监测后的异常判定：game_dead / not_started / task_timeout / unresponsive"""
        if not self._config.get("error_guard"):
            return
        if self._updating:
            # 更新流程期间不判定异常，避免把「启动器还没拉起游戏」误判为 game_dead
            return
        from .health import game_process_info

        info = await asyncio.to_thread(game_process_info)
        game_alive = bool(info.get("pids"))

        # R9.2：BGI 活着、原神进程已死 → 只回报，不继续处理
        if bgi_alive and not game_alive and self._task_state == "running":
            await self._dispatch_error("game_dead", "BetterGI 在运行，但原神进程已退出")

        # R9.3：规定时间段内两个都没启动 → 回报并启动一条龙
        if not bgi_alive and not game_alive:
            start = str(self._config.get("active_window_start") or "")
            end = str(self._config.get("active_window_end") or "")
            if in_active_window(start, end):
                await self._dispatch_error("not_started", "规定时间段内 BetterGI 与原神都未启动")

        # R9.4：任务异常退出、原神仍存活超过规定时长 → 关机
        if game_alive and self._task_state == "error":
            hours = float(info.get("alive_hours") or 0)
            limit = float(self._config.get("game_alive_shutdown_hours") or 12)
            if hours >= limit:
                await self._dispatch_error(
                    "task_timeout",
                    "任务已异常结束，原神进程已连续存活 %.1f 小时（上限 %.0f 小时）" % (hours, limit),
                )

        # R10：连续无事件 / 日志不更新
        await self._update_miss_streak(now)

    async def _update_miss_streak(self, now: float) -> None:
        if self._task_state != "running":
            self._miss_streak = 0
            self._miss_ref = 0.0
            return
        ref = self._activity_ref()
        if ref and ref == self._miss_ref:
            self._miss_streak += 1
        else:
            self._miss_streak = 0
            self._miss_ref = ref
        limit = self._miss_limit()
        if self._miss_streak >= limit:
            mode = str(self._config.get("monitor_mode") or "loose").lower()
            await self._dispatch_error(
                "unresponsive",
                "连续 %d 个监测间隔无事件、日志也未更新（%s 模式，阈值 %d）" % (self._miss_streak, mode, limit),
            )

    async def _handle_bgi_error(self, detail: str) -> None:
        """R9.1：BetterGI 主动报错，按自然日累计"""
        limit = max(1, int(self._config.get("daily_error_limit") or 3))
        info = self._error_counter_obj().bump("bgi_error", limit)
        self._emit("warning", "[BetterGI 报错] 今日第 %d/%d 次：%s" % (info["count"], limit, detail))
        if info["reached"]:
            await self._dispatch_error(
                "bgi_error",
                "今日累计 %d 次报错，判定任务异常：%s" % (info["count"], detail),
            )
            return
        if str(self._config.get("monitor_mode") or "").lower() == "strict":
            await self._notify("BetterGI 报错（今日第 %d/%d 次）：%s" % (info["count"], limit, detail))

    async def _force_restart_task(self) -> Dict[str, Any]:
        """异常处理用的重启：结束现有进程后按当前启动模式重开"""
        lock = self._get_launch_lock()
        if lock.locked():
            return {"ok": False, "message": "其他启动来源正在执行，本次重启跳过"}
        async with lock:
            self._relaunching = True
            try:
                if self.is_running():
                    self.kill()
                    await asyncio.sleep(5)
                mode = str(self._config.get("launch_mode") or "onedragon")
                return self.launch(mode=mode, force_args=True)
            finally:
                self._relaunching = False

    async def _dispatch_error(self, kind: str, detail: str) -> Dict[str, Any]:
        """按异常类型查找动作并执行（唯一分发入口）"""
        if not self._config.get("error_guard"):
            return {"ok": False, "message": "异常守护未启用", "kind": kind}

        now = time.time()
        cooldown = 1800 if kind == "not_started" else max(60, int(self._config.get("retry_interval") or 60))
        last_at = float(self._error_cooldowns.get(kind) or 0)
        if last_at and now - last_at < cooldown:
            return {"ok": False, "message": "同类异常冷却中", "kind": kind}
        self._error_cooldowns[kind] = now
        if kind == "unresponsive":
            self._miss_streak = 0

        action = normalize_action_map(self._config.get("error_action_map")).get(kind, "report_only")
        name = ERROR_NAMES.get(kind, kind)
        self._last_error = {"ts": now, "kind": kind, "name": name, "detail": detail, "action": action}
        self._emit("error", "[%s] %s -> %s" % (name, detail, action))

        if action == "relaunch":
            result = await self._force_restart_task()
            if result.get("ok"):
                msg = "异常处理（%s）：%s；已重启一条龙" % (name, detail)
                await self._notify(msg)
                return {"ok": True, "message": msg, "kind": kind, "action": action}
            msg = "异常处理（%s）重启失败：%s" % (name, result.get("message") or "")
            self._state = "alert"
            await self._notify(msg)
            return {"ok": False, "message": msg, "kind": kind, "action": action}

        if action == "shutdown":
            if not self._config.get("auto_shutdown"):
                self._state = "alert"
                msg = "异常处理（%s）：%s；自动关机未开启，已转为告警" % (name, detail)
                await self._notify(msg)
                return {"ok": False, "message": msg, "kind": kind, "action": action}
            delay = max(0, int(self._config.get("shutdown_delay") or 60))
            try:
                subprocess.Popen(["shutdown", "/s", "/t", str(delay)])
            except Exception as e:
                msg = "异常处理（%s）：发起自动关机失败 %s" % (name, e)
                self._state = "alert"
                await self._notify(msg)
                return {"ok": False, "message": msg, "kind": kind, "action": action}
            self._shutdown_pending = True
            msg = "异常处理（%s）：%s；%d 秒后自动关机（可取消）" % (name, detail, delay)
            await self._notify(msg)
            return {"ok": True, "message": msg, "kind": kind, "action": action}

        if action == "self_heal":
            result = await self.run_health_chain("%s：%s" % (name, detail))
            return {"ok": bool(result.get("ok")), "message": result.get("message") or "", "kind": kind, "action": action}

        # report_only
        self._state = "alert"
        msg = "异常告警（%s）：%s，已停止自动处理，请人工介入" % (name, detail)
        await self._notify(msg)
        return {"ok": True, "message": msg, "kind": kind, "action": action}

    # ---------- 更新流程（R5） ----------

    def _record_exit_reason(self, reason: str) -> None:
        """记录最近一次退出/结束原因，供开机计划判断是否「任务异常中止」"""
        path = os.path.join(self._bgi_data_dir(), "boot_state.json")
        data = self._read_boot_state()
        data.update({"last_exit_reason": reason, "last_exit_at": time.time()})
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"写入退出原因失败: {e}")

    def _read_boot_state(self) -> Dict[str, Any]:
        path = os.path.join(self._bgi_data_dir(), "boot_state.json")
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def _write_boot_state(self, patch: Dict[str, Any]) -> None:
        data = self._read_boot_state()
        data.update(patch or {})
        path = os.path.join(self._bgi_data_dir(), "boot_state.json")
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"写入开机计划状态失败: {e}")

    def update_flow_status(self) -> Dict[str, Any]:
        from .update_flow import flow_definitions

        return {
            "running": self._updating,
            "last": self._update_result,
            "launch_onedragon": bool(self._config.get("update_launch_onedragon")),
            "flows": flow_definitions(),
        }

    def _update_launcher(self, target: str) -> Dict[str, Any]:
        """更新流程里的「打开程序」动作"""
        target = (target or "").strip().lower()
        if target == "game":
            return self._launch_game()
        if target == "bgi":
            if self.is_running():
                return {"ok": True, "message": "BetterGI 已在运行"}
            return self.launch(mode="normal")
        return {"ok": False, "message": f"未知启动目标: {target}"}

    async def run_update(self, target: str = "game") -> Dict[str, Any]:
        """执行一条更新流程；完成后按配置启动一条龙"""
        from .update_flow import FLOW_NAMES, run_flow

        target = (target or "game").strip().lower()
        if target not in ("game", "bgi"):
            return {"ok": False, "message": f"不支持的更新目标: {target}"}
        if not self._config.get("enabled"):
            return {"ok": False, "message": "托管总开关未启用"}
        if self._updating:
            return {"ok": False, "message": "已有更新流程在执行"}

        # UIPI：BetterGI 以管理员身份运行时，非管理员的我们无法向其窗口注入点击
        if not _is_admin():
            pids = [int(x.get("pid") or 0) for x in (self._iter_processes() or []) if x.get("pid")]
            if target == "bgi" and pids and _pid_is_elevated(pids[0]):
                return {
                    "ok": False,
                    "message": "BetterGI 正在以管理员身份运行，而本程序不是管理员，系统会拦截自动点击。"
                               "请用「以管理员身份运行」启动本程序后再试更新。",
                    "elevation_blocked": True,
                }

        custom = self._config.get("update_flow_%s" % target)
        label = FLOW_NAMES.get(target, target)
        self._updating = True
        self._emit("info", f"[更新流程] 开始执行：{label}")
        try:
            result = await run_flow(
                target,
                self._vision_save_dir(),
                launcher=self._update_launcher,
                custom=custom,
                on_event=self._emit,
            )
        except Exception as e:
            result = {"success": False, "target": target, "message": f"流程异常: {e}", "steps": []}
        finally:
            self._updating = False

        self._update_result = {"ts": time.time(), "target": target, **result}
        level = "info" if result.get("success") else "error"
        self._emit(level, f"[更新流程] {label}：{result.get('message') or ''}")
        await self._notify(f"BetterGI {label}流程{'完成' if result.get('success') else '失败'}：{result.get('message') or ''}")

        if result.get("success") and self._config.get("update_launch_onedragon"):
            launched = await self._force_restart_task()
            self._emit(
                "info" if launched.get("ok") else "warning",
                "[更新流程] 更新完成，启动预设一条龙：" + (launched.get("message") or ""),
            )
        return result

    # ---------- 开机任务计划（R6.3） ----------

    def boot_plan_status(self) -> Dict[str, Any]:
        state = self._read_boot_state()
        return {
            "enabled": bool(self._config.get("boot_task_plan")),
            "delay": max(0, int(self._config.get("boot_task_delay") or 60)),
            "targets": _split_names(self._config.get("boot_plan_targets") or "game,bgi"),
            "last_exit_reason": state.get("last_exit_reason") or "",
            "last_exit_at": state.get("last_exit_at") or 0,
            "plan_date": state.get("boot_plan_date") or "",
            "running": self._boot_plan_task is not None and not self._boot_plan_task.done(),
        }

    async def boot_task_plan_startup(self) -> Dict[str, Any]:
        """开机检查：BGI 未运行且上次非任务异常中止时，执行更新任务计划"""
        if not self._config.get("boot_task_plan"):
            return {"ok": True, "skipped": True, "message": "开机任务计划未启用"}
        if self._boot_plan_task is not None and not self._boot_plan_task.done():
            return {"ok": False, "message": "开机任务计划已在执行"}
        self._boot_plan_task = asyncio.get_running_loop().create_task(self._run_boot_plan())
        return {"ok": True, "message": "开机任务计划已排队"}

    async def _run_boot_plan(self) -> None:
        state = self._read_boot_state()
        today = datetime.now().strftime("%Y-%m-%d")
        if state.get("boot_plan_date") == today:
            self._emit("info", "[开机计划] 今天已执行过，跳过")
            return

        delay = max(0, int(self._config.get("boot_task_delay") or 60))
        self._emit("info", f"[开机计划] {delay} 秒后检查 BetterGI 状态")
        await asyncio.sleep(delay)

        if self.is_running():
            self._emit("info", "[开机计划] BetterGI 已在运行，跳过")
            return
        if state.get("last_exit_reason") == "task_error":
            self._emit("info", "[开机计划] 上次为任务异常中止，跳过自动检查更新")
            return

        targets = _split_names(self._config.get("boot_plan_targets") or "game,bgi")
        self._emit("info", "[开机计划] 开始执行更新任务计划：" + "、".join(targets))
        results = []
        for target in targets:
            results.append({"target": target, "result": await self.run_update(target)})
            await asyncio.sleep(2)
        self._write_boot_state({"boot_plan_date": today})
        ok_count = len([x for x in results if x["result"].get("success")])
        self._emit("info", f"[开机计划] 完成，成功 {ok_count}/{len(results)}")
        await self._notify(f"BetterGI 开机任务计划完成：成功 {ok_count}/{len(results)}")

    # ---------- 预留能力 ----------

    async def image_recover(self) -> Dict[str, Any]:
        """图像识别兜底（手动入口）"""
        return await self.vision_recover_once()

    # ---------- 状态 ----------

    def get_status(self) -> Dict[str, Any]:
        procs = self._iter_processes()
        now = time.time()
        uptime = 0
        started = None
        if procs:
            started = procs[0].get("create_time") or self._launched_at
        elif self._launched_at:
            started = self._launched_at
        if started:
            uptime = max(0, int(now - float(started)))

        task_elapsed = 0
        if self._task_state == "running" and self._task_started_at:
            task_elapsed = int(now - self._task_started_at)

        return {
            "state": self._state,
            "monitoring": self._running_monitor(),
            "running": bool(procs),
            "pids": [p.get("pid") for p in procs],
            "uptime": uptime,
            "restarts": self._restarts,
            "last_seen_at": self._last_seen_at,
            "last_error": self._last_error,
            "task_state": self._task_state,
            "task_elapsed": task_elapsed,
            "last_task_result": self._last_task_result,
            "last_event": self._last_event,
            "shutdown_pending": self._shutdown_pending,
            "vision_attempts": self._vision_attempts,
            "last_recovery": self._last_recovery,
            "health": self.health_status(),
            "relaunch": self.relaunch_status(),
            "errors": self.error_status(),
            "update": self.update_flow_status(),
            "boot_plan": self.boot_plan_status(),
            "log": self.log_status(),
            "stall_seconds": self._stall_seconds(now),
            "backup_count": len(self.list_backups()),
            "detected_path": self._detect_or_config_path(),
            "config": self.get_config(),
            "events": self.get_events(30),
        }

    def _stall_seconds(self, now: Optional[float] = None) -> int:
        """任务进行中且无事件/无日志更新的秒数"""
        if self._task_state != "running":
            return 0
        now = now or time.time()
        ref = max(
            float(self._task_started_at or 0),
            float((self._last_event or {}).get("ts") or 0),
            float(self._last_log_mtime or 0),
        )
        return max(0, int(now - ref)) if ref else 0


_manager: Optional[BetterGiManager] = None


def get_bettergi_manager() -> BetterGiManager:
    """获取全局 BetterGI 管理器（懒加载单例）"""
    global _manager
    if _manager is None:
        _manager = BetterGiManager()
    return _manager
