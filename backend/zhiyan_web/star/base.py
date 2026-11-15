from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .star import StarMetadata, star_map, star_registry

if TYPE_CHECKING:
    from .context import Context

logger = logging.getLogger("ZhiYanWeb")


class Star:
    """所有插件（Star）的父类，所有插件都应该继承于这个类"""

    author: str
    name: str
    context: Context

    def __init__(self, context: Context, config: dict | None = None) -> None:
        self.context = context

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not star_map.get(cls.__module__):
            metadata = StarMetadata(
                star_cls_type=cls,
                module_path=cls.__module__,
            )
            star_map[cls.__module__] = metadata
            star_registry.append(metadata)
        else:
            star_map[cls.__module__].star_cls_type = cls
            star_map[cls.__module__].module_path = cls.__module__

    async def initialize(self) -> None:
        """当插件被激活时会调用这个方法"""

    async def terminate(self) -> None:
        """当插件被禁用、重载插件时会调用这个方法"""

    def __del__(self) -> None:
        """[Deprecated] 当插件被禁用、重载插件时会调用这个方法"""