"""
子代理工具注册

对外暴露的工具（对齐 DSH 的 tool-subagent / tool-subagent-list-agents）：

- spawn_subagent             临时起一个「内联子代理」，跑完只回结论
- spawn_background_subagent  后台跑（主 agent 不阻塞），完成后再回灌
- delegate_to_agent          交给 Agent 管理页里已配置的某个 Agent（带工具循环）
- list_subagents             列出可用类型与已配置 Agent
- board_read / board_write   并行子代理之间的共享白板

派发类工具属于 tool_groups 的 subagent 分组；子代理自身能否再拿到它们，由
runner 按「嵌套深度 + 回合预算」判断（见 runner.scope_tools）。白板工具单独
成组且在所有场景放行，被裁掉只会让并行子代理重复劳动。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from . import events
from .board import current_board
from .registry import (
    SubAgentType,
    default_type,
    describe_types,
    get_subagent_type,
    list_subagent_types,
)
from .runner import run_subagent

logger = logging.getLogger("ZhiYanWeb.SubAgent.Tools")

# 后台任务需要持有强引用，否则可能被 GC 掉
_background_tasks: set = set()


def _spawn_tool_description() -> str:
    """工具描述保持精简：类型的适用场景由系统提示词的「子代理」段落统一给出，
    这里再列一遍会重复占用 prompt token。"""
    return ("把一段自包含的子任务派给独立上下文的子代理执行，只回收它的结论。"
            "适合多步工具操作、会产生大量中间输出、需要独立探查后再汇报的子任务；"
            "子代理看不到对话历史，task 必须自包含。"
            "可用 agent_type: " + "/".join(t.name for t in list_subagent_types()) + "。")


def _resolve_type(name: str, allow_empty_default: bool = True):
    """按名字解析子代理类型，返回 (type, error)。"""
    if not name:
        return (default_type() if allow_empty_default else None), None
    stype = get_subagent_type(name)
    if stype is None:
        return None, {"ok": False,
                      "error": f"未知的子代理类型 '{name}'",
                      "available": [t.name for t in list_subagent_types()]}
    return stype, None


def _with_extra_tools(stype: SubAgentType, tools) -> SubAgentType:
    extras = [str(x).strip() for x in (tools or []) if str(x).strip()]
    if not extras:
        return stype
    return SubAgentType(**{**stype.__dict__,
                           "allow_tools": list(stype.allow_tools) + extras})


# ── 工具实现 ────────────────────────────────────────────────────────────────

async def _spawn_subagent(task: str = "", agent_type: str = "", context: str = "",
                          title: str = "", tools: Optional[list] = None,
                          instructions: str = "") -> Dict[str, Any]:
    """spawn_subagent 的 handler（同步等待子代理跑完）。"""
    if not (task or "").strip():
        return {"ok": False, "error": "task 不能为空：请写清楚要让子代理完成的子任务"}

    stype, err = _resolve_type(agent_type)
    if err:
        return err
    stype = _with_extra_tools(stype, tools)

    depth_token = events.push_depth()
    try:
        return await run_subagent(
            task, stype,
            context=context or "",
            title=title or "",
            parent_conv_id=_current_conv_id(),
            extra_instructions=instructions or "",
        )
    finally:
        events.pop_depth(depth_token)


async def _spawn_background_subagent(task: str = "", agent_type: str = "",
                                     context: str = "", title: str = "",
                                     instructions: str = "") -> Dict[str, Any]:
    """spawn_background_subagent 的 handler（立即返回，跑完再回灌）。"""
    if not (task or "").strip():
        return {"ok": False, "error": "task 不能为空：请写清楚要后台完成的事"}

    stype, err = _resolve_type(agent_type)
    if err:
        return err

    from .runtime import create_job, finish_job

    # 注意：这里不预先扣预算。run_subagent 内部会 acquire_run()，
    # 提前扣会导致一次后台任务吃掉两个额度。
    conv_id = _current_conv_id()
    task_text = f"{task}\n\n**已知背景:**\n{context}" if context else task
    logger.info(f"后台子代理任务已受理: {title or task[:60]}")

    async def _runner() -> None:
        # 后台运行必须切断父流的 SSE 通道：父对话流此时可能已经结束，
        # 事件推到一个没人消费的队列只会白占内存。
        events.set_sink(None)
        depth_token = events.push_depth()
        try:
            res = await run_subagent(
                task, stype,
                context=context or "",
                title=title or "",
                parent_conv_id=conv_id,
                extra_instructions=instructions or "",
                background=True,
                job_id=job_id,
            )
            finish_job(job_id, ok=bool(res.get("ok")),
                       result=str(res.get("result") or ""),
                       error=str(res.get("error") or ""),
                       status=str(res.get("status") or ""))
        except asyncio.CancelledError:
            finish_job(job_id, ok=False, error="后台任务被中断", status="cancelled")
            raise
        except Exception as e:
            logger.error(f"后台子代理执行失败: {e}")
            finish_job(job_id, ok=False, error=str(e))
        finally:
            events.pop_depth(depth_token)

    job = create_job(task=task_text, agent_type=stype.name, conv_id=conv_id)
    job_id = job.id

    running = asyncio.create_task(_runner())
    _background_tasks.add(running)
    running.add_done_callback(_background_tasks.discard)

    return {
        "ok": True,
        "status": "running",
        "job_id": job_id,
        "subagent": stype.name,
        "label": stype.display(),
        "message": (f"已在后台启动「{stype.display()}」执行该任务。"
                    "你现在可以先回应用户，任务完成后结果会自动回到对话里。"),
    }


async def _delegate_to_agent(agent: str = "", task: str = "", context: str = "",
                             title: str = "") -> Dict[str, Any]:
    """delegate_to_agent 的 handler：把任务交给已配置的 Agent。"""
    if not (task or "").strip():
        return {"ok": False, "error": "task 不能为空：请写清楚要让该 Agent 完成的子任务"}
    if not (agent or "").strip():
        return {"ok": False, "error": "agent 不能为空：请给出 Agent 的 id 或名称"}

    try:
        from zhiyan_web.agents.registry import get_registry
        registry = get_registry()
    except Exception as e:
        return {"ok": False, "error": f"Agent 注册表不可用: {e}"}

    cfg = registry.get(agent)
    if cfg is None:
        key = agent.strip().lower()
        for candidate in registry.list():
            if candidate.name.lower() == key or candidate.agent_id.lower() == key:
                cfg = candidate
                break
    if cfg is None:
        return {"ok": False,
                "error": f"未找到 Agent '{agent}'",
                "available": [f"{a.agent_id}({a.name})" for a in registry.list()]}

    provider = _provider_for_agent(cfg)

    stype = SubAgentType(
        name=f"agent:{cfg.agent_id}",
        label=cfg.name,
        avatar=cfg.avatar or "🤖",
        description=cfg.system_prompt[:120],
        system_prompt=cfg.system_prompt or f"你是 {cfg.name}，负责完成交给你的子任务。",
        scenario=cfg.tool_scenario or "full",
        tool_groups=list(cfg.tool_groups or []),
        skills=list(cfg.skills or []),
        max_iterations=int(cfg.max_tool_iterations or 24),
        temperature=float(cfg.temperature or 0.7),
        max_depth=2,
        builtin=False,
    )

    depth_token = events.push_depth()
    try:
        result = await run_subagent(
            task, stype,
            provider=provider,
            context=context or "",
            title=title or cfg.name,
        )
    finally:
        events.pop_depth(depth_token)

    result["agent_id"] = cfg.agent_id
    result["agent_name"] = cfg.name
    return result


def _provider_for_agent(cfg: Any):
    """按 Agent 自身配置建 provider；未单独配置模型时回退到全局 provider。

    修复历史问题：旧 Coordinator 无视 worker 自己的 provider/model，一律用全局
    provider 跑；这里按 AgentConfig 独立生效。
    """
    try:
        from zhiyan_web.core.llm.providers import create_provider
        from zhiyan_web.config.settings import get_settings

        settings = get_settings()
        provider_type = (cfg.provider or "").strip()
        if provider_type:
            api_key = (cfg.api_key or "").strip() or settings.get("api_key", "")
            if api_key:
                kwargs: Dict[str, Any] = {"api_key": api_key}
                if cfg.model:
                    kwargs["model"] = cfg.model
                kwargs["temperature"] = float(cfg.temperature or 0.7)
                # provider 走内置 base_url，openai/custom 才需要显式传地址
                if provider_type not in ("deepseek", "zhipu", "qwen", "moonshot",
                                         "doubao", "minimax", "hunyuan", "ernie",
                                         "spark", "siliconflow", "together", "gemini",
                                         "anthropic", "xai", "mistral", "groq",
                                         "openrouter"):
                    if cfg.api_url:
                        kwargs["base_url"] = cfg.api_url
                    kwargs["provider_name"] = provider_type
                    provider_type = "openai"
                built = create_provider(provider_type, **kwargs)
                if built is not None and built.is_available():
                    return built
    except Exception as e:
        logger.debug(f"为 Agent {getattr(cfg, 'agent_id', '?')} 构建独立 provider 失败: {e}")
    return None


async def _list_subagents() -> Dict[str, Any]:
    """list_subagents 的 handler。"""
    types = describe_types()
    agents: list = []
    try:
        from zhiyan_web.agents.registry import get_registry
        for a in get_registry().list():
            agents.append({
                "agent_id": a.agent_id,
                "name": a.name,
                "role": a.role,
                "avatar": a.avatar,
                "has_own_model": bool(a.provider),
                "model": a.model or "",
                "description": (a.system_prompt or "")[:150],
            })
    except Exception as e:
        logger.debug(f"列出已配置 Agent 失败: {e}")

    from .runtime import current_budget
    budget = current_budget()
    return {
        "subagent_types": [
            {"name": t["name"], "label": t["label"], "avatar": t["avatar"],
             "description": t["description"], "max_iterations": t["max_iterations"],
             "max_depth": t["max_depth"]}
            for t in types
        ],
        "configured_agents": agents,
        "budget": budget.snapshot() if budget is not None else None,
        "hint": ("用 spawn_subagent 派给内置类型；用 spawn_background_subagent 跑长任务；"
                 "用 delegate_to_agent 交给 configured_agents 里的角色。"
                 "同一条回复里可以同时发起多个 spawn_subagent 并行执行。"),
    }


async def _board_write(key: str = "", value: str = "", author: str = "") -> Dict[str, Any]:
    """board_write 的 handler。"""
    board = current_board()
    if board is None:
        return {"ok": False, "error": "当前没有共享白板（不在子代理并发批次里）"}
    from .board import current_author, current_author_id
    return board.write(key, value,
                       author=author or current_author() or "agent",
                       author_id=current_author_id())


async def _board_read(key: str = "") -> Dict[str, Any]:
    """board_read 的 handler。"""
    board = current_board()
    if board is None:
        return {"ok": False, "error": "当前没有共享白板（不在子代理并发批次里）"}
    return board.read(key)


# ── 当前会话标识（供子代理标注来源，避免循环 import 用函数内取） ──────
_conv_id: str = ""


def set_current_conv_id(conv_id: str) -> None:
    global _conv_id
    _conv_id = conv_id or ""


def _current_conv_id() -> str:
    return _conv_id


# ── 注册 ────────────────────────────────────────────────────────────────────

SPAWN_SUBAGENT_SCHEMA = [
    {"name": "task", "type": "string",
     "description": "要交给子代理的完整子任务描述。必须自包含：写清目标、已知条件、期望产出。"},
    {"name": "agent_type", "type": "string",
     "description": "子代理类型（general/researcher/coder/writer/reviewer/operator），留空用 general"},
    {"name": "context", "type": "string",
     "description": "可选。子代理看不到对话历史，需要它知道的背景信息写这里。"},
    {"name": "title", "type": "string",
     "description": "可选。这个子任务的一句话标题，用于前端进度展示。"},
    {"name": "tools", "type": "array",
     "description": "可选。额外允许子代理使用的工具名列表（在类型默认权限之上追加）。"},
    {"name": "instructions", "type": "string",
     "description": "可选。对输出格式/口径的额外要求。"},
]

BACKGROUND_SUBAGENT_SCHEMA = [
    {"name": "task", "type": "string",
     "description": "要在后台完成的子任务，需自包含"},
    {"name": "agent_type", "type": "string",
     "description": "子代理类型，留空用 general"},
    {"name": "context", "type": "string",
     "description": "可选。该子代理需要知道的背景信息。"},
    {"name": "title", "type": "string",
     "description": "可选。任务标题，用于进度与结果展示。"},
    {"name": "instructions", "type": "string",
     "description": "可选。对输出格式/口径的额外要求。"},
]

DELEGATE_TO_AGENT_SCHEMA = [
    {"name": "agent", "type": "string",
     "description": "Agent 的 id 或名称（先用 list_subagents 查可用角色）"},
    {"name": "task", "type": "string",
     "description": "要交给该 Agent 完成的子任务，需自包含"},
    {"name": "context", "type": "string", "description": "可选。该 Agent 需要知道的背景信息。"},
    {"name": "title", "type": "string", "description": "可选。任务标题，用于前端展示。"},
]

BOARD_WRITE_SCHEMA = [
    {"name": "key", "type": "string", "description": "条目标识，用简短的主题短语（如 '竞品价格'）"},
    {"name": "value", "type": "string", "description": "结论摘要，控制在一两句话"},
    {"name": "author", "type": "string", "description": "可选。写下这条的署名，留空自动填"},
]

BOARD_READ_SCHEMA = [
    {"name": "key", "type": "string", "description": "可选。留空返回白板全部内容"},
]


def register_subagent_tools(manager: Any = None) -> int:
    """把子代理相关工具注册到 FunctionToolManager。"""
    if manager is None:
        try:
            from zhiyan_web.core import func_tool_manager as manager  # type: ignore
        except Exception:
            try:
                from zhiyan_web.provider.func_tool_manager import get_func_tool_manager
                manager = get_func_tool_manager()
            except Exception:
                return 0
    if manager is None:
        return 0

    manager.add_func(
        name="spawn_subagent",
        func_args=SPAWN_SUBAGENT_SCHEMA,
        desc=_spawn_tool_description(),
        handler=_spawn_subagent,
        group="subagent",
    )
    manager.add_func(
        name="spawn_background_subagent",
        func_args=BACKGROUND_SUBAGENT_SCHEMA,
        desc=("把耗时的子任务丢到后台跑，立刻返回、不阻塞当前回复；"
              "任务完成后结论会自动回到对话里。适合预期要跑很久"
              "（大量检索、批量处理、长时间本机操作）且用户不需要立刻看到过程的任务。"
              "可以连续发起多个后台任务。"),
        handler=_spawn_background_subagent,
        group="subagent",
    )
    manager.add_func(
        name="delegate_to_agent",
        func_args=DELEGATE_TO_AGENT_SCHEMA,
        desc=("把子任务交给 Agent 管理页里已配置的某个角色（worker）执行。"
              "与 spawn_subagent 的区别：这些角色有各自独立的人格、模型配置和技能白名单。"
              "先用 list_subagents 查看有哪些角色可用。"),
        handler=_delegate_to_agent,
        group="subagent",
    )
    manager.add_func(
        name="list_subagents",
        func_args=[],
        desc="列出可用的子代理类型与已配置的 Agent 角色，以及本回合还剩多少子代理额度。",
        handler=_list_subagents,
        group="subagent",
    )
    manager.add_func(
        name="board_write",
        func_args=BOARD_WRITE_SCHEMA,
        desc=("往共享白板写一条结论。与你并行运行的子代理能看到它，"
              "用来避免重复劳动。key 用主题短语，value 一两句话。"),
        handler=_board_write,
        group="board",
    )
    manager.add_func(
        name="board_read",
        func_args=BOARD_READ_SCHEMA,
        desc="读共享白板。动手前先看一眼并行同伴已经查到什么；留空读全部。",
        handler=_board_read,
        group="board",
    )
    logger.info("子代理工具注册完成：spawn_subagent / spawn_background_subagent / "
                "delegate_to_agent / list_subagents / board_read / board_write")
    return 6
