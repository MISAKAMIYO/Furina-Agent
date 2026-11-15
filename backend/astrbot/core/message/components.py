"""
AstrBot message.components 兼容模块

用于 `import astrbot.core.message.components as Comp` 的导入方式。
"""

from astrbot.api.message_components import Plain, Image, At, Face, Reply, Node, Nodes, Poke, Share, Record, File, Video


class Video:
    """视频消息组件（兼容 AstrBot）"""
    def __init__(self, file: str = "", url: str = "", **kwargs) -> None:
        self.file = file
        self.url = url or file
        self.type = "Video"

    def __str__(self) -> str:
        return f"[Video: {self.url or self.file}]"


class BaseMessageComponent:
    """消息组件基类（兼容 AstrBot）"""
    type: str = ""


class Json:
    """JSON 消息卡片组件（兼容 AstrBot）。data 始终为 dict，传入 str 时自动解析。"""
    type: str = "Json"
    data: dict

    def __init__(self, data=None, **_kwargs) -> None:
        if isinstance(data, str):
            try:
                import json as _json
                data = _json.loads(data)
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}
        self.data = data

    def __str__(self) -> str:
        return f"[Json:{self.data}]"


__all__ = ["Plain", "Image", "At", "Face", "Reply", "Node", "Nodes", "Poke", "Share", "Record", "Video", "BaseMessageComponent", "Json"]