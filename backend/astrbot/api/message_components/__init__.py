"""
AstrBot 消息组件兼容模块

支持 AstrBot 插件常用的消息组件:
- Plain: 纯文本
- Image: 图片
- At: @某人
- Face: QQ 表情
- Reply: 回复
- Node/Nodes: 合并转发
- Poke: 戳一戳
- Share: 链接分享
"""

from __future__ import annotations

from typing import Any


class Plain:
    """纯文本消息组件"""
    def __init__(self, text: str = "", **kwargs) -> None:
        self.text = text
        self.type = "Plain"

    def __str__(self) -> str:
        return self.text


class Image:
    """图片消息组件"""
    def __init__(self, file: str = "", url: str = "", **kwargs) -> None:
        self.file = file
        self.url = url or file
        self.type = "Image"

    @staticmethod
    def fromBytes(data: bytes, **kwargs) -> Image:
        return Image(file="[bytes]", **kwargs)

    def __str__(self) -> str:
        return f"[Image: {self.url or self.file}]"


class At:
    """@某人 消息组件"""
    def __init__(self, qq: str = "", **kwargs) -> None:
        self.qq = qq
        self.type = "At"

    def __str__(self) -> str:
        return f"@{{{self.qq}}}"


class Face:
    """QQ 表情消息组件"""
    def __init__(self, id: int = 0, **kwargs) -> None:
        self.id = id
        self.type = "Face"

    def __str__(self) -> str:
        return f"[Face:{self.id}]"


class Reply:
    """回复消息组件"""
    def __init__(self, id: str = "", **kwargs) -> None:
        self.id = id
        self.type = "Reply"

    def __str__(self) -> str:
        return f"[Reply:{self.id}]"


class Node:
    """合并转发节点"""
    def __init__(self, uin: str = "", name: str = "", content: list | None = None, **kwargs) -> None:
        self.uin = uin
        self.name = name
        self.content = content or []
        self.type = "Node"

    def __str__(self) -> str:
        return f"[Node:{self.name}]"


class Nodes:
    """合并转发消息列表"""
    def __init__(self, nodes: list[Node] | None = None, **kwargs) -> None:
        self.nodes = nodes or []
        self.type = "Nodes"

    def __str__(self) -> str:
        return f"[Nodes: {len(self.nodes)} items]"


class Poke:
    """戳一戳消息组件"""
    def __init__(self, id: str = "", **kwargs) -> None:
        self.id = id
        self.type = "Poke"

    def __str__(self) -> str:
        return f"[Poke:{self.id}]"


class Share:
    """链接分享消息组件"""
    def __init__(self, url: str = "", title: str = "", content: str = "", image: str = "", **kwargs) -> None:
        self.url = url
        self.title = title
        self.content = content
        self.image = image
        self.type = "Share"

    def __str__(self) -> str:
        return f"[Share:{self.title}]({self.url})"


class Record:
    """语音消息组件（兼容 AstrBot）"""
    def __init__(self, file: str = "", **kwargs) -> None:
        self.file = file
        self.type = "Record"

    def __str__(self) -> str:
        return f"[Record: {self.file}]"


class File:
    """文件消息组件（兼容 AstrBot）"""
    def __init__(self, name: str = "", file: str = "", url: str = "", **kwargs) -> None:
        self.name = name or file.split("/")[-1].split("\\")[-1]
        self.file = file
        self.url = url or file
        self.type = "File"

    def __str__(self) -> str:
        return f"[File: {self.name}]({self.file})"


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


__all__ = [
    "Plain",
    "Image",
    "At",
    "Face",
    "Reply",
    "Node",
    "Nodes",
    "Poke",
    "Share",
    "Record",
    "File",
    "Video",
    "BaseMessageComponent",
]