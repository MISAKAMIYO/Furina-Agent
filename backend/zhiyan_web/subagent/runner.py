"""
子代理运行器

在父 agent 的 ReAct 循环内部，用一份**独立的消息列表**跑完一个自包含子任务，
最后只把结论回灌父上下文。这是子代理最核心的价值：把「探索过程的噪音」
（几十次工具调用、大段原始输出）挡在主对话历史之外。

与 DSH 内核的对应关系：DSH 的 tool-subagent 由内核进程内实现；这里是软件自身
引擎的实现，能力对齐并额外支持：并发、受控嵌套、后台运行、取消、token 计量、
共享白板、结论逐字回传。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Sequence

from zhiyan_web.core.llm.providers import LLMMessage, LLMProvider

from . import events
from .board import current_board, reset_author, set_author
from .registry import SubAgentType, default_type, get_subagent_type
from .runtime import (
    TurnBudget,
    current_budget,
    finish_run,
    is_cancelled,
    start_run,
)

logger = logging.getLogger("ZhiYanWeb.SubAgent.Runner")

# 回灌给父代理的结论字数上限：子代理跑得越久产物越长，但父上下文不该被它撑爆
RESULT_MAX_CHARS = 6000
# 单个子代理连续工具失败上限
MAX_CONSECUTIVE_FAILURES = 6
# 父流里子代理工具的展示上限（避免一条事件过大）
PREVIEW_CHARS = 240
# 结论回传时每片的字符数（逐字效果的粒度）
DELTA_CHUNK = 14

# 只属于父对话流的虚拟工具，子代理不可用
PARENT_ONLY_TOOLS = {"todo_list", "ask_questions"}
# 子代理工具分组名：是否放行由嵌套深度决定
SUBAGENT_GROUP = "subagent"


def _resolve_provider(provider: Optional[LLMProvider]) -> Optional[LLMProvider]:
    if provider is not None:
        return provider
    try:
        from zhiyan_web.app import get_chat_manager
        return get_chat_manager().get_provider()
    except Exception as e:
        logger.debug(f"获取全局 provider 失败: {e}")
        return None


def _manager():
    """取函数工具管理器（优先核心实例，未初始化时回退到懒加载单例）。"""
    try:
        from zhiyan_web.provider.func_tool_manager import get_func_tool_manager
        return get_func_tool_manager()
    except Exception as e:
        logger.debug(f"获取 FunctionToolManager 失败: {e}")
        return None


def _all_tools() -> List[Any]:
    mgr = _manager()
    if mgr is None or mgr.empty():
        return []
    try:
        return list(mgr.get_full_tool_set().tools)
    except Exception as e:
        logger.debug(f"获取工具集失败: {e}")
        return []


def nesting_allowed(agent_type: SubAgentType, depth: int,
                    budget: Optional[TurnBudget] = None) -> bool:
    """这个子代理能否再往下派一层子代理。

    需要同时满足两个约束：类型自己允许（max_depth），且回合预算允许。
    """
    if depth <= 0:
        return False
    budget = budget if budget is not None else current_budget()
    if budget is not None and not budget.can_spawn_at(depth):
        return False
    return depth < int(agent_type.max_depth or 0)


def scope_tools(agent_type: SubAgentType, depth: int = 1,
                budget: Optional[TurnBudget] = None) -> List[Any]:
    """按子代理类型裁剪工具集（最小权限原则）。

    嵌套不许可时，subagent 分组的工具（spawn_subagent / delegate_to_agent /
    board_* 之外的派发工具）会被显式禁掉，子代理自然无法无限嵌套。
    """
    from zhiyan_web.agent import tool_groups

    tools = _all_tools()
    deny_groups = list(agent_type.deny_groups or [])
    if not nesting_allowed(agent_type, depth, budget) and SUBAGENT_GROUP not in deny_groups:
        deny_groups.append(SUBAGENT_GROUP)

    deny_tools = set(agent_type.deny_tools) | PARENT_ONLY_TOOLS
    scoped = tool_groups.filter_tools(
        tools,
        scenario=agent_type.scenario,
        allow_groups=agent_type.tool_groups,
        deny_groups=deny_groups,
        allow_tools=agent_type.allow_tools,
        deny_tools=list(deny_tools),
    )
    return [t for t in scoped if getattr(t, "name", "") not in PARENT_ONLY_TOOLS]


def _build_openai_tools(tools: Sequence[Any]) -> List[Dict[str, Any]]:
    result = []
    for tool in tools:
        if hasattr(tool, "to_openai_format"):
            result.append(tool.to_openai_format())
        else:
            result.append({
                "type": "function",
                "function": {
                    "name": getattr(tool, "name", ""),
                    "description": getattr(tool, "description", ""),
                    "parameters": getattr(tool, "parameters", None) or
                                  {"type": "object", "properties": {}},
                },
            })
    return result


def _describe_tools(tools: Sequence[Any]) -> str:
    return "\n".join(
        f"- {getattr(t, 'name', '')}: {getattr(t, 'description', '')}"
        for t in tools
    )


def _workspace_rules() -> str:
    """精简版工作区约束（子代理同样受写入限制）。"""
    try:
        from zhiyan_web.computer.services.workspace_manager import get_workspace_dir
        ws = get_workspace_dir()
        if not ws:
            return ("\n\n**写入限制:** 未配置工作区，所有写文件操作都会被拒绝；"
                    "需要写文件时直接说明需要用户先配置工作区。")
        return (f"\n\n**写入限制:** 只能写入工作区 {ws} 内的路径，"
                f"工作区之外一律会被拒绝；读取不受限制。")
    except Exception:
        return ""


def _build_system_prompt(agent_type: SubAgentType, tools: Sequence[Any],
                         parent_conv_id: str, run_id: str, depth: int,
                         can_nest: bool) -> str:
    base = agent_type.system_prompt or ""

    try:
        from zhiyan_web.skills.disclosure import format_skill_catalog
        base += format_skill_catalog(only=agent_type.skills or None)
    except Exception as e:
        logger.debug(f"注入技能清单失败: {e}")

    # 共享白板：并行同伴的中间发现，避免重复劳动
    try:
        board = current_board()
        if board is not None and not board.is_empty():
            base += board.digest(exclude_author_id=run_id)
    except Exception as e:
        logger.debug(f"注入共享白板失败: {e}")

    base += "\n\n**你可以使用的工具:**\n" + _describe_tools(tools)
    base += _workspace_rules()

    # 白板说明（有白板才有意义）
    if current_board() is not None:
        base += ("\n\n**共享白板:** 与你同时运行的子代理共用一块便签墙。"
                 "动手前先 board_read 看看别人已经查到什么（避免重复检索/重复读文件），"
                 "自己得到关键结论后立刻 board_write 写一条简短结论（key 用主题短语）。")

    if can_nest:
        base += ("\n\n**关于再派子代理:** 你可以在需要时把更细的子任务再派出去，"
                 "但只在确实能并行化或能隔离大量噪音时才这么做，不要为了拆而拆。")

    base += ("\n\n**产出要求:** 最后一条回复就是你要交付的结论。"
             "不要复述工具原始输出，把它消化成结论与必要的关键数据。")
    if parent_conv_id:
        base += (f"\n\n（本次为会话 {parent_conv_id} 派发的独立子任务，"
                 f"运行标识 {run_id}，当前层级 {depth}）")
    return base


async def _execute_tool(name: str, args: Dict[str, Any], max_retries: int = 2) -> Any:
    """执行一个工具（带一次重试），失败返回 {"error": ...} 而不抛异常。"""
    last_error = ""
    for attempt in range(1, max_retries + 1):
        try:
            mgr = _manager()
            if mgr is None:
                return {"error": "func_tool_manager 不可用"}
            tool = mgr.get_func(name)
            if tool is None or not tool.handler:
                return {"error": f"工具 '{name}' 未找到或没有 handler"}
            result = await tool.handler(**args)
            if isinstance(result, dict) and result.get("error") and attempt < max_retries:
                last_error = str(result.get("error"))
                await asyncio.sleep(0.5)
                continue
            return result
        except Exception as e:
            last_error = str(e)
            if attempt < max_retries:
                await asyncio.sleep(0.5)
    return {"error": last_error or "执行失败"}


def _is_success(result: Any) -> bool:
    if isinstance(result, dict):
        if result.get("error"):
            return False
        if "success" in result and not result.get("success"):
            return False
        if result.get("status") == "error":
            return False
    if isinstance(result, str) and result.startswith("错误"):
        return False
    return True


def _to_text(result: Any) -> str:
    if isinstance(result, (dict, list)):
        try:
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception:
            return str(result)
    return str(result)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = int(limit * 0.7)
    tail = limit - head
    return (text[:head]
            + f"\n\n…（中间省略 {len(text) - limit} 字）…\n\n"
            + text[-tail:])


def _accumulate_usage(total: Dict[str, int], usage: Any) -> None:
    if not isinstance(usage, dict):
        return
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        try:
            total[key] = total.get(key, 0) + int(usage.get(key) or 0)
        except (TypeError, ValueError):
            continue


async def _emit_conclusion(run_id: str, text: str) -> None:
    """把结论分片回传，让前端卡片能逐字显示而不是等一大坨。"""
    if not text:
        return
    # 优先按句读边界切，读起来自然；没有标点的长句再按字数硬切
    pieces: List[str] = []
    buffered = ""
    for seg in re.split(r'(?<=[。！？；\n.!?;])', text):
        if not seg:
            continue
        buffered += seg
        while len(buffered) >= DELTA_CHUNK:
            pieces.append(buffered[:DELTA_CHUNK])
            buffered = buffered[DELTA_CHUNK:]
    if buffered:
        pieces.append(buffered)

    for piece in pieces:
        await events.emit({"type": "subagent_delta", "id": run_id, "text": piece})
        await asyncio.sleep(0.01)


async def run_subagent(
    task: str,
    agent_type: Optional[SubAgentType] = None,
    *,
    provider: Optional[LLMProvider] = None,
    context: str = "",
    title: str = "",
    parent_conv_id: str = "",
    extra_instructions: str = "",
    max_iterations: Optional[int] = None,
    background: bool = False,
    job_id: str = "",
    parent_run_id: str = "",
) -> Dict[str, Any]:
    """跑一个子代理，返回结论字典（会作为工具结果回灌父代理）。

    Returns:
        {"ok": bool, "subagent": str, "label": str, "result": str,
         "iterations": int, "tool_calls": [...], "elapsed_sec": float,
         "tokens": {...}, "run_id": str, "status": str, "error": str}
    """
    task = (task or "").strip()
    if not task:
        return {"ok": False, "error": "子任务描述 task 不能为空"}

    agent_type = agent_type or default_type()
    depth = events.current_depth()
    started = time.time()
    tool_call_names: List[str] = []
    usage_total: Dict[str, int] = {}

    record = start_run(name=agent_type.name, label=agent_type.display(),
                       avatar=agent_type.avatar, task=task,
                       conv_id=parent_conv_id, depth=depth,
                       parent_id=parent_run_id, background=background)
    run_id = record.id

    budget = current_budget()
    can_nest = nesting_allowed(agent_type, depth, budget)

    await events.emit({
        "type": "subagent_start",
        "id": run_id,
        "name": agent_type.name,
        "label": agent_type.display(),
        "avatar": agent_type.avatar,
        "title": (title or task)[:120],
        "task": task[:500],
        "depth": depth,
        "background": background,
        "job_id": job_id,
    })

    async def _fail(err: str, status: str = "failed") -> Dict[str, Any]:
        elapsed = round(time.time() - started, 2)
        finish_run(run_id, ok=False, status=status, iterations=0,
                   tool_calls=tool_call_names, elapsed_sec=elapsed,
                   usage=usage_total, error=err)
        if budget is not None:
            budget.add_iterations(0)
        await events.emit({"type": "subagent_error", "id": run_id,
                           "label": agent_type.display(), "error": err,
                           "status": status, "background": background,
                           "job_id": job_id})
        return {"ok": False, "subagent": agent_type.name,
                "label": agent_type.display(), "run_id": run_id,
                "status": status, "error": err, "tool_calls": tool_call_names,
                "elapsed_sec": elapsed, "tokens": dict(usage_total)}

    # ── 预算门禁 ──
    if budget is not None:
        gate = budget.acquire_run()
        if not gate.get("ok"):
            return await _fail(gate.get("error", "超出子代理预算"), status="rejected")

    provider = _resolve_provider(provider)
    if provider is None or not provider.is_available():
        return await _fail("没有可用的 LLM 提供商，子代理无法启动")

    tools = scope_tools(agent_type, depth, budget)
    if not tools:
        return await _fail(
            f"子代理 {agent_type.name} 没有可用工具（工具场景 {agent_type.scenario}）")

    system_prompt = _build_system_prompt(agent_type, tools, parent_conv_id,
                                         run_id, depth, can_nest)
    if extra_instructions:
        system_prompt += f"\n\n**额外要求:**\n{extra_instructions}"

    user_content = task
    if context:
        user_content = f"{task}\n\n**已知背景:**\n{context}"

    messages: List[LLMMessage] = [LLMMessage(role="user", content=user_content)]

    kwargs: Dict[str, Any] = {
        "system_prompt": system_prompt,
        "tools": _build_openai_tools(tools),
        "temperature": agent_type.temperature,
        "max_tokens": 8192,
    }
    try:
        from zhiyan_web.config.settings import get_settings
        s = get_settings()
        kwargs["thinking_enabled"] = bool(s.get("deepseek_thinking_enabled", False))
        kwargs["reasoning_effort"] = s.get("deepseek_reasoning_effort", "high")
        kwargs["response_format"] = s.get("response_format", "text")
    except Exception:
        pass

    cap = int(max_iterations or agent_type.max_iterations or 24)
    # 回合总迭代预算也要卡：一个子代理不能把整轮的额度吃光
    if budget is not None:
        cap = min(cap, max(1, budget.remaining_iterations()))

    iteration = 0
    consecutive_failures = 0
    # 白板署名：标记当前执行者，写白板时自动带上，摘要里也会排除自己写的条目
    author_token = set_author(agent_type.display(), run_id)

    try:
        while iteration < cap:
            # ── 取消检查点 ──
            if is_cancelled(run_id):
                elapsed = round(time.time() - started, 2)
                finish_run(run_id, ok=False, status="cancelled",
                           iterations=iteration, tool_calls=tool_call_names,
                           elapsed_sec=elapsed, usage=usage_total,
                           error="已被用户取消")
                if budget is not None:
                    budget.add_iterations(iteration)
                await events.emit({"type": "subagent_error", "id": run_id,
                                   "label": agent_type.display(),
                                   "error": "已被用户取消", "status": "cancelled",
                                   "background": background, "job_id": job_id})
                return {"ok": False, "subagent": agent_type.name,
                        "label": agent_type.display(), "run_id": run_id,
                        "status": "cancelled", "error": "已被用户取消",
                        "iterations": iteration, "tool_calls": tool_call_names,
                        "elapsed_sec": elapsed, "tokens": dict(usage_total)}

            iteration += 1
            await events.emit({"type": "subagent_step", "id": run_id,
                               "iteration": iteration, "cap": cap})

            response = await provider.chat(messages, **kwargs)
            _accumulate_usage(usage_total, getattr(response, "usage", None))

            content = str(getattr(response, "content", "") or "")
            if content.startswith("错误："):
                raise RuntimeError(content)

            tool_calls = getattr(response, "tool_calls", None) or []
            if tool_calls:
                messages.append(LLMMessage(
                    role="assistant", content=content or "",
                    tool_calls=tool_calls,
                    reasoning_content=getattr(response, "reasoning_content", None),
                ))

                for tc in tool_calls:
                    if is_cancelled(run_id):
                        raise _Cancelled()
                    fn = (tc.get("function") or {}).get("name", "")
                    call_id = tc.get("id", "")
                    raw_args = (tc.get("function") or {}).get("arguments") or "{}"
                    try:
                        fn_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                    except (json.JSONDecodeError, TypeError):
                        fn_args = {}

                    if fn in PARENT_ONLY_TOOLS:
                        messages.append(LLMMessage(
                            role="tool",
                            content=json.dumps(
                                {"error": f"子代理不能使用 {fn}，请直接完成子任务"},
                                ensure_ascii=False),
                            tool_call_id=call_id,
                        ))
                        continue

                    tool_call_names.append(fn)
                    await events.emit({"type": "subagent_tool_call", "id": run_id,
                                       "name": fn, "args": fn_args,
                                       "background": background, "job_id": job_id})

                    result = await _execute_tool(fn, fn_args)
                    result_text = _to_text(result)
                    success = _is_success(result)
                    consecutive_failures = 0 if success else consecutive_failures + 1

                    await events.emit({
                        "type": "subagent_tool_result", "id": run_id,
                        "name": fn, "success": success,
                        "preview": result_text[:PREVIEW_CHARS],
                        "background": background, "job_id": job_id,
                    })

                    messages.append(LLMMessage(
                        role="tool",
                        content=_truncate(result_text, 20000),
                        tool_call_id=call_id,
                    ))

                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        raise RuntimeError(
                            f"连续 {consecutive_failures} 次工具调用失败，已中止子任务")

                continue

            # 无工具调用 → 这就是结论
            final = content or "（子代理没有产出内容）"
            # 逐字回传：前端卡片能看到结论在「写出来」，而不是突然出现
            if not background:
                await _emit_conclusion(run_id, final)

            elapsed = round(time.time() - started, 2)
            finish_run(run_id, ok=True, status="done", iterations=iteration,
                       tool_calls=tool_call_names, elapsed_sec=elapsed,
                       usage=usage_total, result_preview=final)
            if budget is not None:
                budget.add_iterations(iteration)
            await events.emit({
                "type": "subagent_done", "id": run_id, "ok": True,
                "label": agent_type.display(), "avatar": agent_type.avatar,
                "iterations": iteration, "tool_calls": tool_call_names,
                "elapsed_sec": elapsed, "preview": final[:PREVIEW_CHARS],
                "tokens": dict(usage_total), "background": background,
                "job_id": job_id,
            })
            return {
                "ok": True,
                "subagent": agent_type.name,
                "label": agent_type.display(),
                "run_id": run_id,
                "status": "done",
                "result": _truncate(final, RESULT_MAX_CHARS),
                "iterations": iteration,
                "tool_calls": tool_call_names,
                "elapsed_sec": elapsed,
                "tokens": dict(usage_total),
            }

        # 超过轮次预算：把最后一条内容当结论（有总比没有好），并显式标注
        elapsed = round(time.time() - started, 2)
        fallback = "（子代理达到轮次上限，未能给出完整结论；以下是最后一次输出）"
        last_content = ""
        for m in reversed(messages):
            if getattr(m, "role", "") == "assistant" and getattr(m, "content", ""):
                last_content = str(m.content)
                break
        final = (last_content + "\n\n" + fallback).strip()
        finish_run(run_id, ok=False, status="exhausted", iterations=iteration,
                   tool_calls=tool_call_names, elapsed_sec=elapsed,
                   usage=usage_total, error=f"达到轮次上限({cap})未完成",
                   result_preview=final)
        if budget is not None:
            budget.add_iterations(iteration)
        await events.emit({
            "type": "subagent_done", "id": run_id, "ok": False,
            "label": agent_type.display(), "avatar": agent_type.avatar,
            "iterations": iteration, "tool_calls": tool_call_names,
            "elapsed_sec": elapsed, "preview": final[:PREVIEW_CHARS],
            "tokens": dict(usage_total), "background": background,
            "job_id": job_id,
        })
        return {
            "ok": False,
            "subagent": agent_type.name,
            "label": agent_type.display(),
            "run_id": run_id,
            "status": "exhausted",
            "error": f"达到轮次上限({cap})未完成",
            "result": _truncate(final, RESULT_MAX_CHARS),
            "iterations": iteration,
            "tool_calls": tool_call_names,
            "elapsed_sec": elapsed,
            "tokens": dict(usage_total),
        }

    except _Cancelled:
        elapsed = round(time.time() - started, 2)
        finish_run(run_id, ok=False, status="cancelled", iterations=iteration,
                   tool_calls=tool_call_names, elapsed_sec=elapsed,
                   usage=usage_total, error="已被用户取消")
        if budget is not None:
            budget.add_iterations(iteration)
        await events.emit({"type": "subagent_error", "id": run_id,
                           "label": agent_type.display(), "error": "已被用户取消",
                           "status": "cancelled", "background": background,
                           "job_id": job_id})
        return {"ok": False, "subagent": agent_type.name,
                "label": agent_type.display(), "run_id": run_id,
                "status": "cancelled", "error": "已被用户取消",
                "iterations": iteration, "tool_calls": tool_call_names,
                "elapsed_sec": elapsed, "tokens": dict(usage_total)}

    except asyncio.CancelledError:
        finish_run(run_id, ok=False, status="cancelled", iterations=iteration,
                   tool_calls=tool_call_names,
                   elapsed_sec=round(time.time() - started, 2),
                   usage=usage_total, error="运行被中断")
        raise

    except Exception as e:
        logger.error(f"子代理 {agent_type.name} 运行失败: {e}")
        elapsed = round(time.time() - started, 2)
        finish_run(run_id, ok=False, status="failed", iterations=iteration,
                   tool_calls=tool_call_names, elapsed_sec=elapsed,
                   usage=usage_total, error=str(e))
        if budget is not None:
            budget.add_iterations(iteration)
        await events.emit({"type": "subagent_error", "id": run_id,
                           "label": agent_type.display(), "error": str(e),
                           "background": background, "job_id": job_id})
        return {
            "ok": False,
            "subagent": agent_type.name,
            "label": agent_type.display(),
            "run_id": run_id,
            "status": "failed",
            "error": str(e),
            "iterations": iteration,
            "tool_calls": tool_call_names,
            "elapsed_sec": elapsed,
            "tokens": dict(usage_total),
        }

    finally:
        # 无论从哪条路径退出（含各种 return 与异常），都要把署名还原，
        # 否则会污染父 agent 的 board_write 归属
        reset_author(author_token)


class _Cancelled(Exception):
    """内部取消信号（软取消走这条路，不抛 asyncio.CancelledError）。"""


def resolve_type(name: str) -> Optional[SubAgentType]:
    """供工具层复用：按名字找子代理类型。"""
    return get_subagent_type(name)
