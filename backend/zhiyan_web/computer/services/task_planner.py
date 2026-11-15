"""
任务规划引擎 - 提供 Agent 任务分解与执行能力

实现 Plan-and-Execute 模式:
1. 接收用户目标
2. 分解为子任务（Plan）
3. 逐个执行子任务（Execute）
4. 跟踪进度并报告

支持:
- 任务分解（自动 + 手动）
- 依赖管理（DAG）
- 优先级调度
- 进度跟踪（百分比 + 状态）
- 暂停/继续/取消
- 动态调整（任务拆分、重新规划）
- 自适应重试
- 结果汇总
- 执行时间预估
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger("ZhiYanWeb.Computer.TaskPlanner")


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class TaskPriority(int, Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


class Task:
    """单个任务"""

    def __init__(
        self,
        task_id: str,
        title: str,
        description: str = "",
        depends_on: List[str] = None,
        estimated_minutes: int = 0,
        priority: TaskPriority = TaskPriority.NORMAL,
        max_retries: int = 0,
        timeout_seconds: int = 0,
    ):
        self.task_id = task_id
        self.title = title
        self.description = description
        self.depends_on = depends_on or []
        self.estimated_minutes = estimated_minutes
        self.priority = priority
        self.max_retries = max_retries
        self.timeout_seconds = timeout_seconds
        self.status = TaskStatus.PENDING
        self.result: Any = None
        self.error: Optional[str] = None
        self.started_at: Optional[float] = None
        self.completed_at: Optional[float] = None
        self.execution_log: List[str] = []
        self.retry_count = 0
        self.subtasks: List[str] = []  # 如果任务被拆分，记录子任务 ID

    def to_dict(self) -> Dict:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "depends_on": self.depends_on,
            "estimated_minutes": self.estimated_minutes,
            "priority": self.priority.value,
            "max_retries": self.max_retries,
            "timeout_seconds": self.timeout_seconds,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration": round(self.completed_at - self.started_at, 2) if self.started_at and self.completed_at else None,
            "execution_log": self.execution_log[-10:],  # 只保留最近 10 条日志
            "retry_count": self.retry_count,
            "subtasks": self.subtasks,
        }

    def add_log(self, message: str):
        """添加执行日志"""
        timestamp = datetime.now().isoformat()
        self.execution_log.append(f"[{timestamp}] {message}")
        logger.debug(f"[{self.task_id}] {message}")


class TaskPlan:
    """任务计划"""

    def __init__(self, goal: str):
        self.plan_id = uuid.uuid4().hex[:12]
        self.goal = goal
        self.tasks: List[Task] = []
        self.created_at = time.time()
        self.updated_at = time.time()
        self.status = "active"  # active / paused / completed / cancelled

    def add_task(self, task: Task):
        self.tasks.append(task)
        self.updated_at = time.time()

    def remove_task(self, task_id: str) -> bool:
        """移除任务"""
        for i, t in enumerate(self.tasks):
            if t.task_id == task_id:
                self.tasks.pop(i)
                self.updated_at = time.time()
                return True
        return False

    def get_task(self, task_id: str) -> Optional[Task]:
        for t in self.tasks:
            if t.task_id == task_id:
                return t
        return None

    def get_next_tasks(self, max_count: int = 3) -> List[Task]:
        """获取可执行的下一个任务（依赖已完成，按优先级排序）"""
        ready = []
        for task in self.tasks:
            if task.status != TaskStatus.PENDING:
                continue
            # 检查依赖是否都已完成
            deps_met = all(
                any(t.task_id == dep and t.status == TaskStatus.COMPLETED for t in self.tasks)
                for dep in task.depends_on
            )
            if deps_met:
                ready.append(task)

        # 按优先级排序（高优先级在前）
        ready.sort(key=lambda t: t.priority.value, reverse=True)
        return ready[:max_count]

    def get_completed_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == TaskStatus.COMPLETED)

    def get_failed_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == TaskStatus.FAILED)

    def get_running_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == TaskStatus.RUNNING)

    def get_pending_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == TaskStatus.PENDING)

    def get_progress(self) -> float:
        if not self.tasks:
            return 0
        completed = self.get_completed_count()
        total = len(self.tasks)
        return round(completed / total * 100, 1)

    def get_estimated_remaining(self) -> int:
        """预估剩余时间（分钟）"""
        remaining = 0
        for t in self.tasks:
            if t.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                remaining += t.estimated_minutes
        return remaining

    def to_dict(self) -> Dict:
        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "status": self.status,
            "total_tasks": len(self.tasks),
            "completed": self.get_completed_count(),
            "failed": self.get_failed_count(),
            "running": self.get_running_count(),
            "pending": self.get_pending_count(),
            "progress": self.get_progress(),
            "estimated_remaining_minutes": self.get_estimated_remaining(),
            "tasks": [t.to_dict() for t in self.tasks],
            "created_at": datetime.fromtimestamp(self.created_at).isoformat(),
            "updated_at": datetime.fromtimestamp(self.updated_at).isoformat(),
        }


class TaskPlanner:
    """任务规划引擎"""

    def __init__(self):
        self._plans: Dict[str, TaskPlan] = {}
        self._current_plan_id: Optional[str] = None
        self._running_tasks: Dict[str, asyncio.Task] = {}
        self._cancelled = False
        # 最大并发执行任务数
        self._max_concurrent = 3
        # 失败重试延迟（秒）
        self._retry_delay = 2.0

    def create_plan(self, goal: str, tasks: List[Dict]) -> TaskPlan:
        """创建任务计划

        Args:
            goal: 用户目标
            tasks: 任务列表，每项含 title, description, depends_on, estimated_minutes, priority, max_retries

        Returns:
            TaskPlan: 创建的任务计划
        """
        plan = TaskPlan(goal)
        for t in tasks:
            task = Task(
                task_id=t.get("task_id", uuid.uuid4().hex[:8]),
                title=t.get("title", "未命名任务"),
                description=t.get("description", ""),
                depends_on=t.get("depends_on", []),
                estimated_minutes=t.get("estimated_minutes", 0),
                priority=TaskPriority(t.get("priority", 1)),
                max_retries=t.get("max_retries", 0),
                timeout_seconds=t.get("timeout_seconds", 0),
            )
            plan.add_task(task)

        self._plans[plan.plan_id] = plan
        self._current_plan_id = plan.plan_id
        logger.info(f"创建计划 {plan.plan_id}: {goal} ({len(tasks)} 个子任务)")
        return plan

    def get_plan(self, plan_id: str) -> Optional[TaskPlan]:
        return self._plans.get(plan_id)

    def get_current_plan(self) -> Optional[TaskPlan]:
        if self._current_plan_id:
            return self._plans.get(self._current_plan_id)
        return None

    def list_plans(self) -> List[Dict]:
        return [p.to_dict() for p in self._plans.values()]

    def delete_plan(self, plan_id: str) -> bool:
        """删除计划"""
        if plan_id in self._plans:
            del self._plans[plan_id]
            if self._current_plan_id == plan_id:
                self._current_plan_id = None
            return True
        return False

    def pause_plan(self, plan_id: str) -> bool:
        """暂停计划"""
        plan = self._plans.get(plan_id)
        if not plan:
            return False
        plan.status = "paused"
        plan.updated_at = time.time()
        logger.info(f"计划已暂停: {plan_id}")
        return True

    def resume_plan(self, plan_id: str) -> bool:
        """恢复计划"""
        plan = self._plans.get(plan_id)
        if not plan:
            return False
        plan.status = "active"
        plan.updated_at = time.time()
        logger.info(f"计划已恢复: {plan_id}")
        return True

    def split_task(self, plan_id: str, task_id: str, subtasks: List[Dict]) -> Optional[TaskPlan]:
        """将一个大任务拆分为多个子任务

        Args:
            plan_id: 计划 ID
            task_id: 要拆分的任务 ID
            subtasks: 子任务列表

        Returns:
            Optional[TaskPlan]: 更新后的计划
        """
        plan = self._plans.get(plan_id)
        if not plan:
            return None

        task = plan.get_task(task_id)
        if not task:
            return None

        # 标记原任务为已拆分
        task.status = TaskStatus.SKIPPED
        task.add_log("已拆分为子任务")

        # 创建子任务
        for i, st in enumerate(subtasks):
            sub_task = Task(
                task_id=uuid.uuid4().hex[:8],
                title=st.get("title", f"子任务 {i+1}"),
                description=st.get("description", ""),
                depends_on=st.get("depends_on", []) + task.depends_on,
                estimated_minutes=st.get("estimated_minutes", 5),
                priority=task.priority,
                max_retries=st.get("max_retries", task.max_retries),
            )
            plan.add_task(sub_task)
            task.subtasks.append(sub_task.task_id)

        plan.updated_at = time.time()
        logger.info(f"任务 {task_id} 已拆分为 {len(subtasks)} 个子任务")
        return plan

    async def execute_plan(
        self,
        plan_id: str,
        task_executor: Callable[[Task], Any],
    ) -> TaskPlan:
        """执行计划

        Args:
            plan_id: 计划 ID
            task_executor: 任务执行回调函数，接收 Task 对象，返回执行结果

        Returns:
            TaskPlan: 执行完的计划
        """
        plan = self._plans.get(plan_id)
        if not plan:
            raise ValueError(f"计划不存在: {plan_id}")

        self._cancelled = False

        while not self._cancelled:
            # 检查是否暂停
            if plan.status == "paused":
                await asyncio.sleep(1)
                continue

            ready_tasks = plan.get_next_tasks(max_count=self._max_concurrent)
            if not ready_tasks:
                # 检查是否所有任务都已经结束
                if all(t.status in (
                    TaskStatus.COMPLETED, TaskStatus.FAILED,
                    TaskStatus.SKIPPED, TaskStatus.CANCELLED
                ) for t in plan.tasks):
                    break
                # 有任务正在执行，等待
                await asyncio.sleep(0.5)
                continue

            # 执行就绪的任务
            for task in ready_tasks:
                if self._cancelled:
                    break

                task.status = TaskStatus.RUNNING
                task.started_at = time.time()
                task.add_log(f"开始执行 (优先级: {task.priority.name})")

                # 带超时执行
                try:
                    if task.timeout_seconds > 0:
                        result = await asyncio.wait_for(
                            task_executor(task),
                            timeout=task.timeout_seconds,
                        )
                    else:
                        result = await task_executor(task)

                    task.result = result
                    task.status = TaskStatus.COMPLETED
                    task.add_log("执行完成")
                    logger.info(f"任务完成: {task.title}")

                except asyncio.TimeoutError:
                    task.error = "执行超时"
                    task.status = TaskStatus.FAILED
                    task.add_log("执行超时")
                    logger.warning(f"任务超时: {task.title}")

                except Exception as e:
                    task.error = str(e)
                    task.status = TaskStatus.FAILED
                    task.add_log(f"执行失败: {e}")
                    logger.error(f"任务失败: {task.title} - {e}")

                    # 自动重试
                    if task.retry_count < task.max_retries:
                        task.retry_count += 1
                        task.status = TaskStatus.PENDING
                        task.add_log(f"准备重试 ({task.retry_count}/{task.max_retries})")
                        await asyncio.sleep(self._retry_delay)
                        logger.info(f"任务重试: {task.title} ({task.retry_count}/{task.max_retries})")

                task.completed_at = time.time()
                plan.updated_at = time.time()

        # 更新计划状态
        if all(t.status == TaskStatus.COMPLETED for t in plan.tasks):
            plan.status = "completed"
        elif self._cancelled:
            plan.status = "cancelled"

        return plan

    def cancel_plan(self, plan_id: str) -> bool:
        """取消计划"""
        plan = self._plans.get(plan_id)
        if not plan:
            return False

        self._cancelled = True
        for task in plan.tasks:
            if task.status == TaskStatus.RUNNING:
                task.status = TaskStatus.CANCELLED
                task.completed_at = time.time()
                task.add_log("已取消")
        plan.status = "cancelled"
        plan.updated_at = time.time()
        logger.info(f"计划已取消: {plan_id}")
        return True

    def auto_decompose(self, goal: str, llm_call: Callable[[str], str], context: str = "") -> TaskPlan:
        """使用 LLM 自动分解任务（增强版，支持上下文感知）

        Args:
            goal: 用户目标
            llm_call: LLM 调用函数，输入 prompt，返回任务列表 JSON
            context: 额外的上下文信息（如历史执行结果、记忆等）

        Returns:
            TaskPlan: 分解后的任务计划
        """
        context_section = ""
        if context:
            context_section = f"\n参考上下文:\n{context}\n"

        prompt = f"""请将以下用户目标分解为可执行的子任务列表。

用户目标: {goal}
{context_section}
要求:
1. 将目标分解为 3-8 个具体的子任务
2. 每个子任务应该具体、可执行（单一职责）
3. 标注任务之间的依赖关系（使用 DAG）
4. 预估每个任务的执行时间（分钟）
5. 标注任务优先级（0=低, 1=普通, 2=高, 3=紧急）
6. 对复杂任务标注 max_retries（最大重试次数，默认 0）

请以 JSON 格式返回，格式如下:
{{
    "tasks": [
        {{
            "title": "子任务标题",
            "description": "子任务描述",
            "depends_on": ["依赖的任务标题数组"],
            "estimated_minutes": 10,
            "priority": 1,
            "max_retries": 0
        }}
    ]
}}

注意:
- 第一个任务没有依赖
- 后续任务如果依赖前面的任务，请使用前面任务的 title 作为依赖标识
- 高优先级任务应优先执行
- 复杂或易出错的任务应设置 max_retries > 0
"""

        try:
            response = llm_call(prompt)
            # 解析 JSON
            data = json.loads(response)
            tasks = data.get("tasks", [])

            # 处理依赖关系 - 将 title 引用转换为 task_id 引用
            title_to_id = {}
            for i, t in enumerate(tasks):
                tid = t.get("task_id", f"task_{i + 1:02d}")
                title_to_id[t["title"]] = tid
                t["task_id"] = tid

            for t in tasks:
                tid = title_to_id[t["title"]]
                t["depends_on"] = [
                    title_to_id.get(dep, dep) for dep in t.get("depends_on", [])
                    if dep in title_to_id
                ]

            return self.create_plan(goal, tasks)
        except Exception as e:
            logger.error(f"自动分解任务失败: {e}")
            # 降级为简单计划
            return self.create_plan(goal, [
                {"title": "分析需求", "description": f"理解用户目标: {goal}", "depends_on": [], "estimated_minutes": 2, "priority": 1},
                {"title": "执行主要工作", "description": f"执行核心任务: {goal}", "depends_on": ["task_01"], "estimated_minutes": 15, "priority": 2},
                {"title": "验证结果", "description": "验证执行结果是否符合预期", "depends_on": ["task_02"], "estimated_minutes": 3, "priority": 1},
            ])

    def adaptive_adjust(self, plan_id: str, task_executor: Callable[[Task], Any]) -> Optional[TaskPlan]:
        """自适应调整计划（根据已执行结果动态调整后续任务）

        在计划执行过程中，根据已完成任务的结果动态调整
        后续任务的优先级、参数或是否需要拆分。

        Args:
            plan_id: 计划 ID
            task_executor: 任务执行回调函数

        Returns:
            Optional[TaskPlan]: 调整后的计划
        """
        plan = self._plans.get(plan_id)
        if not plan:
            return None

        completed = [t for t in plan.tasks if t.status == TaskStatus.COMPLETED]
        if not completed:
            return plan

        # 根据已完成任务的结果调整后续任务
        for t in plan.tasks:
            if t.status != TaskStatus.PENDING:
                continue

            # 如果前置任务失败，降低依赖任务的优先级
            failed_deps = [
                dep for dep in t.depends_on
                if any(ct.task_id == dep and ct.status == TaskStatus.FAILED for ct in plan.tasks)
            ]
            if failed_deps:
                t.add_log(f"前置任务失败，调整优先级: {', '.join(failed_deps)}")
                t.priority = TaskPriority.LOW

        plan.updated_at = time.time()
        return plan


# 全局单例
_task_planner: Optional[TaskPlanner] = None


def get_task_planner() -> TaskPlanner:
    global _task_planner
    if _task_planner is None:
        _task_planner = TaskPlanner()
    return _task_planner