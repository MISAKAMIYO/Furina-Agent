"""
代码编辑器服务层
提供文件管理、项目管理、代码执行等核心业务逻辑
"""
from __future__ import annotations

import os
import json
import logging
import subprocess
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from zhiyan_web.config import DATA_DIR

logger = logging.getLogger("ZhiYanWeb.CodeEditor")

# 工作区根目录
WORKSPACE_DIR = os.path.join(DATA_DIR, "workspace")
# 项目目录
PROJECTS_DIR = os.path.join(DATA_DIR, "projects")

# 确保目录存在
os.makedirs(WORKSPACE_DIR, exist_ok=True)
os.makedirs(PROJECTS_DIR, exist_ok=True)

# 语言执行映射
LANGUAGE_EXEC_MAP = {
    ".py": {
        "command": ["python", "{file}"],
        "name": "Python",
        "extensions": [".py"],
    },
    ".js": {
        "command": ["node", "{file}"],
        "name": "JavaScript",
        "extensions": [".js", ".mjs"],
    },
    ".html": {
        "command": None,  # 浏览器预览
        "name": "HTML",
        "extensions": [".html", ".htm"],
    },
    ".css": {
        "command": None,
        "name": "CSS",
        "extensions": [".css", ".scss", ".less"],
    },
    ".json": {
        "command": None,
        "name": "JSON",
        "extensions": [".json"],
    },
    ".md": {
        "command": None,
        "name": "Markdown",
        "extensions": [".md"],
    },
    ".txt": {
        "command": None,
        "name": "Plain Text",
        "extensions": [".txt", ".text"],
    },
    ".yaml": {
        "command": None,
        "name": "YAML",
        "extensions": [".yaml", ".yml"],
    },
    ".toml": {
        "command": None,
        "name": "TOML",
        "extensions": [".toml"],
    },
    ".xml": {
        "command": None,
        "name": "XML",
        "extensions": [".xml", ".svg"],
    },
    ".sql": {
        "command": None,
        "name": "SQL",
        "extensions": [".sql"],
    },
    ".sh": {
        "command": ["bash", "{file}"],
        "name": "Shell",
        "extensions": [".sh", ".bash"],
    },
    ".bat": {
        "command": ["cmd", "/c", "{file}"],
        "name": "Batch",
        "extensions": [".bat", ".cmd"],
    },
    ".ps1": {
        "command": ["powershell", "-File", "{file}"],
        "name": "PowerShell",
        "extensions": [".ps1"],
    },
    ".rs": {
        "command": None,  # 需要编译
        "name": "Rust",
        "extensions": [".rs"],
    },
    ".go": {
        "command": ["go", "run", "{file}"],
        "name": "Go",
        "extensions": [".go"],
    },
}

# Monaco Editor 语言映射
MONACO_LANG_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".json": "json",
    ".md": "markdown",
    ".txt": "plaintext",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "plaintext",
    ".xml": "xml",
    ".svg": "xml",
    ".sql": "sql",
    ".sh": "shell",
    ".bash": "shell",
    ".bat": "bat",
    ".ps1": "powershell",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".cpp": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".php": "php",
    ".rb": "ruby",
    ".swift": "swift",
    ".kt": "kotlin",
    ".dart": "dart",
    ".lua": "lua",
    ".r": "r",
    ".pl": "perl",
    ".dockerfile": "dockerfile",
    ".gitignore": "plaintext",
    ".env": "plaintext",
    ".ini": "ini",
    ".cfg": "ini",
    ".conf": "ini",
}


def get_language_from_extension(filename: str) -> str:
    """根据文件扩展名获取语言名称"""
    _, ext = os.path.splitext(filename)
    return LANGUAGE_EXEC_MAP.get(ext, {}).get("name", "Plain Text")


def get_monaco_language(filename: str) -> str:
    """根据文件扩展名获取 Monaco Editor 语言 ID"""
    _, ext = os.path.splitext(filename)
    return MONACO_LANG_MAP.get(ext, "plaintext")


# ════════════════════════════════════════════════════
# 文件操作
# ════════════════════════════════════════════════════

def resolve_path(project: str, path: str) -> str:
    """解析项目内文件路径，支持绝对路径项目（本地目录浏览），防止路径穿越"""
    # 检查是否为绝对路径项目（本地目录浏览模式，project 为电脑真实路径）
    if os.path.isabs(project):
        base = os.path.normpath(os.path.abspath(project))
        if not os.path.exists(base):
            raise FileNotFoundError(f"目录不存在: {base}")
        if not os.path.isdir(base):
            raise NotADirectoryError(f"不是目录: {base}")
    else:
        # 常规项目（工作区内）
        base = os.path.abspath(os.path.join(WORKSPACE_DIR, project))
        base = os.path.normpath(base)
    # 目标路径
    target = os.path.normpath(os.path.abspath(os.path.join(base, path)))
    # 确保目标在基路径内（使用 commonpath 兼容盘符根目录与大小写）
    try:
        common = os.path.commonpath([base, target])
    except ValueError:
        raise PermissionError(f"路径访问被拒绝: {path}")
    if common != base:
        raise PermissionError(f"路径访问被拒绝: {path}")
    return target


def list_system_drives() -> List[str]:
    """获取系统根路径列表：Windows 返回盘符，Unix 返回根目录 /"""
    drives = []
    if os.name == "nt":
        import string
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                drives.append(drive)
    else:
        drives.append("/")
    return drives


def read_file(project: str, path: str) -> Dict[str, Any]:
    """读取文件内容"""
    try:
        filepath = resolve_path(project, path)
        if not os.path.exists(filepath):
            return {"success": False, "error": "文件不存在"}
        if not os.path.isfile(filepath):
            return {"success": False, "error": "路径不是文件"}

        # 尝试以 UTF-8 读取
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            # 二进制文件，返回 base64
            with open(filepath, "rb") as f:
                import base64
                content = base64.b64encode(f.read()).decode("utf-8")
            return {
                "success": True,
                "data": {
                    "content": content,
                    "encoding": "base64",
                    "language": get_language_from_extension(path),
                    "monaco_language": get_monaco_language(path),
                    "size": os.path.getsize(filepath),
                    "modified_at": os.path.getmtime(filepath),
                }
            }

        return {
            "success": True,
            "data": {
                "content": content,
                "encoding": "utf-8",
                "language": get_language_from_extension(path),
                "monaco_language": get_monaco_language(path),
                "size": len(content),
                "modified_at": os.path.getmtime(filepath),
            }
        }
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"读取文件失败: {e}")
        return {"success": False, "error": f"读取失败: {str(e)}"}


def save_file(project: str, path: str, content: str) -> Dict[str, Any]:
    """保存文件内容"""
    try:
        filepath = resolve_path(project, path)
        # 确保目录存在
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return {
            "success": True,
            "data": {
                "size": len(content),
                "modified_at": os.path.getmtime(filepath),
            }
        }
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"保存文件失败: {e}")
        return {"success": False, "error": f"保存失败: {str(e)}"}


def delete_file(project: str, path: str) -> Dict[str, Any]:
    """删除文件或目录"""
    try:
        filepath = resolve_path(project, path)
        if not os.path.exists(filepath):
            return {"success": False, "error": "路径不存在"}

        if os.path.isdir(filepath):
            shutil.rmtree(filepath)
        else:
            os.remove(filepath)

        return {"success": True, "message": "删除成功"}
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"删除失败: {e}")
        return {"success": False, "error": f"删除失败: {str(e)}"}


def create_file(project: str, path: str, content: str = "") -> Dict[str, Any]:
    """创建新文件"""
    return save_file(project, path, content)


def create_folder(project: str, path: str) -> Dict[str, Any]:
    """创建新目录"""
    try:
        folder_path = resolve_path(project, path)
        os.makedirs(folder_path, exist_ok=True)
        return {"success": True, "message": "目录创建成功"}
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"创建目录失败: {e}")
        return {"success": False, "error": f"创建失败: {str(e)}"}


def rename_item(project: str, path: str, new_name: str) -> Dict[str, Any]:
    """重命名文件或目录"""
    try:
        old_path = resolve_path(project, path)
        new_path = os.path.join(os.path.dirname(old_path), new_name)
        if not os.path.exists(old_path):
            return {"success": False, "error": "源路径不存在"}
        if os.path.exists(new_path):
            return {"success": False, "error": "目标路径已存在"}
        os.rename(old_path, new_path)
        return {"success": True, "message": "重命名成功"}
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"重命名失败: {e}")
        return {"success": False, "error": f"重命名失败: {str(e)}"}


def move_item(project: str, path: str, target_dir: str) -> Dict[str, Any]:
    """移动文件或目录到目标目录（用于拖放调整层级）"""
    try:
        source = resolve_path(project, path)
        if not os.path.exists(source):
            return {"success": False, "error": "源路径不存在"}

        target_base = resolve_path(project, target_dir)
        if not os.path.isdir(target_base):
            return {"success": False, "error": "目标位置不是目录"}

        # 防止移动到自身或其子目录
        source_abs = os.path.abspath(source)
        target_abs = os.path.abspath(target_base)
        if os.path.normpath(target_abs).startswith(os.path.normpath(source_abs) + os.sep):
            return {"success": False, "error": "不能移动到自身或其子目录"}

        name = os.path.basename(source_abs)
        dest = os.path.join(target_abs, name)
        if os.path.exists(dest):
            return {"success": False, "error": f"目标位置已存在同名项 '{name}'"}

        shutil.move(source, dest)
        return {"success": True, "message": "移动成功"}
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"移动失败: {e}")
        return {"success": False, "error": f"移动失败: {str(e)}"}


def list_files(project: str, path: str = "") -> Dict[str, Any]:
    """列出目录内容（文件树）"""
    try:
        dir_path = resolve_path(project, path)
        if not os.path.exists(dir_path):
            return {"success": False, "error": "目录不存在"}
        if not os.path.isdir(dir_path):
            return {"success": False, "error": "路径不是目录"}

        entries = []
        for name in sorted(os.listdir(dir_path)):
            if name.startswith("."):
                continue  # 隐藏文件
            full_path = os.path.join(dir_path, name)
            is_dir = os.path.isdir(full_path)
            stat = os.stat(full_path)
            # 统一使用正斜杠路径，避免 Windows 反斜杠导致前端不一致
            rel = os.path.join(path, name).replace(os.sep, "/")
            entries.append({
                "name": name,
                "path": rel,
                "type": "directory" if is_dir else "file",
                "size": stat.st_size if not is_dir else 0,
                "modified_at": stat.st_mtime,
                "language": get_language_from_extension(name) if not is_dir else None,
                "monaco_language": get_monaco_language(name) if not is_dir else None,
            })

        return {
            "success": True,
            "data": {
                "path": path or "/",
                "entries": entries,
                "total": len(entries),
            }
        }
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"列出文件失败: {e}")
        return {"success": False, "error": f"列出失败: {str(e)}"}


def list_files_tree(project: str, max_depth: int = 20) -> Dict[str, Any]:
    """递归列出整个项目文件树（用于搜索与统计），限制深度防止过深遍历"""
    try:
        base = resolve_path(project, "")
        if not os.path.isdir(base):
            return {"success": False, "error": "项目目录不存在"}

        flat = []
        tree = []
        total_files = 0
        total_folders = 0

        def _walk(dir_path, rel_path, depth):
            nonlocal total_files, total_folders
            if depth > max_depth:
                return
            try:
                items = sorted(os.listdir(dir_path))
            except PermissionError:
                return
            for name in items:
                if name.startswith("."):
                    continue
                full = os.path.join(dir_path, name)
                rel = os.path.join(rel_path, name).replace(os.sep, "/")
                is_dir = os.path.isdir(full)
                try:
                    stat = os.stat(full)
                    size = stat.st_size if not is_dir else 0
                    mtime = stat.st_mtime
                except OSError:
                    size, mtime = 0, 0
                entry = {
                    "name": name,
                    "path": rel,
                    "type": "directory" if is_dir else "file",
                    "size": size,
                    "modified_at": mtime,
                }
                flat.append(entry)
                if is_dir:
                    total_folders += 1
                    children = []
                    node = dict(entry, children=children)
                    tree.append(node)
                    _walk(full, rel, depth + 1)
                    if not children:
                        node.pop("children", None)
                else:
                    total_files += 1
                    tree.append(entry)

        _walk(base, "", 0)

        return {
            "success": True,
            "data": {
                "tree": tree,
                "flat": flat,
                "total_files": total_files,
                "total_folders": total_folders,
            }
        }
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"递归列出文件失败: {e}")
        return {"success": False, "error": f"遍历失败: {str(e)}"}


# ════════════════════════════════════════════════════
# 项目管理
# ════════════════════════════════════════════════════

def list_projects() -> Dict[str, Any]:
    """列出所有项目"""
    try:
        projects = []
        for name in sorted(os.listdir(WORKSPACE_DIR)):
            project_path = os.path.join(WORKSPACE_DIR, name)
            if os.path.isdir(project_path):
                # 读取项目元数据
                meta_path = os.path.join(project_path, ".zhiyan_project.json")
                metadata = {}
                if os.path.exists(meta_path):
                    try:
                        with open(meta_path, "r") as f:
                            metadata = json.load(f)
                    except:
                        pass

                # 统计文件数
                file_count = 0
                for root, dirs, files in os.walk(project_path):
                    if ".git" in root.split(os.sep):
                        continue
                    file_count += len(files)

                projects.append({
                    "name": name,
                    "display_name": metadata.get("name", name),
                    "description": metadata.get("description", ""),
                    "language": metadata.get("language", ""),
                    "file_count": file_count,
                    "created_at": os.path.getctime(project_path),
                    "modified_at": os.path.getmtime(project_path),
                })

        return {
            "success": True,
            "data": {
                "projects": projects,
                "total": len(projects),
            }
        }
    except Exception as e:
        logger.error(f"列出项目失败: {e}")
        return {"success": False, "error": f"列出项目失败: {str(e)}"}


def create_project(name: str, description: str = "", language: str = "") -> Dict[str, Any]:
    """创建新项目"""
    try:
        project_path = os.path.join(WORKSPACE_DIR, name)
        if os.path.exists(project_path):
            return {"success": False, "error": f"项目 '{name}' 已存在"}

        os.makedirs(project_path)

        # 创建项目元数据
        metadata = {
            "name": name,
            "description": description,
            "language": language,
            "created_at": datetime.now().isoformat(),
        }
        with open(os.path.join(project_path, ".zhiyan_project.json"), "w") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        # 创建默认文件
        default_files = {
            "README.md": f"# {name}\n\n{description}\n",
        }
        if language == "python":
            default_files["main.py"] = "# " + name + "\n\nprint('Hello, ZhiYan!')\n"
        elif language == "javascript":
            default_files["index.js"] = "// " + name + "\n\nconsole.log('Hello, ZhiYan!');\n"
        elif language == "html":
            default_files["index.html"] = "<!DOCTYPE html>\n<html>\n<head>\n    <title>" + name + "</title>\n</head>\n<body>\n    <h1>Hello, ZhiYan!</h1>\n</body>\n</html>\n"
        else:
            default_files["main.py"] = "# " + name + "\n\nprint('Hello, ZhiYan!')\n"

        for filename, content in default_files.items():
            with open(os.path.join(project_path, filename), "w") as f:
                f.write(content)

        return {
            "success": True,
            "data": {
                "name": name,
                "path": project_path,
                "files_created": list(default_files.keys()),
            }
        }
    except Exception as e:
        logger.error(f"创建项目失败: {e}")
        return {"success": False, "error": f"创建项目失败: {str(e)}"}


def delete_project(name: str) -> Dict[str, Any]:
    """删除项目"""
    try:
        project_path = os.path.join(WORKSPACE_DIR, name)
        if not os.path.exists(project_path):
            return {"success": False, "error": f"项目 '{name}' 不存在"}
        shutil.rmtree(project_path)
        return {"success": True, "message": "项目已删除"}
    except Exception as e:
        logger.error(f"删除项目失败: {e}")
        return {"success": False, "error": f"删除失败: {str(e)}"}


# ════════════════════════════════════════════════════
# 代码执行（多语言）
# ════════════════════════════════════════════════════

def execute_code(project: str, path: str, code: str = None) -> Dict[str, Any]:
    """执行代码"""
    try:
        # 如果有 path 且 code 为空，则读取文件内容
        if code is None:
            filepath = resolve_path(project, path)
            if not os.path.exists(filepath):
                return {"success": False, "error": "文件不存在"}
            with open(filepath, "r", encoding="utf-8") as f:
                code = f.read()
            _, ext = os.path.splitext(filepath)
        else:
            # 使用临时文件
            _, ext = os.path.splitext(path) if path else (".py", ".py")
            filepath = None

        lang_config = LANGUAGE_EXEC_MAP.get(ext, LANGUAGE_EXEC_MAP.get(".py"))
        if not lang_config or not lang_config["command"]:
            return {
                "success": False,
                "error": f"不支持执行 {lang_config['name'] if lang_config else '该类型'} 文件",
                "data": {
                    "language": lang_config["name"] if lang_config else "Unknown",
                    "suggestion": "该文件类型当前不支持直接执行，请使用预览功能查看",
                }
            }

        # 创建临时文件执行
        tmp_file = None
        if not filepath:
            tmp_dir = tempfile.mkdtemp()
            tmp_file = os.path.join(tmp_dir, f"script{ext}")
            with open(tmp_file, "w", encoding="utf-8") as f:
                f.write(code)
            filepath = tmp_file

        # 构建命令
        cmd = [part.replace("{file}", filepath) for part in lang_config["command"]]

        logger.info(f"执行代码: {cmd}")

        # 执行
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=os.path.dirname(filepath),
        )

        output = result.stdout
        error = result.stderr if result.stderr else None
        exit_code = result.returncode

        # 清理临时文件
        if tmp_file:
            try:
                shutil.rmtree(os.path.dirname(tmp_file))
            except:
                pass

        return {
            "success": exit_code == 0,
            "data": {
                "output": output,
                "error": error,
                "exit_code": exit_code,
                "language": lang_config["name"],
                "execution_time": "N/A",
            }
        }

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "执行超时（30秒）"}
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except FileNotFoundError as e:
        return {"success": False, "error": f"执行环境未找到: {str(e)}"}
    except Exception as e:
        logger.error(f"执行代码失败: {e}")
        return {"success": False, "error": f"执行失败: {str(e)}"}


# ════════════════════════════════════════════════════
# 文件模板
# ════════════════════════════════════════════════════

FILE_TEMPLATES = {
    "python": {
        "name": "Python 脚本",
        "ext": ".py",
        "content": "# !/usr/bin/env python3\n# -*- coding: utf-8 -*-\n\"\"\"\n{description}\n\"\"\"\n\n\ndef main():\n    {pass}\n\n\nif __name__ == \"__main__\":\n    main()\n",
    },
    "javascript": {
        "name": "JavaScript 脚本",
        "ext": ".js",
        "content": "/**\n * {description}\n */\n\nfunction main() {\n    {pass}\n}\n\nmain();\n",
    },
    "html": {
        "name": "HTML 页面",
        "ext": ".html",
        "content": "<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n    <meta charset=\"UTF-8\">\n    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n    <title>{title}</title>\n    <style>\n        body { font-family: sans-serif; padding: 20px; }\n    </style>\n</head>\n<body>\n    <h1>{title}</h1>\n    <p>开始编辑...</p>\n</body>\n</html>\n",
    },
    "css": {
        "name": "CSS 样式表",
        "ext": ".css",
        "content": "/* {description} */\n\n* {\n    margin: 0;\n    padding: 0;\n    box-sizing: border-box;\n}\n\nbody {\n    font-family: sans-serif;\n    line-height: 1.6;\n}\n",
    },
    "json": {
        "name": "JSON 文件",
        "ext": ".json",
        "content": "{\n    \"name\": \"{name}\",\n    \"version\": \"1.0.0\",\n    \"description\": \"{description}\"\n}\n",
    },
    "markdown": {
        "name": "Markdown 文档",
        "ext": ".md",
        "content": "# {title}\n\n{description}\n\n## 开始\n\n在这里编写内容...\n",
    },
    "shell": {
        "name": "Shell 脚本",
        "ext": ".sh",
        "content": "#!/bin/bash\n# {description}\n\necho \"Hello from ZhiYan!\"\n",
    },
    "batch": {
        "name": "批处理文件",
        "ext": ".bat",
        "content": "@echo off\nREM {description}\n\necho Hello from ZhiYan!\npause\n",
    },
    "sql": {
        "name": "SQL 查询",
        "ext": ".sql",
        "content": "-- {description}\n\nSELECT * FROM users;\n",
    },
    "rust": {
        "name": "Rust 源码",
        "ext": ".rs",
        "content": "// {description}\n\nfn main() {\n    println!(\"Hello from ZhiYan!\");\n}\n",
    },
    "go": {
        "name": "Go 源码",
        "ext": ".go",
        "content": "package main\n\nimport \"fmt\"\n\nfunc main() {\n    fmt.Println(\"Hello from ZhiYan!\")\n}\n",
    },
    "typescript": {
        "name": "TypeScript 源码",
        "ext": ".ts",
        "content": "/**\n * {description}\n */\n\nfunction main(): void {\n    console.log('Hello from ZhiYan!');\n}\n\nmain();\n",
    },
}


def list_templates() -> Dict[str, Any]:
    """列出文件模板"""
    return {
        "success": True,
        "data": {
            "templates": [
                {
                    "id": key,
                    "name": tmpl["name"],
                    "extension": tmpl["ext"],
                    "content_preview": tmpl["content"][:100],
                }
                for key, tmpl in FILE_TEMPLATES.items()
            ],
            "total": len(FILE_TEMPLATES),
        }
    }


def get_template(template_id: str, variables: Dict[str, str] = None) -> Dict[str, Any]:
    """获取模板内容"""
    tmpl = FILE_TEMPLATES.get(template_id)
    if not tmpl:
        return {"success": False, "error": f"模板 '{template_id}' 不存在"}

    content = tmpl["content"]
    if variables:
        for key, value in variables.items():
            placeholder = "{" + key + "}"
            content = content.replace(placeholder, value)

    # 替换默认变量
    content = content.replace("{description}", "新建文件")
    content = content.replace("{name}", "untitled")
    content = content.replace("{title}", "未命名")
    content = content.replace("{pass}", "pass")

    return {
        "success": True,
        "data": {
            "id": template_id,
            "name": tmpl["name"],
            "extension": tmpl["ext"],
            "content": content,
        }
    }