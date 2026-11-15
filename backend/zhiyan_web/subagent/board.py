"""
共享白板

同一轮里并行派出的多个子代理，容易互相不知道对方已经做过什么，导致重复检索、
重复读同一批文件。白板就是给它们共用的便签墙：谁有了结论就写一条，别人动手前
先读一眼。

作用域是「一次用户回合」：回合开始时建立，回合结束（或超过存活时间）后回收。
它挂在 ContextVar 上，asyncio 派生子任务时会复制上下文，因此同一回合里派出的
所有子代理（含嵌套层级）拿到的是同一个白板对象。

条目刻意保持极简（key + 一句话结论 + 作者），因为它的内容会被注入子代理的
系统提示词，写太长就等于把噪音搬进每个子代理的上下文。
"""
from __future__ import annotations

import logging
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ZhiYanWeb.SubAgent.Board")

# 单条 value 的长度上限：白板是摘要，不是仓库
MAX_VALUE_CHARS = 1200
# 白板条目上限，超出后淘汰最早的
MAX_ENTRIES = 40
# 注入系统提示词时的整体长度上限
MAX_DIGEST_CHARS = 1800


@dataclass
class BoardEntry:
    key: str
    value: str
    author: str = ""          # 展示用署名（如「调研员」）
    author_id: str = ""       # 归属标识（run_id），用于把作者自己写的内容从他的摘要里排除
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "value": self.value, "author": self.author,
                "author_id": self.author_id, "ts": self.ts}


class SharedBoard:
    """一次回合内的共享白板（内存对象，不做持久化）。"""

    def __init__(self, turn_id: str = "") -> None:
        self.turn_id = turn_id
        self._entries: List[BoardEntry] = []
        self.created_at = time.time()

    # ── 读写 ──
    def write(self, key: str, value: str, author: str = "",
              author_id: str = "") -> Dict[str, Any]:
        key = (key or "").strip()
        value = (value or "").strip()
        if not key or not value:
            return {"ok": False, "error": "key 与 value 都不能为空"}
        if len(value) > MAX_VALUE_CHARS:
            value = value[:MAX_VALUE_CHARS] + "…（已截断）"
        # 同 key 更新不再追加，避免白板被同一个主题刷屏
        for entry in self._entries:
            if entry.key == key:
                entry.value = value
                entry.author = author or entry.author
                entry.author_id = author_id or entry.author_id
                entry.ts = time.time()
                return {"ok": True, "updated": True, "key": key,
                        "author": entry.author}
        self._entries.append(BoardEntry(key=key, value=value, author=author,
                                        author_id=author_id))
        if len(self._entries) > MAX_ENTRIES:
            self._entries = self._entries[-MAX_ENTRIES:]
        return {"ok": True, "updated": False, "key": key, "author": author}

    def read(self, key: str = "") -> Any:
        key = (key or "").strip()
        if key:
            for entry in self._entries:
                if entry.key == key:
                    return entry.to_dict()
            return {"ok": False, "error": f"白板上没有 '{key}' 这条记录",
                    "keys": [e.key for e in self._entries]}
        return {"ok": True, "entries": [e.to_dict() for e in self._entries]}

    def keys(self) -> List[str]:
        return [e.key for e in self._entries]

    def is_empty(self) -> bool:
        return not self._entries

    def snapshot(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self._entries]

    # ── 注入子代理系统提示词 ──
    def digest(self, exclude_author_id: str = "") -> str:
        """返回给子代理看的白板摘要（无内容时返回空串）。"""
        lines: List[str] = []
        for entry in self._entries:
            if exclude_author_id and entry.author_id == exclude_author_id:
                continue
            who = f"（{entry.author}）" if entry.author else ""
            lines.append(f"- 【{entry.key}】{entry.value}{who}")
        if not lines:
            return ""
        text = "\n".join(lines)
        if len(text) > MAX_DIGEST_CHARS:
            text = text[:MAX_DIGEST_CHARS] + "\n…（白板内容过长已截断）"
        return ("\n\n**共享白板（同一批并行子代理的中间发现，动手前先看一眼，"
                "自己有了结论也写回去）:**\n" + text)


_board: ContextVar[Optional[SharedBoard]] = ContextVar("zhiyan_subagent_board", default=None)
# 当前执行者的署名 / 唯一标识：由 runner 在每个子代理自己的任务上下文里设置，
# 因此并发子代理之间不会互相覆盖（asyncio 任务各自持有上下文副本）。
_author: ContextVar[str] = ContextVar("zhiyan_subagent_author", default="")
_author_id: ContextVar[str] = ContextVar("zhiyan_subagent_author_id", default="")


def begin_board(turn_id: str = "") -> tuple[Optional[SharedBoard], Any]:
    """开启回合白板，返回 (board, token)。"""
    board = SharedBoard(turn_id)
    return board, _board.set(board)


def end_board(token: Any) -> None:
    try:
        _board.reset(token)
    except (ValueError, LookupError):
        pass


def current_board() -> Optional[SharedBoard]:
    return _board.get()


def set_author(name: str, author_id: str = "") -> tuple[Any, Any]:
    return _author.set(name or ""), _author_id.set(author_id or "")


def reset_author(token: tuple[Any, Any]) -> None:
    try:
        _author.reset(token[0])
        _author_id.reset(token[1])
    except (ValueError, LookupError, IndexError, TypeError):
        pass


def current_author() -> str:
    return _author.get()


def current_author_id() -> str:
    return _author_id.get()
