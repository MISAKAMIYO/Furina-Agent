from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class MessageSession:
    platform_name: str = "webchat"
    message_type: str = "friend"
    session_id: str = ""
    sender_id: str = ""
    group_id: str = ""

    def __str__(self) -> str:
        return f"{self.platform_name}:{self.message_type}:{self.session_id}"