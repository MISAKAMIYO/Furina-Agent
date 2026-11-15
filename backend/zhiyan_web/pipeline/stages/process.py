"""
核心处理阶段

ProcessStage 是管道的核心决策节点，负责：
1. 按优先级执行插件 Handler（StarRequestSubStage）- 使用 AstrBot 兼容的 star_handlers_registry
2. 如果没有 Handler 处理，则调用智能体（AgentRequestSubStage）
"""

from __future__ import annotations

import logging
import inspect
import functools
import re
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import TYPE_CHECKING

from ..stage import Stage, register_stage
from ..scheduler import PipelineEvent
from zhiyan_web.star.star import star_map, star_registry, platform_matches, resolve_platforms
from zhiyan_web.star.star_handler import EventType, star_handlers_registry
from zhiyan_web.star.platform import AstrMessageEvent, MessageChain, MessageEventResult

if TYPE_CHECKING:
    pass


def _convert_result(result) -> dict:
    """将插件返回值转换为统一格式的 dict

    支持以下输入类型:
    - str: 直接包装为 {"response": text}
    - dict: 原样返回
    - MessageChain: 通过 __str__ 转换为文本（含图片/语音标记）
    - MessageEventResult: 提取文本，如有 _url 则转为 Markdown 图片格式
    - 其他: 转换为字符串
    """
    if isinstance(result, dict):
        return result
    if isinstance(result, MessageEventResult):
        # 检查是否有图片 URL（通过 event.image_result() 设置）
        url = getattr(result, '_url', None)
        if url:
            return {"response": f"![Image]({url})"}
        return {"response": result.get_text()}
    if isinstance(result, MessageChain):
        return {"response": str(result)}
    if isinstance(result, str):
        return {"response": result}
    return {"response": str(result)}


logger = logging.getLogger("ZhiYanWeb.Pipeline")

# 运行时实例方法绑定的缓存：handler_full_name -> 已绑定的 partial（可复用）
_bind_instance_cache: dict[str, callable] = {}


def _bind_instance_handler(handler, hander_fn):
    """为未绑定的实例方法 handler 查找宿主实例并绑定（幂等、带缓存）。

    场景：star_manager 预绑定因模块重载/注册路径不一致而未对 handler.handler 生效，
    此时 hander_fn 是未绑定的原生函数，直接调用（hander_fn(event)）会误把 event 当 self，
    报 Missing 'event'。这里按函数对象身份在全部已加载插件实例中找到宿主再绑定。

    安全：仅当实例的对应属性正是该函数（__func__ is hander_fn）时才绑定，
    避免误绑模块级闭包 handler（如 PluginBase 的 _wrapped_handler）。
    """
    if isinstance(hander_fn, functools.partial):
        return hander_fn  # 已绑定（正常路径）
    _full = getattr(handler, "handler_full_name", None)
    if _full and _full in _bind_instance_cache:
        return _bind_instance_cache[_full]
    try:
        _name = handler.handler_name
        _mpath = getattr(handler, "handler_module_path", None)
        _candidates = list(star_map.values()) + list(star_registry)
        # 快路径：按实例属性名查找，且该 bound 方法的 __func__ 正是 hander_fn（函数对象一致）
        for _md in _candidates:
            _inst = getattr(_md, "star_cls", None)
            if _inst is None:
                continue
            _bound = getattr(_inst, _name, None)
            if callable(_bound) and getattr(_bound, "__func__", None) is hander_fn:
                _bound_fn = functools.partial(hander_fn, _inst)
                if _full:
                    _bind_instance_cache[_full] = _bound_fn
                logger.debug(
                    f"[HANDLER] {getattr(handler, 'handler_name', '')}: 运行时绑定实例方法"
                )
                return _bound_fn
        # 兜底：模块二次加载导致 md.handler 残留旧函数副本时，快路径 __func__ 比对失败。
        # 此时按 handler_module_path 精确定位宿主实例（宿主类所在模块与 handler 模块一致），
        # 取实例上同名的当前方法重新绑定，确保绑定到最新代码副本。
        for _md in _candidates:
            _inst = getattr(_md, "star_cls", None)
            if _inst is None:
                continue
            if not _mpath or type(_inst).__module__ != _mpath:
                continue
            _by_name = getattr(_inst, _name, None)
            if callable(_by_name) and getattr(_by_name, "__func__", None) is not None:
                _bound_fn = functools.partial(_by_name.__func__, _inst)
                if _full:
                    _bind_instance_cache[_full] = _bound_fn
                logger.debug(
                    f"[HANDLER] {getattr(handler, 'handler_name', '')}: 按模块路径绑定实例方法(兜底)"
                )
                return _bound_fn
    except Exception as _bind_e:
        logger.debug(f"[HANDLER] 运行时绑定异常: {_bind_e}")
    # 找不到宿主实例：可能是模块级闭包 handler 或纯静态 handler，原样返回
    return hander_fn


@register_stage(order=3)
class ProcessStage(Stage):
    """核心处理阶段

    决策逻辑:
    1. 检查是否有激活的插件 Handler
    2. 如果有 → 执行 StarRequestSubStage（插件处理）
    3. 如果没有 Handler 处理 → 检查唤醒条件，执行 AgentRequestSubStage
    """

    def __init__(self, context):
        super().__init__(context)
        self._star_request = StarRequestSubStage(context)
        self._agent_request = AgentRequestSubStage(context)

    async def process(self, event: PipelineEvent) -> AsyncGenerator[None, None] | None:
        """核心处理决策

        注意：不使用洋葱模型（无 yield），因为这是决策阶段。
        插件处理后通过 event.stop_event() 阻止后续阶段执行。
        """
        message = event.get("message", "")
        text = str(message or "").strip()
        is_wake = event.get("is_at_or_wake_command", False)
        # 仅插件处理、禁止 agent 兜底（@全体成员 等场景：插件未处理则静默截停）
        no_agent_fallback = event.get("no_agent_fallback", False)

        logger.info(f"核心处理: message={message[:60]!r}, is_wake={is_wake}, no_agent_fallback={no_agent_fallback}")

        # 阶段 0: miao-plugin 路由（按 miao_plugin.mode 分流）
        #   - yunzai: # 与 miao/喵喵 命令完全交由 TRSS-Yunzai 原生响应，ZhiYan 静默截停，
        #     避免两软件同时监听 NapCat 时对同一命令双重回应。
        #   - off:    关闭 miao 处理，# 命令不特殊处理，走普通流程。
        if text and self._is_miao_command(text):
            from zhiyan_web.config import get_settings
            mp = get_settings().get("miao_plugin") or {}
            mode = mp.get("mode", "yunzai")
            if mode == "yunzai":
                pname = str(event.get("platform_name") or "").lower()
                is_qq = bool(re.match(r"^(qq|onebot|napcat)", pname))
                if is_qq:
                    # QQ 平台：NapCat 已同推 Yunzai（websocketClients），Yunzai 原生回复到 QQ。
                    # ZhiYan 静默截停，避免双端对同一命令双重回应。
                    logger.info(f"miao-plugin=yunzai：QQ 平台 # 命令交由 TRSS-Yunzai，静默截停: {text[:40]!r}")
                    event.stop_event()
                    return
                # 网页端：消息不经 NapCat，Yunzai 收不到；提示到 QQ 使用，避免无响应。
                logger.info(f"miao-plugin=yunzai：网页端 # 命令提示到 QQ 使用: {text[:40]!r}")
                event.set_result({
                    "response": "喵喵插件的 # 命令由 TRSS-Yunzai 原生处理，网页端暂不参与。请在 QQ 群/私聊中发送 # 命令获取回复（如 #帮助、#面板）。"
                })
                event.stop_event()
                return
            if mode == "off":
                logger.info(f"miao-plugin=off：# 命令不特殊处理: {text[:40]!r}")

        # 阶段 1: 尝试插件 Handler（使用 AstrBot 兼容的 star_handlers_registry）
        handler_handled = await self._star_request.process(event)

        if handler_handled:
            # 插件已处理，停止事件传播，不再调用 LLM
            event.stop_event()
            result = event.get_result()
            result_preview = str(result)[:80] if result else "None"
            logger.info(f"插件已处理消息: {message[:40]!r} -> {result_preview}")
        elif no_agent_fallback:
            # 禁止 agent 兜底：插件未处理则完全静默截停，不发送任何回复
            logger.info(f"no_agent_fallback: 插件未处理，静默截停 message={message[:40]!r}")
            event.stop_event()
        elif is_wake:
            # 检查是否以 / 或 ! 开头（命令前缀）
            wake_prefixes = event.get("wake_prefixes", ["/", "!"])
            is_command = any(message.strip().startswith(p) for p in wake_prefixes)

            if is_command:
                # / 命令无匹配插件，返回提示，不调用 LLM
                logger.info(f"命令无匹配处理器: {message[:40]!r}")
                event.set_result({
                    "response": "未找到对应的命令解释器，请检查命令是否正确或使用 /plugin_list 查看可用命令"
                })
                event.stop_event()
            else:
                # 非命令唤醒消息（如 @ 唤醒），调用智能体
                logger.info(f"非命令唤醒消息，调用智能体: {message[:40]!r}")
                await self._agent_request.process(event)

    @staticmethod
    def _is_miao_command(text: str) -> bool:
        """miao 命令判定。

        1) 以 # 开头的“#角色/#面板/#深渊/#帮助”等 miao 风格指令（miao 无需 miao/喵喵 前缀）
        2) miao/喵喵 前缀（含 /miao、#喵喵、裸 miao）—— 原版规则覆盖
        注意：不带前缀的 / 与 ! 仍是 ZhiYan 自带插件的唤醒前缀，不转发，避免被 miao 抢占。
        """
        return bool(text) and (
            text.startswith("#")
            or re.match(r"^\s*[/!#]?\s*(miao|喵喵)", text, re.IGNORECASE) is not None
        )

class StarRequestSubStage:
    """插件 Handler 子阶段（AstrBot 兼容）

    使用 star_handlers_registry 获取已注册的插件 Handler，
    按优先级执行，并应用 Handler 的过滤器（CommandFilter / RegexFilter）。
    如果 Handler 返回了结果，则停止事件传播，不再调用智能体。
    """

    def __init__(self, context):
        self.context = context

    async def process(self, event: PipelineEvent) -> bool:
        """执行插件 Handler

        Returns:
            bool: 是否有 Handler 处理了该事件
        """
        if not self._can_process():
            logger.warning("StarRequestSubStage: 没有可用的插件 Handler")
            return False

        message = event.get("message", "")
        is_wake = event.get("is_at_or_wake_command", False)

        logger.info(f"StarRequestSubStage: 处理消息 message={message[:60]!r}, is_wake={is_wake}")

        try:
            handlers = star_handlers_registry.get_handlers_by_event_type(
                EventType.AdapterMessageEvent,
                only_activated=True,
            )
            logger.info(f"StarRequestSubStage: 找到 {len(handlers)} 个已激活的 Handler")
            for h in handlers:
                cmd_names = []
                for f in h.event_filters:
                    if hasattr(f, 'command_name'):
                        cmd_names.append(f.command_name)
                    elif hasattr(f, 'group_name'):
                        cmd_names.append(f"组:{f.group_name}")
                logger.info(f"  - Handler: {h.handler_full_name}, "
                           f"命令={cmd_names}, "
                           f"过滤器类型={[type(f).__name__ for f in h.event_filters]}")
            
            if not handlers:
                logger.warning("StarRequestSubStage: 没有已激活的 AdapterMessageEvent Handler")
                return False

            # 构造 AstrMessageEvent 用于过滤器匹配
            session_id = event.get("session_id", "")
            sender_id = event.get("sender_id", "")
            sender_name = event.get("sender_name", "")
            platform_name = event.get("platform_name") or "webchat"
            astr_event = AstrMessageEvent(
                message_str=message,
                session_id=session_id,
                sender_id=sender_id,
                sender_name=sender_name,
            )
            astr_event.is_at_or_wake_command = is_wake
            astr_event.is_wake = is_wake
            astr_event.wake_prefixes = event.get("wake_prefixes", ["/", "!"])

            # 注入完整用户 / 平台 / 会话信息（用户信息管理：插件可通过
            # get_sender_id / get_sender_name / get_platform_name / get_group_id
            # 等接口获取发送者上下文，参照 AstrBot）
            astr_event.session.platform_name = platform_name
            astr_event.session.message_type = event.get("message_type", "friend")
            astr_event.session.session_id = session_id
            astr_event.session.sender_id = sender_id
            astr_event.session.group_id = event.get("group_id", "")
            astr_event.role = event.get("sender_role") or "member"
            astr_event.message_obj.message_type = event.get("message_type", "friend")
            astr_event.message_obj.group_id = event.get("group_id", "")
            astr_event.message_obj.user_id = event.get("user_id", sender_id)
            astr_event.message_obj.self_id = event.get("self_id", "")
            astr_event.message_obj.sender = SimpleNamespace(
                user_id=sender_id,
                nickname=sender_name,
                card="",
                role=astr_event.role,
            )
            astr_event.platform_name = platform_name

            # 设置额外的上下文数据供过滤器使用
            cfg = {}
            for key, value in event.data.items():
                if isinstance(value, (str, int, float, bool, list, dict)):
                    cfg[key] = value
            cfg.setdefault("platform_name", platform_name)

            for handler in handlers:
                handler_name = handler.handler_name
                if event.is_stopped():
                    logger.debug(f"  [HANDLER] 事件已停止，跳过剩余 handler")
                    break

                # 平台过滤：插件解析后的可用平台（含未声明时的默认规则）与当前平台不匹配时跳过
                plugin_meta = star_map.get(handler.handler_module_path)
                if plugin_meta:
                    effective_platforms = resolve_platforms(plugin_meta.support_platforms)
                    if not platform_matches(effective_platforms, platform_name):
                        logger.debug(
                            f"  [HANDLER] {handler_name}: 插件 {plugin_meta.name} 不支持平台 "
                            f"{platform_name}（可用 {effective_platforms}，声明 {plugin_meta.support_platforms}），跳过"
                        )
                        continue

                # 跳过命令组处理器（命令组本身不处理消息，子命令由子命令处理器处理）
                if handler.extras_configs.get("command_group"):
                    logger.debug(f"  [HANDLER] 跳过命令组处理器: {handler_name}")
                    continue

                try:
                    # 应用过滤器
                    filter_passed = True
                    if handler.event_filters:
                        for f in handler.event_filters:
                            filter_name = type(f).__name__
                            try:
                                filter_result = f.filter(astr_event, cfg)
                                logger.debug(f"  [HANDLER] {handler_name}: 过滤器 {filter_name} -> {filter_result}")
                                if not filter_result:
                                    filter_passed = False
                                    break
                            except Exception as filter_e:
                                logger.debug(f"  [HANDLER] {handler_name}: 过滤器 {filter_name} 异常: {filter_e}")
                                filter_passed = False
                                break
                    else:
                        logger.debug(f"  [HANDLER] {handler_name}: 无过滤器")
                    
                    if not filter_passed:
                        logger.debug(f"  [HANDLER] {handler_name}: 过滤器未通过，跳过")
                        continue

                    # QQ 平台前置插件审核：对命中有封号风险关键词的插件/命令直接拦截，
                    # 不执行插件、不调用大模型，返回明确的禁用提示。
                    if platform_name == "qq":
                        try:
                            from zhiyan_web.platform.qq_plugin_audit import assess as qq_audit_assess
                            plugin_name = (plugin_meta.name
                                           if plugin_meta else "")
                            cmd_names: list = []
                            for _f in (handler.event_filters or []):
                                _gcn = getattr(_f, "get_complete_command_names", None)
                                if callable(_gcn):
                                    try:
                                        cmd_names.extend(_gcn())
                                    except Exception:
                                        pass
                            blocked_reason = qq_audit_assess(
                                platform_name, plugin_name, cmd_names
                            )
                            if blocked_reason:
                                logger.warning(
                                    f"QQ 平台前置插件审核拦截: plugin={plugin_name!r}, "
                                    f"handler={handler_name}, command={cmd_names}"
                                )
                                event.set_result({"response": blocked_reason})
                                return True
                        except Exception as audit_e:
                            logger.debug(f"QQ 前置插件审核异常（放行）: {audit_e}")

                    logger.debug(f"  [HANDLER] {handler_name}: 开始执行")

                    # 执行 Handler（支持 async generator 和普通 async function）
                    hander_fn = handler.handler
                    # 兜底绑定未绑定的实例方法（当 star_manager 预绑定因模块路径不一致等原因未生效时，
                    # handler 仍是未绑定的原生函数，直接调用会 Missing 'event'）。
                    # 按函数对象身份在全部已加载插件实例中查找真正的宿主实例再绑定，
                    # 避免依赖 module_path 匹配（模块重载后 module_name 可能不一致）。
                    hander_fn = _bind_instance_handler(handler, hander_fn)
                    result = None
                    
                    # 注入命令过滤器解析出的参数（如 /jm 123456 中的 album_id）
                    call_kwargs = {}
                    try:
                        parsed_params = astr_event.get_extra("parsed_params")
                        if isinstance(parsed_params, dict) and parsed_params:
                            call_kwargs.update(parsed_params)
                    except Exception:
                        pass
                    
                    # 调用 Handler（可能是 async generator / coroutine / 同步返回值）
                    handler_result = hander_fn(astr_event, **call_kwargs)
                    logger.debug(f"  [HANDLER] {handler_name}: 调用结果类型={type(handler_result).__name__}")
                    # 检查是否是 async generator
                    if inspect.isasyncgen(handler_result):
                        # 消费所有 yield 项：插件通常每轮先设置 event result 再 yield event，
                        # 将多轮消息累积拼接，避免只取第一条导致后续消息丢失（如下载进度/完成结果）
                        yielded_messages: list[str] = []
                        async for item in handler_result:
                            logger.debug(f"  [HANDLER] {handler_name}: yield item type={type(item).__name__}, value={str(item)[:100]!r}")
                            if item is None:
                                continue
                            yielded = item
                            # 若插件直接 yield 了 MessageEventResult / MessageChain / 字符串，优先使用
                            yielded_text = None
                            try:
                                if isinstance(yielded, MessageEventResult):
                                    yielded_text = yielded.get_text() if hasattr(yielded, 'get_text') else str(getattr(yielded, 'text', yielded))
                                elif isinstance(yielded, MessageChain):
                                    yielded_text = str(yielded)
                                elif isinstance(yielded, str):
                                    yielded_text = yielded
                            except Exception:
                                pass
                            # 否则读取插件通过 plain_result/chain_result 设置到 event 上的 result
                            if not yielded_text:
                                event_result = astr_event.get_result()
                                if event_result is not None:
                                    yielded_text = event_result.get_text() if hasattr(event_result, 'get_text') else str(getattr(event_result, 'text', event_result))
                            if yielded_text and str(yielded_text).strip():
                                yielded_messages.append(str(yielded_text))
                        if yielded_messages:
                            result = "\n".join(yielded_messages)
                            astr_event.set_result(MessageEventResult.message(result))
                    elif inspect.isawaitable(handler_result):
                        result = await handler_result
                        logger.debug(f"  [HANDLER] {handler_name}: await result type={type(result).__name__ if result else 'None'}")
                    else:
                        # 非协程/非异步生成器：直接使用同步返回结果
                        result = handler_result
                        logger.debug(f"  [HANDLER] {handler_name}: 同步返回类型={type(result).__name__ if result is not None else 'None'}")
                    
                    # 检查 Handler 返回值
                    if result is not None:
                        processed = _convert_result(result)
                        event.set_result(processed)
                        logger.debug(f"  [HANDLER] {handler_name}: 处理成功，返回结果")
                        return True
                    
                    logger.debug(f"  [HANDLER] {handler_name}: handler 返回 None，检查 event result")
                    
                    # 检查 astr_event.send() 设置的 result
                    astr_result = astr_event.get_result()
                    if astr_result is not None:
                        processed = _convert_result(astr_result)
                        event.set_result(processed)
                        logger.debug(f"  [HANDLER] {handler_name}: event result 处理成功")
                        return True
                    
                    logger.debug(f"  [HANDLER] {handler_name}: 未处理消息")

                except Exception as e:
                    logger.error(f"  [HANDLER] {handler_name}: 异常: {e}", exc_info=True)
                    # 命令匹配成功但执行崩溃时，向用户暴露真实错误，避免误报"未找到命令解释器"
                    is_wake = event.get("is_at_or_wake_command", False)
                    wake_prefixes = event.get("wake_prefixes", ["/", "!"])
                    msg_text = event.get("message", "")
                    if is_wake and any(msg_text.strip().startswith(p) for p in wake_prefixes):
                        event.set_result({
                            "response": f"❌ 插件 [{handler_name}] 执行出错: {type(e).__name__}: {e}"
                        })
                        logger.warning(f"命令 {msg_text[:40]!r} 匹配到插件但执行失败，错误已反馈给用户")
                        return True
                    continue

        except Exception as e:
            logger.error(f"StarRequestSubStage 异常: {e}")

        return False

    def _can_process(self) -> bool:
        """检查是否可以执行插件 Handler"""
        try:
            handlers = star_handlers_registry.get_handlers_by_event_type(
                EventType.AdapterMessageEvent,
                only_activated=True,
            )
            return len(handlers) > 0
        except Exception:
            return False


class AgentRequestSubStage:
    """智能体子阶段

    调用本地智能体（ToolLoopAgentRunner）或第三方智能体。
    """

    def __init__(self, context):
        self.context = context

    async def process(self, event: PipelineEvent) -> None:
        """执行智能体处理"""
        if not self._can_process():
            logger.debug("智能体不可用，跳过")
            return

        message = event.get("message", "")
        if not message:
            return

        try:
            # 获取当前 LLM 提供商
            provider = self._get_provider()
            if not provider:
                event.set_result({"response": "错误：LLM 提供商未配置"})
                return

            # 获取聊天管理器
            cm = self.context.chat_manager
            if not cm:
                event.set_result({"response": "错误：聊天管理器未初始化"})
                return

            # 记录用户消息
            cm.add_message("user", message)
            event.set("conversation_id", cm._default_conversation_id)

            # 获取系统提示词
            system_prompt = event.get("system_prompt", "")
            if not system_prompt:
                try:
                    from zhiyan_web.config import get_settings
                    settings = get_settings()
                    system_prompt = settings.get("system_prompt", "")
                except Exception:
                    pass

            # ===== 注入情感/羁绊/好感度上下文到系统提示词 =====
            context_parts = []

            # 情感状态
            if self.context.emotion_engine:
                try:
                    emotion_prompt = self.context.emotion_engine.format_for_prompt()
                    if emotion_prompt:
                        context_parts.append(emotion_prompt)
                except Exception as e:
                    logger.debug(f"获取情感上下文失败: {e}")

            # 羁绊状态
            if self.context.bond_engine:
                try:
                    bond_status = self.context.bond_engine.get_status()
                    context_parts.append(
                        f"【当前羁绊状态】关系等级: {bond_status['level']}，"
                        f"温暖度: {bond_status['warmth']:.2f}，信任度: {bond_status['trust']:.2f}。"
                        f"请在回复中自然体现这一关系亲密度。"
                    )
                except Exception as e:
                    logger.debug(f"获取羁绊上下文失败: {e}")

            # ===== 注入记忆系统上下文到系统提示词 =====
            try:
                from zhiyan_web.memory.conversation_store import (
                    search_conversations, get_recent_context
                )
                from zhiyan_web.memory.user_profile import get_profile_context

                memory_context_parts = []

                # 1. 用户画像上下文
                profile_context = get_profile_context()
                if profile_context:
                    memory_context_parts.append(profile_context)
                    logger.debug("已注入用户画像上下文")

                # 2. 最近对话上下文（当前会话的连续性）
                recent_context = get_recent_context(max_messages=8)
                if recent_context:
                    memory_context_parts.append(recent_context)
                    logger.debug("已注入最近对话上下文")

                # 3. 相关历史对话检索（基于关键词匹配）
                if message and len(message) > 2:
                    retrieved = search_conversations(message, max_results=3)
                    if retrieved:
                        history_parts = []
                        for item in retrieved:
                            role_label = "用户" if item.get("role") == "user" else "AI"
                            content = item.get("content", "")
                            if len(content) > 150:
                                content = content[:150] + "..."
                            history_parts.append(f"  [{role_label}]: {content}")
                        if history_parts:
                            memory_context_parts.append(
                                "【相关历史对话参考】\n" + "\n".join(history_parts)
                            )
                            logger.debug(f"已注入 {len(retrieved)} 条相关历史对话")

                # 将所有记忆上下文合并
                if memory_context_parts:
                    context_parts.append(
                        "===== 记忆系统上下文（AI 需参考以下信息进行回复）=====\n" +
                        "\n\n".join(memory_context_parts)
                    )
            except Exception as e:
                logger.debug(f"注入记忆系统上下文失败: {e}")

            if context_parts:
                system_prompt = system_prompt.rstrip() + "\n\n" + "\n".join(context_parts)
                logger.debug(f"已注入情感/羁绊上下文到系统提示词")

            # 获取上下文
            conv = cm.get_conversation()
            messages = list(conv.messages)

            # 调用 LLM
            response = await provider.chat(
                messages,
                system_prompt=system_prompt,
            )

            if response and response.content:
                cm.add_message("assistant", response.content)
                event.set_result({"response": response.content})
                logger.debug(f"智能体响应: {response.content[:100]!r}...")
            else:
                logger.warning("LLM 返回空响应")
                event.set_result({"response": "抱歉，我没有得到有效的回复。"})

        except Exception as e:
            logger.error(f"AgentRequestSubStage 异常: {e}", exc_info=True)
            event.set_result({"response": f"错误：{str(e)}"})

    def _get_provider(self):
        """获取当前 LLM 提供商"""
        if self.context.provider_manager:
            return self.context.provider_manager.get_using_provider("chat_completion")
        if self.context.chat_manager:
            return self.context.chat_manager.get_provider()
        return None

    def _can_process(self) -> bool:
        """检查是否可以执行智能体"""
        provider = self._get_provider()
        if provider is None:
            return False
        try:
            return provider.is_available()
        except AttributeError:
            return True