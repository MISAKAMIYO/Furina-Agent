"""
聊天管理器模块
负责管理对话状态、消息历史、聊天流程和上下文管理

核心功能:
- 管理多个对话会话
- 添加和检索消息
- 调用 LLM 提供商进行聊天
- 支持流式和非流式聊天
- 自动注入角色系统提示词
- 上下文窗口管理（滑动窗口 + Token 估算）
- 上下文摘要压缩（防止长对话超出 Token 限制）
- 管道调度集成（洋葱模型事件处理）

优化:
- 上下文窗口管理：自动截断超长对话，保留最近消息
- Token 估算：基于字符数的近似 Token 计数
- 消息类型支持：支持 text, system, thinking 等消息类型
- 思考指示器：支持 AI 内部思考过程的展示
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any, Callable, AsyncIterator

from .providers import LLMProvider, LLMMessage, LLMResponse, MockProvider, _strip_dsml
from . import approval_broker

# 交互式提问 Agent
from zhiyan_web.questions.question_agent import question_agent

logger = logging.getLogger("ZhiYanWeb")

# ReAct 循环最大迭代次数（默认值，会被 settings.json 覆盖）
# 说明：AI 执行超大任务时可能连续调用上百轮工具，默认值放宽到 200，
# 可通过 settings.json 的 "max_tool_iterations" 动态调整。
MAX_TOOL_ITERATIONS = 200

# 写操作工具集合（调用时向 AI 主动注入工作区提醒，防止越界写入）
WRITE_TOOLS = {
    "write_file", "write_file_batch", "edit_file", "append_file", "save_file",
    "create_directory", "copy_file", "move_file", "rename_file",
    "delete_file", "delete_directory",
}


def _get_max_tool_iterations() -> int:
    """从设置中获取最大工具调用次数"""
    try:
        from zhiyan_web.config.settings import get_settings
        return get_settings().get("max_tool_iterations", MAX_TOOL_ITERATIONS)
    except Exception:
        return MAX_TOOL_ITERATIONS


# 同一条回复里发起多个派发类调用时，这些工具会被并发执行（其余工具保持串行，
# 避免文件写入之类的操作互相打架）。
PARALLEL_TOOLS = {"spawn_subagent", "delegate_to_agent"}


def _get_subagent_config() -> Dict[str, Any]:
    """读取子代理能力边界配置（读失败时给一套保守默认值）。"""
    try:
        from zhiyan_web.config.settings import get_settings
        s = get_settings()

        def _int(key: str, default: int) -> int:
            try:
                return int(s.get(key, default))
            except (TypeError, ValueError):
                return default

        return {
            "max_depth": max(0, _int("subagent_max_depth", 2)),
            "max_runs": max(0, _int("subagent_max_runs", 12)),
            "max_iterations": max(0, _int("subagent_max_iterations", 240)),
            "concurrency": max(1, _int("subagent_concurrency", 3)),
        }
    except Exception:
        return {"max_depth": 2, "max_runs": 12, "max_iterations": 240,
                "concurrency": 3}


# 默认上下文配置
DEFAULT_CONTEXT_WINDOW = 20        # 保留的最近消息数
DEFAULT_MAX_TOKENS = 4096          # 最大 Token 数（估算值）
DEFAULT_RESERVE_TOKENS = 1024      # 为系统提示和响应预留的 Token
TOKEN_CHAR_RATIO = 1.8             # 中文字符与 Token 的近似比例


@dataclass
class Conversation:
    """
    对话会话数据类

    一个会话 = 一段独立的对话上下文。它既是 LLM 的上下文（messages），
    也是界面渲染与历史恢复的唯一真源（time / think_ms 存在消息上）。
    """
    id: str
    messages: List[LLMMessage]
    model: str = ""
    summary: str = ""
    context_window: int = DEFAULT_CONTEXT_WINDOW
    title: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """序列化为落盘结构（messages 里只保留有意义的字段）。"""
        msgs = []
        for m in self.messages:
            content = m.content or ""
            if m.role == "assistant" and content and "DSML" in content:
                content = _strip_dsml(content)
            item: Dict[str, Any] = {"role": m.role, "content": content}
            if m.tool_call_id:
                item["tool_call_id"] = m.tool_call_id
            if m.tool_calls:
                item["tool_calls"] = m.tool_calls
            if m.reasoning_content:
                item["reasoning_content"] = m.reasoning_content
            if getattr(m, "time", ""):
                item["time"] = m.time
            if getattr(m, "think_ms", 0):
                item["think_ms"] = int(m.think_ms)
            msgs.append(item)
        return {
            "id": self.id,
            "title": self.title,
            "model": self.model,
            "summary": self.summary,
            "context_window": self.context_window,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": len(self.messages),
            "messages": msgs,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Conversation":
        msgs: List[LLMMessage] = []
        for item in (data.get("messages") or []):
            if not isinstance(item, dict):
                continue
            try:
                role = str(item.get("role") or "user")
                content = str(item.get("content") or "")
                # 旧会话可能把 DSML 特殊标记存进了正文，读入时一并清掉
                if role == "assistant" and content and "DSML" in content:
                    content = _strip_dsml(content)
                msgs.append(LLMMessage(
                    role=role,
                    content=content,
                    tool_call_id=item.get("tool_call_id"),
                    tool_calls=item.get("tool_calls"),
                    reasoning_content=item.get("reasoning_content"),
                    time=str(item.get("time") or ""),
                    think_ms=int(item.get("think_ms") or 0),
                ))
            except Exception:
                continue
        return cls(
            id=str(data.get("id") or ""),
            messages=msgs,
            model=str(data.get("model") or ""),
            summary=str(data.get("summary") or ""),
            context_window=int(data.get("context_window") or DEFAULT_CONTEXT_WINDOW),
            title=str(data.get("title") or ""),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )

    def display_messages(self) -> List[Dict[str, Any]]:
        """界面渲染用的消息视图：用户/助手文本 + 思考过程 + 工具调用记录。

        工具调用（assistant.tool_calls）与对应的 role=tool 结果会被拼成一条
        role="tool_call" 的展示项，前端据此在历史里重建工具卡片。
        """
        # call_id -> 工具结果，便于把结果回填到对应的调用卡片
        tool_results: Dict[str, str] = {}
        for m in self.messages:
            cid = getattr(m, "tool_call_id", None)
            if m.role == "tool" and cid:
                tool_results[str(cid)] = m.content or ""

        out: List[Dict[str, Any]] = []
        for m in self.messages:
            if m.role == "tool":
                continue
            if m.role == "assistant":
                calls = getattr(m, "tool_calls", None) or []
                for call in calls:
                    if not isinstance(call, dict):
                        continue
                    fn = call.get("function") or {}
                    cid = str(call.get("id") or "")
                    raw_args = fn.get("arguments", "{}")
                    if isinstance(raw_args, str):
                        try:
                            args = json.loads(raw_args) if raw_args.strip() else {}
                        except Exception:
                            args = {"_raw": raw_args}
                    elif isinstance(raw_args, dict):
                        args = raw_args
                    else:
                        args = {"_raw": str(raw_args)}
                    out.append({
                        "role": "tool_call",
                        "call_id": cid,
                        "name": fn.get("name", "") or "",
                        "args": args,
                        "output": tool_results.get(cid, ""),
                        "time": getattr(m, "time", "") or "",
                    })
            if m.role not in ("user", "assistant"):
                continue
            text = (m.content or "").strip()
            if not text:
                continue
            item: Dict[str, Any] = {
                "role": m.role,
                "content": m.content,
                "time": getattr(m, "time", "") or "",
            }
            # 思考过程随消息一起回到界面（重启后仍能展开查看）
            if m.role == "assistant" and (m.reasoning_content or "").strip():
                item["reasoning_content"] = m.reasoning_content
            think = int(getattr(m, "think_ms", 0) or 0)
            if m.role == "assistant" and think > 0:
                item["think_ms"] = think
            out.append(item)
        return out

    def visible_cut_index(self, keep_display: int) -> int:
        """按「可见消息条数」换算到 messages 的下标（用于会话分支截断）"""
        if keep_display <= 0:
            return len(self.messages)
        seen = 0
        for idx, m in enumerate(self.messages):
            if m.role in ("user", "assistant") and (m.content or "").strip():
                seen += 1
            if seen >= keep_display:
                return idx + 1
        return len(self.messages)

    def fork_messages(self, keep_display: int) -> List["LLMMessage"]:
        """截出用于分支的消息副本（含工具调用中间态）"""
        cut = self.visible_cut_index(keep_display)
        return [copy.deepcopy(m) for m in self.messages[:cut]]

    def has_content(self) -> bool:
        """是否已有真正可展示的对话内容（空白会话不入历史、不落盘）。"""
        return bool(self.display_messages())


def estimate_tokens(text: str) -> int:
    """
    估算文本的 Token 数量
    
    基于字符数进行近似估算，中文约 1.8 字符/Token，英文约 4 字符/Token
    
    参数:
        text: 输入文本
    
    返回:
        int: 估算的 Token 数量
    """
    if not text:
        return 0
    
    # 中文字符计数
    chinese_chars = len(re.findall(r'[\u4e00-\u9fff\u3400-\u4dbf]', text))
    # 非中文字符（英文、数字、空格、标点等）
    other_chars = len(text) - chinese_chars
    
    # 中文约 1.8 字符/Token，英文约 4 字符/Token
    tokens = chinese_chars / TOKEN_CHAR_RATIO + other_chars / 4
    return int(tokens) + 1


class ChatManager:
    """
    聊天管理器
    
    管理多个对话会话，提供消息管理和 LLM 调用接口
    """
    
    def __init__(self, provider: Optional[LLMProvider] = None):
        """
        初始化聊天管理器
        
        参数:
            provider: LLM 提供商实例
        """
        self._provider = provider
        self._conversations: Dict[str, Conversation] = {}
        self._default_conversation_id = "default"
        self._pipeline = None  # PipelineScheduler 实例，由 register_pipeline 设置
        # 从磁盘恢复既有会话；一个都没有时建 default
        self._load_conversations()
        self._init_default_conversation()

    # ──────────────── 会话持久化 ────────────────

    @staticmethod
    def sessions_dir() -> str:
        """会话落盘目录。

        刻意不复用 data/conversations/ —— 那是 memory 模块的加密归档目录，
        它的 /conversations/list 会把目录下所有 json 都列出来，混在一起会互相污染。
        """
        try:
            import os
            from zhiyan_web.config import DATA_DIR
            path = os.path.join(DATA_DIR, "chat_sessions")
            os.makedirs(path, exist_ok=True)
            return path
        except Exception:
            import os
            path = os.path.join(os.getcwd(), "chat_sessions")
            os.makedirs(path, exist_ok=True)
            return path

    @classmethod
    def _session_path(cls, conv_id: str) -> str:
        import os
        import re
        safe = re.sub(r"[^A-Za-z0-9_\-.]", "_", str(conv_id or "default"))[:80] or "default"
        return os.path.join(cls.sessions_dir(), f"{safe}.json")

    def _load_conversations(self) -> None:
        """启动时从磁盘载入全部会话。"""
        import os
        import glob
        loaded = 0
        try:
            for path in glob.glob(os.path.join(self.sessions_dir(), "*.json")):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if not isinstance(data, dict):
                        continue
                    conv = Conversation.from_dict(data)
                    if not conv.id:
                        conv.id = os.path.splitext(os.path.basename(path))[0]
                    if conv.id == self._default_conversation_id and conv.id in self._conversations:
                        continue
                    # 空白会话（历史遗留的空文件）不载入：避免重启后历史里冒出空对话
                    if not conv.has_content():
                        try:
                            os.remove(path)
                            logger.info(f"已清理空白会话文件: {os.path.basename(path)}")
                        except OSError:
                            pass
                        continue
                    self._conversations[conv.id] = conv
                    loaded += 1
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning(f"读取会话文件失败 {os.path.basename(path)}: {e}")
        except Exception as e:
            logger.warning(f"扫描会话目录失败: {e}")
        if loaded:
            logger.info(f"已加载 {loaded} 个对话会话")

    def save_conversation(self, conv_id: str = None) -> bool:
        """把单个会话写入磁盘（原子写）。

        空白会话不落盘：既避免历史里出现空对话，也顺手清掉清理后残留的空文件。
        """
        import os
        conv_id = conv_id or self._default_conversation_id
        conv = self._conversations.get(conv_id)
        if conv is None:
            return False
        path = self._session_path(conv_id)
        if not conv.has_content():
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError as e:
                logger.warning(f"清理空白会话文件失败 {conv_id}: {e}")
            return True
        now = datetime.now().isoformat()
        if not conv.created_at:
            conv.created_at = now
        conv.updated_at = now
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(conv.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            return True
        except Exception as e:
            logger.error(f"保存会话失败 {conv_id}: {e}")
            return False

    def create_conversation(self, title: str = "", conv_id: str = None) -> Conversation:
        """新建一个会话（不切换当前会话，由调用方决定）。

        只创建内存对象，不立刻写盘：等第一条消息产生后再落盘，
        这样"点新建但没说话"的空白会话不会进入历史。
        """
        import uuid
        cid = conv_id or f"s_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
        now = datetime.now().isoformat()
        conv = Conversation(id=cid, messages=[], title=(title or "").strip(),
                            created_at=now, updated_at=now)
        self._conversations[cid] = conv
        logger.info(f"已创建会话: {cid} ({conv.title or '未命名'})")
        return conv

    def fork_conversation(self, src_id: str, keep_display: int = 0,
                          title: str = "") -> Optional[Conversation]:
        """从某个会话分支出新会话（复制到指定可见消息为止的上下文）"""
        src = self._conversations.get(src_id)
        if src is None:
            return None
        new_title = (title or "").strip() or ((src.title or "会话") + " · 分支")
        conv = self.create_conversation(title=new_title)
        conv.messages = src.fork_messages(keep_display)
        conv.model = src.model
        conv.context_window = src.context_window
        self.save_conversation(conv.id)
        logger.info(f"会话分支: {src_id} -> {conv.id}（保留 {keep_display or '全部'} 条可见消息）")
        return conv

    def rename_conversation(self, conv_id: str, title: str) -> bool:
        """重命名会话。"""
        conv = self._conversations.get(conv_id)
        if conv is None:
            return False
        conv.title = (title or "").strip()[:60]
        self.save_conversation(conv_id)
        return True

    def delete_conversation(self, conv_id: str) -> bool:
        """删除会话（含磁盘文件）。至少会保留一个会话。"""
        import os
        conv_id = conv_id or ""
        if conv_id not in self._conversations:
            return False
        if len(self._conversations) <= 1:
            # 最后一个会话不允许删除，改为清空，避免出现「没有任何会话」的空状态
            self.clear_conversation(conv_id)
            return True
        self._conversations.pop(conv_id, None)
        try:
            path = self._session_path(conv_id)
            if os.path.isfile(path):
                os.remove(path)
        except OSError as e:
            logger.warning(f"删除会话文件失败 {conv_id}: {e}")
        logger.info(f"已删除会话: {conv_id}")
        return True

    def load_conversations(self) -> None:
        """重新扫描磁盘（供外部在文件被其他进程改动后刷新）。"""
        self._load_conversations()

    def auto_title(self, conv_id: str, first_user_message: str) -> None:
        """会话还没有标题时，用首条用户消息生成标题。"""
        conv = self._conversations.get(conv_id)
        if conv is None or conv.title:
            return
        text = " ".join((first_user_message or "").split())
        if not text:
            return
        conv.title = text[:24] + ("…" if len(text) > 24 else "")

    def _init_default_conversation(self) -> None:
        """初始化默认对话会话"""
        if self._default_conversation_id not in self._conversations:
            self._conversations[self._default_conversation_id] = Conversation(
                id=self._default_conversation_id,
                messages=[],
            )

    def _get_deepseek_config(self) -> Dict[str, Any]:
        """从配置中获取 DeepSeek 思考模式参数"""
        try:
            from zhiyan_web.config import get_settings
            settings = get_settings()
            return {
                "thinking_enabled": settings.get("deepseek_thinking_enabled", False),
                "reasoning_effort": settings.get("deepseek_reasoning_effort", "high"),
                "response_format": settings.get("response_format", "text"),
            }
        except Exception:
            return {"thinking_enabled": False, "reasoning_effort": "high", "response_format": "text"}
    
    def _get_system_prompt(self) -> str:
        """
        从配置中获取系统提示词
        
        返回:
            str: 系统提示词内容
        """
        try:
            from zhiyan_web.config import get_settings
            settings = get_settings()
            return settings.get("system_prompt", "")
        except Exception:
            return ""
    
    def _get_context_window(self) -> int:
        """获取上下文窗口大小"""
        try:
            from zhiyan_web.config import get_settings
            settings = get_settings()
            return int(settings.get("context_window", DEFAULT_CONTEXT_WINDOW))
        except Exception:
            return DEFAULT_CONTEXT_WINDOW
    
    async def _manage_context(self, conv: Conversation) -> None:
        """
        管理对话上下文窗口（语义压缩版）

        优先使用上下文压缩系统的语义压缩：当对话上下文接近或达到预设
        长度限制时，对早期非关键对话内容进行语义压缩，并前置注入摘要，
        保证关键信息、对话主题与上下文理解不丢失，维持对话连续性。

        压缩不可用或失败时，降级为原有截断逻辑。
        """
        non_system = [m for m in conv.messages if m.role != "system"]
        if len(non_system) <= conv.context_window:
            return

        # 优先尝试语义压缩（保留关键信息与对话主题）
        try:
            from zhiyan_web.context_compress.manager import get_context_compress_manager
            manager = get_context_compress_manager()
            result = await manager.compress_context(conv.messages, conv_id=conv.id)
            if result.get("compressed"):
                summary = result.get("summary") or ""
                kept = result.get("kept") or []
                summary_msg = LLMMessage(
                    role="system",
                    content=f"[上下文压缩摘要] {summary}",
                )
                conv.messages = [summary_msg] + list(kept)
                conv.summary = summary
                eval_score = (result.get("evaluation") or {}).get("score", 0)
                logger.info(
                    f"对话上下文已语义压缩: 移除 {result.get('removed_count', 0)} 条消息, "
                    f"策略={result.get('used_strategy', '')}, 评估分={eval_score}"
                )
                return
        except Exception as e:
            logger.warning(f"语义压缩上下文失败，回退截断: {e}")

        # 降级：原有截断逻辑（保留最近的 context_window 条非系统消息）
        excess = len(non_system) - conv.context_window
        # 不要把窗口切在 tool 结果中间：否则保留列表会以孤儿 tool 开场，
        # 服务端会报「Messages with role 'tool' must be a response to ...」
        while 0 < excess < len(non_system) and non_system[excess].role == "tool":
            excess += 1
        removed = non_system[:excess]
        summary_text = " ".join(m.content[:50] for m in removed if m.content)
        if summary_text:
            conv.summary = f"[已压缩 {excess} 条消息] {summary_text[:200]}"
            logger.info(f"对话上下文已截断，移除了 {excess} 条消息")

        system_prompts = [m for m in conv.messages if m.role == "system"]
        conv.messages = system_prompts + non_system[excess:]
    
    def set_provider(self, provider: LLMProvider) -> None:
        """
        设置 LLM 提供商
        
        参数:
            provider: LLM 提供商实例
        """
        self._provider = provider
        logger.info(f"LLM 提供商已设置为: {provider.name}")
    
    def get_provider(self) -> Optional[LLMProvider]:
        """
        获取当前 LLM 提供商
        
        返回:
            Optional[LLMProvider]: 当前提供商实例
        """
        return self._provider
    
    def get_conversation(self, conv_id: str = None) -> Conversation:
        """
        获取对话会话
        
        参数:
            conv_id: 会话 ID，默认为默认会话
        
        返回:
            Conversation: 对话会话对象
        """
        conv_id = conv_id or self._default_conversation_id
        if conv_id not in self._conversations:
            self._conversations[conv_id] = Conversation(id=conv_id, messages=[])
        return self._conversations[conv_id]
    
    def clear_conversation(self, conv_id: str = None) -> None:
        """
        清空对话会话的消息
        
        参数:
            conv_id: 会话 ID，默认为默认会话
        """
        conv_id = conv_id or self._default_conversation_id
        if conv_id in self._conversations:
            self._conversations[conv_id].messages = []
            self._conversations[conv_id].summary = ""
            self.save_conversation(conv_id)
        logger.info(f"对话会话已清空: {conv_id}")
    
    def add_message(self, role: str, content: str, conv_id: str = None, reasoning_content: str = None) -> None:
        """
        添加消息到对话会话
        
        参数:
            role: 消息角色 (user, assistant, system)
            content: 消息内容
            conv_id: 会话 ID，默认为默认会话
            reasoning_content: 思考内容（仅 assistant 消息；DeepSeek thinking 模式
                               要求将上轮的 reasoning_content 回传给 API，否则返回 400）

        落盘策略：只在 user / assistant 消息上写盘（一轮对话的起止），
        role=tool 的中间结果不落盘 —— 否则一次上百轮的工具循环会写上百次文件。
        """
        conv = self.get_conversation(conv_id)
        conv.messages.append(LLMMessage(
            role=role, content=content,
            reasoning_content=reasoning_content,
            time=datetime.now().isoformat(),
        ))
        if role == "user":
            # 首条用户消息用来生成会话标题
            self.auto_title(conv.id, content)
            self.save_conversation(conv.id)
        elif role == "assistant":
            self.save_conversation(conv.id)
    
    def get_context(self, conv_id: str = None) -> Dict[str, Any]:
        """
        获取对话上下文信息
        
        参数:
            conv_id: 会话 ID
        
        返回:
            Dict: 包含消息数量、Token 估算、摘要等信息
        """
        conv = self.get_conversation(conv_id)
        total_tokens = 0
        for msg in conv.messages:
            total_tokens += estimate_tokens(msg.content)
        
        return {
            "message_count": len(conv.messages),
            "estimated_tokens": total_tokens,
            "summary": conv.summary,
            "context_window": conv.context_window,
        }
    
    async def chat(self, message: str, conv_id: str = None, **kwargs) -> LLMResponse:
        """
        同步聊天接口
        
        参数:
            message: 用户消息内容
            conv_id: 会话 ID
            **kwargs: 额外参数传递给 LLM 提供商
        
        返回:
            LLMResponse: LLM 响应对象
        """
        if not self._provider or not self._provider.is_available():
            return LLMResponse(content="错误：未配置 LLM 提供商，请在设置中配置 API Key")
        
        conv = self.get_conversation(conv_id)
        self.add_message("user", message, conv_id)
        
        # 管理上下文窗口
        await self._manage_context(conv)
        
        # 自动注入角色系统提示词
        kwargs["system_prompt"] = self._get_system_prompt()
        
        # 注入 DeepSeek 思考模式配置
        deepseek_config = self._get_deepseek_config()
        kwargs.setdefault("thinking_enabled", deepseek_config["thinking_enabled"])
        kwargs.setdefault("reasoning_effort", deepseek_config["reasoning_effort"])
        kwargs.setdefault("response_format", deepseek_config["response_format"])
        
        try:
            response = await self._provider.chat(conv.messages, **kwargs)
        except Exception as e:
            logger.error(f"LLM 聊天异常: {e}")
            return LLMResponse(content=f"错误：LLM 调用失败 - {str(e)}")
        
        if response.content:
            self.add_message("assistant", response.content, conv_id,
                             reasoning_content=response.reasoning_content)
        
        return response

    async def one_shot_chat(self, system_prompt: str = "", user_content: str = "",
                            temperature: float = 0.7, max_tokens: int = 1500,
                            thinking_enabled: bool = False, **kwargs) -> str:
        """独立的一次性 LLM 调用（不写入任何会话历史）。

        用于会话评价 / 摘要等一次性任务，避免污染主对话上下文。
        返回 LLM 回复文本；失败时返回以 "错误：" 开头的提示。
        """
        if not self._provider or not self._provider.is_available():
            return "错误：未配置 LLM 提供商，请在设置中配置 API Key"
        messages: List[LLMMessage] = [LLMMessage(role="user", content=user_content or "")]
        call_kwargs: Dict[str, Any] = {
            "temperature": temperature,
            "max_tokens": max_tokens,
            "thinking_enabled": thinking_enabled,
        }
        call_kwargs.update(kwargs or {})
        if system_prompt:
            call_kwargs["system_prompt"] = system_prompt
        try:
            response = await self._provider.chat(messages, **call_kwargs)
            return (response.content or "").strip()
        except Exception as e:
            logger.error(f"一次性 LLM 调用失败: {e}")
            return f"错误：LLM 调用失败 - {e}"

    async def chat_stream(self, message: str, conv_id: str = None, 
                          callback: Callable = None, **kwargs) -> str:
        """
        流式聊天接口
        
        参数:
            message: 用户消息内容
            conv_id: 会话 ID
            callback: 回调函数，接收每个 chunk
            **kwargs: 额外参数传递给 LLM 提供商
        
        返回:
            str: 完整响应内容
        """
        if not self._provider or not self._provider.is_available():
            error_msg = "错误：LLM 提供商未配置或不可用"
            if callback:
                callback(error_msg)
            return error_msg
        
        conv = self.get_conversation(conv_id)
        self.add_message("user", message, conv_id)
        
        # 管理上下文窗口
        await self._manage_context(conv)
        
        # 自动注入角色系统提示词
        kwargs["system_prompt"] = self._get_system_prompt()
        
        # 注入 DeepSeek 思考模式配置
        deepseek_config = self._get_deepseek_config()
        kwargs.setdefault("thinking_enabled", deepseek_config["thinking_enabled"])
        kwargs.setdefault("reasoning_effort", deepseek_config["reasoning_effort"])
        kwargs.setdefault("response_format", deepseek_config["response_format"])
        
        full_response = ""
        reasoning_buffer = ""
        
        try:
            async for chunk in self._provider.chat_stream(conv.messages, **kwargs):
                if chunk:
                    # 处理 __REASONING__: 前缀（DeepSeek 思考模式）
                    if chunk.startswith("__REASONING__:"):
                        reasoning_buffer += chunk[14:]
                        # 可选：将 reasoning_content 通过特殊前缀传递给回调
                        if callback:
                            callback(chunk)  # 前端可以过滤并显示为思考过程
                        continue
                    full_response += chunk
                    if callback:
                        callback(chunk)
            
            if full_response:
                self.add_message("assistant", full_response, conv_id,
                                 reasoning_content=reasoning_buffer)
        
        except Exception as e:
            logger.error(f"LLM 流式聊天异常: {e}")
            if callback:
                callback(f"错误：{str(e)}")
        
        return full_response

    # ──────────────── Agent 工具调用 (ReAct 循环) ────────────────

    async def chat_with_tools(self, message: str, conv_id: str = None,
                               callback: Callable = None, **kwargs) -> str:
        """带工具调用的聊天（外层包装：负责子代理回合预算与共享白板）。

        非流式路径（QQ 通道、/api/chat/agent-send）也需要预算兜底，
        否则子代理在无预算状态下会失去次数与深度限制。
        """
        from zhiyan_web.subagent import board as subagent_board
        from zhiyan_web.subagent import runtime as subagent_runtime

        cfg = _get_subagent_config()
        _, budget_token = subagent_runtime.begin_budget(
            max_depth=cfg["max_depth"], max_runs=cfg["max_runs"],
            max_iterations=cfg["max_iterations"], turn_id=conv_id or "",
        )
        _, board_token = subagent_board.begin_board(turn_id=conv_id or "")
        started = time.monotonic()
        try:
            return await self._chat_with_tools_impl(message, conv_id, callback, **kwargs)
        finally:
            self._finalize_turn(conv_id, started)
            subagent_board.end_board(board_token)
            subagent_runtime.end_budget(budget_token)

    async def _chat_with_tools_impl(self, message: str, conv_id: str = None,
                                    callback: Callable = None, **kwargs) -> str:
        """带工具调用的聊天接口（ReAct 循环）

        1. 将用户消息和工具定义发送给 LLM
        2. LLM 返回内容或工具调用请求
        3. 如果是工具调用，执行工具并将结果返回给 LLM
        4. 重复直到 LLM 返回最终内容或达到最大迭代次数

        Args:
            message: 用户消息
            conv_id: 会话 ID
            callback: 流式回调函数（收到工具调用结果时的通知）
            **kwargs: 额外参数

        Returns:
            str: 最终回复内容
        """
        if not self._provider or not self._provider.is_available():
            error_msg = "错误：未配置 LLM 提供商，请在设置中配置 API Key"
            if callback:
                callback(error_msg)
            return error_msg

        conv = self.get_conversation(conv_id)
        self.add_message("user", message, conv_id)
        await self._manage_context(conv)

        # 触发情感引擎更新（基于用户消息）
        try:
            from zhiyan_web.emotion.engine import EmotionEngine
            emotion_engine = EmotionEngine()
            emotion_engine.update(text=message)
        except Exception as e:
            logger.debug(f"情感引擎更新失败: {e}")

        # 触发羁绊更新
        try:
            from zhiyan_web.emotion.bond import BondEngine
            bond_engine = BondEngine()
            bond_engine.update(user_message=message)
        except Exception as e:
            logger.debug(f"羁绊更新失败: {e}")

        # 触发好感度更新（每次对话+1 EXP）
        try:
            from zhiyan_web.likability.likability import get_likability_engine
            likability = get_likability_engine()
            likability.add_exp(1, "chat")
        except Exception as e:
            logger.debug(f"好感度更新失败: {e}")

        # 触发用户画像更新
        try:
            from zhiyan_web.memory.user_profile import update_profile_from_messages
            if hasattr(conv, 'messages') and conv.messages:
                profile_messages = []
                for m in conv.messages[-10:]:
                    profile_messages.append({
                        "role": m.role,
                        "content": m.content or "",
                        "time": getattr(m, 'timestamp', ''),
                    })
                if profile_messages:
                    update_profile_from_messages(profile_messages)
        except Exception as e:
            logger.debug(f"用户画像更新失败: {e}")

        # 获取注册的工具
        tools = self._get_registered_tools()
        if not tools:
            logger.info("没有注册工具，使用普通聊天模式")
            return await self.chat_stream(message, conv_id, callback, **kwargs)

        # 构建 Agent 系统提示词（注入记忆上下文 + 工具描述）
        system_prompt = self._get_system_prompt()
        agent_prompt = await self._build_agent_prompt(system_prompt, message, tools)

        kwargs["system_prompt"] = agent_prompt
        kwargs["tools"] = self._build_openai_tools(tools)

        # 注入 DeepSeek 思考模式配置（如果 provider 为 deepseek）
        deepseek_config = self._get_deepseek_config()
        kwargs.setdefault("thinking_enabled", deepseek_config["thinking_enabled"])
        kwargs.setdefault("reasoning_effort", deepseek_config["reasoning_effort"])
        kwargs.setdefault("response_format", deepseek_config["response_format"])

        # ReAct 循环
        iteration = 0
        all_tool_results = []
        # 记录当前消息数，用于后续清理工具结果消息
        base_msg_count = len(conv.messages)
        max_iterations = _get_max_tool_iterations()

        while iteration < max_iterations:
            iteration += 1
            logger.info(f"ReAct 迭代 #{iteration}")

            try:
                response = await self._provider.chat(conv.messages, **kwargs)
            except Exception as e:
                logger.error(f"LLM 调用失败 (迭代 #{iteration}): {e}")
                if iteration > 1:
                    # 已有工具结果，尝试让 LLM 基于已有结果总结
                    summary = self._build_tool_summary(all_tool_results)
                    summary += "\n\n（注意：LLM 在后续推理中遇到错误，请基于已有工具执行结果给出最终回答）"
                    self.add_message("assistant", summary, conv_id)
                    return summary
                error_result = f"错误：LLM 调用失败 - {str(e)}"
                if callback:
                    callback(error_result)
                return error_result

            # 检查是否有工具调用
            if response.tool_calls:
                # 1. 将 assistant 消息（含 tool_calls 和 reasoning_content）添加到对话
                conv.messages.append(LLMMessage(
                    role="assistant",
                    content="",
                    tool_calls=response.tool_calls,
                    reasoning_content=response.reasoning_content,
                ))

                for tc in response.tool_calls:
                    func_name = tc["function"]["name"]
                    tool_call_id = tc.get("id", "")
                    try:
                        func_args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        func_args = {}

                    logger.info(f"调用工具: {func_name}({func_args})")

                    # 通知前端：工具调用开始
                    if callback:
                        callback(f"\n\n🔧 **调用工具: {func_name}**\n")

                    # 执行工具（带重试）
                    tool_result = await self._execute_tool_with_retry(func_name, func_args)
                    all_tool_results.append({
                        "name": func_name,
                        "args": func_args,
                        "result": tool_result,
                    })

                    # 2. 将工具结果（含 tool_call_id）添加到对话
                    tool_result_str = json.dumps(tool_result, ensure_ascii=False, indent=2) if isinstance(tool_result, (dict, list)) else str(tool_result)
                    # 写工具调用：主动注入工作区提醒，告知 AI 工作区边界（防止反复越界写入）
                    if func_name in WRITE_TOOLS:
                        reminder = self._build_workspace_reminder(func_name, func_args)
                        if reminder:
                            tool_result_str = reminder + "\n\n" + tool_result_str
                    conv.messages.append(LLMMessage(
                        role="tool",
                        content=tool_result_str,
                        tool_call_id=tool_call_id,
                    ))

                    # 通知前端：工具调用结果
                    if callback:
                        is_success = self._is_tool_result_success(tool_result)
                        icon = "✅" if is_success else "❌"
                        result_preview = tool_result_str[:300]
                        callback(f"{icon} **执行完成**\n```\n{result_preview}\n```\n\n")

                # 继续循环，让 LLM 基于工具结果进一步推理
                continue

            # 没有工具调用，返回 LLM 的最终回复
            final_content = response.content or ""

            # 构建最终回复（包含工具执行摘要）
            if all_tool_results:
                final_content = self._build_final_response(final_content, all_tool_results)

            if final_content:
                self.add_message("assistant", final_content, conv_id,
                                 reasoning_content=response.reasoning_content)

            # 自动提取重要信息到记忆系统
            try:
                await self._auto_extract_memory(message, final_content, conv_id)
            except Exception:
                pass

            return final_content
        error_msg = f"工具调用循环超过最大迭代次数 ({max_iterations})，已终止。"
        if all_tool_results:
            error_msg = self._build_tool_summary(all_tool_results) + "\n\n" + error_msg
        self.add_message("assistant", error_msg, conv_id)
        logger.warning(error_msg)
        return error_msg

    async def chat_with_tools_stream(self, message: str, conv_id: str = None,
                                     resume_ctx: dict = None) -> AsyncIterator[Dict[str, Any]]:
        """带工具调用的流式聊天（外层包装：负责子代理回合预算与共享白板）。

        这两样东西都是「一次用户回合」级别的作用域，必须在外层建立、在 finally
        里回收；真实逻辑在 _chat_with_tools_stream_impl 里，保持原样不动。
        """
        from zhiyan_web.subagent import board as subagent_board
        from zhiyan_web.subagent import runtime as subagent_runtime

        cfg = _get_subagent_config()
        budget, budget_token = subagent_runtime.begin_budget(
            max_depth=cfg["max_depth"], max_runs=cfg["max_runs"],
            max_iterations=cfg["max_iterations"], turn_id=conv_id or "",
        )
        _, board_token = subagent_board.begin_board(turn_id=conv_id or "")
        started = time.monotonic()
        try:
            async for event in self._chat_with_tools_stream_impl(
                    message, conv_id, resume_ctx):
                yield event
        finally:
            self._finalize_turn(conv_id, started)
            subagent_board.end_board(board_token)
            subagent_runtime.end_budget(budget_token)
            snap = budget.snapshot()
            if snap.get("used_runs"):
                logger.info(
                    f"本回合子代理用量: {snap['used_runs']}/{snap['max_runs']} 次，"
                    f"累计 {snap['used_iterations']} 轮"
                )

    async def run_demo_stream(self, conv_id: str = None, user_message: str = None) -> AsyncIterator[Dict[str, Any]]:
        """播放预设模式演示任务（不调用 LLM、不消耗 token）。

        供两处使用：模拟模式（MockProvider）自动播放；以及用户发送「演示」
        时手动触发。审批事件走真实的 approval_broker，因此允许/拒绝是真生效的。
        """
        from . import demo_agent

        if user_message:
            self.add_message("user", user_message, conv_id)
        conv = self.get_conversation(conv_id)
        logger.info("预设模式：播放 Agent 演示任务")
        final_text = ""
        async for ev in demo_agent.run_demo_trace(conv.id):
            if ev.get("type") == "done":
                final_text = ev.get("content") or ""
                continue
            yield ev
        final_text = final_text or "演示任务结束。"
        self.add_message("assistant", final_text, conv_id)
        yield {"type": "done", "content": final_text}

    def _finalize_turn(self, conv_id: Optional[str], started: float) -> None:
        """一轮对话结束时收尾：补思考耗时 + 落盘。

        思考耗时（think_ms）由这里统一写入最后一条助手消息，而不是散落在各个
        路由里 —— 这样无论从哪个入口进来（HTTP / WS / QQ），历史恢复时都能带上。
        """
        try:
            conv = self._conversations.get(conv_id or self._default_conversation_id)
            if conv is None:
                return
            elapsed_ms = int((time.monotonic() - started) * 1000)
            for m in reversed(conv.messages):
                if m.role == "assistant" and (m.content or "").strip():
                    if not getattr(m, "think_ms", 0):
                        m.think_ms = max(0, elapsed_ms)
                    break
            self.save_conversation(conv.id)
        except Exception as e:
            logger.debug(f"会话收尾失败: {e}")

    async def _chat_with_tools_stream_impl(self, message: str, conv_id: str = None,
                                           resume_ctx: dict = None) -> AsyncIterator[Dict[str, Any]]:
        """带工具调用的流式聊天接口（逐条产生事件，用于 SSE 流式输出）

        产生的事件类型:
        - {"type": "thinking", "message": "..."}     — LLM 正在思考
        - {"type": "tool_call", "name": "...", "args": {...}}  — 正在调用工具
        - {"type": "tool_result", "name": "...", "success": bool, "preview": "..."} — 工具执行结果
        - {"type": "question", "questions": [...]}   — 交互式提问（等待用户回答）
        - {"type": "chunk", "text": "..."}     — 流式内容片段（最终回复）
        - {"type": "done", "content": "..."}   — 响应完成
        - {"type": "error", "message": "..."}  — 错误信息

        子代理相关的事件（由 subagent 模块通过事件通道推上来）:
        - subagent_start / subagent_step / subagent_tool_call / subagent_tool_result
        - subagent_delta（结论逐字回传）/ subagent_done / subagent_error
        - background_result（后台任务跑完，本轮开头回灌）

        Args:
            message: 用户消息（恢复模式时为回答文本或空串）
            conv_id: 会话 ID
            resume_ctx: 恢复上下文（用户回答交互式提问后继续任务时传入），
                        包含 answers/tool_call_id/iteration/tool_results/base_msg_count

        Yields:
            Dict[str, Any]: 事件字典
        """
        if not self._provider or not self._provider.is_available():
            yield {"type": "error", "message": "未配置 LLM 提供商，请在设置中配置 API Key"}
            return

        conv = self.get_conversation(conv_id)

        if resume_ctx:
            # ========== 恢复模式：用户已回答交互式提问 ==========
            answers = resume_ctx.get("answers") or {}
            tool_call_id = resume_ctx.get("tool_call_id", "")
            answer_text = question_agent.format_answers(answers)
            # 将回答作为工具结果注入，闭合 ask_questions 工具调用
            # （注意：不插入 user 消息，保证 tool 响应紧跟 assistant 的 tool_calls，符合工具调用协议）
            conv.messages.append(LLMMessage(
                role="tool",
                content=answer_text,
                tool_call_id=tool_call_id,
            ))
            await self._manage_context(conv)
            iteration = resume_ctx.get("iteration", 0)
            all_tool_results = resume_ctx.get("tool_results", [])
            base_msg_count = resume_ctx.get("base_msg_count", len(conv.messages))
        else:
            # ========== 普通模式：新用户消息 ==========
            self.add_message("user", message, conv_id)
            await self._manage_context(conv)

            # 触发情感引擎更新（基于用户消息）
            try:
                from zhiyan_web.emotion.engine import EmotionEngine
                emotion_engine = EmotionEngine()
                emotion_engine.update(text=message)
            except Exception as e:
                logger.debug(f"情感引擎更新失败: {e}")

            # 触发羁绊更新
            try:
                from zhiyan_web.emotion.bond import BondEngine
                bond_engine = BondEngine()
                bond_engine.update(user_message=message)
            except Exception as e:
                logger.debug(f"羁绊更新失败: {e}")

            # 触发好感度更新（每次对话+1 EXP）
            try:
                from zhiyan_web.likability.likability import get_likability_engine
                likability = get_likability_engine()
                likability.add_exp(1, "chat")
            except Exception as e:
                logger.debug(f"好感度更新失败: {e}")

            iteration = 0
            all_tool_results = []
            base_msg_count = len(conv.messages)

        # 获取注册的工具
        tools = self._get_registered_tools()

        # 预设（模拟）模式：不调用真实模型、不消耗 token，直接播放一条完整的
        # Agent 演示轨迹（思考 / 读取 / 搜索 / 编辑 / 命令失败重试 / 越权写入审批 / 总结），
        # 用于检查对话过程区的样式；其中审批仍走真实的 approval_broker。
        if isinstance(self._provider, MockProvider):
            async for ev in self.run_demo_stream(conv_id):
                yield ev
            return

        if not tools:
            logger.info("没有注册工具，使用普通流式聊天模式")
            yield {"type": "thinking", "message": "正在思考..."}
            async for chunk in self._provider.chat_stream(conv.messages, system_prompt=self._get_system_prompt()):
                if chunk:
                    yield {"type": "chunk", "text": chunk}
            yield {"type": "done", "content": ""}
            return

        # 构建 Agent 系统提示词
        system_prompt = self._get_system_prompt()
        agent_prompt = await self._build_agent_prompt(system_prompt, message, tools)

        kwargs = {
            "system_prompt": agent_prompt,
            "tools": self._build_openai_tools(tools),
        }

        # ── 回灌后台子代理已完成的结论 ──
        # 后台任务跑完之后没有活的 SSE 流可推，所以挂在下一轮对话的开头交回给
        # 主 agent（同时给前端一张卡片，让用户看到「之前派出去的事办完了」）。
        try:
            from zhiyan_web.subagent import runtime as subagent_runtime
            pending = subagent_runtime.pending_jobs(conv_id or "")
            if pending:
                lines = ["", "", "**后台任务已完成（你之前派出去的任务，结果如下）:**"]
                for job in pending:
                    head = f"- [{job.agent_type}] {job.task[:80]}"
                    if job.status == "done":
                        lines.append(head + f"\n  结论：{job.result[:1500]}")
                    else:
                        lines.append(head + f"\n  未成功：{job.error or job.status}")
                kwargs["system_prompt"] = kwargs["system_prompt"] + "\n".join(lines)
                subagent_runtime.mark_delivered([j.id for j in pending])
                yield {
                    "type": "background_result",
                    "jobs": [{
                        "id": j.id, "agent_type": j.agent_type,
                        "task": j.task[:200], "status": j.status,
                        "result": j.result[:1500], "error": j.error,
                    } for j in pending],
                }
                logger.info(f"已回灌 {len(pending)} 个后台子代理结果")
        except Exception as e:
            logger.debug(f"回灌后台子代理结果失败: {e}")

        # 添加 todo_list 虚拟工具（用于在右侧面板展示任务计划）
        todo_list_tool = {
            "type": "function",
            "function": {
                "name": "todo_list",
                "description": "设置任务计划列表，显示在右侧面板。在开始执行任务前调用此工具规划步骤，每完成一个步骤后调用更新状态。参数 items 为任务列表数组，每个元素可以是字符串或 {text, done} 对象。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "description": "任务列表，每个元素可以是字符串或 {text: string, done: boolean} 对象",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "text": {"type": "string", "description": "任务描述"},
                                    "done": {"type": "boolean", "description": "是否已完成"}
                                }
                            }
                        }
                    },
                    "required": ["items"]
                }
            }
        }
        kwargs["tools"] = kwargs.get("tools", []) + [todo_list_tool]

        # 添加 ask_questions 虚拟工具（交互式提问：收集需求/确认权限/获取参数）
        kwargs["tools"] = kwargs.get("tools", []) + [question_agent.get_tool_schema()]

        # 注入 DeepSeek 思考模式配置（如果 provider 为 deepseek）
        deepseek_config = self._get_deepseek_config()
        kwargs.setdefault("thinking_enabled", deepseek_config["thinking_enabled"])
        kwargs.setdefault("reasoning_effort", deepseek_config["reasoning_effort"])
        kwargs.setdefault("response_format", deepseek_config["response_format"])

        # ReAct 循环
        max_iterations = _get_max_tool_iterations()
        consecutive_failures = 0  # 连续工具失败计数（防止 agent 反复尝试同一失败操作）
        MAX_CONSECUTIVE_FAILURES = 8  # 连续失败上限，达到后终止循环
        last_reasoning = ""  # 上一轮 reasoning 摘要（用于在工具调用前展示思考链）

        # ── 钩子总线接线（agent/hooks.py 定义的那套生命周期钩子，
        #    此前挂在没人用的 runner 上，这里接到真正在跑的循环）──
        from zhiyan_web.agent import hook_bus as _hook_bus_mod
        hook_bus = _hook_bus_mod.get_hook_bus()
        run_ctx = _hook_bus_mod.build_run_context(
            conv_id=conv_id or "", engine="agent", iteration=0,
            messages=conv.messages, max_tool_calls=max_iterations,
        )
        hooks_active = hook_bus.count() > 0
        if hooks_active:
            await hook_bus.emit_agent_begin(run_ctx)

        # 让子代理知道自己是哪个会话派出来的（仅用于前端展示与日志）
        try:
            from zhiyan_web.subagent.tools import set_current_conv_id
            set_current_conv_id(conv_id or "")
        except Exception:
            pass

        async def _finish(result_text: str) -> None:
            """统一收尾：触发 on_agent_done（无钩子时是空操作）。"""
            if not hooks_active:
                return
            try:
                await hook_bus.emit_agent_done(run_ctx, result_text)
            except Exception as e:
                logger.debug(f"on_agent_done 钩子失败: {e}")

        while iteration < max_iterations:
            iteration += 1
            run_ctx.context["iteration"] = iteration
            logger.info(f"ReAct 迭代 #{iteration}")
            yield {"type": "thinking", "message": f"正在分析（第 {iteration} 轮）..."}

            try:
                # 真流式：边收边推 chunk，结束时再决定是工具调用还是最终回复。
                # 工具调用轮里模型往往先输出一段叙述，这段叙述也会即时展示。
                streamed_parts: list = []
                streamed_calls: list = []
                streamed_reasoning = ""
                streamed_usage: dict = {}
                async for ev in self._provider.chat_stream_events(conv.messages, **kwargs):
                    etype = ev.get("type")
                    if etype == "reasoning":
                        yield {"type": "reasoning", "content": ev.get("text", "")}
                    elif etype == "text":
                        txt = ev.get("text", "")
                        if txt:
                            streamed_parts.append(txt)
                            yield {"type": "chunk", "text": txt}
                    elif etype == "error":
                        raise RuntimeError(ev.get("message") or "LLM 流式调用失败")
                    elif etype == "done":
                        streamed_calls = ev.get("tool_calls") or []
                        streamed_reasoning = ev.get("reasoning") or ""
                        streamed_usage = ev.get("usage") or {}
                response = LLMResponse(
                    content="".join(streamed_parts),
                    model=getattr(self._provider, "_model", ""),
                    usage=streamed_usage,
                    tool_calls=streamed_calls or None,
                    reasoning_content=streamed_reasoning,
                )
                # DSH 式 meta 行：把本轮 token 用量单独推一帧，前端累计显示
                if streamed_usage:
                    yield {
                        "type": "usage",
                        "usage": streamed_usage,
                        "model": getattr(self._provider, "_model", ""),
                    }
            except Exception as e:
                logger.error(f"LLM 调用失败 (迭代 #{iteration}): {e}")
                if iteration > 1:
                    summary = self._build_tool_summary(all_tool_results)
                    summary += "\n\n（注意：LLM 在后续推理中遇到错误，请基于已有工具执行结果给出最终回答）"
                    self.add_message("assistant", summary, conv_id)
                    await _finish(summary)
                    yield {"type": "chunk", "text": summary}
                    yield {"type": "done", "content": summary}
                    return
                error_msg = f"错误：LLM 调用失败 - {str(e)}"
                await _finish("")
                yield {"type": "error", "message": error_msg}
                return

            # 检测 provider 返回的错误内容（如 404 模型不存在 / 429 限流等），
            # 直接暴露给用户，避免被当作正常回复继续 ReAct 循环。
            if response.content and str(response.content).startswith("错误："):
                logger.error(f"LLM 返回错误内容 (迭代 #{iteration}): {str(response.content)[:300]}")
                error_msg = str(response.content)
                self.add_message("assistant", error_msg, conv_id)
                yield {"type": "error", "message": error_msg}
                return

            # 检查是否有工具调用
            if response.tool_calls:
                # 工具调用前的思考过程作为 reasoning 事件推送，显示在回复气泡上方
                if response.reasoning_content and response.reasoning_content != last_reasoning:
                    last_reasoning = response.reasoning_content
                    yield {"type": "reasoning", "content": response.reasoning_content}
                # 1. 将 assistant 消息（含 tool_calls 和 reasoning_content）添加到对话
                assistant_msg = LLMMessage(
                    role="assistant",
                    content="",
                    tool_calls=response.tool_calls,
                    reasoning_content=response.reasoning_content,
                )
                conv.messages.append(assistant_msg)

                # ── 子代理并发预启动 ──
                # 同一条回复里如果模型一次发起多个派发类调用，就把它们先全部启动，
                # 后面按顺序回收结果。这样「让 5 个调研员分头查」是真的并行，
                # 而不是一个个等（原来是串行，总耗时是累加的）。
                batch_tasks: Dict[str, asyncio.Task] = {}
                batch_queue: Optional[asyncio.Queue] = None
                batch_token = None
                try:
                    sub_names = [((tc.get("function") or {}).get("name") or "")
                                 for tc in response.tool_calls]
                    sub_total = sum(1 for n in sub_names if n in PARALLEL_TOOLS)
                    sub_cfg = _get_subagent_config()
                    if sub_total >= 2 and sub_cfg["concurrency"] > 1:
                        from zhiyan_web.subagent import events as subagent_events
                        batch_queue = asyncio.Queue()

                        async def _batch_sink(ev, _q=batch_queue):
                            await _q.put(ev)

                        batch_token = subagent_events.set_sink(_batch_sink)
                        sem = asyncio.Semaphore(sub_cfg["concurrency"])
                        for tc in response.tool_calls:
                            nm = (tc.get("function") or {}).get("name") or ""
                            if nm not in PARALLEL_TOOLS:
                                continue
                            cid = tc.get("id", "")
                            try:
                                a = json.loads((tc.get("function") or {}).get("arguments") or "{}")
                            except json.JSONDecodeError:
                                a = {}

                            async def _job(_n=nm, _a=a, _s=sem):
                                async with _s:
                                    return await self._execute_tool_with_retry(_n, _a)

                            batch_tasks[cid] = asyncio.create_task(_job())
                        logger.info(
                            f"子代理并发批次: {len(batch_tasks)} 个任务，"
                            f"并发度 {sub_cfg['concurrency']}"
                        )
                except Exception as e:
                    logger.warning(f"准备子代理并发批次失败，退回串行执行: {e}")
                    if batch_token is not None:
                        try:
                            from zhiyan_web.subagent import events as subagent_events
                            subagent_events.reset_sink(batch_token)
                        except Exception:
                            pass
                    batch_tasks = {}
                    batch_queue = None
                    batch_token = None

                for tc in response.tool_calls:
                    func_name = tc["function"]["name"]
                    tool_call_id = tc.get("id", "")
                    # DSH 式工具行需要稳定 id 才能把 result 精确回填到对应行
                    call_id = tool_call_id or ("call_" + uuid.uuid4().hex[:12])
                    try:
                        func_args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        func_args = {}

                    # 特殊处理 todo_list 虚拟工具
                    if func_name == "todo_list":
                        logger.info(f"Todo List 更新: {func_args}")
                        yield {"type": "todo_list", "items": func_args.get("items", [])}
                        # 将结果注入对话，让 LLM 知道 todo_list 已成功
                        conv.messages.append(LLMMessage(
                            role="tool",
                            content="[todo_list 已更新]",
                            tool_call_id=tool_call_id,
                        ))
                        continue

                    # 特殊处理 ask_questions 虚拟工具（交互式提问）
                    if func_name == "ask_questions":
                        questions = func_args.get("questions", [])
                        if not isinstance(questions, list) or not questions:
                            conv.messages.append(LLMMessage(
                                role="tool",
                                content=json.dumps(
                                    {"error": "questions 参数不能为空，请重新提出有效问题"},
                                    ensure_ascii=False,
                                ),
                                tool_call_id=tool_call_id,
                            ))
                            yield {"type": "tool_result", "call_id": call_id, "name": "ask_questions",
                                   "success": False, "preview": "questions 参数为空"}
                            continue
                        # 记录待回答状态（保存恢复循环所需的上下文）
                        question_agent.register_pending(
                            conv_id,
                            questions,
                            tool_call_id,
                            {
                                "iteration": iteration,
                                "tool_results": list(all_tool_results),
                                "base_msg_count": base_msg_count,
                            },
                        )
                        logger.info(f"交互式提问: {len(questions)} 个问题")
                        # 将提问以事件推送给前端，并结束本流（等待用户回答）
                        yield {"type": "question", "questions": questions}
                        yield {"type": "done", "content": "", "awaiting_input": True}
                        return

                    logger.info(f"调用工具: {func_name}({func_args})")
                    yield {"type": "tool_call", "call_id": call_id, "name": func_name, "args": func_args}

                    # ── 越权操作：先挂起，等用户在对话里放行 ──
                    approved_once = False
                    need = approval_broker.evaluate(func_name, func_args)
                    if need:
                        req = approval_broker.open_request(
                            conv.id, func_name, func_args,
                            permission=need["permission"],
                            target=need["target"],
                            detail=need["detail"],
                            reason=need.get("reason", ""),
                        )
                        yield {"type": "approval_request", **req}
                        decision = await approval_broker.wait(req["id"])
                        if decision != "allow":
                            denied_text = (
                                "用户拒绝了本次操作，请换一种方式或先与用户确认。"
                                if decision == "deny"
                                else "等待用户审批超时，本次操作已取消。"
                            )
                            conv.messages.append(LLMMessage(
                                role="tool",
                                content=denied_text,
                                tool_call_id=tool_call_id,
                            ))
                            yield {"type": "approval_result", "id": req["id"], "decision": decision}
                            yield {"type": "tool_result", "call_id": call_id, "name": func_name,
                                   "success": False, "preview": denied_text}
                            continue
                        yield {"type": "approval_result", "id": req["id"], "decision": "allow"}
                        approved_once = True
                        logger.info(f"用户已放行本次操作: {func_name}")

                    # ── 写操作前落检查点（文件级回滚）──
                    try:
                        from zhiyan_web.agent import checkpoints as _checkpoints
                        _checkpoints.snapshot_before(conv.id, func_name, func_args)
                    except Exception as _ck_err:
                        logger.debug(f"检查点快照失败: {_ck_err}")

                    # ── 执行工具（带重试）──
                    # 执行期间子代理 / 其它扩展可能往外推事件，这里挂一个队列作为
                    # sink，边等边把事件转成 SSE 事件推给前端（子代理进度可视化）。
                    _tool_t0 = time.perf_counter()
                    hook_tool = self._get_tool_object(func_name)
                    if hooks_active:
                        await hook_bus.emit_tool_start(run_ctx, hook_tool, func_args)

                    shared_task = batch_tasks.pop(tool_call_id, None)
                    if shared_task is not None and batch_queue is not None:
                        # 命中并发批次：任务已经在跑，共用一个事件队列
                        exec_task = shared_task
                        evq = batch_queue
                        sink_token = None
                    else:
                        shared_task = None
                        evq = asyncio.Queue()

                        async def _sub_sink(ev: Dict[str, Any], _q=evq) -> None:
                            await _q.put(ev)

                        sink_token = None
                        try:
                            from zhiyan_web.subagent import events as subagent_events
                            sink_token = subagent_events.set_sink(_sub_sink)
                        except Exception as e:
                            logger.debug(f"装载子代理事件通道失败: {e}")

                        if approved_once:
                            # 放行严格限定在这一个工具协程内，结束即失效
                            from zhiyan_web.computer.services.safety_manager import (
                                reset_approval_bypass,
                                set_approval_bypass,
                            )

                            async def _run_with_bypass():
                                token = set_approval_bypass(True)
                                try:
                                    return await self._execute_tool_with_retry(func_name, func_args)
                                finally:
                                    reset_approval_bypass(token)

                            exec_task = asyncio.create_task(_run_with_bypass())
                        else:
                            exec_task = asyncio.create_task(
                                self._execute_tool_with_retry(func_name, func_args))

                    try:
                        while True:
                            if exec_task.done() and evq.empty():
                                break
                            try:
                                ev = await asyncio.wait_for(evq.get(), timeout=0.2)
                                yield ev
                            except asyncio.TimeoutError:
                                continue
                        tool_result = exec_task.result()
                        # 排空剩余事件（工具刚结束时可能还有最后几条）
                        while not evq.empty():
                            yield evq.get_nowait()
                    finally:
                        if sink_token is not None:
                            try:
                                from zhiyan_web.subagent import events as subagent_events
                                subagent_events.reset_sink(sink_token)
                            except Exception:
                                pass
                        if not exec_task.done():
                            exec_task.cancel()

                    if hooks_active:
                        await hook_bus.emit_tool_end(run_ctx, hook_tool, func_args, tool_result)
                    all_tool_results.append({
                        "name": func_name,
                        "args": func_args,
                        "result": tool_result,
                    })

                    # 2. 将工具结果（含 tool_call_id）添加到对话
                    tool_result_str = json.dumps(tool_result, ensure_ascii=False, indent=2) if isinstance(tool_result, (dict, list)) else str(tool_result)
                    # 写工具调用：主动注入工作区提醒，告知 AI 工作区边界（防止反复越界写入）
                    if func_name in WRITE_TOOLS:
                        reminder = self._build_workspace_reminder(func_name, func_args)
                        if reminder:
                            tool_result_str = reminder + "\n\n" + tool_result_str
                    conv.messages.append(LLMMessage(
                        role="tool",
                        content=tool_result_str,
                        tool_call_id=tool_call_id,
                    ))

                    is_success = self._is_tool_result_success(tool_result)
                    consecutive_failures = consecutive_failures + 1 if not is_success else 0
                    # DSH 式工具行：完整输出 + 耗时 + 退出码，前端直接渲染统计徽标
                    _duration_ms = int((time.perf_counter() - _tool_t0) * 1000)
                    _output = tool_result_str
                    if len(_output) > 8000:
                        _output = _output[:8000] + f"\\n…（输出已截断，共 {len(tool_result_str)} 字符）"
                    yield {
                        "type": "tool_result",
                        "call_id": call_id,
                        "name": func_name,
                        "success": is_success,
                        "preview": tool_result_str[:300],
                        "output": _output,
                        "duration_ms": _duration_ms,
                        "exit_code": self._extract_exit_code(tool_result),
                    }

                    # 连续失败保护：避免 agent 反复尝试相同失败操作陷入死循环
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        stop_msg = (
                            self._build_tool_summary(all_tool_results)
                            + "\n\n**多次操作连续失败，已停止继续尝试。**\n"
                            + "请检查以上失败原因（如路径权限、参数错误、目标不存在等），"
                            + "调整后再重新发起，或告诉我您希望的替代方案。"
                        )
                        self.add_message("assistant", stop_msg, conv_id)
                        logger.warning(f"连续失败 {consecutive_failures} 次，终止 ReAct 循环")
                        await _finish(stop_msg)
                        yield {"type": "chunk", "text": stop_msg}
                        yield {"type": "done", "content": stop_msg}
                        return

                # ── 并发批次收尾 ──
                # 排空共享队列里剩余事件（最后一个子代理结束时可能还剩几条），
                # 还原事件通道，并取消任何还没被回收的残留任务。
                if batch_token is not None or batch_tasks:
                    try:
                        if batch_queue is not None:
                            while not batch_queue.empty():
                                yield batch_queue.get_nowait()
                    except Exception:
                        pass
                    if batch_token is not None:
                        try:
                            from zhiyan_web.subagent import events as subagent_events
                            subagent_events.reset_sink(batch_token)
                        except Exception:
                            pass
                    for leftover in batch_tasks.values():
                        if not leftover.done():
                            leftover.cancel()
                    batch_tasks = {}

                continue  # 继续循环

            # 没有工具调用 → 正文已在流式阶段实时推送，这里只做收尾
            final_content = response.content or ""
            reasoning_content = response.reasoning_content or ""

            if final_content:
                # 保存 assistant 消息时携带 reasoning_content，确保后续 thinking 模式调用不报 400
                self.add_message("assistant", final_content, conv_id,
                                 reasoning_content=reasoning_content)

                # 正文已在流式阶段实时推给前端；这里只补充工具执行摘要
                if all_tool_results:
                    suffix = "\n\n[fold=📋 工具执行摘要]" + self._build_tool_summary(all_tool_results) + "[/fold]"
                    final_content += suffix
                    yield {"type": "chunk", "text": suffix}
            else:
                # LLM 没有生成文本，输出工具摘要
                final_content = self._build_tool_summary(all_tool_results)
                yield {"type": "chunk", "text": final_content}

            # 自动提取记忆
            try:
                await self._auto_extract_memory(message, final_content, conv_id)
            except Exception:
                pass

            await _finish(final_content)
            yield {"type": "done", "content": final_content}
            return

        # 超过最大迭代次数
        error_msg = f"工具调用循环超过最大迭代次数 ({max_iterations})，已终止。"
        if all_tool_results:
            error_msg = self._build_tool_summary(all_tool_results) + "\n\n" + error_msg
        self.add_message("assistant", error_msg, conv_id)
        logger.warning(error_msg)
        await _finish(error_msg)
        yield {"type": "chunk", "text": error_msg}
        yield {"type": "done", "content": error_msg}

    async def _build_agent_prompt(self, system_prompt: str, user_message: str, tools: list) -> str:
        """构建 Agent 系统提示词（注入记忆上下文、情感、关系等）"""
        base = system_prompt

        # 注入记忆上下文
        try:
            from zhiyan_web.memory.vector_memory import get_vector_memory
            vm = get_vector_memory()
            memory_context = vm.get_context(user_message, max_entries=5)
            if memory_context:
                base += f"\n\n**相关记忆:**\n{memory_context}"
        except Exception as e:
            logger.debug(f"获取记忆上下文失败: {e}")

        # 注入用户画像
        try:
            from zhiyan_web.memory.user_profile import get_profile_context
            profile = get_profile_context()
            if profile:
                base += f"\n\n**用户画像:**\n{profile}"
        except Exception as e:
            logger.debug(f"获取用户画像失败: {e}")

        # 注入关系上下文（与观众的关系）
        try:
            from zhiyan_web.memory.user_profile import get_relationship_context
            relationship = get_relationship_context()
            if relationship:
                base += f"\n\n**与观众的关系:**\n{relationship}"
        except Exception as e:
            logger.debug(f"获取关系上下文失败: {e}")

        # 注入情感状态
        try:
            from zhiyan_web.emotion.engine import EmotionEngine
            emotion_engine = EmotionEngine()
            emotion_text = emotion_engine.format_for_prompt()
            if emotion_text:
                base += f"\n\n{emotion_text}"
        except Exception as e:
            logger.debug(f"获取情感状态失败: {e}")

        # 注入羁绊状态
        try:
            from zhiyan_web.emotion.bond import BondEngine
            bond_engine = BondEngine()
            bond_status = bond_engine.get_status()
            if bond_status:
                base += f"\n\n**羁绊状态:** 当前关系: {bond_status.get('level', '熟悉')} | 温暖度: {bond_status.get('warmth', 0.5):.2f} | 信任度: {bond_status.get('trust', 0.5):.2f}"
        except Exception as e:
            logger.debug(f"获取羁绊状态失败: {e}")

        # 注入好感度状态
        try:
            from zhiyan_web.likability.likability import get_likability_engine
            likability = get_likability_engine()
            status = likability.get_status()
            if status and status.get("success"):
                base += f"\n\n**好感度:** 等级 {status.get('level', 1)} ({status.get('level_name', '陌生人')}) - 经验值 {status.get('exp', 0)}/{status.get('next_exp', 100)}"
        except Exception as e:
            logger.debug(f"获取好感度状态失败: {e}")

        # 注入工具描述（技能与子代理类工具不在此列，由下面两段专门呈现）
        base += "\n\n**可用工具:**\n" + self._build_tools_description(
            tools, exclude_groups=("skills", "subagent"))

        # 注入技能清单（渐进式披露：只给名字/描述/适用场景，正文用 load_skill 按需拉取）
        try:
            from zhiyan_web.skills.disclosure import format_skill_catalog
            catalog = format_skill_catalog()
            if catalog:
                base += catalog
        except Exception as e:
            logger.debug(f"注入技能清单失败: {e}")

        # 注入子代理使用指南（有子代理工具时才注入，避免无谓的 prompt 开销）
        try:
            tool_names = {str(getattr(t, "name", "")) for t in tools}
            if "spawn_subagent" in tool_names:
                from zhiyan_web.subagent.registry import list_subagent_types
                lines = [
                    "",
                    "",
                    "**子代理（把子任务派出去做，不要什么都自己扛）:**",
                    "- spawn_subagent(task, agent_type, context, title)：派给一个临时子代理，"
                    "它有独立上下文和自己的一套工具，跑完只把结论给你。",
                    "- spawn_background_subagent(task, agent_type, ...)：把耗时的子任务丢到后台，"
                    "立刻返回，你可以先回应用户，任务完成后结论会自动回到对话里。",
                    "- delegate_to_agent(agent, task, context)：交给某个已配置的角色执行，"
                    "该角色有自己的人格、模型和技能白名单。",
                    "- list_subagents()：查看有哪些类型、已配置角色，以及本回合还剩多少子代理额度。",
                    "- board_read / board_write：并行子代理之间的共享便签墙，用来避免重复劳动。",
                    "- 什么该派：多步工具操作、会产生大量中间输出（截图、大段日志、网页原文）、"
                    "需要独立探查后再汇报的子任务。派出去后这些过程不会占用你的上下文。",
                    "- 什么不该派：一句话能答的、需要与用户来回确认的、需要保留完整过程细节的。",
                    "- 子代理看不到你与用户的对话，所以 task 必须自包含（目标 + 已知条件 + 期望产出）。",
                    "- 并行：同一条回复里可以同时发起多个 spawn_subagent，它们会并发执行；"
                    "多个互不依赖的探查方向就一次发起，不要分几轮慢慢来。",
                    "- 嵌套：子代理本身也能再派子代理，但层数与总次数都有上限，"
                    "超了会被拒绝并返回原因，看到就改用已有结果。",
                    "可用类型：",
                ]
                for t in list_subagent_types():
                    lines.append(f"  · {t.name}（{t.display()}）: {t.description}")
                base += "\n".join(lines)
        except Exception as e:
            logger.debug(f"注入子代理指南失败: {e}")

        # 注入 QQ 好友/群列表上下文（Agent 可针对对象回复）
        try:
            from zhiyan_web.platform.manager import get_qq_platform_manager
            mgr = get_qq_platform_manager()
            client = getattr(mgr, "client", None)
            if client and getattr(client, "is_online", False):
                friends = mgr.presence.get("friends", []) or []
                groups = mgr.presence.get("groups", []) or []
                qq_lines = []
                if friends:
                    qq_lines.append("**QQ 好友（可私聊对象）:**")
                    for f in friends[:50]:
                        name = f.get("remark") or f.get("nickname") or ""
                        qq_lines.append(f"- {name} (QQ:{f.get('user_id')})")
                if groups:
                    qq_lines.append("**QQ 群（可发消息对象）:**")
                    for g in groups[:50]:
                        qq_lines.append(f"- {g.get('group_name')} (群号:{g.get('group_id')})")
                if qq_lines:
                    qq_lines.append(
                        "如需向 QQ 好友/群发送消息，使用 qq_send_private_msg / qq_send_group_msg / "
                        "qq_send_media 工具，并从这里获取对方的 QQ 号/群号。"
                    )
                    base += "\n\n" + "\n".join(qq_lines)
        except Exception as e:
            logger.debug(f"注入 QQ 联系人上下文失败: {e}")

        # 注入工作区路径和安全管控模式（避免AI反复触发安全限制）
        try:
            from zhiyan_web.computer.services.workspace_manager import get_workspace_dir
            from zhiyan_web.computer.services.safety_manager import get_security_mode, get_security_info
            ws = get_workspace_dir()
            mode = get_security_mode()
            info = get_security_info()
            mode_name = info.get("mode_name", "普通")
            base += (
                f"\n\n**工作区和安全限制（必须严格遵守，违反会导致操作被拦截并浪费 tokens）:**\n"
                f"- 当前安全模式: {mode_name}\n"
            )
            if not ws:
                base += (
                    f"- 未配置 Agent 工作区路径，所有写入文件的操作目前都会被拒绝。\n"
                    f"- 当用户要求创建/保存文件时，先告知用户需在设置中配置工作区路径，不要尝试写入任意路径。\n"
                )
            else:
                base += f"- 工作区路径（唯一允许写入的根目录）: {ws}\n"
                if mode == "normal":
                    base += (
                        f"- 写入/创建/编辑/删除/移动文件：只能在工作区目录 {ws} 内进行。\n"
                        f"- 在执行任何写入类工具（写文件、创建文件、编辑文件、删除文件、移动文件、保存文件等）之前，"
                        f"必须先检查目标绝对路径是否以工作区路径 {ws} 开头；不是则以失败告终并浪费大量 tokens。\n"
                        f"- 绝对不要尝试写入服务器代码目录、工作区之外的任意路径（如 C:、桌面、项目源码目录等）。\n"
                        f"- 当用户要求把文件保存到工作区之外时：先礼貌告知用户受普通模式限制无法写入，"
                        f"并建议保存到工作区内路径（如 {ws} 下），不要反复尝试越界写入。\n"
                        f"- 你可以读取计算机上任何位置的文件（不受工作区限制），但写入一律限定在工作区。\n"
                    )
                elif mode == "full_control":
                    base += (
                        f"- 你拥有整台计算机的完全读写权限\n"
                        f"- 高敏感命令（格式化、磁盘操作等）需要用户确认\n"
                    )
                elif mode == "high_security":
                    base += (
                        f"- 你仅能写入工作区内的文件（唯一允许写入根目录: {ws}）\n"
                        f"- 你可以读取非系统关键位置的文件\n"
                        f"- 系统关键路径（C:\\Windows、C:\\Program Files等）已被禁止访问\n"
                        f"- 危险命令（格式化、删除系统文件、修改注册表等）已被禁止\n"
                    )
        except Exception as e:
            logger.debug(f"注入工作区信息失败: {e}")

        # 注入主动提问指南（交互式提问 Agent）
        base += question_agent.get_ask_guidelines()

        # 注入 ReAct 行为指南
        base += (
            "\n\n**行为指南:**\n"
            "1. 分析用户需求，选择合适的工具\n"
            "2. 在开始执行多步骤任务前，先调用 todo_list 工具设置任务计划，展示在右侧面板\n"
            "3. 一次只调用一个工具，等待结果后再决定下一步\n"
            "4. 工具执行结果会以 tool role 返回给你\n"
            "5. 如果工具执行失败，尝试其他方法或告知用户\n"
            "6. 每完成一个步骤后，调用 todo_list 更新该步骤状态（将 done 设为 true）\n"
            "7. 完成所有必要的工具调用后，给出最终回答\n"
            "8. 最终回答应包含工具执行结果的摘要\n"
            "9. 当用户提到个人喜好、习惯、重要事实时，调用 write_memory 工具记录\n"
            "10. 每次对话最多调用 write_memory 1-2 次，避免重复记录相同信息\n"
            "11. 你依然是芙宁娜，自称「本神」、称用户为「观众」，保留少量歌剧与审判庭的舞台口癖（如「精彩」「谢幕」「本神」，偶尔称用户为「旅人」），但当前是任务执行模式\n"
            "12. 执行任务时聚焦任务本身：认真、严谨、高效地完成每一步，汇报结果简洁清晰（像芙宁娜认真办事时的利落作风），不要过多情绪化渲染、撒娇或闲聊\n"
            "13. 任务全部完成后，再以芙宁娜的语气简单收尾（如「谢幕！还有什么需要本神效劳的吗？」），全程保持可靠\n"
        )

        return base

    async def _execute_tool_with_retry(self, name: str, args: dict, max_retries: int = 2) -> Any:
        """执行工具调用（带重试）"""
        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                from zhiyan_web.core import func_tool_manager
                if func_tool_manager:
                    tool = func_tool_manager.get_func(name)
                    if tool and tool.handler:
                        result = await tool.handler(**args)
                        # 检查结果是否表示失败
                        if isinstance(result, dict) and not result.get("success", True):
                            error = result.get("error", "未知错误")
                            if attempt < max_retries:
                                logger.warning(f"工具 '{name}' 执行失败 (尝试 {attempt}/{max_retries}): {error}")
                                continue
                        return result
                    return {"error": f"工具 '{name}' 未找到或没有 handler"}
                return {"error": "func_tool_manager 不可用"}
            except Exception as e:
                last_error = str(e)
                if attempt < max_retries:
                    logger.warning(f"工具 '{name}' 异常 (尝试 {attempt}/{max_retries}): {e}")
                    await asyncio.sleep(1)
                else:
                    logger.error(f"工具 '{name}' 执行失败（已重试 {max_retries} 次）: {e}")
        return {"error": last_error or "执行失败"}

    def _build_workspace_reminder(self, func_name: str, func_args: dict) -> str:
        """构建写工具的工作区提醒信息（AI 调用写工具时注入，主动告知工作区边界）

        当 AI 试图调用写操作类工具（写文件、创建目录、删除、移动等）时，
        将工作区路径与写入边界以提醒形式返回给 AI，避免其反复越界写入浪费 tokens。
        """
        try:
            from zhiyan_web.computer.services.workspace_manager import (
                get_workspace_dir, is_path_in_workspace,
            )
            ws = get_workspace_dir()
            if not ws:
                return (
                    "【工作区提醒】当前未配置 Agent 工作区路径，所有写入操作都会被拒绝。"
                    "请告知用户先配置工作区路径，不要继续尝试写入任意路径。"
                )
            # 从工具参数中提取目标路径
            path = (
                func_args.get("path")
                or func_args.get("source")
                or func_args.get("dest")
                or func_args.get("directory")
            )
            msg = f"【工作区提醒】工作区路径（唯一允许写入的根目录）: {ws}"
            if path:
                inside = is_path_in_workspace(str(path))
                if inside:
                    msg += (
                        f"\n本次操作目标路径 {path} 位于工作区内，可以正常执行。"
                    )
                else:
                    msg += (
                        f"\n注意：目标路径 {path} 位于工作区之外，该写入操作会被拒绝。"
                        f"请将目标改为工作区内路径（如 {ws} 下的某个子目录）。"
                    )
            else:
                msg += "\n请在执行写入前确认目标路径位于工作区内。"
            return msg
        except Exception as e:
            logger.debug(f"构建工作区提醒失败: {e}")
            return ""

    @staticmethod
    def _is_tool_result_success(tool_result: Any) -> bool:
        """判断工具执行结果是否成功"""
        if isinstance(tool_result, dict):
            return tool_result.get("success", True)
        if isinstance(tool_result, str):
            # 字符串以 "错误:" 开头表示失败，否则视为成功
            return not tool_result.startswith("错误:")
        return True

    @staticmethod
    def _extract_exit_code(tool_result: Any) -> Optional[int]:
        """从工具结果里取进程退出码（命令类工具才有），供 DSH 式失败行展示"""
        if isinstance(tool_result, dict):
            for key in ("exit_code", "exitCode", "returncode"):
                val = tool_result.get(key)
                if isinstance(val, int) and not isinstance(val, bool):
                    return val
        return None

    def _build_tool_summary(self, tool_results: list) -> str:
        """构建工具执行摘要（原始 JSON 格式，会被前端折叠显示）"""
        lines = ["**工具执行摘要:**"]
        for r in tool_results:
            is_success = self._is_tool_result_success(r["result"])
            icon = "✅" if is_success else "❌"
            result_str = str(r["result"])[:200]
            lines.append(f"{icon} **{r['name']}**: {result_str}")
        return "\n".join(lines)

    def _build_final_response(self, content: str, tool_results: list) -> str:
        """构建最终回复（工具摘要以折叠形式显示）"""
        if not content.strip() and tool_results:
            return self._build_tool_summary(tool_results)

        summary = "\n\n[fold=📋 工具执行摘要]" + self._build_tool_summary(tool_results) + "[/fold]"
        return content + summary

    async def _auto_extract_memory(self, user_message: str, response: str, conv_id: str = None) -> None:
        """自动从对话中提取重要信息到长期记忆"""
        try:
            from zhiyan_web.memory.vector_memory import get_vector_memory
            vm = get_vector_memory()

            # 提取用户偏好信息
            preference_keywords = ["我喜欢", "我想要", "我习惯", "我常用", "请记住", "别忘了"]
            for kw in preference_keywords:
                if kw in user_message:
                    vm.add(
                        content=user_message,
                        memory_type="preference",
                        importance=0.7,
                        source="conversation",
                        tags=["auto_extracted", "preference"],
                    )
                    break

            # 提取关键事实（用户明确要求的记住内容）
            if "记住" in user_message or "记住" in response:
                vm.add(
                    content=f"用户: {user_message}",
                    memory_type="fact",
                    importance=0.6,
                    source="conversation",
                    tags=["auto_extracted", "user_request"],
                )

            # 定期清理过期记忆
            stats = vm.get_stats()
            if stats.get("total", 0) > 900:  # 接近上限时清理
                old = vm.get_recent(hours=72, limit=1000)
                # 保留最近的和重要的
                pass
        except Exception as e:
            logger.debug(f"自动提取记忆失败: {e}")

    def _get_registered_tools(self) -> list:
        """获取已注册的工具列表（按设置的场景做裁剪）"""
        try:
            from zhiyan_web.core import func_tool_manager
            if func_tool_manager and not func_tool_manager.empty():
                tools = list(func_tool_manager.get_full_tool_set().tools)
                return self._apply_tool_scenario(tools)
        except (ImportError, AttributeError) as e:
            logger.debug(f"获取注册工具失败: {e}")
        return []

    @staticmethod
    def _apply_tool_scenario(tools: list) -> list:
        """按 settings.agent_tool_scenario 裁剪工具集。

        默认场景是 full（不裁剪），保持与历史行为一致；用户在设置里选了其它
        场景后才会真正减少工具数量。裁剪失败一律回退到全量，不影响可用性。
        """
        try:
            from zhiyan_web.agent import tool_groups
            scenario = tool_groups.get_default_scenario()
            if not scenario or scenario == tool_groups.DEFAULT_SCENARIO:
                return tools
            return tool_groups.filter_tools(tools, scenario=scenario)
        except Exception as e:
            logger.debug(f"工具场景裁剪失败: {e}")
            return tools

    def _get_tool_object(self, name: str):
        """取工具对象（供钩子拿到 FunctionTool 元信息，取不到时造一个占位）。"""
        try:
            from zhiyan_web.core import func_tool_manager
            if func_tool_manager:
                tool = func_tool_manager.get_func(name)
                if tool is not None:
                    return tool
        except Exception:
            pass
        from zhiyan_web.agent.tool import FunctionTool
        return FunctionTool(name=name, description="", parameters={}, handler=None)

    def _build_tools_description(self, tools: list, exclude_groups: tuple = ()) -> str:
        """构建工具描述文本（用于系统提示词）

        exclude_groups 里的工具分组会被跳过 —— 技能类工具由技能清单段落单独
        呈现（带 when-to-use，且函数型技能的参数在按需加载时给出），重复列一遍
        既浪费 token 又会让模型分不清「技能」和「系统工具」。
        """
        from zhiyan_web.agent import tool_groups

        lines = []
        for tool in tools:
            try:
                if exclude_groups:
                    grp = tool_groups.group_of(
                        str(getattr(tool, "name", "")),
                        str(getattr(tool, "group", "") or ""),
                    )
                    if grp in exclude_groups:
                        continue
            except Exception:
                pass
            params_desc = []
            if tool.parameters and "properties" in tool.parameters:
                for pname, pinfo in tool.parameters["properties"].items():
                    required = pname in tool.parameters.get("required", [])
                    params_desc.append(f"  - {pname} ({pinfo.get('type', 'string')}): {pinfo.get('description', '')}{' [必填]' if required else ''}")
            lines.append(f"- **{tool.name}**: {tool.description}")
            if params_desc:
                lines.extend(params_desc)
        return "\n".join(lines)

    def _build_openai_tools(self, tools: list) -> list:
        """构建 OpenAI 工具格式"""
        result = []
        for tool in tools:
            if hasattr(tool, 'to_openai_format'):
                result.append(tool.to_openai_format())
            else:
                result.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters or {"type": "object", "properties": {}},
                    },
                })
        return result

    def get_conversations_list(self) -> List[Dict[str, Any]]:
        """
        获取所有会话列表（按最后更新时间倒序）

        返回:
            List[Dict]: 会话摘要信息列表
        """
        result = []
        for conv_id, conv in self._conversations.items():
            # 空白会话不进入历史列表（新建后还没说话的会话保持"隐形"）
            if not conv.has_content():
                continue
            preview = ""
            for m in reversed(conv.messages):
                if m.role in ("user", "assistant") and (m.content or "").strip():
                    preview = " ".join(m.content.split())[:60]
                    break
            result.append({
                "id": conv_id,
                "title": conv.title or ("新对话" if not conv.messages else conv_id),
                "message_count": len(conv.messages),
                "display_count": len(conv.display_messages()),
                "summary": conv.summary,
                "model": conv.model,
                "preview": preview,
                "created_at": conv.created_at,
                "updated_at": conv.updated_at or conv.created_at,
                "is_default": conv_id == self._default_conversation_id,
            })
        result.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
        return result

    def get_display_messages(self, conv_id: str = None) -> List[Dict[str, Any]]:
        """取某个会话的界面展示消息（供历史恢复 / 切换会话时渲染）。"""
        conv = self._conversations.get(conv_id or self._default_conversation_id)
        if conv is None:
            return []
        return conv.display_messages()

    def latest_conversation_id(self) -> str:
        """返回最近更新的**有内容**会话 id；没有则返回默认会话 id。"""
        best_id, best_at = "", ""
        for conv_id, conv in self._conversations.items():
            if not conv.has_content():
                continue
            stamp = conv.updated_at or conv.created_at or ""
            if stamp > best_at:
                best_id, best_at = conv_id, stamp
        return best_id or self._default_conversation_id

    def conversation_exists(self, conv_id: str) -> bool:
        return bool(conv_id) and conv_id in self._conversations

    # ──────────────── 管道集成方法 ────────────────

    def register_pipeline(self, pipeline_scheduler) -> None:
        """注册管道调度器

        将 ChatManager 与管道调度器关联，使聊天方法通过管道处理。

        Args:
            pipeline_scheduler: PipelineScheduler 实例
        """
        self._pipeline = pipeline_scheduler
        # 将 ChatManager 自身注入管道上下文
        pipeline_scheduler.context.chat_manager = self
        logger.info("ChatManager 已集成管道调度器")

    async def process_via_pipeline(self, message: str, **kwargs) -> Dict[str, Any]:
        """通过管道调度器处理消息

        将消息封装为 PipelineEvent，通过管道处理。

        Args:
            message: 用户消息
            **kwargs: 额外参数（如 system_prompt, tts_enabled 等）

        Returns:
            Dict: 处理结果（包含 response 字段）
        """
        if not self._pipeline:
            logger.warning("管道调度器未注册，使用传统聊天方式")
            resp = await self.chat(message)
            return {"response": resp.content}

        from zhiyan_web.pipeline.scheduler import PipelineEvent

        # 构建事件数据（默认平台为 webchat，QQ 等平台通过 kwargs 覆盖）
        event_data = {
            "message": message,
            "is_at": True,  # 默认视为唤醒
            # # 供 miao-plugin 等插件命令使用：未命中的 # 命令返回提示而非回落到 LLM
            "wake_prefixes": ["/", "!", "#"],
            "system_prompt": kwargs.get("system_prompt", self._get_system_prompt()),
            "tts_enabled": kwargs.get("tts_enabled", False),
            "platform_name": "webchat",
            "message_type": "friend",
            "sender_id": "webuser",
            "sender_name": "用户",
        }
        event_data.update(kwargs)

        # 通过管道执行
        event = PipelineEvent(data=event_data)
        await self._pipeline.execute(event)

        # 获取结果
        result = event.get_result()
        if result is None:
            return {"response": ""}

        if isinstance(result, dict):
            return result
        
        # 处理 MessageChain (通过 __str__ 已含图片/语音标记)
        from zhiyan_web.star.platform import MessageChain, MessageEventResult
        if isinstance(result, MessageChain):
            return {"response": str(result)}
        if isinstance(result, MessageEventResult):
            return {"response": result.get_text()}
        
        return {"response": str(result)}

    async def process_via_pipeline_stream(
        self, message: str, callback: Callable = None, **kwargs
    ) -> Dict[str, Any]:
        """通过管道调度器处理消息（流式）

        不使用管道，直接调用原有流式聊天方法。
        管道流式处理将在后续迭代中实现。

        Args:
            message: 用户消息
            callback: 流式回调函数
            **kwargs: 额外参数

        Returns:
            Dict: 处理结果
        """
        if not self._pipeline:
            response = await self.chat_stream(message, callback=callback, **kwargs)
            return {"response": response}

        # 先通过管道处理（非流式），然后通过回调模拟流式
        result = await self.process_via_pipeline(message, **kwargs)
        response_text = result.get("response", "")

        if callback and response_text:
            from zhiyan_web.config import get_settings
            settings = get_settings()
            from zhiyan_web.core import chat_manager as cm
            cm.add_message("assistant", response_text)

        return result