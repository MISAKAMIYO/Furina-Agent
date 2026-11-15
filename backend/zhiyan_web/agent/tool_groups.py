"""
工具分组与场景裁剪

FunctionToolManager 里现在扁平堆着上百个工具（电脑操控 66 个 + 浏览器 30 多个 +
MCP + 技能 + QQ + 记忆…），全量塞给模型有两个实际代价：
1. 工具描述本身就占 prompt token，且每轮都要重复发；
2. 候选工具太多会显著干扰模型的工具选择准确率。

本模块把这些工具按能力分组，并允许：
- 按场景（scenario）裁剪：chat / coding / research / lightweight …
- 子代理声明自己需要的分组，做到最小权限（对齐 DSH 内核按 profile + patch
  决定暴露哪些工具行的做法）；
- 插件通过 register_tool_group() 声明式追加分组，不需要改本文件。

分组归属优先级：工具自身声明的 group 字段 > 正则模式匹配 > 落入 other。
未分组工具（other）只在 full 场景出现，其余场景会被裁掉并记一条 debug 日志。
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

logger = logging.getLogger("ZhiYanWeb.Agent.ToolGroups")

# 工具分组：分组名 -> (展示名, 匹配模式列表)
# 模式可以是精确工具名、"前缀*" 或正则字符串。
_GROUPS: Dict[str, Dict[str, Any]] = {}
_GROUP_ORDER: List[str] = []


def register_tool_group(name: str, patterns: Sequence[str],
                        label: str = "", description: str = "",
                        replace: bool = False) -> None:
    """声明式注册一个工具分组（插件可在自己的初始化里调用）。

    Args:
        name: 分组标识（英文小写短名，如 "computer"）
        patterns: 匹配模式，支持精确名、"prefix*" 通配、正则（如 "^mcp__"）
        label: 展示名（默认用 name）
        description: 分组说明
        replace: 已存在时是否覆盖（默认保留先注册的，避免插件互踩）
    """
    if name in _GROUPS and not replace:
        return
    if name not in _GROUPS:
        _GROUP_ORDER.append(name)
    _GROUPS[name] = {
        "label": label or name,
        "description": description,
        "patterns": list(patterns),
    }


def _match(pattern: str, tool_name: str) -> bool:
    if pattern.endswith("*"):
        return tool_name.startswith(pattern[:-1])
    if pattern.startswith("^") or ".*" in pattern or pattern.endswith("$"):
        try:
            return re.search(pattern, tool_name) is not None
        except re.error:
            return False
    return tool_name == pattern


# ── 内置分组 ────────────────────────────────────────────────────────────────
# 顺序即匹配优先级：先声明的分组先认领工具。
register_tool_group(
    "subagent", ["spawn_subagent", "delegate_to_agent", "list_subagents",
                 "spawn_background_subagent"],
    label="子代理",
    description="派发子任务给独立上下文的子代理（含后台运行），或交给已配置的 Agent",
)
register_tool_group(
    "board", ["board_read", "board_write"],
    label="共享白板",
    description="同一批并行子代理之间交换中间发现的便签墙（无外部副作用）",
)
register_tool_group(
    "skills", ["execute_skill", "get_skill_list", "load_skill"],
    label="技能",
    description="技能清单与按需加载（渐进式披露）",
)
register_tool_group(
    "core", [
        "read_file", "write_file", "write_file_batch", "append_file", "save_file",
        "edit_file", "list_files", "search_files", "get_file_info",
        "copy_file", "move_file", "rename_file", "create_directory",
        "delete_file", "delete_directory",
    ],
    label="文件",
    description="工作区内的读写、检索、增删改",
)
register_tool_group(
    "command", ["command_prompt_execute", "execute_command"],
    label="命令",
    description="执行系统命令（走审批与安全校验）",
)
register_tool_group(
    "browser", ["browser_*"],
    label="浏览器",
    description="浏览器自动化（导航、点击、填表、截图）",
)
register_tool_group(
    "computer", [
        "take_screenshot", "capture_screen", "start_screen_recording",
        "stop_screen_recording", "get_screen_recording_status",
        "show_image", "get_mouse_position", "move_mouse", "click_mouse",
        "double_click", "drag_mouse", "scroll_mouse", "type_text", "press_key",
        "get_screen_size", "get_clipboard", "set_clipboard",
        "list_windows", "activate_window", "minimize_window", "close_window",
        "open_application", "open_url", "get_system_info", "list_processes",
    ],
    label="电脑操控",
    description="鼠标键盘、截屏录屏、窗口与进程",
)
register_tool_group(
    "web", ["web_fetch", "web_search"],
    label="联网",
    description="网页抓取与搜索",
)
register_tool_group(
    "memory", [
        "write_memory", "review_memory", "summarize_memories",
        "search_knowledge", "add_knowledge",
    ],
    label="记忆与知识库",
    description="长期记忆与知识库读写",
)
register_tool_group(
    "qq", ["qq_*"],
    label="QQ 平台",
    description="QQ 好友/群消息收发",
)
register_tool_group(
    "mcp", ["^mcp__"],
    label="MCP",
    description="外部 MCP 服务提供的工具",
)
register_tool_group(
    "utils", [
        "calculate", "format_json", "extract_json", "count_tokens",
        "base64_encode", "base64_decode", "generate_uuid",
        "get_current_time", "hash_text", "list_available_tools",
    ],
    label="通用工具",
    description="计算、编解码、时间、JSON 等",
)


# ── 场景：一组分组的命名组合 ────────────────────────────────────────────────
SCENARIOS: Dict[str, Dict[str, Any]] = {
    "full": {
        "label": "全部工具",
        "groups": None,  # None = 不裁剪，全部放行（含未分组工具）
        "note": "与历史行为一致：注册了什么就都给模型。",
    },
    "chat": {
        "label": "日常对话",
        "groups": ["core", "utils", "memory", "web", "skills", "subagent"],
        "note": "去掉电脑操控 / 浏览器 / 命令 / QQ，避免闲聊时误触发重操作。",
    },
    "coding": {
        "label": "编码任务",
        "groups": ["core", "command", "utils", "skills", "subagent", "web"],
        "note": "保留文件与命令，不含鼠标键盘与浏览器。",
    },
    "research": {
        "label": "调研检索",
        "groups": ["web", "core", "utils", "memory", "skills", "subagent"],
        "note": "以联网检索与资料整理为主。",
    },
    "computer": {
        "label": "电脑操控",
        "groups": ["computer", "browser", "core", "command", "utils",
                   "skills", "subagent"],
        "note": "需要操作本机界面时使用。",
    },
    "lightweight": {
        "label": "最精简",
        "groups": ["core", "utils", "skills"],
        "note": "仅文件与通用工具，prompt 最省。",
    },
}
DEFAULT_SCENARIO = "full"

# 白板工具在所有场景都放行：它只是并行子代理之间的便签墙，不碰外部世界，
# 被裁掉只会让并行的子代理重复劳动。
for _meta in SCENARIOS.values():
    _groups = _meta.get("groups")
    if isinstance(_groups, list) and "board" not in _groups:
        _groups.append("board")


def list_scenarios() -> List[Dict[str, Any]]:
    """返回场景清单（供设置界面渲染）。"""
    return [
        {"name": name, "label": meta.get("label", name),
         "note": meta.get("note", ""),
         "groups": meta.get("groups")}
        for name, meta in SCENARIOS.items()
    ]


def list_groups() -> List[Dict[str, Any]]:
    """返回分组清单。"""
    return [
        {"name": name, "label": _GROUPS[name]["label"],
         "description": _GROUPS[name]["description"],
         "patterns": _GROUPS[name]["patterns"]}
        for name in _GROUP_ORDER
    ]


def group_of(tool_name: str, declared: str = "") -> str:
    """判断工具属于哪个分组。

    优先取工具自己声明的分组（declared），其次按注册顺序做模式匹配。
    """
    if declared and declared in _GROUPS:
        return declared
    for name in _GROUP_ORDER:
        for pattern in _GROUPS[name]["patterns"]:
            if _match(pattern, tool_name):
                return name
    return "other"


def expand_groups(names: Iterable[str]) -> Set[str]:
    """把分组名展开为匹配模式集合（返回模式而不是工具名，因为技能这类
    工具名是动态的，只能在拿到实际工具列表后才能匹配）。"""
    patterns: Set[str] = set()
    for name in names:
        meta = _GROUPS.get(name)
        if meta:
            patterns.update(meta["patterns"])
    return patterns


def _tool_declared_group(tool: Any) -> str:
    return str(getattr(tool, "group", "") or "")


def filter_tools(
    tools: Sequence[Any],
    scenario: Optional[str] = None,
    allow_groups: Optional[Sequence[str]] = None,
    deny_groups: Optional[Sequence[str]] = None,
    allow_tools: Optional[Sequence[str]] = None,
    deny_tools: Optional[Sequence[str]] = None,
) -> List[Any]:
    """按场景 / 分组白名单裁剪工具列表。

    优先级：deny_tools > allow_tools > 分组规则。显式 allow_tools 里的工具
    即使不在场景分组内也会保留（用于子代理临时点几个额外工具）。
    场景为 None 或 "full" 且没有分组限制时，原样返回。
    """
    allow_groups = list(allow_groups or [])
    deny_groups = list(deny_groups or [])
    allow_tools_set = set(allow_tools or [])
    deny_tools_set = set(deny_tools or [])

    scenario = (scenario or DEFAULT_SCENARIO).strip() or DEFAULT_SCENARIO
    meta = SCENARIOS.get(scenario)
    if meta is None:
        logger.debug(f"未知工具场景 {scenario!r}，按全部工具处理")
        scenario = DEFAULT_SCENARIO
        meta = SCENARIOS[scenario]

    scenario_groups = meta.get("groups")
    is_wide_open = scenario_groups is None and not allow_groups
    if is_wide_open and not deny_groups and not allow_tools_set and not deny_tools_set:
        return list(tools)

    allowed_patterns = expand_groups(scenario_groups or []) | expand_groups(allow_groups)
    denied_patterns = expand_groups(deny_groups)

    kept: List[Any] = []
    dropped: List[str] = []
    for tool in tools:
        tname = str(getattr(tool, "name", ""))
        if tname in deny_tools_set:
            dropped.append(tname)
            continue
        if tname in allow_tools_set:
            kept.append(tool)
            continue
        if any(_match(p, tname) for p in denied_patterns):
            dropped.append(tname)
            continue
        if allowed_patterns and not any(_match(p, tname) for p in allowed_patterns):
            dropped.append(tname)
            continue
        kept.append(tool)

    if dropped:
        logger.debug(
            f"工具场景 {scenario}: 保留 {len(kept)} 个，裁剪 {len(dropped)} 个 "
            f"({', '.join(sorted(dropped)[:12])}{' …' if len(dropped) > 12 else ''})"
        )
    return kept


def get_default_scenario() -> str:
    """读取设置里的默认工具场景（读取失败时回退到 full）。"""
    try:
        from zhiyan_web.config.settings import get_settings
        value = str(get_settings().get("agent_tool_scenario") or "").strip()
        return value if value in SCENARIOS else DEFAULT_SCENARIO
    except Exception:
        return DEFAULT_SCENARIO
