"""
技能渐进式披露（Progressive Disclosure）

参照 DSH 内核 / Agent Skills 规范的技能用法：

1. **清单进系统提示词**：只把技能的名称、描述、适用场景（when-to-use）注入
   system prompt，让模型知道「有哪些技能、什么时候该用」，而不是把每个技能的
   正文都塞进上下文；
2. **按需加载正文**：模型判断需要某个技能时，调用 `load_skill` 把它完整的
   SKILL.md 正文拉进当前轮次；函数型技能则返回其参数说明，由模型决定是否调用。

这样做的收益：技能数量增长时，prompt 体积几乎不变（原来每个技能都注册成一个
独立工具，工具描述本身就要占用量级相当的 token）。

本模块只负责「读取与格式化」，不负责注册工具（注册在 computer/register_tools.py）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger("ZhiYanWeb.Skills.Disclosure")

# 单次注入技能清单的条数上限，避免技能极多时把 prompt 撑爆
CATALOG_LIMIT = 80


def _manager():
    try:
        from zhiyan_web.skills import get_skill_manager
        return get_skill_manager()
    except Exception as e:
        logger.debug(f"获取技能管理器失败: {e}")
        return None


def list_skill_entries(only: Optional[Sequence[str]] = None,
                       include_inactive: bool = False) -> List[Dict[str, Any]]:
    """列出技能条目（名称/描述/适用场景/类型/参数）。"""
    mgr = _manager()
    if mgr is None:
        return []
    try:
        skills = list(mgr.get_all_skills())
    except Exception as e:
        logger.debug(f"枚举技能失败: {e}")
        return []

    allow = {str(x) for x in only} if only else None
    entries: List[Dict[str, Any]] = []
    for skill in skills:
        try:
            if not getattr(skill, "active", True) and not include_inactive:
                continue
            md = skill.metadata
            if allow is not None and md.name not in allow:
                continue
            entries.append({
                "name": md.name,
                "description": md.description or "",
                "when_to_use": getattr(md, "when_to_use", "") or md.description or "",
                "kind": getattr(md, "kind", "python"),
                "version": md.version,
                "tags": list(md.tags or []),
                "parameters": [
                    {"name": p.name, "type": p.type, "description": p.description,
                     "required": bool(p.required)}
                    for p in (md.parameters or [])
                ],
                "path": md.path or "",
            })
        except Exception:
            continue
    return entries


def format_skill_catalog(only: Optional[Sequence[str]] = None,
                         limit: int = CATALOG_LIMIT) -> str:
    """把技能清单格式化成可注入系统提示词的段落（无技能时返回空串）。"""
    entries = list_skill_entries(only=only)
    if not entries:
        return ""

    lines = [
        "\n\n**可用技能（技能是一套现成的做事方法/流程，需要时先加载再照做）:**",
        "使用方式：先用 load_skill 工具加载技能名字，拿到完整步骤后再执行；",
        "函数型技能（kind=python）也可以直接按名称作为工具调用。",
    ]
    for item in entries[:limit]:
        when = item.get("when_to_use") or item.get("description") or ""
        kind = item.get("kind", "python")
        tag = "指令型" if kind == "prompt" else "函数型"
        lines.append(f"- {item['name']}（{tag}）: {when}")
    if len(entries) > limit:
        lines.append(f"- … 另有 {len(entries) - limit} 个技能未列出，可用 get_skill_list 查询")
    return "\n".join(lines)


async def load_skill(name: str) -> Dict[str, Any]:
    """加载指定技能的完整内容。

    指令型（SKILL.md）返回正文；函数型返回参数说明与调用提示，
    由调用方决定是否真的执行该技能。
    """
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "缺少技能名 name 参数"}

    mgr = _manager()
    if mgr is None:
        return {"ok": False, "error": "技能系统不可用"}

    skill = None
    try:
        skill = mgr.get_skill(name)
    except Exception as e:
        logger.debug(f"查找技能失败 {name}: {e}")
    if skill is None:
        # 名字没对上时给一次模糊匹配机会（模型偶尔会写近似的名字）
        entries = list_skill_entries()
        lowered = name.lower()
        for item in entries:
            if item["name"].lower() == lowered:
                try:
                    skill = mgr.get_skill(item["name"])
                except Exception:
                    skill = None
                break
        if skill is None:
            return {
                "ok": False,
                "error": f"未找到技能 '{name}'",
                "available": [e["name"] for e in entries[:30]],
            }

    md = skill.metadata
    result: Dict[str, Any] = {
        "ok": True,
        "name": md.name,
        "kind": getattr(md, "kind", "python"),
        "description": md.description,
        "version": md.version,
    }

    if getattr(md, "kind", "") == "prompt":
        # 指令型技能：正文就是要照着做的方法/流程
        try:
            body = await skill.execute()
        except Exception as e:
            return {"ok": False, "name": md.name, "error": f"读取技能正文失败: {e}"}
        result["instructions"] = str(body or "")
        result["note"] = "以上是该技能的完整说明，请按其中的步骤执行本次任务。"
        return result

    result["parameters"] = [
        {"name": p.name, "type": p.type, "description": p.description,
         "required": bool(p.required)}
        for p in (md.parameters or [])
    ]
    result["note"] = (
        f"这是函数型技能，请直接调用名为 '{md.name}' 的工具来执行，参数见 parameters。"
    )
    return result


def catalog_summary() -> Dict[str, Any]:
    """供扩展中心展示的统计信息。"""
    entries = list_skill_entries(include_inactive=True)
    by_kind: Dict[str, int] = {}
    for item in entries:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
    return {"total": len(entries), "by_kind": by_kind}
