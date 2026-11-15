"""
QQOfficialPlatformAdapter 兼容占位类

ZhiYan 不注册 QQ官方(botpy) 平台适配器，此占位类仅供第三方插件做 isinstance
类型检查；ZhiYan 平台管理器返回的适配器不会是它的实例，相关分支会被跳过。
"""

from __future__ import annotations


class QQOfficialPlatformAdapter:
    """兼容 AstrBot 的 QQ官方平台适配器（占位，未接入实际平台）。"""

    def get_client(self):
        """返回 QQ官方 bot 客户端（占位为空）。"""
        return None


__all__ = ["QQOfficialPlatformAdapter"]