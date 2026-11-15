"""QQ 平台前置插件审核

在 QQ 平台执行任何插件前，先对插件名 / 命令名做一次安全审核：
命中内置（或配置追加）的「高风险关键词」时，视为可能触发 QQ 封号，
不执行插件，并返回明确的报错提示，告知用户应在 QQ 平台禁用该插件。

本模块仅对 "qq" 平台生效；网页端等本地平台不受影响。
"""
from __future__ import annotations

from typing import Dict, List, Optional

# 内置高风险命令关键词（子串匹配，命中即拦截）。这些类型在 QQ 上极容易被
# 风控/封号体系识别并处罚：批量群发、自动回复、抢红包、防撤回、刷屏/轰炸、
# 自动化养号/加群/点赞、抽奖转盘等。
QQ_RISK_COMMANDS_DEFAULT: List[str] = [
    "群发", "私发广告", "广告", "轰炸", "刷屏", "刷赞", "刷粉",
    "自动回复", "关键词回复", "被动回复", "围观自动",
    "抢红包", "自动抢", "抢专属红包",
    "防撤回", "撤回助手", "撤回监控",
    "批量加群", "自动加群", "自动退群", "批量退群",
    "自动点赞", "自动关注", "养号", "辅助验证",
    "定时群发", "群发助手", "整点毒鸡汤",
    "签到打卡自动", "福利秒杀",
    "艾特全体", "at all", "@全体成员自动",
    "抽奖", "转盘", "概率赠送", "秒杀",
]

# 内置高风险插件名关键词（子串匹配插件（Star）注册名）。同时覆盖中文插件名，
# 便于对「广告群发」「红包助手」这类中文命名的插件也能拦截。
QQ_RISK_PLUGINS_DEFAULT: List[str] = [
    "auto_reply", "keyword_reply", "advert", "advertise", "mass_send",
    "sweeper", "redpacket", "anti_withdraw", "withdraw",
    "group_sort", "auto_add_group", "like_helper", "both_auto", "养号",
    "lottery", "bonus", "福利", "秒杀", "群管自动",
    # —— 中文插件名关键词 ——
    "群发", "广告", "红包", "抢包", "防撤回", "撤回",
    "刷屏", "轰炸", "自动回复", "关键词回复", "批量加群", "自动加群",
    "抽奖", "转盘", "签到自动", "秒杀", "群发助手", "点赞", "刷赞",
]

_BLOCKED_TPL = (
    "⛔ 安全拦截：QQ 平台前置插件审核已阻止「{plugin}」的 /{command_line} 命令。\n"
    "该插件/命令存在较高触发 QQ 封号的风险（命中规则：{rule}），已禁止在 QQ 平台执行，"
    "且未调用任何大模型。\n"
    "请在网页端/功能面板中禁用该插件，或删改对应命令后再试。若确需保留，"
    "可在 QQ 平台配置中关闭『插件审核』或将其从风险列表移除。"
)


def _config() -> Dict:
    """读取 QQ 平台插件审核配置（含懒加载，避免启动期循环导入）"""
    try:
        from zhiyan_web.config.settings import get_settings
        qq = (get_settings() or {}).get("qq_platform") or {}
        audit = qq.get("plugin_audit") or {}
        if not isinstance(audit, dict):
            audit = {}
        return audit
    except Exception:  # noqa: BLE001 - 配置异常时按未启用处理，不阻断插件
        return {}


def _risk_commands() -> List[str]:
    extra = _config().get("risk_commands") or []
    return list(QQ_RISK_COMMANDS_DEFAULT) + [c for c in extra if isinstance(c, str)]


def _risk_plugins() -> List[str]:
    extra = _config().get("risk_plugins") or []
    return list(QQ_RISK_PLUGINS_DEFAULT) + [p for p in extra if isinstance(p, str)]


def assess(platform_name: str,
           plugin_name: str = "",
           command_names: Optional[List[str]] = None) -> Optional[str]:
    """对将要在某平台执行的插件做前置审核。

    Args:
        platform_name: 当前平台（仅 "qq" 触发审核）
        plugin_name: 插件（Star）注册名
        command_names: 该命令过滤器的全部命令名/别名

    Returns:
        命中风险时返回报错文案（阻塞理由），否则返回 None（放行）。
    """
    if str(platform_name).lower() != "qq":
        return None

    cfg = _config()
    if not cfg.get("enabled", True):
        return None

    plugin_name = (plugin_name or "").strip()
    names = [n for n in (command_names or []) if isinstance(n, str) and n]
    # 取首个命令名用于提示（每个 handler 可能注册多个命令/别名）
    display = names[0] if names else plugin_name or "未知命令"

    # 1) 命中风险插件名
    for kw in _risk_plugins():
        if kw and kw in plugin_name:
            return _block(plugin_name, display, "高风险插件「{}」".format(kw))

    # 2) 命中风险命令关键词
    if names:
        for kw in _risk_commands():
            if not kw:
                continue
            for n in names:
                if kw in n.lower():
                    return _block(plugin_name, display, "高风险命令关键词「{}」".format(kw))

    return None


def _block(plugin_name: str, command_line: str, rule: str) -> str:
    return _BLOCKED_TPL.format(
        plugin=plugin_name or "未知名插件",
        command_line=command_line,
        rule=rule,
    )