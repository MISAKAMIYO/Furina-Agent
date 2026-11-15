"""扩展工具集：文件深读、检索、诊断类能力

补齐原有 66 个工具里缺的几块「读代码 / 查问题」能力：
- read_file_lines  大文件按行段读（避免一次读爆上下文）
- grep_files       正则检索文件内容（原来只能搜纯文本且结果是文件名）
- file_hash        文件指纹（md5/sha1/sha256）
- diff_files       两文件差异（unified diff）
- list_tree        目录树（深度/条数受限）
- csv_preview      CSV 预览（表头 + 前 N 行）
- zip_list         压缩包清单
- append_file      追加写入
- unzip_file       解压到工作区
- delete_file      移入回收站（dsh.trash，可还原）

路径校验统一复用 file_ops._check_workspace：读操作全盘可读，
写操作在工作区内或经用户放行后才允许。
"""
from __future__ import annotations

import csv
import difflib
import hashlib
import io
import os
import re
import zipfile
from typing import Any, Dict, List

from zhiyan_web.computer.tools.file_ops import _check_workspace

# 单次读取/检索的硬上限，防止把上下文打爆
_MAX_PREVIEW_BYTES = 2 * 1024 * 1024      # grep / diff 单文件上限 2MB
_MAX_LINES_PER_READ = 2000                # read_file_lines 单次行数上限
_MAX_TREE_ENTRIES = 500


def _is_probably_binary(raw: bytes) -> bool:
    return b"\x00" in raw[:4096]


def read_file_lines(path: str, start: int = 1, count: int = 80) -> Dict[str, Any]:
    """按行段读取文件内容（1 基行号）"""
    abs_path = _check_workspace(path, "read_file")
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"文件不存在: {abs_path}")
    start = max(1, int(start or 1))
    count = max(1, min(int(count or 80), _MAX_LINES_PER_READ))
    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    total = len(lines)
    end = min(total, start - 1 + count)
    chunk = "".join(lines[start - 1:end])
    return {
        "path": abs_path,
        "start": start,
        "end": end,
        "total_lines": total,
        "has_more": end < total,
        "content": chunk,
    }


def grep_files(pattern: str, path: str = "", max_results: int = 40,
               case_sensitive: bool = False) -> Dict[str, Any]:
    """在目录里按正则检索文件内容，返回命中的文件/行/行号"""
    root = _check_workspace(path or os.getcwd(), "list_files")
    if not os.path.exists(root):
        raise FileNotFoundError(f"路径不存在: {root}")
    try:
        regex = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
    except re.error as e:
        raise ValueError(f"正则表达式无效: {e}")

    max_results = max(1, min(int(max_results or 40), 200))
    matches: List[Dict[str, Any]] = []
    scanned = 0
    truncated = False
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv",
                 "dist", "build", "dist38", "build38", ".idea", ".vscode"}

    targets = [root] if os.path.isfile(root) else None
    if targets is None:
        walk = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".")]
            for fn in filenames:
                walk.append(os.path.join(dirpath, fn))
            if len(walk) > 20000:
                break
        targets = walk

    for file_path in targets:
        if len(matches) >= max_results:
            truncated = True
            break
        try:
            if os.path.getsize(file_path) > _MAX_PREVIEW_BYTES:
                continue
            with open(file_path, "rb") as f:
                raw = f.read()
            if _is_probably_binary(raw):
                continue
            text = raw.decode("utf-8", errors="replace")
        except OSError:
            continue
        scanned += 1
        for lineno, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                matches.append({
                    "path": os.path.relpath(file_path, root) if not os.path.isfile(root) else file_path,
                    "line": lineno,
                    "text": line[:300],
                })
                if len(matches) >= max_results:
                    truncated = True
                    break

    return {
        "pattern": pattern,
        "root": root,
        "scanned_files": scanned,
        "count": len(matches),
        "truncated": truncated,
        "matches": matches,
    }


def file_hash(path: str, algorithm: str = "sha256") -> Dict[str, Any]:
    """计算文件指纹"""
    abs_path = _check_workspace(path, "get_file_info")
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"文件不存在: {abs_path}")
    algo = (algorithm or "sha256").lower()
    if algo not in hashlib.algorithms_available:
        raise ValueError(f"不支持的算法: {algorithm}")
    digest = hashlib.new(algo)
    size = 0
    with open(abs_path, "rb") as f:
        while True:
            block = f.read(1024 * 1024)
            if not block:
                break
            size += len(block)
            digest.update(block)
    return {"path": abs_path, "algorithm": algo, "hash": digest.hexdigest(), "size": size}


def diff_files(path_a: str, path_b: str, context: int = 3) -> Dict[str, Any]:
    """比较两个文本文件的差异（unified diff）"""
    a = _check_workspace(path_a, "read_file")
    b = _check_workspace(path_b, "read_file")
    for p in (a, b):
        if not os.path.isfile(p):
            raise FileNotFoundError(f"文件不存在: {p}")

    def _read(p: str) -> List[str]:
        if os.path.getsize(p) > _MAX_PREVIEW_BYTES:
            raise ValueError(f"文件过大，无法比较: {p}")
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()

    lines_a, lines_b = _read(a), _read(b)
    diff = list(difflib.unified_diff(
        lines_a, lines_b,
        fromfile=os.path.basename(a), tofile=os.path.basename(b),
        n=max(0, min(int(context or 3), 20)),
    ))
    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    return {
        "a": a,
        "b": b,
        "identical": not diff,
        "added": added,
        "removed": removed,
        "diff": "".join(diff)[:20000],
    }


def list_tree(path: str = "", max_depth: int = 2, max_entries: int = 200) -> Dict[str, Any]:
    """目录树（深度与条数受限，适合让模型先看结构）"""
    root = _check_workspace(path or os.getcwd(), "list_files")
    if not os.path.isdir(root):
        raise NotADirectoryError(f"不是目录: {root}")
    max_depth = max(1, min(int(max_depth or 2), 6))
    max_entries = max(1, min(int(max_entries or 200), _MAX_TREE_ENTRIES))
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv",
                 "dist", "build", "dist38", "build38", ".idea", ".vscode"}
    entries: List[Dict[str, Any]] = []

    def walk(current: str, depth: int) -> None:
        if depth > max_depth or len(entries) >= max_entries:
            return
        try:
            names = sorted(os.listdir(current))
        except OSError:
            return
        for name in names:
            if len(entries) >= max_entries:
                return
            full = os.path.join(current, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            is_dir = os.path.isdir(full)
            if is_dir and (name in skip_dirs or name.startswith(".")):
                continue
            entries.append({
                "path": rel,
                "type": "dir" if is_dir else "file",
                "depth": depth,
                "size": 0 if is_dir else os.path.getsize(full),
            })
            if is_dir:
                walk(full, depth + 1)

    walk(root, 1)
    return {
        "root": root,
        "max_depth": max_depth,
        "count": len(entries),
        "truncated": len(entries) >= max_entries,
        "entries": entries,
    }


def csv_preview(path: str, rows: int = 10) -> Dict[str, Any]:
    """预览 CSV：表头 + 前 N 行 + 总行数"""
    abs_path = _check_workspace(path, "read_file")
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"文件不存在: {abs_path}")
    rows = max(1, min(int(rows or 10), 100))
    with open(abs_path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.reader(f)
        all_rows = list(reader)
    header = all_rows[0] if all_rows else []
    body = all_rows[1:1 + rows]
    return {
        "path": abs_path,
        "columns": header,
        "rows": body,
        "preview_rows": len(body),
        "total_rows": max(0, len(all_rows) - 1),
    }


def zip_list(path: str) -> Dict[str, Any]:
    """列出压缩包内的文件"""
    abs_path = _check_workspace(path, "get_file_info")
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"文件不存在: {abs_path}")
    with zipfile.ZipFile(abs_path) as zf:
        items = [{"path": info.filename, "size": info.file_size}
                 for info in zf.infolist()][:500]
    return {"path": abs_path, "count": len(items), "entries": items}


def append_file(path: str, content: str) -> Dict[str, Any]:
    """在文件末尾追加内容（文件不存在则创建）"""
    abs_path = _check_workspace(path, "append_file")
    os.makedirs(os.path.dirname(abs_path) or ".", exist_ok=True)
    existed = os.path.isfile(abs_path)
    with open(abs_path, "a", encoding="utf-8") as f:
        f.write(content or "")
    return {
        "path": abs_path,
        "created": not existed,
        "appended_chars": len(content or ""),
        "size": os.path.getsize(abs_path),
    }


def unzip_file(path: str, dest: str = "") -> Dict[str, Any]:
    """解压到工作区目录（防目录穿越）"""
    abs_path = _check_workspace(path, "get_file_info")
    if not os.path.isfile(abs_path):
        raise FileNotFoundError(f"文件不存在: {abs_path}")
    base = os.path.dirname(abs_path) if not dest else dest
    abs_dest = _check_workspace(base, "create_directory")
    os.makedirs(abs_dest, exist_ok=True)
    extracted: List[str] = []
    with zipfile.ZipFile(abs_path) as zf:
        for info in zf.infolist():
            target = os.path.abspath(os.path.join(abs_dest, info.filename))
            if not target.startswith(os.path.abspath(abs_dest) + os.sep) and target != os.path.abspath(abs_dest):
                continue  # 跳过目录穿越条目
            zf.extract(info, abs_dest)
            extracted.append(info.filename)
    return {"path": abs_path, "dest": abs_dest, "count": len(extracted), "entries": extracted[:200]}


def delete_file(path: str) -> Dict[str, Any]:
    """把文件/目录移到回收站（可还原，不做硬删除）"""
    abs_path = _check_workspace(path, "delete_file")
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"路径不存在: {abs_path}")
    from zhiyan_web.dsh.trash import recycle
    result = recycle(abs_path)
    return {"path": abs_path, "trash": result}


def _json(obj: Any) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, indent=2)
