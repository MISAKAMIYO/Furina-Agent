"""
定时任务调度引擎
支持 Cron 表达式和一次性提醒
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timedelta
from dataclasses import dataclass, field, asdict
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("ZhiYanWeb.scheduler")


@dataclass
class ScheduledTask:
    """定时任务定义"""
    id: str
    name: str
    task_type: str  # "cron" / "once"
    cron_expr: str = ""
    run_at: str = ""
    message: str = ""
    enabled: bool = True
    created_at: str = ""
    last_run: str = ""
    next_run: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    def to_dict(self) -> Dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict) -> 'ScheduledTask':
        return ScheduledTask(**data)


class CronParser:
    """Cron 表达式解析器"""

    @staticmethod
    def parse(cron_expr: str) -> Dict[str, List[int]]:
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            raise ValueError(f"Cron 表达式格式错误: {cron_expr}")
        minute, hour, day, month, weekday = parts
        return {
            'minute': CronParser._parse_field(minute, 0, 59),
            'hour': CronParser._parse_field(hour, 0, 23),
            'day': CronParser._parse_field(day, 1, 31),
            'month': CronParser._parse_field(month, 1, 12),
            'weekday': CronParser._parse_field(weekday, 0, 6),
        }

    @staticmethod
    def _parse_field(field: str, min_val: int, max_val: int) -> List[int]:
        if field == '*':
            return list(range(min_val, max_val + 1))
        values = []
        for part in field.split(','):
            if '-' in part:
                start, end = part.split('-')
                values.extend(range(int(start), int(end) + 1))
            elif '/' in part:
                base, step = part.split('/')
                start = int(base) if base != '*' else min_val
                values.extend(range(start, max_val + 1, int(step)))
            else:
                values.append(int(part))
        return sorted(set(v for v in values if min_val <= v <= max_val))

    @staticmethod
    def next_run(cron_expr: str, from_time: datetime = None) -> datetime:
        if from_time is None:
            from_time = datetime.now()
        parsed = CronParser.parse(cron_expr)
        check_time = from_time.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(366 * 24 * 60):
            if (check_time.minute in parsed['minute'] and
                check_time.hour in parsed['hour'] and
                check_time.day in parsed['day'] and
                check_time.month in parsed['month'] and
                check_time.weekday() in parsed['weekday']):
                return check_time
            check_time += timedelta(minutes=1)
        raise ValueError("无法计算下次执行时间")


class SchedulerEngine:
    """调度引擎 - 单例模式"""

    _instance = None
    _initialized = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, storage_path: str = None):
        if self._initialized:
            return
        self._initialized = True

        if storage_path is None:
            from zhiyan_web.config import DATA_DIR
            storage_path = os.path.join(DATA_DIR, "scheduled_tasks.json")
        self.storage_path = storage_path

        self.tasks: Dict[str, ScheduledTask] = {}
        self._on_trigger: Optional[Callable] = None
        self._running = False
        self._load_tasks()
        logger.info(f"调度引擎已初始化，加载了 {len(self.tasks)} 个任务")

    def _load_tasks(self):
        """加载任务"""
        if not os.path.exists(self.storage_path):
            return
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for t in data.get("tasks", []):
                task = ScheduledTask.from_dict(t)
                self.tasks[task.id] = task
        except Exception as e:
            logger.error(f"加载任务失败: {e}")

    def _save_tasks(self):
        """保存任务"""
        try:
            os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
            data = {
                "tasks": [t.to_dict() for t in self.tasks.values()],
                "updated_at": datetime.now().isoformat(),
            }
            temp_path = self.storage_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            if os.path.exists(self.storage_path):
                os.remove(self.storage_path)
            os.rename(temp_path, self.storage_path)
        except Exception as e:
            logger.error(f"保存任务失败: {e}")

    def set_trigger_callback(self, callback: Callable):
        """设置任务触发回调"""
        self._on_trigger = callback

    def create_task(self, name: str, task_type: str, message: str,
                    cron_expr: str = "", run_at: str = "") -> ScheduledTask:
        """创建定时任务"""
        task_id = uuid.uuid4().hex[:12]
        next_run = ""
        if task_type == "cron" and cron_expr:
            try:
                next_run = CronParser.next_run(cron_expr).isoformat()
            except Exception:
                next_run = ""
        elif task_type == "once" and run_at:
            next_run = run_at

        task = ScheduledTask(
            id=task_id, name=name, task_type=task_type,
            cron_expr=cron_expr, run_at=run_at, message=message,
            enabled=True, next_run=next_run,
        )
        self.tasks[task.id] = task
        self._save_tasks()
        logger.info(f"创建任务: {name} (ID: {task_id})")
        return task

    def delete_task(self, task_id: str) -> bool:
        if task_id in self.tasks:
            task = self.tasks.pop(task_id)
            self._save_tasks()
            logger.info(f"删除任务: {task.name}")
            return True
        return False

    def enable_task(self, task_id: str, enabled: bool = True) -> bool:
        if task_id in self.tasks:
            self.tasks[task_id].enabled = enabled
            self._save_tasks()
            return True
        return False

    def get_task(self, task_id: str) -> Optional[ScheduledTask]:
        return self.tasks.get(task_id)

    def list_tasks(self, enabled_only: bool = False) -> List[ScheduledTask]:
        tasks = [t for t in self.tasks.values() if not enabled_only or t.enabled]
        return sorted(tasks, key=lambda t: t.next_run or "")

    async def start(self):
        """启动调度循环"""
        self._running = True
        logger.info("调度引擎已启动")
        while self._running:
            try:
                now = datetime.now()
                for task in list(self.tasks.values()):
                    if not task.enabled or not task.next_run:
                        continue
                    try:
                        next_run = datetime.fromisoformat(task.next_run)
                    except Exception:
                        continue
                    if now >= next_run:
                        logger.info(f"触发任务: {task.name}")
                        if self._on_trigger:
                            try:
                                self._on_trigger(task)
                            except Exception as e:
                                logger.error(f"任务触发回调错误: {e}")

                        if task.task_type == "cron":
                            try:
                                task.next_run = CronParser.next_run(task.cron_expr, now).isoformat()
                            except Exception:
                                task.enabled = False
                                task.next_run = ""
                        else:
                            task.enabled = False
                            task.next_run = ""
                        task.last_run = now.isoformat()
                        self._save_tasks()
                await asyncio.sleep(10)
            except Exception as e:
                logger.error(f"调度循环错误: {e}")
                await asyncio.sleep(30)

    def stop(self):
        """停止调度引擎"""
        self._running = False
        logger.info("调度引擎已停止")