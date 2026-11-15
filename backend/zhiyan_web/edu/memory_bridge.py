"""
连续记忆桥接

把「授课问答」的上下文接到现有记忆系统上：
- 会话历史：复用 memory.conversation_store（加密落盘，会话 id 前缀 teach_）
- 长期记忆：复用 memory.vector_memory（TF-IDF 向量检索），记录知识点与学情
- 上下文拼装：会话摘要 + 长期记忆检索 + 最近对话

对外接口:
- get_history(session_id, limit) / append_message(session_id, role, content)
- remember(session_id, content, memory_type, importance, tags)
- build_context(session_id, query)
- clear_session(session_id) / stats()
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ZhiYanWeb.Edu")

_PREFIX = "teach_"


def _conv_id(session_id: str) -> str:
    sid = re.sub(r"[^\w\-]+", "_", str(session_id or "")).strip("_") or "default"
    return f"{_PREFIX}{sid}"


def _store():
    try:
        from zhiyan_web.memory import conversation_store
        return conversation_store
    except Exception:
        return None


def _vmem():
    try:
        from zhiyan_web.memory.vector_memory import get_vector_memory
        return get_vector_memory()
    except Exception:
        return None


def get_history(session_id: str, limit: int = 40) -> List[Dict[str, Any]]:
    """读取会话历史（[{role, content, ...}]）"""
    store = _store()
    if store is None:
        return []
    try:
        messages = store.load_conversation(_conv_id(session_id)) or []
    except Exception as e:
        logger.debug(f"读取会话历史失败: {e}")
        return []
    if limit and len(messages) > limit:
        messages = messages[-limit:]
    return messages


def append_message(session_id: str, role: str, content: str) -> None:
    """追加一条消息并保存（自动裁剪到最近 200 条）"""
    store = _store()
    if store is None:
        return
    try:
        messages = store.load_conversation(_conv_id(session_id)) or []
        messages.append({"role": role, "content": content, "timestamp": datetime.now().isoformat(timespec="seconds")})
        store.save_conversation(_conv_id(session_id), messages[-200:])
    except Exception as e:
        logger.debug(f"保存会话历史失败: {e}")


def remember(session_id: str, content: str, memory_type: str = "fact",
             importance: float = 0.6, tags: Optional[List[str]] = None) -> Dict[str, Any]:
    """写入长期记忆"""
    vmem = _vmem()
    if vmem is None or not (content or "").strip():
        return {"success": False, "message": "记忆系统不可用或内容为空"}
    try:
        entry = vmem.add(
            content=content.strip(),
            memory_type=memory_type,
            importance=float(importance),
            source=f"edu_teaching:{_conv_id(session_id)}",
            tags=(tags or []) + ["教辅", "授课"],
        )
        return {"success": True, "entry": entry}
    except Exception as e:
        logger.debug(f"写入长期记忆失败: {e}")
        return {"success": False, "message": str(e)}


def search(query: str, top_k: int = 6) -> List[Dict[str, Any]]:
    vmem = _vmem()
    if vmem is None or not (query or "").strip():
        return []
    try:
        return vmem.search(query, top_k=top_k) or []
    except Exception:
        return []


def build_context(session_id: str, query: str = "", max_history: int = 8) -> str:
    """拼装连续记忆上下文（长期记忆检索 + 最近对话）"""
    parts: List[str] = []

    hits = search(query, top_k=5) if query else []
    if hits:
        lines = []
        for h in hits:
            content = str(h.get("content") or "").strip().replace("\n", " ")
            if content:
                lines.append(f"- {content[:160]}")
        if lines:
            parts.append("【相关长期记忆】\n" + "\n".join(lines))

    history = get_history(session_id, limit=max_history)
    if history:
        lines = []
        for msg in history[-max_history:]:
            role = "学生" if msg.get("role") == "user" else "老师"
            content = str(msg.get("content") or "").strip().replace("\n", " ")
            if content:
                lines.append(f"{role}：{content[:200]}")
        if lines:
            parts.append("【最近对话】\n" + "\n".join(lines))

    return "\n\n".join(parts)


def clear_session(session_id: str) -> Dict[str, Any]:
    store = _store()
    if store is None:
        return {"success": False, "message": "会话存储不可用"}
    try:
        ok = store.delete_conversation(_conv_id(session_id))
        return {"success": bool(ok), "message": "已清空该会话记忆" if ok else "会话不存在"}
    except Exception as e:
        return {"success": False, "message": str(e)}


def stats() -> Dict[str, Any]:
    out: Dict[str, Any] = {"sessions": 0, "vector_total": 0, "teaching_entries": 0}
    store = _store()
    if store is not None:
        try:
            convs = store.list_conversations() or []
            out["sessions"] = len([c for c in convs if str(c.get("id") or c.get("conversation_id") or "").startswith(_PREFIX)])
        except Exception:
            pass
    vmem = _vmem()
    if vmem is not None:
        try:
            vs = vmem.get_stats() or {}
            out["vector_total"] = int(vs.get("total") or vs.get("total_entries") or 0)
            entries = vmem.get_by_type("fact", limit=1000) or []
            out["teaching_entries"] = len([e for e in entries if "edu_teaching" in str(e.get("source") or "")])
        except Exception:
            pass
    return out