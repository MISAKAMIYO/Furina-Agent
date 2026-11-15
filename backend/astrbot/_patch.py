"""
AstrBot 兼容层 - 补丁模块

在现有类上添加 AstrBot 插件所需的缺失方法。
此模块在 astrbot 包初始化时自动执行。
"""

from __future__ import annotations

import logging

from zhiyan_web.star.platform.astr_message_event import AstrMessageEvent
from zhiyan_web.star.platform.message_chain import MessageEventResult, MessageChain

logger = logging.getLogger("ZhiYanWeb.star")


# ────────── AstrMessageEvent 补丁 ──────────

def _plain_result(self, text: str) -> MessageEventResult:
    """返回纯文本结果（兼容 AstrBot）"""
    result = MessageEventResult.message(text)
    self.set_result(result)
    return result

def _image_result(self, url: str) -> MessageEventResult:
    """返回图片结果（兼容 AstrBot）"""
    result = MessageEventResult()
    result._text = f"[Image: {url}]"
    result._url = url
    self.set_result(result)
    return result

def _chain_result(self, chain: list) -> MessageEventResult:
    """返回消息链结果（兼容 AstrBot）"""
    result = MessageEventResult()
    result._text = " ".join(str(c) for c in chain) if chain else ""
    self.set_result(result)
    return result

def _get_group_id(self) -> str:
    """获取群组 ID（兼容 AstrBot）"""
    if hasattr(self, 'session') and hasattr(self.session, 'group_id'):
        return self.session.group_id or ""
    return ""

def _is_admin(self) -> bool:
    """检查是否为管理员（兼容 AstrBot）"""
    return self.role == "admin" if hasattr(self, 'role') else False

def _get_message_type(self) -> str:
    """获取消息类型（兼容 AstrBot）"""
    if hasattr(self, 'session') and hasattr(self.session, 'message_type'):
        return self.session.message_type
    return "friend"

def _get_session_id(self) -> str:
    """获取会话 ID（兼容 AstrBot）"""
    if hasattr(self, 'session') and hasattr(self.session, 'session_id'):
        return self.session.session_id
    return ""

# 应用补丁
AstrMessageEvent.plain_result = _plain_result
AstrMessageEvent.image_result = _image_result
AstrMessageEvent.chain_result = _chain_result
AstrMessageEvent.get_group_id = _get_group_id
AstrMessageEvent.is_admin = _is_admin
AstrMessageEvent.get_message_type = _get_message_type
AstrMessageEvent.get_session_id = _get_session_id

# ─── event.bot 兼容补丁 ──────────────────────────────
# 部分插件（如 astrbot_plugin_box）无条件访问 event.bot 调用 QQ API。
# 在 webchat 平台下，暴露一个占位 bot 对象（避免 AttributeError）。
# 注意：调用 bot API 时**抛出** BotAPINotAvailableError 而不是返回空值，
# 因为返回空 dict 会让依赖 API 数据的插件（如 box）拿到空数据后继续执行、
# 在绘制图片等下游环节才崩溃（报出让人困惑的 ValueError: height and width must be > 0）。
# 抛出异常后，插件自身的 try/except（如 box 的 except: return "无效QQ号"）能正常兜底。
class BotAPINotAvailableError(Exception):
    """Web 环境不支持 QQ Bot API 调用。"""


async def _noop_api_call(*args, **kwargs):
    """Web 环境 bot API 占位：标记不可用。"""
    raise BotAPINotAvailableError(
        "webchat 平台不支持 QQ Bot API 调用，当前环境无法完成该操作"
    )


class _DummyBotAPI:
    """占位 bot API 客户端，避免插件因 event.bot 缺失而崩溃。"""
    api = None

    async def call_action(self, action: str, **params):
        logger.warning(f"webchat 平台不支持 bot API 调用: {action}")
        raise BotAPINotAvailableError(
            f"webchat 平台不支持 QQ Bot API 调用: {action}"
        )

    def __getattr__(self, name):
        logger.warning(f"webchat 平台不支持 bot API 调用: {name}")
        return _noop_api_call


class _DummyBot:
    def __init__(self):
        self.api = _DummyBotAPI()

    def __getattr__(self, name):
        logger.warning(f"webchat 平台不支持 bot 属性: {name}")
        return _noop_api_call


def _get_bot(self):
    """获取事件关联的 bot 客户端（兼容 AstrBot）"""
    if not hasattr(self, '_dummy_bot'):
        object.__setattr__(self, '_dummy_bot', _DummyBot())
    return self._dummy_bot


AstrMessageEvent.bot = property(_get_bot)

logger.debug("AstrBot 兼容补丁已应用到 AstrMessageEvent")