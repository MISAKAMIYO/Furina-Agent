"""
对话持久化存储模块
提供加密的聊天历史存储与检索功能

数据存储路径: data/conversations/
"""
from __future__ import annotations

import os
import json
import base64
import hashlib
import logging
import shutil
from datetime import datetime, date
from typing import Optional

from zhiyan_web.config.settings import DATA_DIR

logger = logging.getLogger("ZhiYanWeb.Memory")

# ============= 配置 =============

CONVERSATIONS_DIR = os.path.join(DATA_DIR, 'conversations')
os.makedirs(CONVERSATIONS_DIR, exist_ok=True)

STORAGE_VERSION = 1
MAX_CONVERSATION_FILES = 100  # 最大保留对话文件数
MAX_SEARCH_BATCH = 20        # 每次搜索最多扫描的文件数

# ============= 加密支持 =============

_HAS_CRYPTO = False
_CIPHER = None

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    _HAS_CRYPTO = True
except ImportError:
    logger.warning("cryptography 库未安装，对话历史将以明文存储")


def _get_encryption_key() -> bytes:
    """生成基于机器信息的加密密钥"""
    machine_id = os.environ.get("COMPUTERNAME", "ZHIYAN_WEB")
    user_drive = os.path.splitdrive(os.path.abspath(__file__))[0]
    salt = b"zhiyan_conversation_salt_2026"
    key_material = f"{machine_id}|{user_drive}|ZHIYAN_CONVERSATION_SECRET".encode()
    derived = hashlib.sha256(key_material + salt).digest()
    return base64.urlsafe_b64encode(derived[:32])


def _get_cipher():
    """获取加密器实例"""
    global _CIPHER
    if _CIPHER is None and _HAS_CRYPTO:
        try:
            key = _get_encryption_key()
            _CIPHER = Fernet(key)
        except Exception as e:
            logger.error(f"初始化加密器失败: {e}")
    return _CIPHER


def encrypt_data(data: str) -> str:
    """加密字符串数据"""
    cipher = _get_cipher()
    if cipher:
        try:
            return cipher.encrypt(data.encode('utf-8')).decode('utf-8')
        except Exception as e:
            logger.error(f"加密失败: {e}")
    return data


def decrypt_data(encrypted: str) -> str:
    """解密字符串数据"""
    cipher = _get_cipher()
    if cipher:
        try:
            return cipher.decrypt(encrypted.encode('utf-8')).decode('utf-8')
        except Exception as e:
            logger.error(f"解密失败: {e}")
            return ""
    return encrypted


# ============= 数据存储格式 =============

def _conversation_file_path(conversation_id: str) -> str:
    """获取对话文件的路径"""
    # 使用 SHA256 哈希确保文件名安全
    safe_name = hashlib.sha256(conversation_id.encode()).hexdigest()[:16]
    return os.path.join(CONVERSATIONS_DIR, f"conv_{safe_name}.json")


def _get_default_conversation_data(conversation_id: str) -> dict:
    """获取默认对话数据"""
    return {
        "version": STORAGE_VERSION,
        "conversation_id": conversation_id,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "message_count": 0,
        "messages": [],
        "metadata": {
            "encrypted": _HAS_CRYPTO,
            "tags": [],
            "summary": "",
        }
    }


def save_conversation(conversation_id: str, messages: list) -> bool:
    """
    保存对话到加密文件

    Args:
        conversation_id: 对话 ID
        messages: 消息列表，格式为 [{"role": str, "content": str, "time": str}, ...]

    Returns:
        bool: 是否保存成功
    """
    if not messages:
        return True

    filepath = _conversation_file_path(conversation_id)

    data = _get_default_conversation_data(conversation_id)
    data["messages"] = messages
    data["message_count"] = len(messages)
    data["updated_at"] = datetime.now().isoformat()

    # 限制消息数（最多保留最近 500 条）
    if len(messages) > 500:
        data["messages"] = messages[-500:]
        data["message_count"] = 500

    try:
        json_str = json.dumps(data, ensure_ascii=False, indent=2)
        encrypted_str = encrypt_data(json_str)

        # 原子写入
        temp_path = filepath + '.tmp'
        with open(temp_path, 'w', encoding='utf-8') as f:
            # 写入加密标记，方便识别
            if _HAS_CRYPTO:
                f.write("__ENCRYPTED__\n")
            f.write(encrypted_str)
        os.replace(temp_path, filepath)

        # 清理旧文件
        _cleanup_old_files()

        logger.debug(f"对话已保存: {conversation_id[:16]}... ({len(messages)} 条消息)")
        return True
    except Exception as e:
        logger.error(f"保存对话失败: {e}")
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        return False


def load_conversation(conversation_id: str) -> list:
    """
    从加密文件加载对话

    Args:
        conversation_id: 对话 ID

    Returns:
        list: 消息列表
    """
    filepath = _conversation_file_path(conversation_id)
    if not os.path.exists(filepath):
        return []

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()

        # 检查加密标记
        is_encrypted = content.startswith("__ENCRYPTED__\n")
        if is_encrypted:
            encrypted_str = content[len("__ENCRYPTED__\n"):]
            json_str = decrypt_data(encrypted_str)
        else:
            json_str = content

        if not json_str:
            logger.error("解密对话数据失败")
            return []

        data = json.loads(json_str)
        messages = data.get("messages", [])
        logger.debug(f"对话已加载: {conversation_id[:16]}... ({len(messages)} 条消息)")
        return messages
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"加载对话失败: {e}")
        # 备份损坏文件
        try:
            backup_path = filepath + '.corrupted.' + datetime.now().strftime('%Y%m%d_%H%M%S')
            shutil.copy2(filepath, backup_path)
        except (OSError, shutil.Error):
            pass
        return []


def delete_conversation(conversation_id: str) -> bool:
    """删除对话文件"""
    filepath = _conversation_file_path(conversation_id)
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            logger.info(f"对话已删除: {conversation_id[:16]}...")
            return True
        return False
    except Exception as e:
        logger.error(f"删除对话失败: {e}")
        return False


def list_conversations() -> list:
    """列出所有保存的对话"""
    conversations = []
    try:
        if not os.path.exists(CONVERSATIONS_DIR):
            return []

        for fname in os.listdir(CONVERSATIONS_DIR):
            if not fname.startswith('conv_') or not fname.endswith('.json'):
                continue
            filepath = os.path.join(CONVERSATIONS_DIR, fname)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    first_line = f.readline().strip()
                    is_encrypted = first_line == "__ENCRYPTED__"
                    if is_encrypted:
                        encrypted_str = f.read()
                        json_str = decrypt_data(encrypted_str)
                    else:
                        # 第一行是 JSON 的一部分
                        f.seek(0)
                        json_str = f.read()

                    if not json_str:
                        continue

                    data = json.loads(json_str)
                    conversations.append({
                        "conversation_id": data.get("conversation_id", fname),
                        "message_count": data.get("message_count", 0),
                        "created_at": data.get("created_at", ""),
                        "updated_at": data.get("updated_at", ""),
                        "encrypted": is_encrypted,
                        "file": fname,
                    })
            except Exception:
                continue

        # 按更新时间倒序排列
        conversations.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        return conversations
    except Exception as e:
        logger.error(f"列出对话失败: {e}")
        return []


def _cleanup_old_files():
    """清理超过最大数量的旧文件"""
    try:
        files = sorted(
            [os.path.join(CONVERSATIONS_DIR, f) for f in os.listdir(CONVERSATIONS_DIR)
             if f.startswith('conv_') and f.endswith('.json')],
            key=os.path.getmtime
        )
        while len(files) > MAX_CONVERSATION_FILES:
            oldest = files.pop(0)
            os.remove(oldest)
            logger.debug(f"已清理旧对话文件: {os.path.basename(oldest)}")
    except Exception:
        pass


def get_conversation_summary(conversation_id: str) -> str:
    """
    生成对话摘要（用于 LLM 上下文）

    从对话中提取关键信息，压缩到适合 LLM 上下文的长度
    """
    messages = load_conversation(conversation_id)
    if not messages:
        return ""

    # 提取最近的消息作为上下文（最多 10 条）
    recent = messages[-10:]
    summary_parts = []
    for msg in recent:
        role = "用户" if msg.get("role") == "user" else "AI"
        content = msg.get("content", "")
        # 截断过长的消息
        if len(content) > 200:
            content = content[:200] + "..."
        summary_parts.append(f"{role}: {content}")

    return "\n".join(summary_parts)


def _read_conversation_file(filepath: str) -> Optional[dict]:
    """读取单个对话文件并返回解析后的数据"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            first_line = f.readline().strip()
            is_encrypted = first_line == "__ENCRYPTED__"
            if is_encrypted:
                encrypted_str = f.read()
                json_str = decrypt_data(encrypted_str)
            else:
                f.seek(0)
                json_str = f.read()
        if not json_str:
            return None
        return json.loads(json_str)
    except Exception:
        return None


def search_conversations(query: str, max_results: int = 5) -> list:
    """
    搜索对话历史（基于关键词匹配）

    优化策略:
    1. 优先搜索最近修改的文件（热点数据优先）
    2. 限制每次搜索扫描的文件数量
    3. 按相关度排序，取最优结果

    Args:
        query: 搜索关键词
        max_results: 最大结果数

    Returns:
        list: 匹配的消息列表，包含 conversation_id 和消息内容
    """
    results = []
    query_lower = query.lower()
    if not query_lower:
        return []

    try:
        # 获取所有对话文件，按修改时间排序（最新优先）
        files = []
        for fname in os.listdir(CONVERSATIONS_DIR):
            if not fname.startswith('conv_') or not fname.endswith('.json'):
                continue
            filepath = os.path.join(CONVERSATIONS_DIR, fname)
            files.append((filepath, os.path.getmtime(filepath)))

        # 按修改时间倒序排列（最新文件优先搜索）
        files.sort(key=lambda x: x[1], reverse=True)

        # 仅扫描最近修改的文件（提高性能）
        files_to_scan = files[:MAX_SEARCH_BATCH]

        for filepath, _ in files_to_scan:
            data = _read_conversation_file(filepath)
            if not data:
                continue

            conv_id = data.get("conversation_id", os.path.basename(filepath))
            for msg in data.get("messages", []):
                content = msg.get("content", "")
                if query_lower in content.lower():
                    results.append({
                        "conversation_id": conv_id,
                        "role": msg.get("role", "unknown"),
                        "content": content[:300],  # 截断
                        "time": msg.get("time", ""),
                        "relevance": content.lower().count(query_lower),
                    })
                    if len(results) >= max_results * 10:
                        break

        # 按相关度排序
        results.sort(key=lambda x: x.get("relevance", 0), reverse=True)
        return results[:max_results]
    except Exception as e:
        logger.error(f"搜索对话失败: {e}")
        return []


def get_recent_context(max_messages: int = 10) -> str:
    """
    获取最近对话的上下文摘要（用于 LLM 系统提示词）

    从最近保存的对话中提取最新消息，生成格式化的上下文文本

    Args:
        max_messages: 最大消息数

    Returns:
        str: 格式化后的对话上下文
    """
    try:
        # 获取最近修改的对话文件
        files = []
        for fname in os.listdir(CONVERSATIONS_DIR):
            if not fname.startswith('conv_') or not fname.endswith('.json'):
                continue
            filepath = os.path.join(CONVERSATIONS_DIR, fname)
            files.append((filepath, os.path.getmtime(filepath)))

        if not files:
            return ""

        # 取最新文件
        files.sort(key=lambda x: x[1], reverse=True)
        latest_file = files[0][0]

        data = _read_conversation_file(latest_file)
        if not data:
            return ""

        messages = data.get("messages", [])
        if not messages:
            return ""

        # 取最近的消息
        recent = messages[-max_messages:]
        parts = []
        for msg in recent:
            role = "用户" if msg.get("role") == "user" else "AI"
            content = msg.get("content", "")
            if len(content) > 200:
                content = content[:200] + "..."
            parts.append(f"{role}: {content}")

        return "【本对话历史记录】\n" + "\n".join(parts)
    except Exception as e:
        logger.debug(f"获取最近对话上下文失败: {e}")
        return ""


# 自动清理旧对话文件
_cleanup_old_files()