"""
聊天 API 路由
提供聊天相关的 HTTP API 接口

接口列表:
- GET /api/chat/history        - 获取聊天历史
- DELETE /api/chat/history     - 清空聊天历史
- POST /api/chat/send          - 发送消息（非流式）
- GET /api/chat/context        - 获取对话上下文信息
- GET /api/chat/sessions       - 获取所有会话列表
- WS /api/chat/ws              - 流式聊天 WebSocket
- POST /api/chat/history/save  - 手动保存聊天历史到文件
- POST /api/chat/history/export - 导出聊天历史为可下载文件
- POST /api/chat/history/import - 从文件导入聊天历史
- GET /api/chat/history/files  - 列出已保存的历史记录文件
"""
from __future__ import annotations

import os
import re
import json
import time
import asyncio
import logging
import uuid
import shutil
from datetime import datetime
from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse

from zhiyan_web.config import get_settings, DATA_DIR
from zhiyan_web.core.websocket.connection import ConnectionManager
from zhiyan_web.star.platform import MessageChain, MessageEventResult
from zhiyan_web.questions.question_agent import question_agent

router = APIRouter(prefix="/api/chat", tags=["chat"])
manager = ConnectionManager()

logger = logging.getLogger("ZhiYanWeb")

# 延迟获取核心实例（在使用时获取）
def get_chat_manager():
    from zhiyan_web.core import chat_manager
    return chat_manager

def get_tts_engine():
    from zhiyan_web.core import tts_engine
    return tts_engine

# 全局聊天历史（当前活跃会话的界面视图，真源是 ChatManager 的会话对象）
chat_history = []

# 当前活跃会话 id：所有聊天端点默认都作用在它上面
_active_session_id = "default"

# 会话是否已从磁盘恢复（ChatManager 未就绪时延后到首次访问再恢复）
_sessions_initialized = False

# 会话 ID（每次启动生成唯一 ID，用于区分不同会话）
SESSION_ID = datetime.now().strftime("session_%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]

# ============= 聊天历史持久化存储 =============

# 聊天历史存储目录
CHAT_HISTORY_DIR = os.path.join(DATA_DIR, 'chat_history')
AUTO_SAVE_FILE = os.path.join(CHAT_HISTORY_DIR, 'auto_save.json')
EXPORT_DIR = os.path.join(CHAT_HISTORY_DIR, 'exports')

# 确保存储目录存在
os.makedirs(CHAT_HISTORY_DIR, exist_ok=True)
os.makedirs(EXPORT_DIR, exist_ok=True)

# 存储格式版本
STORAGE_VERSION = 1
# 最大自动保存消息数（超过此数量时触发新的自动保存）
MAX_AUTO_SAVE_MESSAGES = 5000

def _message_to_dict(msg: dict) -> dict:
    """提取单条消息的可持久化字段（think_ms 为 Agent 任务的思考耗时，单位毫秒）"""
    item = {
        "role": msg.get("role", "unknown"),
        "content": msg.get("content", ""),
        "time": msg.get("time", datetime.now().isoformat()),
    }
    try:
        think_ms = int(msg.get("think_ms") or 0)
    except (TypeError, ValueError):
        think_ms = 0
    if think_ms > 0:
        item["think_ms"] = think_ms
    return item

def build_ai_message(content: str, elapsed_ms: int = 0) -> dict:
    """构造助手消息（携带 Agent 思考耗时，供前端气泡下方展示）"""
    msg = {"role": "assistant", "content": content, "time": datetime.now().isoformat()}
    try:
        if int(elapsed_ms) > 0:
            msg["think_ms"] = int(elapsed_ms)
    except (TypeError, ValueError):
        pass
    return msg

def _serialize_chat_history(messages: list) -> dict:
    """将聊天历史序列化为标准存储格式"""
    return {
        "version": STORAGE_VERSION,
        "saved_at": datetime.now().isoformat(),
        "message_count": len(messages),
        "messages": [_message_to_dict(msg) for msg in messages]
    }

def _deserialize_chat_history(data: dict) -> list:
    """从存储格式反序列化聊天历史"""
    if not isinstance(data, dict):
        return []
    messages = data.get("messages", [])
    if not isinstance(messages, list):
        return []
    return [_message_to_dict(msg) for msg in messages if isinstance(msg, dict)]

def save_chat_history_to_file(filepath: str, messages: list) -> bool:
    """将聊天历史保存到指定文件"""
    try:
        data = _serialize_chat_history(messages)
        # 原子写入：先写临时文件，再重命名
        temp_path = filepath + '.tmp'
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, filepath)
        logger.info(f"聊天历史已保存到: {os.path.basename(filepath)} ({len(messages)} 条消息)")
        return True
    except Exception as e:
        logger.error(f"保存聊天历史失败 ({filepath}): {e}")
        # 清理临时文件
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        return False

def load_chat_history_from_file(filepath: str) -> list:
    """从指定文件加载聊天历史"""
    try:
        if not os.path.exists(filepath):
            logger.debug(f"聊天历史文件不存在: {filepath}")
            return []
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        messages = _deserialize_chat_history(data)
        logger.info(f"从文件加载了 {len(messages)} 条聊天历史记录: {os.path.basename(filepath)}")
        return messages
    except json.JSONDecodeError as e:
        logger.error(f"聊天历史文件格式错误 ({filepath}): {e}")
        # 备份损坏文件
        try:
            corrupted_path = filepath + '.corrupted.' + datetime.now().strftime('%Y%m%d_%H%M%S')
            shutil.copy2(filepath, corrupted_path)
            logger.info(f"已备份损坏文件到: {corrupted_path}")
        except (OSError, shutil.Error):
            pass
        return []
    except Exception as e:
        logger.error(f"加载聊天历史失败 ({filepath}): {e}")
        return []

def auto_save_chat_history():
    """把当前活跃会话落盘。

    改造后持久化的真源是 ChatManager 的会话文件（data/chat_sessions/<id>.json），
    这里只负责「保存当前会话」这件事，不再写全局的 auto_save.json。
    """
    try:
        return get_chat_manager().save_conversation(_active_session_id)
    except Exception as e:
        logger.error(f"保存会话失败: {e}")
        return False


def auto_load_chat_history():
    """启动时恢复会话。

    优先读会话文件；如果从来没有任何会话文件，而旧的 chat_history/auto_save.json
    里还有消息，就把它一次性迁移成默认会话，然后把旧文件改名归档，避免反复迁移。

    注意：本函数依赖 ChatManager，而 ChatManager 由 core 初始化后才可用。模块被
    提前导入时（例如打包工具扫描模块、或将来重构了导入顺序）拿到的会是 None，
    这时不做任何事，交给 _ensure_sessions_loaded() 在首次真正访问时补做。
    """
    global chat_history, _active_session_id, _sessions_initialized
    cm = get_chat_manager()
    if cm is None:
        logger.debug("ChatManager 尚未初始化，会话恢复延后到首次访问")
        return chat_history
    try:
        if not _has_session_files() and os.path.exists(AUTO_SAVE_FILE):
            legacy = load_chat_history_from_file(AUTO_SAVE_FILE)
            if legacy:
                from zhiyan_web.core.llm.providers import LLMMessage
                conv = cm.get_conversation("default")
                for item in legacy:
                    role = item.get("role")
                    if role not in ("user", "assistant"):
                        continue
                    text = (item.get("content") or "").strip()
                    if not text:
                        continue
                    conv.messages.append(LLMMessage(
                        role=role, content=item.get("content", ""),
                        time=item.get("time", "") or "",
                        think_ms=int(item.get("think_ms") or 0),
                    ))
                if conv.messages:
                    cm.auto_title(conv.id, conv.messages[0].content)
                cm.save_conversation("default")
                logger.info(f"已把旧的全局聊天历史迁移为默认会话（{len(conv.messages)} 条消息）")
            try:
                os.replace(AUTO_SAVE_FILE, AUTO_SAVE_FILE + ".migrated")
            except OSError:
                pass
        _active_session_id = cm.latest_conversation_id()
        _rebuild_display_history(_active_session_id)
        _sessions_initialized = True
    except Exception as e:
        logger.error(f"加载会话失败: {e}")
    return chat_history


def _ensure_sessions_loaded() -> None:
    """确保会话已从磁盘恢复（幂等，可在任何请求入口安全调用）。"""
    if _sessions_initialized:
        return
    if get_chat_manager() is None:
        return
    auto_load_chat_history()


def _activate_if_needed(session_id) -> None:
    """请求入口的会话预处理：先保证会话已恢复，再按需切换。"""
    _ensure_sessions_loaded()
    sid = (session_id or "").strip() if isinstance(session_id, str) else ""
    if sid and sid != _active_session_id:
        switch_session(sid)


def _has_session_files() -> bool:
    """磁盘上是否已经存在会话文件（用于判断要不要做一次性的旧数据迁移）。"""
    try:
        for name in os.listdir(get_chat_manager().sessions_dir()):
            if name.endswith(".json"):
                return True
    except OSError:
        pass
    return False


def _rebuild_display_history(session_id: str) -> list:
    """把某个会话重建成界面渲染用的 chat_history 视图。"""
    global chat_history
    try:
        chat_history = get_chat_manager().get_display_messages(session_id)
    except Exception as e:
        logger.error(f"重建会话视图失败: {e}")
        chat_history = []
    return chat_history


def switch_session(session_id: str) -> str:
    """切换活跃会话：先落盘旧会话，再载入目标会话的界面消息。

    传进来的 id 不存在时会被创建（容错：前端本地存了个已删除的 id 也不会炸）。
    ChatManager 尚未初始化时不做切换（打包工具扫描模块等场景会走到这里）。
    """
    global _active_session_id
    target = (session_id or "").strip() or "default"
    cm = get_chat_manager()
    if cm is None:
        logger.debug("ChatManager 未就绪，暂不切换会话")
        return _active_session_id
    if _active_session_id and _active_session_id != target:
        cm.save_conversation(_active_session_id)
    if not cm.conversation_exists(target):
        cm.create_conversation(conv_id=target)
    _active_session_id = target
    _rebuild_display_history(target)
    logger.info(f"已切换到会话: {target}")
    return target


# 启动时自动加载历史记录
auto_load_chat_history()


@router.get("/history")
async def get_chat_history(session_id: str = None):
    """
    获取聊天历史（默认返回当前活跃会话）

    参数:
        session_id: 可选，指定要读取的会话；不传则返回当前活跃会话
    """
    _activate_if_needed(session_id)
    return {
        "messages": chat_history,
        "session_id": _active_session_id,
        "sessions": get_chat_manager().get_conversations_list(),
    }


@router.delete("/history")
async def clear_chat_history(session_id: str = None):
    """
    清空聊天历史（默认清空当前活跃会话）
    """
    global chat_history
    _activate_if_needed(session_id)
    chat_history = []
    get_chat_manager().clear_conversation(_active_session_id)
    logger.info(f"会话已清空: {_active_session_id}")
    return {"status": "success", "message": "聊天历史已清空",
            "session_id": _active_session_id}


@router.post("/history/delete-message")
async def delete_chat_message(data: dict):
    """
    删除单条聊天消息
    
    参数:
        data: {"timestamp": "消息的time字段值"}
    
    返回:
        dict: 操作结果
    """
    global chat_history
    timestamp = data.get("timestamp", "")
    if not timestamp:
        return {"status": "error", "message": "缺少 timestamp 参数"}

    before = len(chat_history)
    chat_history = [msg for msg in chat_history if msg.get("time", "") != timestamp]
    removed = before - len(chat_history)

    # 同步从会话里删掉，否则刷新页面/切回会话时它又会冒出来
    try:
        cm = get_chat_manager()
        conv = cm.get_conversation(_active_session_id)
        keep = [m for m in conv.messages if (getattr(m, "time", "") or "") != timestamp]
        if len(keep) != len(conv.messages):
            conv.messages = keep
            cm.save_conversation(_active_session_id)
    except Exception as e:
        logger.warning(f"从会话中删除消息失败: {e}")

    if removed > 0:
        logger.info(f"已删除聊天消息: timestamp={timestamp}")
        return {"status": "success", "message": f"已删除 {removed} 条消息"}
    else:
        return {"status": "error", "message": "未找到匹配的消息"}


@router.post("/history/save")
async def manual_save_chat_history():
    """
    手动保存聊天历史到文件
    
    每次保存生成一个带时间戳的文件，同时更新自动保存文件。
    
    返回:
        dict: 保存结果
    """
    global chat_history
    if not chat_history:
        return {"status": "success", "message": "暂无聊天记录需要保存", "count": 0}
    
    # 生成带时间戳的备份文件
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_file = os.path.join(EXPORT_DIR, f'chat_history_backup_{timestamp}.json')
    
    success = save_chat_history_to_file(backup_file, chat_history)
    if success:
        # 同时更新自动保存文件
        auto_save_chat_history()
        return {
            "status": "success",
            "message": f"聊天历史已保存 ({len(chat_history)} 条消息)",
            "count": len(chat_history),
            "file": os.path.basename(backup_file)
        }
    else:
        raise HTTPException(status_code=500, detail="保存聊天历史失败")


@router.post("/history/export")
async def export_chat_history():
    """
    导出聊天历史为可下载的 JSON 文件
    
    返回:
        FileResponse: 包含聊天历史的 JSON 文件
    """
    global chat_history
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    export_file = os.path.join(EXPORT_DIR, f'chat_history_export_{timestamp}.json')
    
    success = save_chat_history_to_file(export_file, chat_history)
    if not success:
        raise HTTPException(status_code=500, detail="导出聊天历史失败")
    
    if not os.path.exists(export_file):
        raise HTTPException(status_code=500, detail="导出文件生成失败")
    
    return FileResponse(
        export_file,
        media_type='application/json',
        filename=f'chat_history_export_{timestamp}.json',
        headers={
            "Content-Disposition": f'attachment; filename="chat_history_export_{timestamp}.json"'
        }
    )


@router.post("/history/import")
async def import_chat_history(file: UploadFile = File(...)):
    """
    从上传的 JSON 文件导入聊天历史
    
    参数:
        file: 上传的 JSON 文件
    
    返回:
        dict: 导入结果
    """
    global chat_history
    
    if not file.filename or not file.filename.endswith('.json'):
        raise HTTPException(status_code=400, detail="只支持 JSON 格式的聊天历史文件")
    
    try:
        content = await file.read()
        
        # 限制文件大小（10MB）
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="文件大小不能超过 10MB")
        
        data = json.loads(content.decode('utf-8'))
        imported_messages = _deserialize_chat_history(data)
        
        if not imported_messages:
            raise HTTPException(status_code=400, detail="文件中没有有效的聊天记录")
        
        # 备份当前聊天历史
        if chat_history:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_file = os.path.join(EXPORT_DIR, f'pre_import_backup_{timestamp}.json')
            save_chat_history_to_file(backup_file, chat_history)
        
        # 替换当前聊天历史
        chat_history = imported_messages
        
        # 保存到自动保存文件
        auto_save_chat_history()
        
        return {
            "status": "success",
            "message": f"成功导入 {len(imported_messages)} 条聊天记录",
            "count": len(imported_messages)
        }
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="文件格式无效：不是有效的 JSON 文件")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"导入聊天历史失败: {e}")
        raise HTTPException(status_code=500, detail=f"导入失败: {str(e)}")


@router.get("/history/files")
async def list_history_files():
    """
    列出所有已保存的历史记录文件
    
    返回:
        dict: 文件列表
    """
    files = []
    try:
        # 列出自动保存和导出目录中的 JSON 文件
        all_files = []
        if os.path.exists(CHAT_HISTORY_DIR):
            all_files.extend([
                os.path.join(CHAT_HISTORY_DIR, f)
                for f in os.listdir(CHAT_HISTORY_DIR)
                if f.endswith('.json') and os.path.isfile(os.path.join(CHAT_HISTORY_DIR, f))
            ])
        if os.path.exists(EXPORT_DIR):
            all_files.extend([
                os.path.join(EXPORT_DIR, f)
                for f in os.listdir(EXPORT_DIR)
                if f.endswith('.json') and os.path.isfile(os.path.join(EXPORT_DIR, f))
            ])
        
        for fpath in sorted(all_files, key=os.path.getmtime, reverse=True):
            try:
                stat = os.stat(fpath)
                # 尝试读取文件中的消息数
                msg_count = 0
                try:
                    with open(fpath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        msg_count = data.get("message_count", 0)
                except (json.JSONDecodeError, OSError):
                    pass
                
                files.append({
                    "name": os.path.basename(fpath),
                    "path": fpath,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "message_count": msg_count
                })
            except OSError:
                continue
        
        return {"files": files, "count": len(files)}
    except Exception as e:
        logger.error(f"列出历史文件失败: {e}")
        return {"files": [], "count": 0, "error": str(e)}


@router.get("/context")
async def get_chat_context():
    """
    获取对话上下文信息
    
    返回:
        dict: 包含消息数量、Token 估算、摘要等信息
    """
    try:
        context = get_chat_manager().get_context()
        return context
    except Exception as e:
        logger.error(f"获取上下文信息失败: {e}")
        return {"message_count": 0, "estimated_tokens": 0, "summary": "", "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════
# 多轮会话管理
#
# 一个会话 = 一段独立的 LLM 上下文 + 一段独立的界面历史。真源在 ChatManager，
# 这里只做增删改查与切换。切换会同时换掉 LLM 的上下文和界面消息，两边不会脱节。
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/sessions")
async def list_sessions(refresh: bool = False):
    """
    获取所有会话列表

    参数:
        refresh: 传 true 时先重新扫描磁盘（多进程/外部改动后使用）
    """
    try:
        cm = get_chat_manager()
        _ensure_sessions_loaded()
        if refresh:
            cm.load_conversations()
        sessions = cm.get_conversations_list()
        return {"sessions": sessions, "count": len(sessions),
                "active": _active_session_id}
    except Exception as e:
        logger.error(f"获取会话列表失败: {e}")
        return {"sessions": [], "count": 0, "active": _active_session_id,
                "error": str(e)}


@router.post("/sessions")
async def create_session(payload: dict = {}):
    """新建会话并切换过去。

    参数:
        payload: {"title": 可选标题}
    """
    try:
        cm = get_chat_manager()
        _ensure_sessions_loaded()
        title = str((payload or {}).get("title") or "").strip()
        cm.save_conversation(_active_session_id)
        conv = cm.create_conversation(title=title)
        switch_session(conv.id)
        return {"status": "success", "session_id": conv.id,
                "sessions": cm.get_conversations_list(), "messages": []}
    except Exception as e:
        logger.error(f"新建会话失败: {e}")
        return {"status": "error", "message": str(e)}


@router.get("/checkpoints")
async def list_checkpoints(conv_id: str = "", limit: int = 30):
    """列出某个会话的文件检查点（Agent 每次写操作前自动快照）"""
    try:
        from zhiyan_web.agent import checkpoints as ck
        items = ck.list_checkpoints(conv_id or _active_session_id, limit)
        return {"status": "success", "conv_id": conv_id or _active_session_id, "checkpoints": items}
    except Exception as e:
        logger.error(f"读取检查点失败: {e}")
        return {"status": "error", "message": str(e), "checkpoints": []}


@router.post("/checkpoints/{checkpoint_id}/restore")
async def restore_checkpoint(checkpoint_id: str):
    """按检查点回滚文件"""
    try:
        from zhiyan_web.agent import checkpoints as ck
        result = ck.restore(checkpoint_id)
        return result
    except Exception as e:
        logger.error(f"检查点回滚失败: {e}")
        return {"ok": False, "error": str(e)}


@router.delete("/checkpoints/{checkpoint_id}")
async def delete_checkpoint(checkpoint_id: str):
    """删除一个检查点（只删快照，不动文件）"""
    try:
        from zhiyan_web.agent import checkpoints as ck
        return {"ok": ck.discard(checkpoint_id)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/sessions/{session_id}/fork")
async def fork_session(session_id: str, payload: dict = {}):
    """从指定会话分支出新会话。参数: {"keep_display": 保留的可见消息条数, "title": 可选标题}"""
    try:
        _ensure_sessions_loaded()
        cm = get_chat_manager()
        keep = int((payload or {}).get("keep_display") or 0)
        title = str((payload or {}).get("title") or "")
        conv = cm.fork_conversation(session_id, keep, title)
        if conv is None:
            return {"status": "error", "message": "会话不存在"}
        return {
            "status": "success",
            "session_id": conv.id,
            "title": conv.title,
            "message_count": len(conv.messages),
            "sessions": cm.get_conversations_list(),
        }
    except Exception as e:
        logger.error(f"会话分支失败: {e}")
        return {"status": "error", "message": str(e)}


@router.patch("/sessions/{session_id}")
async def rename_session(session_id: str, payload: dict = {}):
    """重命名会话。参数: {"title": "新标题"}"""
    try:
        title = str((payload or {}).get("title") or "").strip()
        if not title:
            return {"status": "error", "message": "标题不能为空"}
        cm = get_chat_manager()
        _ensure_sessions_loaded()
        if not cm.rename_conversation(session_id, title):
            return {"status": "error", "message": "会话不存在"}
        return {"status": "success", "session_id": session_id, "title": title,
                "sessions": cm.get_conversations_list()}
    except Exception as e:
        logger.error(f"重命名会话失败: {e}")
        return {"status": "error", "message": str(e)}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """删除会话。

    如果删掉的正是当前活跃会话，会自动切到最近更新的那个（一个都不剩时
    ChatManager 会把最后一次删除降级为清空，保证永远有会话可用）。
    """
    try:
        global _active_session_id
        _ensure_sessions_loaded()
        cm = get_chat_manager()
        was_active = (session_id == _active_session_id)
        ok = cm.delete_conversation(session_id)
        if not ok:
            return {"status": "error", "message": "会话不存在"}
        if was_active:
            switch_session(cm.latest_conversation_id())
        return {"status": "success", "session_id": session_id,
                "active": _active_session_id,
                "sessions": cm.get_conversations_list(),
                "messages": list(chat_history)}
    except Exception as e:
        logger.error(f"删除会话失败: {e}")
        return {"status": "error", "message": str(e)}


@router.post("/sessions/{session_id}/activate")
async def activate_session(session_id: str):
    """切换活跃会话，返回该会话的界面消息。"""
    try:
        _ensure_sessions_loaded()
        sid = switch_session(session_id)
        return {"status": "success", "session_id": sid,
                "messages": list(chat_history),
                "sessions": get_chat_manager().get_conversations_list()}
    except Exception as e:
        logger.error(f"切换会话失败: {e}")
        return {"status": "error", "message": str(e)}


@router.post("/sessions/{session_id}/clear")
async def clear_session(session_id: str):
    """清空某个会话的消息（保留会话本身）。"""
    try:
        _ensure_sessions_loaded()
        if session_id != _active_session_id:
            switch_session(session_id)
        get_chat_manager().clear_conversation(session_id)
        return {"status": "success", "session_id": session_id, "messages": []}
    except Exception as e:
        logger.error(f"清空会话失败: {e}")
        return {"status": "error", "message": str(e)}


@router.get("/sessions/active")
async def get_active_session():
    """当前活跃会话 id 及其消息（前端启动时用它对齐状态）。"""
    _ensure_sessions_loaded()
    return {"session_id": _active_session_id,
            "messages": list(chat_history),
            "sessions": get_chat_manager().get_conversations_list()}


@router.post("/send")
async def send_message(message: Dict[str, Any]):
    """
    发送消息（非流式）
    
    参数:
        message: 包含 content 和 role 字段的字典
    
    返回:
        dict: 包含 response 字段
    """
    content = message.get("content", "")
    if not content.strip():
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    # 前端可以指定会话；不传就落在当前活跃会话
    _activate_if_needed(message.get("session_id"))

    user_msg = {"role": "user", "content": content, "time": datetime.now().isoformat()}
    chat_history.append(user_msg)
    auto_save_chat_history()
    
    cm = get_chat_manager()
    
    try:
        # 检测是否为命令（以 # / ! 开头，# 供 miao-plugin 等插件使用）
        is_command = any(content.strip().startswith(p) for p in ["/", "!", "#"])

        if is_command and hasattr(cm, '_pipeline') and cm._pipeline:
            # 命令消息：通过管道处理
            result = await cm.process_via_pipeline(content)
            response_text = result.get("response", "")
        else:
            # 非命令消息（或管道不可用）：直接调用 LLM，跳过插件轮询
            response = await cm.chat(content, _active_session_id)
            response_text = response.content
        
        if response_text:
            ai_msg = {"role": "assistant", "content": response_text, "time": datetime.now().isoformat()}
            chat_history.append(ai_msg)
            auto_save_chat_history()
        
        return {"response": response_text, "session_id": _active_session_id}
    except Exception as e:
        import logging
        logging.getLogger("ZhiYanWeb").error(f"聊天错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent-send")
async def agent_send_message(message: Dict[str, Any]):
    """
    发送消息到 Agent（带工具调用能力）
    
    Agent 模式会自动识别用户意图，选择合适的工具来完成任务，
    如文件操作、代码编写、系统命令等。
    
    参数:
        message: 包含 content 字段的字典
    
    返回:
        dict: 包含 response 和 tool_calls 字段
    """
    content = message.get("content", "")
    if not content.strip():
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    _activate_if_needed(message.get("session_id"))

    user_msg = {"role": "user", "content": content, "time": datetime.now().isoformat()}
    chat_history.append(user_msg)
    auto_save_chat_history()

    cm = get_chat_manager()

    try:
        # 命令消息（/ ! # 前缀）先走管道（插件 / miao-plugin），避免 # 开头的 miao 命令被 LLM 拦截
        handled, cmd_response = await _try_pipeline_command(cm, content)
        if handled:
            response_text = cmd_response
        else:
            # 使用带工具调用的 Agent 聊天接口
            response_text = await cm.chat_with_tools(content, _active_session_id)

        if response_text:
            ai_msg = {"role": "assistant", "content": response_text, "time": datetime.now().isoformat()}
            chat_history.append(ai_msg)
            auto_save_chat_history()

        return {"response": response_text, "session_id": _active_session_id}
    except Exception as e:
        logger.error(f"Agent 聊天错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# 命令前缀：# 供 miao-plugin 等插件使用，/ 和 ! 为 ZhiYan 自带插件唤醒前缀。
# 无论当前回复引擎（normal/agent/dsh）如何，命令消息都应优先走管道（插件/miao-plugin），
# 避免 # 开头的 miao 命令被 LLM 直接拦截。
_COMMAND_PREFIXES = ["/", "!", "#"]


def _is_command_message(text: str) -> bool:
    return any(str(text or "").strip().startswith(p) for p in _COMMAND_PREFIXES)


# 演示触发词：发送这些内容会播放预设 Agent 轨迹（不调用 LLM，方便检查样式）
_DEMO_TRIGGERS = {"演示", "演示一下", "演示任务", "跑个演示", "demo"}


def _is_demo_request(text: str) -> bool:
    return str(text or "").strip().lower() in _DEMO_TRIGGERS


async def _try_pipeline_command(cm, content: str):
    """命令消息（/ ! # 前缀）优先通过管道处理（插件 / miao-plugin 桥接）。

    供 agent / dsh 等直接调用 LLM 的流式引擎在转发前拦截命令，
    保证 # 开头的 miao 命令不会误入 LLM。

    Returns:
        tuple[bool, str]: (是否已处理, 响应文本)。已处理时响应文本可能为空；
        非命令或管道不可用返回 (False, "")。
    """
    if not _is_command_message(content):
        return False, ""
    if not (hasattr(cm, "_pipeline") and cm._pipeline):
        return False, ""
    try:
        result = await cm.process_via_pipeline(content)
        return True, result.get("response", "")
    except Exception as e:
        logger.error(f"管道处理命令失败: {e}", exc_info=True)
        return True, f"❌ 命令处理出错: {type(e).__name__}: {e}"


@router.post("/agent-send-stream")
async def agent_send_message_stream(message: Dict[str, Any]):
    """
    发送消息到 Agent（流式 SSE 输出，带工具调用状态实时显示）
    
    Agent 模式会自动识别用户意图，选择合适的工具来完成任务，
    并通过 SSE 事件流实时推送工具调用状态和最终回复内容。
    
    SSE 事件类型:
    - event: thinking  — LLM 正在思考
    - event: tool_call — 正在调用工具
    - event: tool_result — 工具执行结果
    - event: chunk     — 回复内容片段
    - event: done      — 回复完成
    - event: error     — 错误信息
    
    参数:
        message: 包含 content 字段的字典
    
    返回:
        StreamingResponse: SSE 事件流
    """
    content = message.get("content", "")
    if not content.strip():
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    # 会话选择：不传就落在当前活跃会话；传了就先切过去，
    # 这样这一轮的 LLM 上下文和界面历史都指向同一个会话。
    _activate_if_needed(message.get("session_id"))

    user_msg = {"role": "user", "content": content, "time": datetime.now().isoformat()}
    chat_history.append(user_msg)
    # 让会话标题跟随首条消息（ChatManager.add_message 里也会补，这里兜住管道分支）
    try:
        get_chat_manager().auto_title(_active_session_id, content)
    except Exception:
        pass
    auto_save_chat_history()

    cm = get_chat_manager()
    started_at = time.monotonic()

    def _elapsed_ms() -> int:
        return int((time.monotonic() - started_at) * 1000)

    async def event_generator():
        full_content = ""
        try:
            # 命令消息（/ ! # 前缀）先走管道（插件 / miao-plugin），
            # 保证 # 开头的 miao 命令不会被 LLM 直接拦截。
            handled, cmd_response = await _try_pipeline_command(cm, content)
            if handled:
                if cmd_response:
                    full_content += cmd_response
                    yield f"event: chunk\ndata: {json.dumps({'type': 'chunk', 'text': cmd_response}, ensure_ascii=False)}\n\n"
                elapsed = _elapsed_ms()
                yield f"event: done\ndata: {json.dumps({'type': 'done', 'text': full_content, 'think_ms': elapsed, 'session_id': _active_session_id}, ensure_ascii=False)}\n\n"
                if full_content:
                    chat_history.append(build_ai_message(full_content, elapsed))
                    auto_save_chat_history()
                return
            # 演示触发词：直接播放预设 Agent 轨迹（不调用 LLM、不消耗 token），
            # 用来检查工具行 / 思考行 / 审批卡的样式与交互。
            if _is_demo_request(content):
                agent_stream = cm.run_demo_stream(_active_session_id, content)
            else:
                agent_stream = cm.chat_with_tools_stream(content, conv_id=_active_session_id)
            async for event in agent_stream:
                event_type = event.get("type", "")
                if event_type == "done":
                    # 回传最终思考耗时，前端据此定格，与落盘数值保持一致
                    event["think_ms"] = _elapsed_ms()
                    event["session_id"] = _active_session_id
                data = json.dumps(event, ensure_ascii=False)
                yield f"event: {event_type}\ndata: {data}\n\n"
                
                if event_type == "chunk":
                    full_content += event.get("text", "")
                elif event_type == "done":
                    # 保存到聊天历史
                    if full_content:
                        chat_history.append(build_ai_message(full_content, event.get("think_ms", 0)))
                        auto_save_chat_history()
        except asyncio.CancelledError:
            # 客户端断开连接（如用户点击停止键）
            logger.info("Agent 流式输出被客户端终止")
            if full_content:
                full_content += "\n\n（生成已终止）"
                chat_history.append(build_ai_message(full_content, _elapsed_ms()))
                auto_save_chat_history()
        except Exception as e:
            logger.error(f"Agent 流式聊天错误: {e}")
            yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.post("/approval-decision")
async def submit_approval_decision(payload: Dict[str, Any]):
    """提交「等待审批」卡片的选择（允许一次 / 拒绝），恢复被挂起的工具调用。

    参数:
        payload: {"id": 审批请求 id, "decision": "allow" | "deny"}
    """
    req_id = str(payload.get("id") or "")
    decision = str(payload.get("decision") or "")
    if not req_id:
        raise HTTPException(status_code=400, detail="缺少审批请求 id")

    from zhiyan_web.core.llm import approval_broker

    allow = decision.lower() in ("allow", "allow_once", "approve")
    ok = approval_broker.decide(req_id, "allow" if allow else "deny")
    if not ok:
        return {"ok": False, "error": "该审批请求已失效或无待处理项"}
    return {"ok": True, "id": req_id, "decision": "allow" if allow else "deny"}


@router.post("/agent-answer")
async def agent_answer_message(answer: Dict[str, Any]):
    """
    提交交互式提问的回答，恢复 ReAct 循环继续执行任务

    当 Agent 通过 ask_questions 工具向用户发起提问后，前端收集用户回答，
    调用此接口提交回答，后端注入回答后恢复之前的工具循环继续执行。

    参数:
        answer: 包含 answers（键为问题 ID，值为回答内容）和可选 conv_id 的字典

    返回:
        StreamingResponse: SSE 事件流（继续执行，可能再次提问或完成）
    """
    answers = answer.get("answers") or {}
    # 会话 id：优先取前端显式传的，其次回退到当前活跃会话。
    # 必须与提问时用的 conv_id 一致，否则取不到待回答状态。
    _activate_if_needed(answer.get("session_id") or answer.get("conv_id"))
    conv_id = _active_session_id

    cm = get_chat_manager()

    # 取出待回答状态（弹出式，避免重复提交）
    pending = question_agent.pop_pending(conv_id)
    if not pending:
        raise HTTPException(status_code=404, detail="没有待回答的提问")

    # 组装恢复上下文：answers + 之前保存的 ReAct 循环状态
    resume_ctx = {
        "answers": answers,
        "tool_call_id": pending.get("tool_call_id", ""),
        **pending.get("resume_ctx", {}),
    }

    started_at = time.monotonic()

    def _elapsed_ms() -> int:
        return int((time.monotonic() - started_at) * 1000)

    async def answer_event_generator():
        full_content = ""
        try:
            async for event in cm.chat_with_tools_stream("", conv_id, resume_ctx=resume_ctx):
                event_type = event.get("type", "")
                if event_type == "done":
                    event["think_ms"] = _elapsed_ms()
                data = json.dumps(event, ensure_ascii=False)
                yield f"event: {event_type}\ndata: {data}\n\n"

                if event_type == "chunk":
                    full_content += event.get("text", "")
                elif event_type == "done":
                    # 保存到聊天历史
                    if full_content:
                        chat_history.append(build_ai_message(full_content, event.get("think_ms", 0)))
                        auto_save_chat_history()
        except asyncio.CancelledError:
            # 客户端断开连接
            logger.info("Agent 回答流式输出被客户端终止")
            if full_content:
                full_content += "\n\n（生成已终止）"
                chat_history.append(build_ai_message(full_content, _elapsed_ms()))
                auto_save_chat_history()
        except Exception as e:
            logger.error(f"Agent 回答流式聊天错误: {e}")
            yield f"event: error\ndata: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        answer_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


async def trigger_tts(text: str, timeout: float = 120.0) -> str:
    """
    触发 TTS 合成
    
    参数:
        text: 要合成的文本
        timeout: 等待合成的超时时间（秒），默认 120 秒
    
    返回:
        str: 音频文件 URL（成功时），None（失败时）
    """
    try:
        import asyncio
        import logging
        import os
        
        tts = get_tts_engine()
        clean_text = tts._clean_text_for_tts(text)
        if len(clean_text) > 500:
            clean_text = clean_text[:500]
        
        emotion = tts.detect_emotion(text)
        
        result = {"success": False, "error": None, "file_path": None}
        event = asyncio.Event()
        
        def on_complete(file_path):
            nonlocal result
            result["success"] = True
            result["file_path"] = file_path
            event.set()
        
        def on_error(error):
            nonlocal result
            result["error"] = error
            event.set()
        
        tts.synthesize_with_emotion(clean_text, emotion=emotion, on_complete=on_complete, on_error=on_error)
        
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            if result["success"]:
                logging.getLogger("ZhiYanWeb").info(f"TTS 合成成功: {os.path.basename(result['file_path'])}")
                return f"/api/tts/audio/{os.path.basename(result['file_path'])}"
            else:
                logging.getLogger("ZhiYanWeb").warning(f"TTS 合成失败: {result['error']}")
        except asyncio.TimeoutError:
            logging.getLogger("ZhiYanWeb").warning(f"TTS 合成超时（等待 {timeout} 秒）")
            
    except Exception as e:
        import logging
        logging.getLogger("ZhiYanWeb").error(f"触发 TTS 错误: {e}")
    
    return None


# 聊天图片上传目录
CHAT_IMAGES_DIR = os.path.join(DATA_DIR, 'chat_images')


@router.post("/upload_image")
async def upload_chat_image(file: UploadFile = File(...)):
    """
    上传聊天图片
    
    参数:
        file: 上传的图片文件
    
    返回:
        dict: 包含图片 URL 的响应
    """
    if not file.content_type or not file.content_type.startswith('image/'):
        raise HTTPException(status_code=400, detail="只支持图片文件")
    
    try:
        os.makedirs(CHAT_IMAGES_DIR, exist_ok=True)
        
        # 生成唯一文件名
        ext = os.path.splitext(file.filename or 'image.png')[1] or '.png'
        filename = f"{uuid.uuid4().hex}{ext}"
        filepath = os.path.join(CHAT_IMAGES_DIR, filename)
        
        # 保存文件
        content = await file.read()
        
        # 限制文件大小（10MB）
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="图片大小不能超过 10MB")
        
        with open(filepath, 'wb') as f:
            f.write(content)
        
        url = f"/api/chat/images/{filename}"
        
        return {"success": True, "url": url, "filename": filename}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"图片上传失败: {e}")
        return {"success": False, "message": str(e)}


@router.get("/images/{filename}")
async def get_chat_image(filename: str):
    """
    获取聊天图片
    
    参数:
        filename: 图片文件名
    
    返回:
        FileResponse: 图片文件
    """
    filepath = os.path.join(CHAT_IMAGES_DIR, filename)
    # 防止路径穿越
    real_path = os.path.realpath(filepath)
    if not real_path.startswith(os.path.realpath(CHAT_IMAGES_DIR)):
        raise HTTPException(status_code=403, detail="访问被拒绝")
    
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="图片不存在")
    
    return FileResponse(filepath)


# 聊天通用文件上传目录（附加文件）
CHAT_FILES_DIR = os.path.join(DATA_DIR, 'chat_files')


@router.post("/upload_file")
async def upload_chat_file(file: UploadFile = File(...)):
    """
    上传聊天文件（通用文件类型）

    参数:
        file: 上传的文件

    返回:
        dict: 包含文件 URL 的响应
    """
    try:
        os.makedirs(CHAT_FILES_DIR, exist_ok=True)

        # 生成唯一文件名（保留原扩展名）
        ext = os.path.splitext(file.filename or 'file')[1] or '.bin'
        # 限制扩展名长度，防止异常
        if len(ext) > 12:
            ext = '.bin'
        filename = f"{uuid.uuid4().hex}{ext}"
        filepath = os.path.join(CHAT_FILES_DIR, filename)

        content = await file.read()

        # 限制文件大小（50MB）
        if len(content) > 50 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="文件大小不能超过 50MB")

        with open(filepath, 'wb') as f:
            f.write(content)

        url = f"/api/chat/files/{filename}"
        return {
            "success": True,
            "url": url,
            "filename": filename,
            "original_name": file.filename or filename,
            "size": len(content),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"文件上传失败: {e}")
        return {"success": False, "message": str(e)}


@router.get("/files/{filename}")
async def get_chat_file(filename: str):
    """
    获取聊天上传文件

    参数:
        filename: 文件名

    返回:
        FileResponse: 文件内容
    """
    filepath = os.path.join(CHAT_FILES_DIR, filename)
    real_path = os.path.realpath(filepath)
    if not real_path.startswith(os.path.realpath(CHAT_FILES_DIR)):
        raise HTTPException(status_code=403, detail="访问被拒绝")

    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(filepath, filename=filename)


@router.websocket("/ws")
async def chat_websocket(websocket: WebSocket):
    """
    流式聊天 WebSocket 端点
    
    支持的消息类型:
    - user_message: 用户发送消息
    - ai_chunk: AI 响应片段
    - ai_message: AI 完整响应
    - context_info: 上下文信息
    - thinking_start: 开始思考（暂无实际使用）
    - error: 错误消息
    """
    await manager.connect(websocket)
    
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "message")
            message = data.get("content", "")
            
            if not message.strip():
                continue

            # 会话选择：前端可以在每条消息里带上 session_id
            _sid = data.get("session_id") or data.get("sessionId")
            if _sid:
                try:
                    _activate_if_needed(_sid)
                except Exception as e:
                    logger.warning(f"WebSocket 切换会话失败: {e}")
            
            # 检查是否为 /say 指令（格式：/say 语音内容）
            say_pattern = r'^/say\s+(.+)$'
            say_match = re.match(say_pattern, message.strip(), re.DOTALL)
            if say_match:
                say_text = say_match.group(1).strip()
                if say_text:
                    if len(say_text) > 500:
                        say_text = say_text[:500]
                    
                    settings = get_settings()
                    tts_enabled = settings.get("tts_enabled", False)
                    
                    if tts_enabled:
                        # TTS 启用：合成语音
                        audio_url = await trigger_tts(say_text)
                        if audio_url:
                            await manager.broadcast({
                                "type": "voice_message",
                                "audio_url": audio_url,
                                "text": say_text,
                                "time": datetime.now().isoformat()
                            })
                        else:
                            await manager.broadcast({
                                "type": "error",
                                "message": "语音合成失败"
                            })
                    else:
                        # TTS 禁用：保存文本到文件
                        try:
                            save_dir = os.path.join(DATA_DIR, 'say_saved')
                            os.makedirs(save_dir, exist_ok=True)
                            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                            filename = f"say_{timestamp}.txt"
                            filepath = os.path.join(save_dir, filename)
                            with open(filepath, 'w', encoding='utf-8') as f:
                                f.write(say_text)
                            await manager.broadcast({
                                "type": "voice_message",
                                "audio_url": None,
                                "text": say_text,
                                "time": datetime.now().isoformat(),
                                "saved_path": filepath,
                                "mode": "text_only"
                            })
                        except Exception as e:
                            logger.error(f"保存文本失败: {e}")
                            await manager.broadcast({
                                "type": "error",
                                "message": f"保存文本失败: {str(e)}"
                            })
                continue
            
            # 记录用户消息到聊天历史
            user_msg = {"role": "user", "content": message, "time": datetime.now().isoformat()}
            chat_history.append(user_msg)
            auto_save_chat_history()
            
            # 广播用户消息
            await manager.broadcast({"type": "user_message", "message": user_msg})
            
            try:
                full_response = ""
                
                # 检测是否为命令（以 # / ! 开头，# 供 miao-plugin 等插件使用）
                cm = get_chat_manager()
                is_command = any(message.strip().startswith(p) for p in ["/", "!", "#"])

                if is_command and hasattr(cm, '_pipeline') and cm._pipeline:
                    # 命令消息：通过管道处理（包括插件轮询）
                    result = await cm.process_via_pipeline(message)
                    full_response = result.get("response", "")
                    
                    if full_response:
                        # 直接发送完整响应（非流式）
                        await manager.broadcast({
                            "type": "ai_chunk", "chunk": full_response
                        })
                else:
                    # 非命令消息（或管道不可用）：直接流式调用 LLM，跳过插件轮询
                    def callback(chunk):
                        nonlocal full_response
                        # 过滤 __REASONING__: 前缀（DeepSeek 思考模式）
                        if chunk.startswith("__REASONING__:"):
                            reasoning_text = chunk[14:]
                            if reasoning_text:
                                asyncio.create_task(
                                    manager.broadcast({"type": "reasoning_chunk", "content": reasoning_text})
                                )
                            return
                        full_response += chunk
                        # 使用 create_task 在后台广播
                        asyncio.create_task(
                            manager.broadcast({"type": "ai_chunk", "chunk": chunk})
                        )
                    
                    response = await get_chat_manager().chat_stream(
                        message, conv_id=_active_session_id, callback=callback)

                # 广播完整消息
                ai_msg = {"role": "assistant", "content": full_response, "time": datetime.now().isoformat()}
                await manager.broadcast({"type": "ai_message", "message": ai_msg})
                chat_history.append(ai_msg)
                auto_save_chat_history()
                
                # 广播上下文信息
                try:
                    context = get_chat_manager().get_context(_active_session_id)
                    await manager.broadcast({"type": "context_info", "context": context})
                except Exception as e:
                    logger.debug(f"广播上下文信息失败: {e}")

                # 广播情感/好感度状态更新
                try:
                    cm = get_chat_manager()
                    if hasattr(cm, '_pipeline') and cm._pipeline and hasattr(cm._pipeline, 'context'):
                        pipeline_context = cm._pipeline.context
                        if getattr(pipeline_context, 'emotion_engine', None):
                            emotion_data = pipeline_context.emotion_engine.get_emotion()
                            await manager.broadcast({
                                "type": "emotion_update",
                                "emotion": emotion_data,
                            })
                        if getattr(pipeline_context, 'bond_engine', None):
                            bond_data = pipeline_context.bond_engine.get_status()
                            await manager.broadcast({
                                "type": "bond_update",
                                "bond": bond_data,
                            })
                    from zhiyan_web.likability.routers.likability import get_likability_status
                    likability_data = await get_likability_status()
                    if likability_data.get("success"):
                        await manager.broadcast({
                            "type": "likability_update",
                            "likability": likability_data,
                        })
                except Exception as e:
                    logger.debug(f"广播情感状态失败: {e}")

                # 如果启用了 TTS，触发语音合成
                settings = get_settings()
                if settings.get("tts_enabled", False) and full_response:
                    try:
                        await trigger_tts(full_response)
                    except Exception as e:
                        logger.error(f"触发 TTS 失败: {e}")
                
            except Exception as e:
                logger.error(f"WebSocket 聊天错误: {e}")
                await manager.broadcast({"type": "error", "message": str(e)})
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket 异常: {e}")
        manager.disconnect(websocket)

# ============= Composer `@` 文件引用：工作区文件索引 =============

_FILE_INDEX_CACHE: Dict[str, Any] = {"root": "", "ts": 0.0, "files": []}
_FILE_INDEX_TTL = 120.0
_FILE_INDEX_MAX = 4000
_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "dist38", "build", "build38", ".idea", ".vscode",
    ".dsh-acl-repair", "data", "logs", "备份",
}


def _workspace_root_for_index() -> str:
    """取当前工作区根目录（拿不到就退回进程 cwd）"""
    try:
        from zhiyan_web.computer.services.workspace_manager import get_workspace
        root = get_workspace() or ""
    except Exception:
        root = ""
    return root or os.getcwd()


def _scan_workspace_files(root: str) -> list:
    """有界遍历工作区，返回相对路径清单"""
    files: list = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS and not d.startswith(".")
        ]
        depth = dirpath[len(root):].count(os.sep)
        if depth >= 6:
            dirnames[:] = []
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace("\\", "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            files.append({"path": rel, "name": fn, "size": size})
            if len(files) >= _FILE_INDEX_MAX:
                return files
    return files


@router.get("/files")
async def search_chat_files(q: str = "", limit: int = 30):
    """Composer 输入 `@` 时的文件候选（工作区索引，带 2 分钟缓存）

    参数: q 关键字（子串匹配，不区分大小写）, limit 返回条数
    返回: {root, total, count, files:[{path,name,size}]}
    """
    root = _workspace_root_for_index()
    now = time.time()
    if (
        _FILE_INDEX_CACHE["root"] != root
        or now - _FILE_INDEX_CACHE["ts"] > _FILE_INDEX_TTL
        or not _FILE_INDEX_CACHE["files"]
    ):
        try:
            files = await asyncio.to_thread(_scan_workspace_files, root)
        except Exception as e:
            logger.warning(f"工作区文件索引失败: {e}")
            files = []
        _FILE_INDEX_CACHE.update({"root": root, "ts": now, "files": files})

    files = _FILE_INDEX_CACHE["files"]
    query = (q or "").strip().lower()
    matched = [f for f in files if query in f["path"].lower()] if query else list(files)
    matched.sort(key=lambda f: (
        0 if f["name"].lower().startswith(query) else 1,
        len(f["path"]),
        f["path"],
    ))
    try:
        limit = max(1, min(int(limit or 30), 100))
    except (TypeError, ValueError):
        limit = 30
    return {
        "root": root,
        "total": len(files),
        "count": len(matched[:limit]),
        "files": matched[:limit],
    }
