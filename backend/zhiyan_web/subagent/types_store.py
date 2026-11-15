"""
子代理类型的持久化（自定义 / 覆盖内置）

`subagent/registry.py` 只负责内存里的注册与查找；本模块把「用户自己定义的
子代理类型」落到 data/subagent_types.json，并在启动时回灌进注册表。

同名类型会覆盖内置定义（覆盖内置需要显式传 replace），这样用户想微调
「通用执行者」的工具权限时，不需要改代码，新建一个同名类型即可。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ZhiYanWeb.SubAgent.Types")

TYPES_FILE = "_subagent_types.json"

# 允许在界面上编辑的字段（其余字段由代码控制，不接受外部覆盖）
EDITABLE_FIELDS = (
    "name", "label", "avatar", "description", "system_prompt",
    "scenario", "tool_groups", "allow_tools", "deny_tools",
    "skills", "max_iterations", "temperature", "max_depth",
)


def _path() -> str:
    try:
        from zhiyan_web.config import DATA_DIR
        os.makedirs(DATA_DIR, exist_ok=True)
        return os.path.join(DATA_DIR, TYPES_FILE)
    except Exception:
        return os.path.join(os.getcwd(), TYPES_FILE)


def _read() -> List[Dict[str, Any]]:
    path = _path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict) and d.get("name")]
        if isinstance(data, dict):
            return [d for d in data.get("types", []) if isinstance(d, dict)]
    except Exception as e:
        logger.warning(f"读取自定义子代理类型失败: {e}")
    return []


def _write(rows: List[Dict[str, Any]]) -> None:
    try:
        path = _path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        logger.warning(f"保存自定义子代理类型失败: {e}")


def custom_types() -> List[Dict[str, Any]]:
    return _read()


def get_custom(name: str) -> Optional[Dict[str, Any]]:
    for row in _read():
        if row.get("name") == name:
            return row
    return None


def _sanitize(spec: Dict[str, Any]) -> Dict[str, Any]:
    """只保留可编辑字段，并做基本类型归一。"""
    out: Dict[str, Any] = {}
    for key in EDITABLE_FIELDS:
        if key not in spec:
            continue
        value = spec[key]
        if key in ("tool_groups", "allow_tools", "deny_tools", "skills"):
            if isinstance(value, str):
                value = [v.strip() for v in value.replace(",", "\n").split("\n") if v.strip()]
            out[key] = [str(v) for v in (value or []) if str(v).strip()]
        elif key in ("max_iterations", "max_depth"):
            try:
                out[key] = max(0, int(value))
            except (TypeError, ValueError):
                continue
        elif key == "temperature":
            try:
                out[key] = min(2.0, max(0.0, float(value)))
            except (TypeError, ValueError):
                continue
        else:
            out[key] = str(value or "")
    return out


def save_type(spec: Dict[str, Any], replace: bool = True) -> Dict[str, Any]:
    """新增或更新一个自定义子代理类型，并立即生效。"""
    clean = _sanitize(spec)
    name = str(clean.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "缺少 name"}
    if not clean.get("system_prompt"):
        return {"ok": False, "error": "system_prompt 不能为空（子代理需要明确的工作方式）"}

    rows = _read()
    existed = any(r.get("name") == name for r in rows)
    rows = [r for r in rows if r.get("name") != name]
    rows.append(clean)
    _write(rows)

    from .registry import register_subagent_type
    register_subagent_type(clean, replace=replace)
    logger.info(f"自定义子代理类型已保存: {name}（{'更新' if existed else '新建'}）")
    return {"ok": True, "name": name, "updated": existed, "type": clean}


def delete_type(name: str) -> Dict[str, Any]:
    """删除一个自定义子代理类型。内置类型不会被这里删掉。"""
    name = (name or "").strip()
    rows = _read()
    remain = [r for r in rows if r.get("name") != name]
    if len(remain) == len(rows):
        return {"ok": False, "error": f"没有名为 '{name}' 的自定义类型"}
    _write(remain)

    from .registry import unregister_subagent_type
    unregister_subagent_type(name)
    logger.info(f"自定义子代理类型已删除: {name}")
    return {"ok": True, "name": name}


def load_into_registry() -> int:
    """启动时把落盘的自定义类型回灌进注册表。"""
    count = 0
    from .registry import register_subagent_type
    for row in _read():
        try:
            if register_subagent_type(row, replace=True):
                count += 1
        except Exception as e:
            logger.warning(f"加载自定义子代理类型失败 {row.get('name')}: {e}")
    if count:
        logger.info(f"已加载 {count} 个自定义子代理类型")
    return count
