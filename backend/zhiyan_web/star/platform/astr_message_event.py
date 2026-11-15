from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from .message_session import MessageSession
from .message_chain import MessageChain, MessageEventResult


class AstrMessageEvent:
    def __init__(
        self,
        message_str: str = "",
        session_id: str = "",
        sender_id: str = "",
        sender_name: str = "",
    ) -> None:
        self.message_str = message_str
        # 设置 message_obj 为兼容对象，供插件通过 event.message_obj.session_id 等方式访问
        self.message_obj = SimpleNamespace(
            session_id=session_id,
            sender=SimpleNamespace(
                user_id=sender_id,
                nickname=sender_name,
                card="",
                role="member",
            ),
            group_id="",
            user_id=sender_id,
            self_id="",
            raw_message=message_str,
            message_type="",
            post_type="",
            notice_type="",
            message=[],  # 消息组件列表，兼容 AstrBot 插件
            message_str=message_str,  # 原始消息文本
            message_id="",
            timestamp=time.time(),
        )
        self.role = "member"
        self.is_wake = False
        self.is_at_or_wake_command = False
        self._extras: dict[str, Any] = {}
        self._force_stopped: bool = False
        self.session = MessageSession(
            platform_name="webchat",
            message_type="friend",
            session_id=session_id,
        )
        self._result: MessageEventResult | None = None
        self._has_send_oper = False
        self.call_llm = False
        self.plugins_name: list[str] | None = None
        self._sender_id = sender_id
        self._sender_name = sender_name

    @property
    def unified_msg_origin(self) -> str:
        return str(self.session)

    def get_message_str(self) -> str:
        return self.message_str

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_sender_name(self) -> str:
        return self._sender_name

    def get_platform_name(self) -> str:
        return self.session.platform_name

    def get_self_id(self) -> str:
        return ""

    def is_private_chat(self) -> bool:
        return True

    def get_result(self) -> MessageEventResult | None:
        return self._result

    def set_result(self, result: MessageEventResult) -> None:
        self._result = result

    def clear_result(self) -> None:
        self._result = None

    def is_stopped(self) -> bool:
        return self._force_stopped or (
            self._result is not None and hasattr(self._result, '_stop') and self._result._stop
        )

    def stop_event(self) -> None:
        self._force_stopped = True

    def get_extra(self, key: str, default=None) -> Any:
        return self._extras.get(key, default)

    def set_extra(self, key: str, value: Any) -> None:
        self._extras[key] = value

    def get_messages(self) -> list:
        return []

    def get_message_outline(self) -> str:
        return self.message_str[:50]

    def cleanup_temporary_local_files(self) -> None:
        pass

    async def send(self, message_chain: MessageChain | MessageEventResult | str) -> None:
        self._has_send_oper = True
        if isinstance(message_chain, MessageEventResult):
            self.set_result(message_chain)
        elif isinstance(message_chain, str):
            self.set_result(MessageEventResult.message(message_chain))
        elif isinstance(message_chain, MessageChain):
            self.set_result(MessageEventResult.message(str(message_chain)))