"""
子代理运行时：回合预算、取消、运行记录、后台任务

集中管理四类「跨越单次 run_subagent 调用」的状态：

1. 回合预算（ContextVar）：限制嵌套深度、单回合子代理总次数、总迭代数，
   防止一个「派子代理」的循环把 token 烧光；
2. 取消注册表（进程级）：前端可以中断某个正在跑的子代理；
3. 运行记录（进程级 + JSONL 落盘）：每次运行结束后留档，可在界面回看；
4. 后台任务（进程级 + JSON 落盘）：spawn_background_subagent 起的长任务，
   跑完把结论挂到待回灌队列，下一轮对话开始时注入给主 agent。

预算用 ContextVar（按回合隔离，asyncio 子任务继承），其余用进程级字典
（需要跨请求查询，不能绑在某个会话上下文上）。所有落盘操作都容忍失败。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ZhiYanWeb.SubAgent.Runtime")

RUNS_FILE = "_subagent_runs.jsonl"
JOBS_FILE = "_subagent_jobs.json"
RUNS_KEEP = 200          # 内存与文件里保留的运行记录条数
JOBS_KEEP = 30           # 后台任务保留条数


def _data_dir() -> str:
    try:
        from zhiyan_web.config import DATA_DIR
        os.makedirs(DATA_DIR, exist_ok=True)
        return DATA_DIR
    except Exception:
        return os.getcwd()


# ═══════════════════════════════════════════════════════════════════════════
# 1. 回合预算
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class TurnBudget:
    """一个用户回合内的子代理预算。"""
    turn_id: str = ""
    max_depth: int = 2
    max_runs: int = 12
    max_iterations: int = 240
    used_runs: int = 0
    used_iterations: int = 0

    def can_spawn_at(self, depth: int) -> bool:
        """当前深度能否再往下派一层。

        depth 是「已经进入的子代理层数」：主 agent 为 0，一级子代理运行时为 1。
        """
        return depth < max(0, self.max_depth)

    def acquire_run(self) -> Dict[str, Any]:
        """申请一次子代理运行额度。"""
        if self.max_runs and self.used_runs >= self.max_runs:
            return {"ok": False,
                    "error": f"本回合子代理调用次数已达上限（{self.max_runs} 次），"
                             f"请改用已有结果或直接自己完成"}
        self.used_runs += 1
        return {"ok": True, "used": self.used_runs, "limit": self.max_runs}

    def add_iterations(self, n: int) -> None:
        self.used_iterations += max(0, int(n or 0))

    def remaining_iterations(self) -> int:
        if not self.max_iterations:
            return 10 ** 9
        return max(0, self.max_iterations - self.used_iterations)

    def snapshot(self) -> Dict[str, Any]:
        return {"turn_id": self.turn_id, "max_depth": self.max_depth,
                "max_runs": self.max_runs, "used_runs": self.used_runs,
                "max_iterations": self.max_iterations,
                "used_iterations": self.used_iterations}


_budget: ContextVar[Optional[TurnBudget]] = ContextVar("zhiyan_subagent_budget", default=None)


def begin_budget(max_depth: int = 2, max_runs: int = 12,
                 max_iterations: int = 240, turn_id: str = "") -> tuple[TurnBudget, Any]:
    """回合开始时建立预算，返回 (budget, token)。"""
    budget = TurnBudget(turn_id=turn_id or uuid.uuid4().hex[:8],
                        max_depth=max_depth, max_runs=max_runs,
                        max_iterations=max_iterations)
    return budget, _budget.set(budget)


def end_budget(token: Any) -> None:
    try:
        _budget.reset(token)
    except (ValueError, LookupError):
        pass


def current_budget() -> Optional[TurnBudget]:
    return _budget.get()


def effective_max_depth(default: int = 2) -> int:
    """当前回合允许的最大嵌套深度（没有预算时用默认值）。"""
    b = _budget.get()
    if b is None:
        return default
    # 预算里的 max_depth 可能被单次调用收窄，取更小值
    return min(b.max_depth, default) if default else b.max_depth


# ═══════════════════════════════════════════════════════════════════════════
# 2. 运行记录 + 取消
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class RunRecord:
    id: str
    name: str = ""
    label: str = ""
    avatar: str = ""
    task: str = ""
    conv_id: str = ""
    depth: int = 1
    parent_id: str = ""
    background: bool = False
    ok: bool = False
    status: str = "running"      # running / done / failed / cancelled
    iterations: int = 0
    tool_calls: List[str] = field(default_factory=list)
    elapsed_sec: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    error: str = ""
    result_preview: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class _Running:
    __slots__ = ("record", "task", "cancel_event")

    def __init__(self, record: RunRecord) -> None:
        self.record = record
        self.task: Optional[asyncio.Task] = None
        self.cancel_event = asyncio.Event()


_running: Dict[str, _Running] = {}
_history: List[Dict[str, Any]] = []
_history_loaded = False
_history_mtime: float = 0.0


def _runs_path() -> str:
    return os.path.join(_data_dir(), RUNS_FILE)


def _load_history() -> None:
    """读取运行记录文件。

    带 mtime 校验：桌面版与 dev 服务器有可能同时开着，别的进程写完记录后
    本进程内存里的缓存不能一直是旧的（否则界面会显示「还没有运行记录」）。
    """
    global _history_loaded, _history_mtime
    path = _runs_path()
    try:
        mtime = os.path.getmtime(path) if os.path.isfile(path) else 0.0
    except OSError:
        mtime = 0.0
    if _history_loaded and mtime == _history_mtime:
        return
    _history_loaded = True
    _history_mtime = mtime
    if not os.path.isfile(path):
        return
    try:
        rows: List[Dict[str, Any]] = []
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        _history[:] = rows[-RUNS_KEEP:]
    except Exception as e:
        logger.debug(f"读取子代理运行记录失败: {e}")


def _append_history(record: Dict[str, Any]) -> None:
    global _history_mtime
    _load_history()
    _history.append(record)
    if len(_history) > RUNS_KEEP:
        del _history[:-RUNS_KEEP]
    try:
        path = _runs_path()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        # 文件过大时重写一次，避免无限增长
        if os.path.getsize(path) > 4 * 1024 * 1024:
            with open(path, "w", encoding="utf-8") as f:
                for row in _history[-RUNS_KEEP:]:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
        _history_mtime = os.path.getmtime(path)
    except Exception as e:
        logger.debug(f"写入子代理运行记录失败: {e}")


def start_run(name: str, label: str = "", avatar: str = "", task: str = "",
              conv_id: str = "", depth: int = 1, parent_id: str = "",
              background: bool = False, run_id: str = "") -> RunRecord:
    """登记一次运行（状态 running）。"""
    record = RunRecord(id=run_id or f"sa_{uuid.uuid4().hex[:8]}", name=name,
                       label=label or name, avatar=avatar, task=(task or "")[:400],
                       conv_id=conv_id, depth=depth, parent_id=parent_id,
                       background=background)
    _running[record.id] = _Running(record)
    return record


def running_handle(run_id: str) -> Optional[_Running]:
    return _running.get(run_id)


def finish_run(run_id: str, ok: bool, status: str = "", iterations: int = 0,
               tool_calls: Optional[List[str]] = None, elapsed_sec: float = 0.0,
               usage: Optional[Dict[str, Any]] = None, error: str = "",
               result_preview: str = "") -> Optional[Dict[str, Any]]:
    """结束一次运行并落盘记录。"""
    handle = _running.pop(run_id, None)
    if handle is None:
        return None
    record = handle.record
    record.ok = ok
    record.status = status or ("done" if ok else "failed")
    record.iterations = int(iterations or 0)
    record.tool_calls = list(tool_calls or [])
    record.elapsed_sec = round(float(elapsed_sec or 0.0), 2)
    record.error = (error or "")[:600]
    record.result_preview = (result_preview or "")[:600]
    record.finished_at = time.time()
    if usage:
        record.prompt_tokens = int(usage.get("prompt_tokens") or 0)
        record.completion_tokens = int(usage.get("completion_tokens") or 0)
        record.total_tokens = int(usage.get("total_tokens") or 0)
    data = record.to_dict()
    _append_history(data)
    return data


def request_cancel(run_id: str, reason: str = "用户取消") -> bool:
    """请求取消一次运行。

    刻意只做「软取消」：置位后由 runner 在下一个检查点自行退出并落记录。
    不直接 task.cancel() 的原因是这样会抛 CancelledError，可能穿透到父级
    ReAct 循环，把一个子任务的中断升级成整轮对话的中断。
    """
    handle = _running.get(run_id)
    if handle is None:
        return False
    handle.cancel_event.set()
    logger.info(f"已请求取消子代理运行 {run_id}: {reason}")
    return True


def bind_task(run_id: str, task: asyncio.Task) -> None:
    handle = _running.get(run_id)
    if handle is not None:
        handle.task = task


def is_cancelled(run_id: str) -> bool:
    handle = _running.get(run_id)
    return bool(handle and handle.cancel_event.is_set())


def cancel_reason(run_id: str) -> str:
    return "已被取消" if is_cancelled(run_id) else ""


def list_running() -> List[Dict[str, Any]]:
    return [h.record.to_dict() for h in _running.values()]


def list_runs(limit: int = 50) -> List[Dict[str, Any]]:
    _load_history()
    rows = list(_history)[-limit:]
    rows.reverse()   # 最新的在前
    return rows


def runs_stats() -> Dict[str, Any]:
    _load_history()
    total = len(_history)
    ok = sum(1 for r in _history if r.get("ok"))
    tokens = sum(int(r.get("total_tokens") or 0) for r in _history)
    elapsed = sum(float(r.get("elapsed_sec") or 0) for r in _history)
    return {"total": total, "ok": ok, "failed": total - ok,
            "tokens": tokens, "elapsed_sec": round(elapsed, 1),
            "running": len(_running)}


# ═══════════════════════════════════════════════════════════════════════════
# 3. 后台任务
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class JobRecord:
    id: str
    task: str = ""
    agent_type: str = ""
    conv_id: str = ""
    status: str = "running"      # running / done / failed / cancelled
    created_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    result: str = ""
    error: str = ""
    run_id: str = ""
    delivered: bool = False      # 结果是否已回灌给主 agent

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_jobs: Dict[str, JobRecord] = {}
_jobs_loaded = False
_jobs_mtime: float = 0.0


def _jobs_path() -> str:
    return os.path.join(_data_dir(), JOBS_FILE)


def _save_jobs() -> None:
    global _jobs_mtime
    try:
        path = _jobs_path()
        rows = [j.to_dict() for j in list(_jobs.values())[-JOBS_KEEP:]]
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        _jobs_mtime = os.path.getmtime(path)
    except Exception as e:
        logger.debug(f"保存后台子代理任务失败: {e}")


def _load_jobs() -> None:
    global _jobs_loaded, _jobs_mtime
    path = _jobs_path()
    try:
        mtime = os.path.getmtime(path) if os.path.isfile(path) else 0.0
    except OSError:
        mtime = 0.0
    if _jobs_loaded and mtime == _jobs_mtime:
        return
    _jobs_loaded = True
    _jobs_mtime = mtime
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            rows = json.load(f)
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("id"):
                # 进程重启后还标着 running 的，视为中断
                if row.get("status") == "running":
                    row["status"] = "failed"
                    row["error"] = row.get("error") or "进程重启导致任务中断"
                _jobs[row["id"]] = JobRecord(**{
                    k: v for k, v in row.items()
                    if k in JobRecord.__dataclass_fields__
                })
    except Exception as e:
        logger.debug(f"读取后台子代理任务失败: {e}")


def create_job(task: str, agent_type: str, conv_id: str = "",
               run_id: str = "", job_id: str = "") -> JobRecord:
    _load_jobs()
    job = JobRecord(id=job_id or f"job_{uuid.uuid4().hex[:8]}", task=(task or "")[:400],
                    agent_type=agent_type, conv_id=conv_id, run_id=run_id)
    _jobs[job.id] = job
    if len(_jobs) > JOBS_KEEP:
        for key in list(_jobs.keys())[:-JOBS_KEEP]:
            _jobs.pop(key, None)
    _save_jobs()
    return job


def finish_job(job_id: str, ok: bool, result: str = "", error: str = "",
               status: str = "") -> Optional[JobRecord]:
    _load_jobs()
    job = _jobs.get(job_id)
    if job is None:
        return None
    job.status = status or ("done" if ok else "failed")
    job.result = (result or "")[:8000]
    job.error = (error or "")[:600]
    job.finished_at = time.time()
    _save_jobs()
    return job


def get_job(job_id: str) -> Optional[JobRecord]:
    _load_jobs()
    return _jobs.get(job_id)


def list_jobs(limit: int = 20) -> List[Dict[str, Any]]:
    _load_jobs()
    rows = list(_jobs.values())[-limit:]
    rows.reverse()
    return [j.to_dict() for j in rows]


def pending_jobs(conv_id: str = "") -> List[JobRecord]:
    """已完成但还没回灌给主 agent 的后台任务。"""
    _load_jobs()
    return [j for j in _jobs.values()
            if j.status in ("done", "failed", "cancelled") and not j.delivered
            and (not conv_id or not j.conv_id or j.conv_id == conv_id)]


def mark_delivered(job_ids: List[str]) -> None:
    _load_jobs()
    for jid in job_ids:
        job = _jobs.get(jid)
        if job is not None:
            job.delivered = True
    _save_jobs()


def jobs_stats() -> Dict[str, Any]:
    _load_jobs()
    values = list(_jobs.values())
    return {
        "total": len(values),
        "running": sum(1 for j in values if j.status == "running"),
        "done": sum(1 for j in values if j.status == "done"),
        "failed": sum(1 for j in values if j.status in ("failed", "cancelled")),
        "undelivered": sum(1 for j in values
                           if j.status in ("done", "failed", "cancelled")
                           and not j.delivered),
    }
