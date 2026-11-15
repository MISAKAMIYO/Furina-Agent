"""
声明式扩展贡献注册表

对齐 DSH 内核的做法：内核里每个能力都是一行声明式配置（cordis 行 + patch
覆盖层决定启用/停用），扩展不需要改内核代码。本模块把同样的思路搬到软件自身
的扩展体系上 —— 插件/技能目录里放一个 `extension.json`，声明自己额外贡献了
什么，由这里统一加载：

    {
      "name": "my-extension",
      "version": "1.0.0",
      "contributes": {
        "toolGroups": [
          {"name": "my_group", "patterns": ["my_*"], "label": "我的工具",
           "description": "..."}
        ],
        "subagents": [
          {"name": "my_worker", "label": "我的执行者", "description": "何时派给它",
           "system_prompt": "...", "scenario": "chat",
           "tool_groups": ["core"], "max_iterations": 16}
        ],
        "hooks": [
          {"class": "AuditHook", "module": "my_pkg.hooks"},
          {"class": "LocalHook", "path": "D:/ext/hooks.py"}
        ]
      }
    }

扫描位置：data/plugins/*/extension.json、data/skills/*/extension.json、
backend/zhiyan_web/plugins/builtin_plugins/*/extension.json。

重复加载是幂等的（按绝对路径记账）。任一扩展加载失败只记日志，不影响其它扩展
（对应各扩展执行管道互不影响的原则）。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger("ZhiYanWeb.extensions.registry")

MANIFEST_NAME = "extension.json"

_loaded_paths: List[str] = []
_contributions: List[Dict[str, Any]] = []


def _candidate_dirs() -> List[str]:
    """返回需要扫描的扩展目录列表。"""
    dirs: List[str] = []
    try:
        from zhiyan_web.config.settings import DATA_DIR, BUILTIN_PLUGINS_DIR
        for sub in ("plugins", "skills"):
            dirs.append(os.path.join(DATA_DIR, sub))
        dirs.append(BUILTIN_PLUGINS_DIR)
    except Exception as e:
        logger.debug(f"解析扩展目录失败: {e}")
    return dirs


def _read_manifest(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"读取扩展清单失败 {path}: {e}")
        return None
    if not isinstance(data, dict):
        return None
    contributes = data.get("contributes")
    if not isinstance(contributes, dict):
        return None
    return data


def _apply_contributions(manifest: Dict[str, Any], source: str) -> Dict[str, Any]:
    """把一份清单的贡献落到各子系统，返回落地统计。"""
    contributes = manifest.get("contributes") or {}
    applied = {"tool_groups": 0, "subagents": 0, "hooks": 0, "errors": []}

    from zhiyan_web.agent import tool_groups
    from zhiyan_web.agent.hook_bus import get_hook_bus, register_hook_from_spec
    from zhiyan_web.subagent.registry import register_subagent_type

    for spec in (contributes.get("toolGroups") or []):
        try:
            if not isinstance(spec, dict):
                continue
            name = str(spec.get("name") or "").strip()
            patterns = spec.get("patterns") or []
            if not name or not isinstance(patterns, list):
                continue
            tool_groups.register_tool_group(
                name, [str(p) for p in patterns],
                label=str(spec.get("label") or name),
                description=str(spec.get("description") or ""),
            )
            applied["tool_groups"] += 1
        except Exception as e:
            applied["errors"].append(f"toolGroup 注册失败: {e}")

    for spec in (contributes.get("subagents") or []):
        try:
            if register_subagent_type(spec):
                applied["subagents"] += 1
        except Exception as e:
            applied["errors"].append(f"subagent 注册失败: {e}")

    for spec in (contributes.get("hooks") or []):
        try:
            if register_hook_from_spec(spec):
                applied["hooks"] += 1
        except Exception as e:
            applied["errors"].append(f"hook 注册失败: {e}")

    # 技能目录里的扩展还可以声明 mode / note 之类的元信息，这里只记录
    logger.info(
        f"扩展清单已加载 {os.path.basename(os.path.dirname(source)) or source}: "
        f"工具分组 {applied['tool_groups']}，子代理 {applied['subagents']}，"
        f"钩子 {applied['hooks']}"
    )
    return applied


def load_manifests(dirs: Optional[Sequence[str]] = None,
                   force: bool = False) -> Dict[str, Any]:
    """扫描并加载扩展清单（幂等）。

    Returns:
        {"loaded": [...], "skipped": n, "tool_groups": n, "subagents": n, "hooks": n, "errors": [...]}
    """
    if force:
        _loaded_paths.clear()
        _contributions.clear()

    summary: Dict[str, Any] = {"loaded": [], "skipped": 0, "tool_groups": 0,
                               "subagents": 0, "hooks": 0, "errors": []}

    for root in (dirs if dirs is not None else _candidate_dirs()):
        if not root or not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for name in entries:
            sub = os.path.join(root, name)
            if not os.path.isdir(sub):
                continue
            manifest_path = os.path.abspath(os.path.join(sub, MANIFEST_NAME))
            if not os.path.isfile(manifest_path):
                continue
            if manifest_path in _loaded_paths:
                summary["skipped"] += 1
                continue
            manifest = _read_manifest(manifest_path)
            if manifest is None:
                continue
            try:
                applied = _apply_contributions(manifest, manifest_path)
            except Exception as e:
                summary["errors"].append(f"{manifest_path}: {e}")
                continue
            _loaded_paths.append(manifest_path)
            record = {
                "source": manifest_path,
                "name": manifest.get("name") or name,
                "version": manifest.get("version", ""),
                **applied,
            }
            _contributions.append(record)
            summary["loaded"].append(record)
            summary["tool_groups"] += applied["tool_groups"]
            summary["subagents"] += applied["subagents"]
            summary["hooks"] += applied["hooks"]
            summary["errors"].extend(applied["errors"])

    return summary


def contributions() -> List[Dict[str, Any]]:
    """已加载的扩展贡献清单（供扩展中心展示）。"""
    return [dict(c) for c in _contributions]


def summary() -> Dict[str, Any]:
    """扩展贡献的汇总统计。"""
    try:
        from zhiyan_web.agent.hook_bus import get_hook_bus
        hooks = get_hook_bus().count()
    except Exception:
        hooks = 0
    try:
        from zhiyan_web.subagent.registry import list_subagent_types
        subagents = len(list_subagent_types())
    except Exception:
        subagents = 0
    try:
        from zhiyan_web.agent import tool_groups
        groups = len(tool_groups.list_groups())
    except Exception:
        groups = 0

    return {
        "manifests": len(_contributions),
        "tool_groups": groups,
        "subagent_types": subagents,
        "hooks": hooks,
        "items": contributions(),
    }
