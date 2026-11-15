"""
子代理类型注册表

对齐 DSH 内核的 tool-subagent 思路：主 agent 可以把一段自包含的子任务派给
一个「有独立上下文、独立工具权限、独立轮次预算」的子代理去跑，只把结论收回来。

每个子代理类型声明：
- 什么时候该派给它（description，会进主 agent 的工具描述，决定模型的选择）
- 它自己的人格与工作方式（system_prompt）
- 它能用哪些工具（scenario / tool_groups / allow_tools / deny_tools）
- 它最多跑几轮（max_iterations）
- 它能用哪些技能（skills 白名单，空表示全部）

类型可以内置，也可以通过 extension.json 声明式注册（见 extensions/registry.py）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ZhiYanWeb.SubAgent.Registry")


@dataclass
class SubAgentType:
    """子代理类型定义。"""
    name: str
    label: str = ""
    description: str = ""
    system_prompt: str = ""
    avatar: str = "🧩"
    # 工具裁剪：scenario 优先，tool_groups 追加白名单，allow/deny 做精调
    scenario: str = "chat"
    tool_groups: List[str] = field(default_factory=list)
    allow_tools: List[str] = field(default_factory=list)
    deny_tools: List[str] = field(default_factory=list)
    deny_groups: List[str] = field(default_factory=list)
    # 这个类型自己允许的嵌套深度：0 = 自身就是叶子（不能再派子代理），
    # 2 = 自己派出的子代理还能再派一层。最终生效值取
    # min(此字段, 回合预算的 subagent_max_depth)。
    max_depth: int = 2
    # 技能白名单，空 = 不限制（仍受技能自身启用状态约束）
    skills: List[str] = field(default_factory=list)
    max_iterations: int = 24
    temperature: float = 0.4
    builtin: bool = True

    def display(self) -> str:
        return self.label or self.name

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.display(),
            "avatar": self.avatar,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "scenario": self.scenario,
            "tool_groups": list(self.tool_groups),
            "allow_tools": list(self.allow_tools),
            "deny_tools": list(self.deny_tools),
            "deny_groups": list(self.deny_groups),
            "max_depth": self.max_depth,
            "skills": list(self.skills),
            "max_iterations": self.max_iterations,
            "temperature": self.temperature,
            "builtin": self.builtin,
            # 有同名自定义类型时，内置被覆盖：界面上要标出来
            "overridden": (not self.builtin) and (self.name in BUILTIN_TYPES),
        }


_COMMON_RULES = (
    "\n\n**执行准则:**\n"
    "1. 你只负责下面交代的这一个子任务，不要引申到其它任务。\n"
    "2. 先想清楚要几步，再动手；能用一次工具解决的不要拆成很多次。\n"
    "3. 拿不到的信息不要编造，直接说明缺什么。\n"
    "4. 完成后用简洁的结构化文字汇报结果（结论在前，证据在后）。\n"
    "5. 不要把中间过程的噪音写进结论，你的输出会作为工具结果回到主代理。\n"
)


BUILTIN_TYPES: Dict[str, SubAgentType] = {}


def _add(t: SubAgentType) -> None:
    BUILTIN_TYPES[t.name] = t


_add(SubAgentType(
    name="general",
    label="通用执行者",
    avatar="🧩",
    description="适合同一目标下需要多步工具操作、且过程无需占用主对话上下文的子任务。"
                "例如：批量读写/整理文件、跑一段脚本后汇报、把一批资料汇总成结论。",
    system_prompt=(
        "你是一名通用执行者（Sub Agent），被主代理派来独立完成一个自包含的子任务。"
        "你有自己的工具权限，可以在自己的上下文里反复调用工具，直到任务完成。"
        + _COMMON_RULES
    ),
    scenario="coding",
    max_iterations=24,
))

_add(SubAgentType(
    name="researcher",
    label="调研员",
    avatar="🔍",
    description="适合需要先查资料再给结论的子任务：检索网页、抓取页面、比对多个来源、"
                "整理成带出处的要点。不适合需要修改本机文件的任务。",
    system_prompt=(
        "你是一名调研员（Sub Agent）。你的任务是把一个问题查清楚并给出可信结论。"
        "优先使用 web_search / web_fetch 获取一手资料，交叉验证后再下结论；"
        "结论里要标注信息来源。" + _COMMON_RULES
    ),
    scenario="research",
    max_iterations=20,
))

_add(SubAgentType(
    name="coder",
    label="编码助手",
    avatar="⌨️",
    description="适合独立的编码类子任务：读代码、定位问题、改文件、跑命令验证。"
                "涉及跨多个文件的改动时优先派给它可以隔离上下文。",
    system_prompt=(
        "你是一名编码助手（Sub Agent）。先读代码理解现状，再动手改；"
        "改完要用命令或读回文件的方式自查一遍，确认没有引入错误。"
        "严格遵守工作区写入限制。" + _COMMON_RULES
    ),
    scenario="coding",
    max_iterations=32,
))

_add(SubAgentType(
    name="writer",
    label="写作/整理",
    avatar="✍️",
    description="适合纯文本产出类子任务：整理纪要、写文档、把零散材料改写成成稿。"
                "不需要动本机文件时用它。",
    system_prompt=(
        "你是一名写作与整理助手（Sub Agent）。任务是把接到手的材料变成成稿。"
        "先确认体裁与篇幅，再写；不编造材料里没有的事实。" + _COMMON_RULES
    ),
    scenario="lightweight",
    max_iterations=8,
    temperature=0.7,
))

_add(SubAgentType(
    name="reviewer",
    label="审查员",
    avatar="🧐",
    description="适合对已有产出做独立复检：找逻辑漏洞、事实错误、遗漏项。"
                "它只读不改，因此可以安全地对重要产出做二次校验。",
    system_prompt=(
        "你是一名审查员（Sub Agent）。你的职责是挑毛病，不是附和。"
        "逐项核对给到的材料，指出具体问题（错在哪、为什么错、怎么改），"
        "没发现问题也要说明你检查了哪些方面。" + _COMMON_RULES
    ),
    scenario="lightweight",
    deny_tools=["write_file", "edit_file", "delete_file", "delete_directory",
                "command_prompt_execute", "execute_command"],
    allow_tools=["read_file", "list_files", "search_files", "get_file_info"],
    max_iterations=12,
    temperature=0.2,
))

_add(SubAgentType(
    name="operator",
    label="本机操作员",
    avatar="🖱️",
    description="适合需要操作本机界面的子任务：打开软件、点击、截图、录屏、切窗口。"
                "动作类任务交给它可以避免主对话被大量截图结果刷屏。",
    system_prompt=(
        "你是一名本机操作员（Sub Agent）。你通过鼠标键盘和截屏工具操作这台电脑。"
        "每次操作后先确认界面状态再决定下一步；找不到目标元素时换一种定位方式，"
        "不要重复无效点击。" + _COMMON_RULES
    ),
    scenario="computer",
    max_iterations=30,
))


_dynamic: Dict[str, SubAgentType] = {}

# 各内置类型的嵌套许可：0 = 叶子（自己不再往下派子代理），越大允许越深。
# 目的性很强：只让「做事的」类型能再拆任务，纯产出的类型（写作/审查/操作）
# 拆下去只会增加损耗。
_BUILTIN_MAX_DEPTH = {
    "general": 2,
    "researcher": 1,
    "coder": 2,
    "writer": 0,
    "reviewer": 0,
    "operator": 0,
}
for _n, _d in _BUILTIN_MAX_DEPTH.items():
    if _n in BUILTIN_TYPES:
        BUILTIN_TYPES[_n].max_depth = _d


def unregister_subagent_type(name: str) -> bool:
    """注销一个自定义子代理类型（内置类型不受影响，会重新暴露出来）。"""
    key = (name or "").strip()
    if key in _dynamic:
        del _dynamic[key]
        logger.info(f"已注销子代理类型: {key}")
        return True
    return False


def register_subagent_type(spec: Any, replace: bool = False) -> bool:
    """声明式注册一个子代理类型（供扩展清单调用）。

    spec 可以是 SubAgentType 实例，也可以是 dict（字段同名，蛇形命名）。
    """
    try:
        if isinstance(spec, SubAgentType):
            obj = spec
        elif isinstance(spec, dict):
            known = {f for f in SubAgentType.__dataclass_fields__}
            kwargs = {k: v for k, v in spec.items() if k in known}
            name = str(kwargs.get("name") or "").strip()
            if not name:
                logger.warning("子代理类型缺少 name，已跳过")
                return False
            kwargs["name"] = name
            kwargs["builtin"] = False
            obj = SubAgentType(**kwargs)
        else:
            return False
    except Exception as e:
        logger.warning(f"子代理类型定义非法: {e}")
        return False

    if obj.name in _dynamic and not replace:
        return False
    _dynamic[obj.name] = obj
    logger.info(f"已注册子代理类型: {obj.name}（{obj.display()}）")
    return True


def get_subagent_type(name: str) -> Optional[SubAgentType]:
    key = (name or "").strip()
    if not key:
        return None
    if key in _dynamic:
        return _dynamic[key]
    if key in BUILTIN_TYPES:
        return BUILTIN_TYPES[key]
    lowered = key.lower()
    for table in (_dynamic, BUILTIN_TYPES):
        for candidate in table.values():
            if candidate.name.lower() == lowered or candidate.display() == key:
                return candidate
    return None


def list_subagent_types() -> List[SubAgentType]:
    """内置 + 外挂类型（外挂同名会覆盖内置，便于插件改写行为）。"""
    merged: Dict[str, SubAgentType] = dict(BUILTIN_TYPES)
    merged.update(_dynamic)
    return list(merged.values())


def describe_types() -> List[Dict[str, Any]]:
    return [t.to_dict() for t in list_subagent_types()]


def default_type() -> SubAgentType:
    return BUILTIN_TYPES["general"]
