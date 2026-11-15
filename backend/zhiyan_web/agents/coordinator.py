"""
Coordinator - 任务分解与分派引擎

核心职责：
1. 接收用户输入，判断是否需要拆解任务
2. 如需拆解，将任务分解为子任务并分派给合适的 Worker
3. 汇总各 Worker 结果，输出完整回复
4. 需要用户决策时，弹出选择卡片
"""
from __future__ import annotations

import json
import logging
import asyncio
from typing import Optional, List, Dict, Callable, Awaitable, AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime

from zhiyan_web.agents.registry import get_registry, AgentConfig

logger = logging.getLogger("ZhiYanWeb.coordinator")

# 最大 Worker 并行数
MAX_WORKER_PARALLEL = 3


@dataclass
class SubTask:
    """子任务"""
    id: str
    description: str
    assigned_to: str  # agent_id
    status: str = "pending"  # pending | running | done | failed
    result: str = ""
    error: str = ""


@dataclass
class TaskPlan:
    """任务计划"""
    user_input: str
    subtasks: List[SubTask] = field(default_factory=list)
    needs_decision: bool = False
    decision_options: List[str] = field(default_factory=list)
    decision_prompt: str = ""
    summary: str = ""


class Coordinator:
    """任务协调器"""

    def __init__(self):
        self.registry = get_registry()

    async def process(
        self,
        user_input: str,
        chat_history: list,
        on_chunk: Callable[[str], Awaitable[None]],
        on_decision: Optional[Callable[[dict], Awaitable[None]]] = None,
    ) -> str:
        """处理用户输入 - 判断是否需要拆解任务"""
        coordinator = self.registry.get_coordinator()
        if not coordinator:
            logger.warning("未找到 Coordinator Agent，使用直接回复模式")
            return await self._direct_reply(user_input, chat_history, on_chunk)

        # 判断是否需要拆解
        plan = await self._analyze(user_input, coordinator)

        if plan.needs_decision:
            # 需要用户决策
            if on_decision:
                await on_decision({
                    "type": "decision",
                    "prompt": plan.decision_prompt,
                    "options": plan.decision_options,
                    "subtasks": [asdict(s) for s in plan.subtasks],
                })
            # 等待用户决策（由外部注入）
            return "【等待用户决策】"

        if not plan.subtasks:
            # 简单任务，直接回复
            return await self._direct_reply(user_input, chat_history, on_chunk)

        # 复杂任务：拆解执行
        await self._execute_tasks(plan, on_chunk)
        summary = await self._summarize(plan, coordinator)
        return summary

    async def _analyze(self, user_input: str, coordinator: AgentConfig) -> TaskPlan:
        """分析用户输入，制定任务计划"""
        plan = TaskPlan(user_input=user_input)

        system_prompt = f"""你是一个任务分析器。分析用户输入，判断是否需要拆解为多个子任务。

可用 Worker 列表：
{self._format_workers()}

请按以下格式输出 JSON：
{{
    "simple": true/false,        // true=直接回答，false=需拆解
    "subtasks": [                 // 需拆解时填写子任务列表
        {{"description": "子任务描述", "assign_to": "agent_id"}}
    ],
    "needs_decision": true/false, // 是否需要用户选择
    "decision_prompt": "",        // 需要决策时的提示文字
    "decision_options": []        // 决策选项
}}

判断规则：
1. 简单问候、闲聊、感谢 → simple: true
2. 需要多步操作、多专业知识 → 拆解子任务
3. 任务不明确、有多个方向 → 需要用户决策
4. 每个子任务分配最合适的 Worker"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]

        try:
            response = ""
            async for chunk in self._stream_llm(messages, coordinator.temperature):
                response += chunk

            result = self._extract_json(response)
            if result:
                if result.get("simple", True):
                    return plan  # 直接回复，无子任务

                for st in result.get("subtasks", []):
                    agent_id = st.get("assign_to", "zhiyan")
                    plan.subtasks.append(SubTask(
                        id=f"task_{len(plan.subtasks) + 1}",
                        description=st.get("description", ""),
                        assigned_to=agent_id,
                    ))

                plan.needs_decision = result.get("needs_decision", False)
                plan.decision_prompt = result.get("decision_prompt", "")
                plan.decision_options = result.get("decision_options", [])
        except Exception as e:
            logger.error(f"任务分析失败: {e}")

        return plan

    def _format_workers(self) -> str:
        """格式化 Worker 列表供 LLM 参考"""
        workers = self.registry.get_workers()
        return "\n".join([
            f"- {w.agent_id} ({w.name}): {w.avatar} {w.system_prompt[:50]}..."
            for w in workers
        ])

    async def _execute_tasks(self, plan: TaskPlan, on_chunk):
        """并行执行子任务"""
        workers = self.registry.get_workers()
        worker_map = {w.agent_id: w for w in workers}

        # 并行执行子任务
        async def run_task(subtask: SubTask):
            subtask.status = "running"
            worker = worker_map.get(subtask.assigned_to)
            if not worker:
                subtask.status = "failed"
                subtask.error = f"未找到 Worker: {subtask.assigned_to}"
                return

            system_prompt = f"""你是一名 Worker，负责执行特定子任务。

你的身份：{worker.name} {worker.avatar}
你的职责：{worker.system_prompt[:200]}

用户原始需求：{plan.user_input}
子任务：{subtask.description}

请专注于完成这个子任务，输出完整的结果。"""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": subtask.description},
            ]

            # 通知前端开始执行子任务
            await on_chunk(f"\n\n[ {worker.avatar} {worker.name} 正在执行: {subtask.description} ]\n\n")

            try:
                response = ""
                async for chunk in self._stream_llm(messages, worker.temperature):
                    response += chunk
                    await on_chunk(chunk)
                subtask.result = response
                subtask.status = "done"
            except Exception as e:
                subtask.status = "failed"
                subtask.error = str(e)
                await on_chunk(f"\n\n[ {worker.name} 执行失败: {e} ]\n\n")

        # 控制并行度
        semaphore = asyncio.Semaphore(MAX_WORKER_PARALLEL)

        async def limited_task(subtask):
            async with semaphore:
                await run_task(subtask)

        tasks = [limited_task(st) for st in plan.subtasks]
        if tasks:
            await asyncio.gather(*tasks)

    async def _summarize(self, plan: TaskPlan, coordinator: AgentConfig) -> str:
        """汇总各 Worker 结果"""
        done = [s for s in plan.subtasks if s.status == "done"]
        failed = [s for s in plan.subtasks if s.status == "failed"]

        if not done:
            return "团队成员执行任务时出现错误，请稍后重试。"

        workers = self.registry.get_workers()
        worker_map = {w.agent_id: w for w in workers}

        results_text = ""
        for s in done:
            worker = worker_map.get(s.assigned_to, None)
            name = f"{worker.avatar} {worker.name}" if worker else s.assigned_to
            results_text += f"\n## {name} - {s.description}\n{s.result}\n"

        if failed:
            results_text += "\n## 执行失败的任务\n"
            for s in failed:
                results_text += f"- {s.description}: {s.error}\n"

        system_prompt = f"""你是项目经理，负责汇总团队成员的执行结果。

请将以下结果整理成一段连贯、完整的回复给用户：
1. 先概括核心结论
2. 再分点展示各成员的工作成果
3. 如有失败任务，提供解决方案或建议

保持简洁明了，重点突出。"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"用户原始需求：{plan.user_input}\n\n团队执行结果：{results_text}"},
        ]

        summary = ""
        async for chunk in self._stream_llm(messages, 0.5):
            summary += chunk

        return summary

    async def _direct_reply(self, user_input: str, chat_history: list, on_chunk) -> str:
        """直接回复（简单任务）"""
        full = ""
        async for chunk in self._stream_llm(
            [{"role": "user", "content": user_input}],
            temperature=0.7,
        ):
            full += chunk
            await on_chunk(chunk)
        return full

    async def _stream_llm(self, messages: list, temperature: float = 0.7):
        """流式调用 LLM"""
        try:
            from zhiyan_web.app import get_chat_manager
            cm = get_chat_manager()
            provider = cm.get_provider() if cm else None
            if not provider:
                yield "[系统] LLM 提供商未配置"
                return

            system_prompt = ""
            llm_messages = []

            for m in messages:
                if m.get("role") == "system":
                    system_prompt = m.get("content", "")
                else:
                    from zhiyan_web.core.llm import LLMMessage
                    llm_messages.append(LLMMessage(
                        role=m.get("role", "user"),
                        content=m.get("content", ""),
                    ))

            full = ""
            async for chunk in provider.chat_stream(llm_messages, system_prompt=system_prompt, temperature=temperature):
                # 过滤 DeepSeek 思考模式泄漏到正文的 __REASONING__: 标记
                if chunk.startswith("__REASONING__:"):
                    continue
                full += chunk
                yield chunk
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            yield f"\n\n[系统错误] {e}"

    def _extract_json(self, text: str) -> Optional[dict]:
        """从文本中提取 JSON"""
        try:
            # 尝试直接解析
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试从代码块中提取
        import re
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # 尝试查找第一个 { 和最后一个 }
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass

        return None