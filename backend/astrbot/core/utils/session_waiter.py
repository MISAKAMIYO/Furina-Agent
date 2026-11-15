"""
AstrBot session_waiter 兼容模块

提供异步会话等待机制，用于插件需要等待用户输入的场景。
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable


class SessionController:
    """会话控制器，用于控制会话的生命周期"""
    
    def __init__(self) -> None:
        self._stopped = False
        self._timeout = 60
    
    def stop(self) -> None:
        """停止会话"""
        self._stopped = True
    
    def keep(self, timeout: int = 60, reset_timeout: bool = True) -> None:
        """保持会话继续"""
        self._timeout = timeout
    
    @property
    def is_stopped(self) -> bool:
        return self._stopped


class SessionWaiterTimeout(Exception):
    """会话等待超时异常"""
    pass


def session_waiter(timeout: int = 60) -> Callable:
    """会话等待装饰器
    
    用法:
        @session_waiter(timeout=60)
        async def collect_options(controller: SessionController, event: AstrMessageEvent):
            controller.stop()
    """
    def decorator(func: Callable) -> Callable:
        async def wrapper(*args, **kwargs) -> Any:
            controller = SessionController()
            try:
                result = await asyncio.wait_for(
                    func(controller, *args, **kwargs),
                    timeout=timeout
                )
                return result
            except asyncio.TimeoutError:
                raise SessionWaiterTimeout("会话等待超时")
        return wrapper
    return decorator


__all__ = ["session_waiter", "SessionController", "SessionWaiterTimeout"]