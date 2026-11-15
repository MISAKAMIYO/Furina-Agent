"""文件级检查点

在 Agent 执行写操作之前，把目标文件/目录快照到 data/checkpoints/<conv_id>/<cp_id>/，
支持按检查点回滚。设计原则：
- 只快照「会被这次工具调用改动」的路径，不整盘复制；
- 单文件上限 5MB，超限记 skipped 但仍保留元数据（回滚时明确提示未覆盖）；
- 目录只记录是否存在，内容不复制（避免误伤大目录），回滚时只做「不存在则创建」；
- 回滚本身也是破坏性操作，交给前端二次确认。
"""
from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from typing import Any, Dict, List, Optional

from zhiyan_web.config.settings import DATA_DIR

CK_ROOT = os.path.join(DATA_DIR, "checkpoints")
MAX_FILE_BYTES = 5 * 1024 * 1024

WRITE_TOOLS = {
    "write_file", "edit_file", "append_file", "create_directory",
    "delete_file", "copy_file", "move_file", "rename_file", "unzip_file",
}
_PATH_KEYS = ("path", "file_path", "new_path", "destination", "dest", "target", "source")


def _conv_dir(conv_id: str) -> str:
    safe = "".join(c for c in str(conv_id or "default") if c.isalnum() or c in "_-")[:64]
    return os.path.join(CK_ROOT, safe or "default")


def _targets(tool: str, args: Dict[str, Any]) -> List[str]:
    """从工具参数里挑出这次调用会改动的路径"""
    args = args or {}
    paths: List[str] = []
    if tool in ("copy_file", "move_file", "rename_file"):
        for key in ("destination", "dest", "new_path", "source", "old_path", "path"):
            val = args.get(key)
            if isinstance(val, str) and val.strip():
                paths.append(val.strip())
    else:
        for key in _PATH_KEYS:
            val = args.get(key)
            if isinstance(val, str) and val.strip():
                paths.append(val.strip())
    # 去重，排除明显不是路径的值
    out: List[str] = []
    for p in paths:
        if p and p not in out and (os.sep in p or "/" in p or "." in p):
            out.append(p)
    return out


def snapshot_before(conv_id: str, tool: str, args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """写操作执行前快照，返回检查点元数据；不需要快照时返回 None"""
    tool = (tool or "").lower()
    if tool not in WRITE_TOOLS:
        return None
    paths = _targets(tool, args)
    if not paths:
        return None

    cp_id = time.strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:6]
    cp_dir = os.path.join(_conv_dir(conv_id), cp_id)
    files_dir = os.path.join(cp_dir, "files")
    os.makedirs(files_dir, exist_ok=True)

    entries: List[Dict[str, Any]] = []
    for idx, raw in enumerate(paths[:10]):
        abs_path = os.path.abspath(raw)
        entry: Dict[str, Any] = {
            "path": abs_path,
            "existed": os.path.exists(abs_path),
            "is_dir": os.path.isdir(abs_path),
            "blob": "",
            "size": 0,
            "skipped": False,
        }
        if entry["existed"] and not entry["is_dir"]:
            try:
                size = os.path.getsize(abs_path)
                entry["size"] = size
                if size <= MAX_FILE_BYTES:
                    blob = f"{idx}.bin"
                    shutil.copy2(abs_path, os.path.join(files_dir, blob))
                    entry["blob"] = blob
                else:
                    entry["skipped"] = True
            except OSError:
                entry["skipped"] = True
        entries.append(entry)

    meta = {
        "id": cp_id,
        "conv_id": str(conv_id or ""),
        "tool": tool,
        "args": args,
        "time": time.time(),
        "entries": entries,
    }
    with open(os.path.join(cp_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


def list_checkpoints(conv_id: str, limit: int = 30) -> List[Dict[str, Any]]:
    base = _conv_dir(conv_id)
    if not os.path.isdir(base):
        return []
    items: List[Dict[str, Any]] = []
    for name in sorted(os.listdir(base), reverse=True):
        meta_path = os.path.join(base, name, "meta.json")
        if not os.path.isfile(meta_path):
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        items.append({
            "id": meta.get("id", name),
            "tool": meta.get("tool", ""),
            "time": meta.get("time", 0),
            "paths": [e.get("path", "") for e in meta.get("entries", [])],
            "count": len(meta.get("entries", [])),
        })
        if len(items) >= max(1, min(int(limit or 30), 100)):
            break
    return items


def _find_meta(cp_id: str) -> Optional[Dict[str, Any]]:
    if not os.path.isdir(CK_ROOT):
        return None
    for conv in os.listdir(CK_ROOT):
        meta_path = os.path.join(CK_ROOT, conv, cp_id, "meta.json")
        if os.path.isfile(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return None
    return None


def restore(cp_id: str) -> Dict[str, Any]:
    """按检查点回滚：存在的文件写回原内容，不存在过的文件/目录删除"""
    meta = _find_meta(cp_id)
    if not meta:
        return {"ok": False, "error": "检查点不存在"}
    cp_dir = os.path.join(_conv_dir(meta.get("conv_id", "")), cp_id)
    restored, removed, skipped, failed = [], [], [], []

    for entry in meta.get("entries", []):
        path = entry.get("path") or ""
        if not path:
            continue
        try:
            if entry.get("existed") and entry.get("blob"):
                blob = os.path.join(cp_dir, "files", entry["blob"])
                if os.path.isfile(blob):
                    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                    shutil.copy2(blob, path)
                    restored.append(path)
            elif entry.get("existed") and entry.get("is_dir"):
                os.makedirs(path, exist_ok=True)
                restored.append(path)
            elif entry.get("existed") and entry.get("skipped"):
                skipped.append(path)
            else:
                # 原本不存在 → 回滚时删掉（移入回收站，可再还原）
                if os.path.isdir(path):
                    from zhiyan_web.dsh.trash import recycle
                    recycle(path)
                    removed.append(path)
                elif os.path.exists(path):
                    from zhiyan_web.dsh.trash import recycle
                    recycle(path)
                    removed.append(path)
        except Exception as e:
            failed.append({"path": path, "error": str(e)})

    return {
        "ok": not failed,
        "restored": restored,
        "removed": removed,
        "skipped": skipped,
        "failed": failed,
    }


def discard(cp_id: str) -> bool:
    meta = _find_meta(cp_id)
    if not meta:
        return False
    cp_dir = os.path.join(_conv_dir(meta.get("conv_id", "")), cp_id)
    try:
        shutil.rmtree(cp_dir, ignore_errors=True)
        return True
    except Exception:
        return False
