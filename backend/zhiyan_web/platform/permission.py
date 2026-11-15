"""
QQ 平台权限与访问控制
=====================
参照 AstrBot 的权限体系实现：

用户等级（从高到低）：
- master  —— 主人（master_id），最高权限
- admin   —— 管理员（admins_id，兼容 AstrBot 顶层 admins_id）
- whitelist —— 白名单用户（whitelist）
- none    —— 其他用户

访问模式（access_mode）：
- all              —— 所有人可触发
- whitelist        —— 仅 白名单/管理员/主人 可触发
- whitelist_at_only—— 仅 白名单/管理员/主人 可触发，且群内需 @ 机器人（默认）
- admins_only      —— 仅 管理员/主人 可触发

其他管控：
- blacklist 用户 / blocked_groups 群：命中则彻底忽略
- allowed_groups：非空时仅允许这些群
- private_enabled / group_enabled：私聊/群聊总开关
- permission_reply：无权限时的处理（ignore 静默 / notify 提示一次）
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .qq_event import QQMessageEvent

ACCESS_MODES = ("all", "whitelist", "whitelist_at_only", "admins_only")

_PERM_DEFAULTS: Dict[str, Any] = {
    "master_id": "",                # 主人 QQ 号（最高权限）
    "admins_id": [],                # 管理员 QQ 号列表
    "whitelist": [],                # 白名单用户 QQ 号
    "blacklist": [],                # 黑名单用户 QQ 号（消息直接忽略）
    "blocked_groups": [],           # 屏蔽的群（群内消息忽略）
    "allowed_groups": [],           # 仅允许这些群（空 = 不限制）
    "access_mode": "whitelist_at_only",
    "private_enabled": True,        # 是否响应私聊
    "group_enabled": True,          # 是否响应群聊
    "permission_reply": "ignore",   # 无权限处理：ignore 静默 / notify 提示一次
}

_LIST_KEYS = ("admins_id", "whitelist", "blacklist", "blocked_groups", "allowed_groups")

# 允许通过配置接口更新的权限字段
PERM_CONFIG_KEYS = set(_PERM_DEFAULTS.keys())


def get_perm_cfg() -> Dict[str, Any]:
    """读取权限配置（与默认值合并，并兼容顶层 admins_id）"""
    from zhiyan_web.config import get_settings

    try:
        settings = get_settings()
        qq = settings.get("qq_platform") or {}
    except Exception:
        settings, qq = {}, {}

    cfg = dict(_PERM_DEFAULTS)
    for k in _PERM_DEFAULTS:
        if k in qq:
            cfg[k] = qq[k]

    if cfg.get("master_id"):
        cfg["master_id"] = str(cfg["master_id"])
    for k in _LIST_KEYS:
        cfg[k] = [str(x) for x in (cfg[k] or [])]

    # 兼容 AstrBot 风格：顶层 admins_id 并入管理员列表
    top_admins = settings.get("admins_id") or []
    merged = list(dict.fromkeys(cfg["admins_id"] + [str(x) for x in top_admins]))
    cfg["admins_id"] = merged

    if cfg["access_mode"] not in ACCESS_MODES:
        cfg["access_mode"] = "whitelist_at_only"
    return cfg


def user_level(user_id: Any, cfg: Dict[str, Any]) -> str:
    """获取用户等级：master / admin / whitelist / none"""
    uid = str(user_id or "")
    if not uid:
        return "none"
    if cfg.get("master_id") and uid == str(cfg["master_id"]):
        return "master"
    if uid in cfg.get("admins_id", []):
        return "admin"
    if uid in cfg.get("whitelist", []):
        return "whitelist"
    return "none"


def is_blacklisted(user_id: Any, group_id: Any, cfg: Dict[str, Any]) -> bool:
    """用户或群是否命中黑名单"""
    uid, gid = str(user_id or ""), str(group_id or "")
    if uid and uid in cfg.get("blacklist", []):
        return True
    if gid and gid in cfg.get("blocked_groups", []):
        return True
    return False


def group_allowed(group_id: Any, cfg: Dict[str, Any]) -> bool:
    """群是否在允许范围内（allowed_groups 为空表示不限制）"""
    gid = str(group_id or "")
    allowed = cfg.get("allowed_groups", [])
    return (not allowed) or (gid in allowed)


def check_access(ev: QQMessageEvent, cfg: Dict[str, Any]) -> Tuple[bool, str, str]:
    """判定一条消息是否允许处理。

    返回: (是否允许, 用户等级, 原因)
    """
    uid, gid = str(ev.user_id or ""), str(ev.group_id or "")

    if is_blacklisted(uid, gid, cfg):
        return False, "none", "blacklisted"
    if ev.is_private and not cfg.get("private_enabled", True):
        return False, "none", "private_disabled"
    if ev.is_group:
        if not cfg.get("group_enabled", True):
            return False, "none", "group_disabled"
        if not group_allowed(gid, cfg):
            return False, "none", "group_not_allowed"

    mode = cfg.get("access_mode", "whitelist_at_only")
    level = user_level(uid, cfg)

    if mode == "all":
        return True, level, "allowed"
    if level in ("master", "admin"):
        return True, level, "admin_or_master"
    if mode == "admins_only":
        return False, level, "not_admin"
    if level == "whitelist":
        return True, level, "whitelist"
    return False, level, "not_whitelist"


def is_admin_command(text: str) -> bool:
    """是否为 QQ 平台管理命令（形如 /qq_xxx），仅管理员/主人可执行"""
    return text.strip().lower().startswith("/qq_")


def mutate_perm_list(field: str, item: Any = None, action: str = "add") -> Dict[str, Any]:
    """增删权限列表字段（持久化到 qq_platform）"""
    if field not in _LIST_KEYS:
        return {"success": False, "message": f"不支持的字段: {field}"}
    if action not in ("add", "remove", "clear"):
        return {"success": False, "message": f"不支持的操作: {action}"}

    from zhiyan_web.config import get_settings, update_settings

    settings = get_settings()
    qq = dict(settings.get("qq_platform") or {})
    lst = [str(x) for x in (qq.get(field) or [])]

    if action == "clear":
        lst = []
    else:
        item = str(item or "").strip()
        if not item:
            return {"success": False, "message": "缺少 QQ 号"}
        if action == "add":
            if item not in lst:
                lst.append(item)
        else:
            lst = [x for x in lst if x != item]

    qq[field] = lst
    update_settings({"qq_platform": qq})
    return {"success": True, "message": f"{field} 已更新 ({len(lst)} 项)"}
