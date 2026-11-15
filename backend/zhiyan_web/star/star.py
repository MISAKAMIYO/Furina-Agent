from __future__ import annotations

from dataclasses import dataclass, field
from types import ModuleType
from typing import TYPE_CHECKING, Any

star_registry: list[StarMetadata] = []
star_map: dict[str, StarMetadata] = {}

# 平台别名映射：插件 metadata 中的 support_platforms 声明与运行平台归一化对照。
# 例如 astrbot 插件声明 aiocqhttp/napcat/onebot 均视为 QQ 平台。
_PLATFORM_ALIASES: dict[str, set[str]] = {
    "qq": {"qq", "aiocqhttp", "napcat", "onebot", "onebot11", "qqbot", "qqchannel"},
    "webchat": {"webchat", "zhiyan", "zhiyan_web", "console", "consoleweb", "web"},
}


def _normalize_platform(name: str) -> str:
    """将平台名归一化为标准平台：qq / webchat / 其他原样小写"""
    n = (name or "").lower().strip()
    for canonical, aliases in _PLATFORM_ALIASES.items():
        if n in aliases:
            return canonical
    return n


def platform_matches(support_platforms: list[str] | None, platform_name: str | None) -> bool:
    """判断插件声明支持的平台是否包含当前运行平台。

    规则（参照 AstrBot）：
    - support_platforms 为空 / 未声明 → 全平台可用（向后兼容）。
    - 声明了平台 → 任一声明平台与当前平台（含别名）匹配即允许。
    """
    if not support_platforms:
        return True
    cur = _normalize_platform(platform_name)
    if not cur:
        return True
    for sp in support_platforms:
        if _normalize_platform(sp) == cur:
            return True
    return False


def resolve_platforms(support_platforms: list[str] | None) -> list[str]:
    """解析插件的实际可用平台（返回归一化后的标准平台名列表）。

    规则（参照 AstrBot + 本项目约定）：
    - 插件声明了 support_platforms → 按声明解析（含别名归一化）。
    - 未声明 / 声明为空 → 通用（网页 + QQ 均可用）。
      只有显式标注 QQ 平台的插件（如 aiocqhttp / napcat / qq）才走 QQ 平台
      专用通道，其余插件网页端也可运行。
    """
    if support_platforms:
        result: list[str] = []
        for sp in support_platforms:
            norm = _normalize_platform(sp)
            if norm and norm not in result:
                result.append(norm)
        if result:
            return result
    # 未声明 / 声明为空：视为通用（网页端也可使用）
    return ["webchat", "qq"]

if TYPE_CHECKING:
    from .base import Star


@dataclass
class StarMetadata:
    name: str | None = None
    author: str | None = None
    desc: str | None = None
    short_desc: str | None = None
    version: str | None = None
    repo: str | None = None

    star_cls_type: type[Star] | None = None
    module_path: str | None = None

    star_cls: Star | None = None
    module: ModuleType | None = None
    root_dir_name: str | None = None
    reserved: bool = False

    activated: bool = True

    config: dict | None = None
    """插件配置（dict 形式）"""

    star_handler_full_names: list[str] = field(default_factory=list)

    display_name: str | None = None
    logo_path: str | None = None
    support_platforms: list[str] = field(default_factory=list)
    astrbot_version: str | None = None
    i18n: dict[str, dict] = field(default_factory=dict)
    pages: list[dict] = field(default_factory=list)

    # 兼容旧版 PluginMetadata：允许通过 plugin_cls_type 参数名初始化
    plugin_cls_type: Any = field(default=None, repr=False)
    """兼容旧版 PluginMetadata 的字段别名，实际委托给 star_cls_type"""

    def __post_init__(self):
        """初始化后处理：将 plugin_cls_type 的值同步到 star_cls_type"""
        if self.plugin_cls_type is not None and self.star_cls_type is None:
            self.star_cls_type = self.plugin_cls_type

    @property
    def plugin_id(self) -> str:
        p_name = (self.name or "unknown").lower().replace("/", "_")
        p_author = (self.author or "unknown").lower().replace("/", "_")
        return f"{p_author}/{p_name}"

    def __str__(self) -> str:
        return f"Plugin {self.name} ({self.version}) by {self.author}: {self.desc}"

    def __repr__(self) -> str:
        return f"Plugin {self.name} ({self.version}) by {self.author}: {self.desc}"