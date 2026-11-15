from __future__ import annotations

import os
from typing import Any


class MessageChain:
    def __init__(self, chain: list | None = None) -> None:
        self.chain: list = chain or []

    def message(self, text: str) -> MessageChain:
        self.chain.append({"type": "text", "text": text})
        return self

    def image(self, url: str) -> MessageChain:
        self.chain.append({"type": "image", "url": url})
        return self

    def voice(self, url: str) -> MessageChain:
        self.chain.append({"type": "voice", "url": url})
        return self

    def __str__(self) -> str:
        """将消息链转换为可渲染的文本格式"""
        parts = []
        for seg in self.chain:
            if not isinstance(seg, dict):
                # 兼容 Comp.Plain / Comp.Image / Comp.File 等消息组件对象
                parts.append(str(seg))
                continue
            seg_type = seg.get("type", "text")
            if seg_type == "text":
                parts.append(seg.get("text", ""))
            elif seg_type == "image":
                url = seg.get("url", "")
                parts.append(f"![image]({url})")
            elif seg_type == "voice":
                url = seg.get("url", "")
                parts.append(f"[voice]{url}[/voice]")
            else:
                parts.append(str(seg))
        return "".join(parts)

    def to_segments(self) -> list[dict]:
        """返回消息段列表，供前端渲染"""
        return self.chain


class MessageEventResult:
    """插件处理结果（兼容 AstrBot 的 CommandResult）。

    除基本文本外，支持 AstrBot 的链式 API：
      CommandResult().file_image(path).chain(...).error("...")
    图片/语音以 ZhiYan 媒体标记（[图片:路径]/[语音:路径]）渲染。
    """

    def __init__(self) -> None:
        self._message_chain: MessageChain | None = None
        self._text: str = ""
        self._url: str | None = None
        self._error_message: str | None = None
        self._image_paths: list[str] = []
        self._image_urls: list[str] = []
        self._voice_urls: list[str] = []
        self._use_llm: bool | None = None
        self._timeout: float | None = None
        self._stop: bool = False

    @staticmethod
    def message(text: str) -> MessageEventResult:
        result = MessageEventResult()
        result._text = text
        return result

    # ---- AstrBot CommandResult 链式 API ----
    def chain(self, obj) -> MessageEventResult:
        if isinstance(obj, MessageChain):
            for seg in obj.chain:
                self._append_seg(seg)
        elif isinstance(obj, str):
            self._text = self._text + obj if self._text else obj
        elif isinstance(obj, (list, tuple)):
            for seg in obj:
                self._append_seg(seg)
        else:
            self._append_seg(obj)
        return self

    def image(self, url) -> MessageEventResult:
        if url:
            self._image_urls.append(str(url))
        return self

    def file_image(self, path) -> MessageEventResult:
        if path:
            self._image_paths.append(str(path))
        return self

    def voice(self, url) -> MessageEventResult:
        if url:
            self._voice_urls.append(str(url))
        return self

    def record(self, url) -> MessageEventResult:
        if url:
            self._voice_urls.append(str(url))
        return self

    def use_llm(self, val) -> MessageEventResult:
        self._use_llm = bool(val)
        return self

    def set_timeout(self, sec) -> MessageEventResult:
        try:
            self._timeout = float(sec)
        except Exception:
            self._timeout = None
        return self

    def use_t2i(self, val) -> MessageEventResult:
        # AstrBot T2I（文生图）开关；本实现不启用，仅兼容链式调用
        return self

    def error(self, message) -> MessageEventResult:
        self._error_message = str(message)
        return self

    def _append_seg(self, seg) -> None:
        if isinstance(seg, dict):
            st = seg.get("type")
            if st == "text":
                t = seg.get("text", "") or seg.get("content", "")
                if t:
                    self._text = self._text + t if self._text else t
            elif st in ("image", "record", "video", "file"):
                u = seg.get("url") or seg.get("file") or seg.get("path")
                if st == "image" and u:
                    self._image_urls.append(u)
                elif st in ("record", "video", "file") and u:
                    self._voice_urls.append(u)
        elif hasattr(seg, "type"):  # Comp.* 消息组件
            t = getattr(seg, "type", "text")
            try:
                s = str(seg)
            except Exception:
                s = ""
            if t == "image" and s:
                self._image_urls.append(s)
            elif s:
                self._text = self._text + s if self._text else s

    def get_text(self) -> str:
        if self._error_message:
            return self._error_message
        parts: list[str] = []
        if self._text:
            parts.append(self._text)
        for p in self._image_paths:
            parts.append(f"[图片:{os.path.abspath(p)}]")
        for u in self._image_urls:
            parts.append(f"![Image]({u})")
        for v in self._voice_urls:
            parts.append(f"[语音:{os.path.abspath(v)}]")
        if not parts:
            return ""
        return "\n".join(parts)

    def set_text(self, text: str) -> None:
        self._text = text

    text = property(get_text, set_text)