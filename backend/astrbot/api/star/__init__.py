"""
AstrBot star 兼容模块 - 映射到 zhiyan_web.star
"""

from __future__ import annotations

from typing import Any

from zhiyan_web.star import Context as _Context
from zhiyan_web.star import Star as _Star
from zhiyan_web.star.star import StarMetadata, star_map, star_registry


class Star(_Star):
    """AstrBot 兼容的 Star 插件基类
    
    扩展了原 Star 类，添加了 AstrBot 插件常用的方法:
    - html_render() - 渲染 HTML 模板为图片
    - text_to_image() - 将文本渲染为图片
    """

    async def html_render(self, template: str, data: dict | None = None, **kwargs) -> str:
        """渲染 HTML 模板为图片并返回 URL（兼容 AstrBot）
        
        注意: 当前环境不支持 HTML 渲染，返回空字符串。
        插件应使用 text_to_image() 替代或将 HTML 渲染交由前端处理。
        """
        import logging
        logger = logging.getLogger("ZhiYanWeb.star")
        logger.warning("html_render 需要 HTML 渲染后端支持，当前返回空字符串")
        return ""

    async def text_to_image(self, text: str, **kwargs) -> str:
        """将文本渲染为图片并返回 URL（兼容 AstrBot）
        
        注意: 当前环境不支持文本渲染为图片，返回空字符串。
        """
        import logging
        logger = logging.getLogger("ZhiYanWeb.star")
        logger.warning("text_to_image 需要 HTML 渲染后端支持，当前返回空字符串")
        return ""


def register(
    name: str,
    author: str = "unknown",
    desc: str = "",
    version: str = "1.0.0",
    repo: str = "",
    **kwargs,
) -> Any:
    """AstrBot 风格的 @register 类装饰器
    
    用法:
        @register("plugin_name", "author", "description", "1.0.0", "repo_url")
        class MyPlugin(Star):
            ...
    
    这会自动设置插件的元数据。
    """
    def decorator(cls: type) -> type:
        # 设置类属性
        cls.name = name
        cls.author = author
        cls.desc = desc
        cls.version = version
        cls.repo = repo
        
        # 更新已经注册的元数据
        if cls.__module__ in star_map:
            meta = star_map[cls.__module__]
            meta.name = name or meta.name
            meta.author = author or meta.author
            meta.desc = desc or meta.desc
            meta.version = version or meta.version
            meta.repo = repo or meta.repo
        
        return cls
    return decorator


__all__ = ["Context", "Star", "register"]


# 向后兼容 - 确保 Context 可直接访问
from zhiyan_web.star import Context as Context

# 导出 StarTools
from astrbot.core.star.star_tools import StarTools