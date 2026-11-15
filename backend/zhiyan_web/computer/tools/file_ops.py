"""
文件操作工具 - 读写编辑删除搜索文件

根据安全管控模式限制文件操作：
- normal (普通默认): 写入仅限工作区，读取全盘允许
- full_control (完全控制): 全盘读写允许
- high_security (高权限安全): 禁止系统关键位置操作，禁止写入可执行文件
"""
from __future__ import annotations

import os
import glob
import logging
from typing import List, Dict

from zhiyan_web.computer.services.workspace_manager import validate_path
from zhiyan_web.computer.services.safety_manager import (
    is_path_allowed_for_read,
    is_path_allowed_for_write,
    get_security_mode,
    approval_bypass_active,
    MODE_FULL_CONTROL,
    MODE_HIGH_SECURITY,
    MODE_NORMAL,
)

logger = logging.getLogger("ZhiYanWeb.Computer.FileOps")


def _check_workspace(path: str, operation: str = "") -> str:
    """检查路径是否允许操作，返回标准化路径或抛出异常"""
    abs_path = os.path.abspath(path)

    # 判断操作类型：读取类 vs 写入类
    is_read_operation = operation in ("read_file", "get_file_info", "list_files", "search_files")

    # 安全检查
    if is_read_operation:
        # 读取操作：根据安全模式检查
        allowed, reason = is_path_allowed_for_read(abs_path)
        if not allowed:
            raise PermissionError(reason)
        # 读取操作不需要工作区验证
        return abs_path
    else:
        # 写入操作：根据安全模式检查
        allowed, reason = is_path_allowed_for_write(abs_path)
        if not allowed:
            raise PermissionError(reason)
        # 写入操作还需要工作区验证（full_control 模式 / 用户本次审批放行时除外）
        if get_security_mode() != MODE_FULL_CONTROL and not approval_bypass_active():
            ws_allowed, norm_path, ws_error = validate_path(path, operation)
            if not ws_allowed:
                # 如果工作区未配置，给出更友好的提示
                if "未配置" in ws_error:
                    raise PermissionError(
                        "Agent 工作区未配置。请在设置 → 高级配置中设置 Agent 工作区路径。\n"
                        "工作区是 Agent 可以读写文件的唯一目录。"
                    )
                raise PermissionError(ws_error)
            return norm_path
        return abs_path


def read_file(path: str) -> Dict:
    """读取文件内容"""
    abs_path = _check_workspace(path, "read_file")
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"文件不存在: {abs_path}")
    if not os.path.isfile(abs_path):
        raise IsADirectoryError(f"路径不是文件: {abs_path}")

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {
            "path": abs_path,
            "content": content,
            "size": len(content),
            "lines": content.count("\n") + 1,
        }
    except UnicodeDecodeError:
        # 二进制文件
        with open(abs_path, "rb") as f:
            content = f.read()
        return {
            "path": abs_path,
            "content": f"[二进制文件] 大小: {len(content)} 字节",
            "size": len(content),
            "is_binary": True,
        }


def write_file(path: str, content: str) -> Dict:
    """写入文件内容（会覆盖已有文件）"""
    abs_path = _check_workspace(path, "write_file")
    # 确保父目录存在
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(content)
    return {
        "path": abs_path,
        "size": len(content),
        "message": f"文件已写入: {abs_path}",
    }


def edit_file(path: str, old_text: str, new_text: str) -> Dict:
    """编辑文件内容（替换文本）"""
    abs_path = _check_workspace(path, "edit_file")
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"文件不存在: {abs_path}")

    with open(abs_path, "r", encoding="utf-8") as f:
        content = f.read()

    if old_text not in content:
        # 尝试模糊匹配
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if old_text.strip() in line.strip():
                old_text = line
                break
        else:
            raise ValueError(f"未找到匹配的文本: {old_text[:50]}...")

    new_content = content.replace(old_text, new_text, 1)
    if new_content == content:
        raise ValueError("替换后内容无变化，请检查文本是否匹配")

    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(new_content)

    return {
        "path": abs_path,
        "message": "文件已编辑",
        "replacements": 1,
    }


def delete_file(path: str) -> Dict:
    """删除文件"""
    abs_path = _check_workspace(path, "delete_file")
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"文件不存在: {abs_path}")
    if not os.path.isfile(abs_path):
        raise IsADirectoryError("请使用 delete_directory 删除目录")

    os.remove(abs_path)
    return {"message": f"文件已删除: {abs_path}"}


def list_files(path: str = ".", pattern: str = "*") -> Dict:
    """列出目录中的文件"""
    abs_path = _check_workspace(path, "list_files")
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"目录不存在: {abs_path}")
    if not os.path.isdir(abs_path):
        raise NotADirectoryError(f"路径不是目录: {abs_path}")

    items = []
    total_size = 0
    for entry in os.scandir(abs_path):
        try:
            is_dir = entry.is_dir()
            if is_dir and pattern == "*":
                items.append({
                    "name": entry.name,
                    "type": "directory",
                    "size": 0,
                })
            elif not is_dir and glob.fnmatch.fnmatch(entry.name, pattern):
                size = entry.stat().st_size
                total_size += size
                items.append({
                    "name": entry.name,
                    "type": "file",
                    "size": size,
                    "ext": os.path.splitext(entry.name)[1],
                })
        except OSError:
            continue

    items.sort(key=lambda x: (x["type"] != "directory", x["name"].lower()))

    return {
        "path": abs_path,
        "total": len(items),
        "total_size": total_size,
        "items": items,
    }


def search_files(path: str = ".", query: str = "") -> Dict:
    """搜索文件内容"""
    abs_path = _check_workspace(path, "search_files")
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"目录不存在: {abs_path}")

    results = []
    search_extensions = {".py", ".js", ".ts", ".html", ".css", ".json", ".md", ".txt", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".xml", ".sql", ".sh", ".bat", ".ps1"}

    for root, dirs, files in os.walk(abs_path):
        # 跳过隐藏目录和 node_modules
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "node_modules" and d != "__pycache__" and d != ".git"]
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext not in search_extensions:
                continue
            filepath = os.path.join(root, file)
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    for line_num, line in enumerate(f, 1):
                        if query.lower() in line.lower():
                            results.append({
                                "file": filepath,
                                "line": line_num,
                                "content": line.rstrip()[:200],
                            })
                            if len(results) >= 50:  # 最多返回 50 条
                                break
            except Exception:
                continue
            if len(results) >= 50:
                break

    return {
        "query": query,
        "path": abs_path,
        "total": len(results),
        "results": results,
    }


def get_file_info(path: str) -> Dict:
    """获取文件信息"""
    abs_path = _check_workspace(path, "get_file_info")
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"文件不存在: {abs_path}")

    stat = os.stat(abs_path)
    import datetime
    return {
        "path": abs_path,
        "size": stat.st_size,
        "is_dir": os.path.isdir(abs_path),
        "is_file": os.path.isfile(abs_path),
        "created": datetime.datetime.fromtimestamp(stat.st_ctime).isoformat(),
        "modified": datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "permissions": oct(stat.st_mode)[-3:],
    }


def create_directory(path: str) -> Dict:
    """创建目录"""
    abs_path = _check_workspace(path, "create_directory")
    os.makedirs(abs_path, exist_ok=True)
    return {"path": abs_path, "message": f"目录已创建: {abs_path}"}


def copy_file(source: str, destination: str) -> Dict:
    """复制文件"""
    import shutil
    src = _check_workspace(source, "copy_file")
    dst = _check_workspace(destination, "copy_file")
    if not os.path.exists(src):
        raise FileNotFoundError(f"源文件不存在: {src}")
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))
    shutil.copy2(src, dst)
    return {"source": src, "destination": dst, "message": f"文件已复制: {os.path.basename(src)}"}


def move_file(source: str, destination: str) -> Dict:
    """移动文件"""
    import shutil
    src = _check_workspace(source, "move_file")
    dst = _check_workspace(destination, "move_file")
    if not os.path.exists(src):
        raise FileNotFoundError(f"源文件不存在: {src}")
    if os.path.isdir(dst):
        dst = os.path.join(dst, os.path.basename(src))
    shutil.move(src, dst)
    return {"source": src, "destination": dst, "message": f"文件已移动: {os.path.basename(src)}"}


def rename(old_path: str, new_path: str) -> Dict:
    """重命名文件/目录"""
    old = _check_workspace(old_path, "rename")
    new = _check_workspace(new_path, "rename")
    if not os.path.exists(old):
        raise FileNotFoundError(f"文件不存在: {old}")
    os.rename(old, new)
    return {"old_path": old, "new_path": new, "message": f"已重命名为: {os.path.basename(new)}"}