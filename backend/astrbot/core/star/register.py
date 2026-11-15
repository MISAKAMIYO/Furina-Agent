"""
AstrBot core.star.register 兼容模块

用于 `from astrbot.core.star.register import register_star` 的导入方式。
"""


def register_star(*args, **kwargs):
    """注册 Star 插件（兼容 AstrBot）"""
    def decorator(cls):
        return cls
    return decorator


def register_event_message_type(*args, **kwargs):
    """注册事件消息类型处理器（兼容 AstrBot）"""
    def decorator(func):
        return func
    return decorator


__all__ = ["register_star", "register_event_message_type"]