"""
工具循环智能体运行器

实现 Plan-Observe-Act 循环：
1. LLM 生成响应（可含工具调用）
2. 如果有工具调用 → 执行工具 → 将结果追加到上下文 → 继续循环
3. 如果无工具调用 → 完成

支持：
- Provider 回退链（主 provider 失败时自动切换）
- 空输出自动重试
- 用户中断
- 钩子系统
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator
from typing import TYPE_CHECKING, Any, Generic

from ..run_context import AgentResponse, ContextWrapper, LLMResponse, Message
from ..tool import FunctionTool, ToolSet
from .base import AgentState, BaseAgentRunner

if TYPE_CHECKING:
    from zhiyan_web.core.llm.providers import LLMProvider

logger = logging.getLogger("ZhiYanWeb.Agent")


class ToolLoopAgentRunner(BaseAgentRunner):
    """工具循环智能体运行器

    实现 Plan-Observe-Act 循环。
    """

    def __init__(
        self,
        llm_provider: "LLMProvider" | None = None,
        fallback_providers: list["LLMProvider"] | None = None,
        tool_set: ToolSet | None = None,
    ):
        super().__init__()
        self.llm_provider = llm_provider
        self.fallback_providers = fallback_providers or []
        self.tool_set = tool_set or ToolSet()
        self._abort_signal = asyncio.Event()
        self._step_count = 0
        self._max_retries = 3
        self._max_empty_retries = 3

    def set_provider(self, provider: "LLMProvider") -> None:
        """设置 LLM 提供商"""
        self.llm_provider = provider

    def set_tool_set(self, tool_set: ToolSet) -> None:
        """设置工具集"""
        self.tool_set = tool_set

    def done(self) -> bool:
        return self.state in (AgentState.DONE, AgentState.ERROR)

    def request_stop(self) -> None:
        """请求停止"""
        self._abort_signal.set()
        logger.info("智能体已收到停止请求")

    async def step_until_done(
        self, max_step: int = 10
    ) -> AsyncGenerator[AgentResponse, None]:
        """执行步骤直到完成

        Args:
            max_step: 最大步数

        Yields:
            AgentResponse: 每步的响应
        """
        if not self.llm_provider:
            yield AgentResponse(
                type="error",
                content="错误：LLM 提供商未配置",
            )
            self._transition_state(AgentState.ERROR)
            return

        self._transition_state(AgentState.RUNNING)
        await self._on_agent_begin()

        self._step_count = 0

        while not self.done() and self._step_count < max_step:
            self._step_count += 1

            try:
                async for resp in self._step():
                    yield resp

                if self.state == AgentState.RUNNING and self._step_count >= max_step:
                    # 达到最大步数，强制总结
                    await self._force_summarize()
                    async for resp in self._stream_final():
                        yield resp

            except Exception as e:
                logger.error(f"智能体步进异常 (step {self._step_count}): {e}")
                self._transition_state(AgentState.ERROR)
                yield AgentResponse(
                    type="error",
                    content=f"智能体执行错误: {str(e)}",
                )

        await self._on_agent_done(self.run_context)

    async def _step(self) -> AsyncGenerator[AgentResponse, None]:
        """单步执行"""
        if self._abort_signal.is_set():
            await self._finalize_aborted()
            return

        # 1. LLM 请求（含回退链）
        try:
            llm_response = await self._call_llm_with_fallback()
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            yield AgentResponse(type="error", content=f"LLM 调用失败: {str(e)}")
            self._transition_state(AgentState.ERROR)
            return

        if llm_response.role == "err":
            err_msg = llm_response.content or "LLM 返回错误"
            yield AgentResponse(type="error", content=err_msg)
            self._transition_state(AgentState.ERROR)
            return

        # 2. 流式输出响应
        if llm_response.content:
            yield AgentResponse(
                type="streaming_delta",
                content=llm_response.content,
            )

        # 3. 检查工具调用
        if llm_response.tool_calls:
            yield AgentResponse(
                type="text",
                content="\n[调用工具中...]\n",
            )

            try:
                await self._handle_tool_calls(llm_response.tool_calls)
            except Exception as e:
                logger.error(f"工具执行失败: {e}")
                yield AgentResponse(
                    type="error",
                    content=f"工具执行失败: {str(e)}",
                )
                self._transition_state(AgentState.ERROR)
                return
        else:
            # 无工具调用 → 完成
            self._transition_state(AgentState.DONE)
            yield AgentResponse(
                type="done",
                content=llm_response.content or "",
            )

    async def _call_llm_with_fallback(self) -> LLMResponse:
        """调用 LLM（含回退链）

        主 provider 失败时自动切换到备用 provider。
        """
        all_providers = [self.llm_provider] + self.fallback_providers
        last_error = None

        for idx, provider in enumerate(all_providers):
            if provider is None:
                continue
            try:
                logger.debug(f"LLM 调用尝试 {idx + 1}/{len(all_providers)}: {provider.name}")

                # 构建消息列表
                messages = self._build_messages()

                # 空输出重试
                for attempt in range(self._max_empty_retries):
                    response = await provider.chat(
                        messages,
                        system_prompt=self._get_system_prompt(),
                    )

                    if response and response.content:
                        return LLMResponse(
                            role="assistant",
                            content=response.content,
                            tool_calls=getattr(response, "tool_calls", None),
                            usage=getattr(response, "usage", None),
                            model=getattr(response, "model", provider._model if hasattr(provider, '_model') else ""),
                        )

                    logger.warning(f"LLM 返回空响应 (attempt {attempt + 1})")
                    if attempt < self._max_empty_retries - 1:
                        await asyncio.sleep(1 + attempt * 1.5)

                # 所有重试都失败，尝试下一个 provider
                last_error = "空响应"
                continue

            except Exception as e:
                logger.warning(f"Provider {provider.name} 失败: {e}")
                last_error = e
                continue

        # 所有 provider 都失败
        error_msg = str(last_error) if last_error else "所有模型均已失败"
        return LLMResponse(role="err", content=f"错误：{error_msg}")

    def _build_messages(self) -> list:
        """构建 LLM 消息列表"""
        if not self.run_context:
            return []
        messages = []
        for msg in self.run_context.messages:
            entry = {"role": msg.role, "content": msg.content}
            if msg.type == "tool_call" and msg.name:
                entry["name"] = msg.name
            if msg.tool_call_id:
                entry["tool_call_id"] = msg.tool_call_id
            messages.append(entry)
        return messages

    def _get_system_prompt(self) -> str:
        """获取系统提示词（含安全管控与工作区边界提示）

        在普通模式下主动告知 AI 工作区路径与写入边界，
        避免 AI 反复尝试写入工作区外内容被安全拦截而浪费 tokens。
        """
        base = ""
        try:
            from zhiyan_web.config import get_settings
            base = get_settings().get("system_prompt", "")
        except Exception:
            base = ""

        # 注入安全管控边界提示
        try:
            from zhiyan_web.computer.services.safety_manager import (
                get_security_mode,
                MODE_NORMAL,
                MODE_FULL_CONTROL,
                MODE_HIGH_SECURITY,
            )
            from zhiyan_web.computer.services.workspace_manager import get_workspace_dir

            mode = get_security_mode()
            ws = get_workspace_dir()

            boundary = "\n\n【系统权限边界 · 必须遵守】"
            if mode == MODE_FULL_CONTROL:
                boundary += (
                    "\n当前为「完全控制」模式：你对整台电脑拥有完全读写权限，可执行任意命令。"
                    "\n注意：此模式重启后自动恢复为普通模式。涉及格式化、磁盘分区、注册表、关机等高危操作前必须先告知用户并取得确认。"
                )
            elif mode == MODE_HIGH_SECURITY:
                boundary += (
                    "\n当前为「高权限安全」模式："
                    "\n- 写入仅限工作区；禁止操作系统关键路径（C:\\Windows、Program Files 等）及可执行文件（.exe/.dll/.bat/.ps1 等）。"
                    "\n- 禁止危险命令（format、diskpart、reg delete、taskkill /f、shutdown 等）。"
                )
                if ws:
                    boundary += f"\n- 工作区路径：{ws}"
            else:
                boundary += (
                    "\n当前为「普通默认」模式："
                    "\n- 写入/修改/删除文件：仅允许在工作区内，绝对禁止写入工作区以外的任何路径。"
                    "\n- 读取文件：允许全盘读取。"
                    "\n- 执行命令：禁止危险命令（格式化、磁盘操作、系统配置修改、强制删除等）。"
                )
                if ws:
                    boundary += (
                        f"\n- 工作区路径：{ws}"
                        "\n- 需要创建或修改文件时，目标路径必须位于工作区内；若用户要求的路径在工作区之外，"
                        "请主动提示用户或改用工作区内的路径，不要直接尝试写入（会被安全拦截）。"
                    )
            return base + boundary
        except Exception as e:
            logger.debug(f"注入安全边界提示失败: {e}")
            return base

    async def _handle_tool_calls(self, tool_calls: list[dict]) -> None:
        """处理工具调用"""
        for tc in tool_calls:
            if self._abort_signal.is_set():
                break

            tool_name = tc.get("name", tc.get("function", {}).get("name", ""))
            raw_args = tc.get("args", tc.get("function", {}).get("arguments", "{}"))

            if isinstance(raw_args, str):
                import json
                try:
                    tool_args = json.loads(raw_args)
                except json.JSONDecodeError:
                    tool_args = {}
            else:
                tool_args = raw_args

            tool = self.tool_set.get_tool(tool_name)
            if not tool or not tool.handler:
                logger.warning(f"工具未找到: {tool_name}")
                self.run_context.messages.append(
                    Message(
                        role="tool",
                        content=f"错误：工具 {tool_name} 未找到",
                        type="tool_result",
                        name=tool_name,
                        tool_call_id=tc.get("id", ""),
                    )
                )
                continue

            await self._on_tool_start(tool, tool_args)

            try:
                result = await tool.handler(**tool_args)
                result_str = str(result) if result is not None else ""

                self.run_context.messages.append(
                    Message(
                        role="tool",
                        content=result_str,
                        type="tool_result",
                        name=tool_name,
                        tool_call_id=tc.get("id", ""),
                    )
                )

                await self._on_tool_end(tool, tool_args, result)

            except Exception as e:
                logger.error(f"工具执行失败 {tool_name}: {e}")
                self.run_context.messages.append(
                    Message(
                        role="tool",
                        content=f"错误：{str(e)}",
                        type="tool_result",
                        name=tool_name,
                        tool_call_id=tc.get("id", ""),
                    )
                )
                await self._on_tool_end(tool, tool_args, None)

    async def _force_summarize(self) -> None:
        """强制总结（达到最大步数时）"""
        if not self.run_context:
            return
        self.run_context.messages.append(
            Message(
                role="user",
                content="工具调用次数已达到上限，请总结工具调用的结果并回复用户。",
            )
        )
        self.tool_set = ToolSet()  # 移除所有工具

    async def _stream_final(self) -> AsyncGenerator[AgentResponse, None]:
        """流式输出最终结果"""
        try:
            provider = self.llm_provider
            response = await provider.chat(
                self._build_messages(),
                system_prompt=self._get_system_prompt(),
            )
            if response and response.content:
                yield AgentResponse(type="done", content=response.content)
                self._transition_state(AgentState.DONE)
            else:
                self._transition_state(AgentState.ERROR)
                yield AgentResponse(type="error", content="总结失败")
        except Exception as e:
            logger.error(f"强制总结异常: {e}")
            self._transition_state(AgentState.ERROR)
            yield AgentResponse(type="error", content=f"总结失败: {str(e)}")

    async def _finalize_aborted(self) -> None:
        """用户中断处理"""
        self._transition_state(AgentState.DONE)
        self._abort_signal.clear()
        logger.info("智能体已被用户中断")