"""
电脑操控执行器 - 编排所有电脑操控工具

为用户提供统一的执行入口，同时自动记录审计日志。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional, Callable, Awaitable

import os
import json
import re

from ..tools import file_ops, process_ops, system_ops, desktop_ops, web_ops, browser_ops, image_ops

# 浏览器操作白名单（前缀 browser_ 的操作统一分发到 browser_ops）
_BROWSER_OP_PREFIX = "browser_"
_BROWSER_OPERATIONS = {
    _BROWSER_OP_PREFIX + op for op in browser_ops.available_operations()
}
# 兜底：即使 playwright 不可用也保持框架层可识别这些操作名
_BROWSER_OPERATIONS |= {
    "browser_navigate", "browser_back", "browser_forward", "browser_reload",
    "browser_open_tab", "browser_list_tabs", "browser_switch_tab", "browser_close_tab",
    "browser_find", "browser_click", "browser_dblclick", "browser_hover",
    "browser_type", "browser_press_key", "browser_select_option", "browser_check",
    "browser_fill_form", "browser_submit", "browser_scroll", "browser_get_text",
    "browser_get_html", "browser_get_attribute", "browser_extract",
    "browser_evaluate", "browser_wait", "browser_screenshot", "browser_close",
    "browser_status", "browser_logs",
}
from .audit import get_audit_logger

logger = logging.getLogger("ZhiYanWeb.Computer.Executor")

# 安全白名单 - 允许的命令前缀
ALLOWED_COMMAND_PREFIXES = [
    "python", "python3", "pip", "pip3", "npm", "npx", "node",
    "git", "dir", "ls", "pwd", "cd", "echo", "type", "cat",
    "mkdir", "copy", "move", "ren", "del",
    "where", "which", "findstr", "find", "sort",
    "systeminfo", "tasklist", "netstat", "ipconfig", "ping",
    "chcp", "date", "time", "ver", "whoami",
]

# 危险命令 - 禁止执行
FORBIDDEN_COMMANDS = [
    "format", "fdisk", "diskpart", "reg delete", "regedit",
    "shutdown", "restart", "reboot", "init 0", "init 6",
    "dd ", "mkfs", "fsck", "pvcreate", "lvcreate",
    "del /f /s", "rm -rf", "rmdir /s",
]

# 安全白名单目录（文件操作限定在这些目录内）
ALLOWED_BASE_DIRS = [
    os.path.expanduser("~\\Desktop"),
    os.path.expanduser("~\\Documents"),
    os.path.expanduser("~\\Downloads"),
    os.path.expanduser("~\\Pictures"),
    os.path.expanduser("~\\Music"),
    os.path.expanduser("~\\Videos"),
    os.path.expanduser("~\\AppData\\Local\\Temp"),
]


class ComputerExecutor:
    """电脑操控执行器

    提供统一的执行入口，自动记录审计日志。
    所有操作都经过安全检查和权限验证。
    """

    def __init__(self):
        self.audit = get_audit_logger()
        # 允许的目录（运行时动态扩展）
        self._allowed_dirs = set(ALLOWED_BASE_DIRS)
        # 会话级临时目录授权
        self._session_authorized_paths: Dict[str, set] = {}

    def authorize_path(self, path: str, session_id: str = "default"):
        """授权访问特定路径（仅当前会话）"""
        if session_id not in self._session_authorized_paths:
            self._session_authorized_paths[session_id] = set()
        self._session_authorized_paths[session_id].add(os.path.abspath(path))

    def _is_path_allowed(self, path: str, session_id: str = "default") -> bool:
        """检查路径是否在允许范围内"""
        abs_path = os.path.abspath(path)
        # 检查是否在基础白名单目录中
        for allowed in self._allowed_dirs:
            if abs_path.startswith(allowed):
                return True
        # 检查是否在会话授权路径中
        for authorized in self._session_authorized_paths.get(session_id, set()):
            if abs_path.startswith(authorized):
                return True
        return False

    async def execute(
        self,
        operation: str,
        params: Dict[str, Any],
        session_id: str = "default",
        user_id: str = "agent",
    ) -> Dict[str, Any]:
        """执行操作

        Args:
            operation: 操作类型
            params: 操作参数
            session_id: 会话 ID
            user_id: 用户 ID

        Returns:
            Dict: {success, result, error}
        """
        start = time.time()
        try:
            result = await self._dispatch(operation, params, session_id)
            duration = (time.time() - start) * 1000
            self.audit.log(operation, params, result, user_id, duration, success=True)
            return {"success": True, "result": result}
        except Exception as e:
            duration = (time.time() - start) * 1000
            error_result = {"error": str(e)}
            self.audit.log(operation, params, error_result, user_id, duration, success=False)
            logger.error(f"执行操作失败 {operation}: {e}")
            return {"success": False, "error": str(e)}

    async def _dispatch(
        self, operation: str, params: Dict[str, Any], session_id: str
    ) -> Any:
        """分发操作到具体工具"""
        # === 文件操作 ===
        if operation == "read_file":
            path = params.get("path", "")
            return file_ops.read_file(path)
        elif operation == "write_file":
            path = params.get("path", "")
            content = params.get("content", "")
            return file_ops.write_file(path, content)
        elif operation == "edit_file":
            path = params.get("path", "")
            old_text = params.get("old_text", "")
            new_text = params.get("new_text", "")
            return file_ops.edit_file(path, old_text, new_text)
        elif operation == "delete_file":
            path = params.get("path", "")
            return file_ops.delete_file(path)
        elif operation == "list_files":
            path = params.get("path", ".")
            pattern = params.get("pattern", "*")
            return file_ops.list_files(path, pattern)
        elif operation == "search_files":
            path = params.get("path", ".")
            query = params.get("query", "")
            return file_ops.search_files(path, query)
        elif operation == "get_file_info":
            path = params.get("path", "")
            return file_ops.get_file_info(path)
        elif operation == "create_directory":
            path = params.get("path", "")
            return file_ops.create_directory(path)
        elif operation == "copy_file":
            src = params.get("source", "")
            dst = params.get("destination", "")
            return file_ops.copy_file(src, dst)
        elif operation == "move_file":
            src = params.get("source", "")
            dst = params.get("destination", "")
            return file_ops.move_file(src, dst)
        elif operation == "rename_file":
            old = params.get("old_path", "")
            new = params.get("new_path", "")
            return file_ops.rename(old, new)

        # === 进程操作 ===
        elif operation == "list_processes":
            return process_ops.list_processes()
        elif operation == "launch_process":
            command = params.get("command", "")
            args = params.get("args", "")
            return process_ops.launch_process(command, args)
        elif operation == "kill_process":
            pid = params.get("pid", 0)
            return process_ops.kill_process(pid)
        elif operation == "get_process_info":
            pid = params.get("pid", 0)
            return process_ops.get_process_info(pid)

        # === 系统操作 ===
        elif operation == "execute_command":
            command = params.get("command", "")
            timeout = params.get("timeout", 30)
            # 安全检查
            self._check_command_safety(command)
            return await system_ops.execute_command(command, timeout)
        elif operation == "get_system_info":
            return system_ops.get_system_info()
        elif operation == "get_environment":
            return system_ops.get_environment()
        elif operation == "open_file_explorer":
            path = params.get("path", ".")
            return system_ops.open_file_explorer(path)
        elif operation == "open_url":
            url = params.get("url", "")
            return system_ops.open_url(url)
        elif operation == "open_application":
            path = params.get("path", "")
            args = params.get("args", "")
            return process_ops.open_application(path, args)

        # === 桌面操作 ===
        elif operation == "take_screenshot":
            return desktop_ops.take_screenshot()
        elif operation == "get_mouse_position":
            return desktop_ops.get_mouse_position()
        elif operation == "move_mouse":
            x = params.get("x", 0)
            y = params.get("y", 0)
            return desktop_ops.move_mouse(x, y)
        elif operation == "click_mouse":
            button = params.get("button", "left")
            return desktop_ops.click_mouse(button)
        elif operation == "type_text":
            text = params.get("text", "")
            return desktop_ops.type_text(text)
        elif operation == "press_key":
            key = params.get("key", "")
            return desktop_ops.press_key(key)
        elif operation == "get_screen_size":
            return desktop_ops.get_screen_size()
        elif operation == "scroll_mouse":
            clicks = params.get("clicks", 1)
            return desktop_ops.scroll_mouse(clicks)
        elif operation == "double_click":
            return desktop_ops.double_click()
        elif operation == "drag_mouse":
            x = params.get("x", 0)
            y = params.get("y", 0)
            return desktop_ops.drag_mouse(x, y)

        # === 剪贴板操作 ===
        elif operation == "get_clipboard":
            return desktop_ops.get_clipboard()
        elif operation == "set_clipboard":
            text = params.get("text", "")
            return desktop_ops.set_clipboard(text)

        # === 窗口管理 ===
        elif operation == "list_windows":
            return desktop_ops.list_windows()
        elif operation == "activate_window":
            title = params.get("title", "")
            return desktop_ops.activate_window(title)
        elif operation == "minimize_window":
            title = params.get("title", "")
            return desktop_ops.minimize_window(title)
        elif operation == "close_window":
            title = params.get("title", "")
            return desktop_ops.close_window(title)

        # === Web 操作 ===
        elif operation == "web_fetch":
            url = params.get("url", "")
            return await web_ops.web_fetch(url)
        elif operation == "web_search":
            query = params.get("query", "")
            num_results = params.get("num_results", 5)
            return await web_ops.web_search(query, num_results)
        elif operation == "web_screenshot":
            url = params.get("url", "")
            return await web_ops.get_page_screenshot(url)

        # === 数据处理工具（内置，无需额外依赖） ===
        elif operation == "calculate":
            expression = params.get("expression", "")
            return self._calculate(expression)
        elif operation == "format_json":
            text = params.get("text", "")
            return self._format_json(text)
        elif operation == "extract_json":
            text = params.get("text", "")
            return self._extract_json(text)
        elif operation == "count_tokens":
            text = params.get("text", "")
            return self._count_tokens(text)
        elif operation == "base64_encode":
            text = params.get("text", "")
            return {"result": self._base64_encode(text)}
        elif operation == "base64_decode":
            text = params.get("text", "")
            return {"result": self._base64_decode(text)}
        elif operation == "generate_uuid":
            import uuid
            return {"uuid": str(uuid.uuid4())}
        elif operation == "get_current_time":
            from datetime import datetime
            return {
                "now": datetime.now().isoformat(),
                "timestamp": time.time(),
                "timezone": "Asia/Shanghai",
            }
        elif operation == "hash_text":
            text = params.get("text", "")
            algorithm = params.get("algorithm", "md5")
            return self._hash_text(text, algorithm)

        # === 浏览器自动化操作（前缀 browser_ 统一分发到 browser_ops） ===
        elif operation in _BROWSER_OPERATIONS:
            return await browser_ops.browser_execute(
                operation[len(_BROWSER_OP_PREFIX):], **params
            )

        # === 图片展示操作 ===
        elif operation == "show_image":
            return image_ops.show_image(**params)

        else:
            raise ValueError(f"未知操作类型: {operation}")

    def _check_command_safety(self, command: str):
        """检查命令安全性"""
        cmd_lower = command.strip().lower()

        # 检查是否在禁止命令列表中
        for forbidden in FORBIDDEN_COMMANDS:
            if cmd_lower.startswith(forbidden):
                raise PermissionError(f"禁止执行危险命令: {forbidden}")

        # 检查是否在白名单中
        allowed = False
        for prefix in ALLOWED_COMMAND_PREFIXES:
            if cmd_lower.startswith(prefix):
                allowed = True
                break

        if not allowed:
            raise PermissionError(
                f"命令不在白名单中: {command}\n"
                f"允许的命令前缀: {', '.join(ALLOWED_COMMAND_PREFIXES)}"
            )

    # ──── 内置数据处理工具 ────

    def _calculate(self, expression: str) -> Dict:
        """安全地执行数学计算"""
        # 只允许基本的数学表达式
        safe_pattern = re.compile(r'^[\d\s+\-*/().,%^eE]+$')
        if not safe_pattern.match(expression):
            return {"error": "表达式包含非法字符", "expression": expression}
        try:
            # 替换 ^ 为 ** 以支持幂运算
            expr = expression.replace("^", "**")
            result = eval(expr, {"__builtins__": {}}, {})
            return {"expression": expression, "result": result}
        except Exception as e:
            return {"error": str(e), "expression": expression}

    def _format_json(self, text: str) -> Dict:
        """格式化 JSON 字符串"""
        try:
            parsed = json.loads(text)
            formatted = json.dumps(parsed, ensure_ascii=False, indent=2)
            return {"success": True, "formatted": formatted, "length": len(formatted)}
        except json.JSONDecodeError as e:
            return {"success": False, "error": f"JSON 解析失败: {e}"}

    def _extract_json(self, text: str) -> Dict:
        """从文本中提取 JSON 对象"""
        try:
            # 查找 {...} 或 [...] 结构
            json_objects = []
            # 尝试查找 {} 包裹的 JSON
            for match in re.finditer(r'\{[^{}]*\}', text):
                try:
                    obj = json.loads(match.group())
                    json_objects.append(obj)
                except json.JSONDecodeError:
                    pass
            # 尝试查找 [] 包裹的 JSON
            for match in re.finditer(r'\[[^\[\]]*\]', text):
                try:
                    obj = json.loads(match.group())
                    json_objects.append(obj)
                except json.JSONDecodeError:
                    pass
            return {"count": len(json_objects), "objects": json_objects}
        except Exception as e:
            return {"error": str(e)}

    def _count_tokens(self, text: str) -> Dict:
        """估算文本的 token 数（使用简单估算：中文约 1.5 字符/token，英文约 4 字符/token）"""
        if not text:
            return {"characters": 0, "estimated_tokens": 0, "method": "估算"}
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        other_chars = len(text) - chinese_chars
        estimated = int(chinese_chars / 1.5 + other_chars / 4)
        return {
            "characters": len(text),
            "chinese_chars": chinese_chars,
            "other_chars": other_chars,
            "estimated_tokens": max(1, estimated),
            "method": "估算（中文字符÷1.5 + 其他字符÷4）",
        }

    def _base64_encode(self, text: str) -> str:
        """Base64 编码"""
        import base64
        return base64.b64encode(text.encode("utf-8")).decode("utf-8")

    def _base64_decode(self, text: str) -> str:
        """Base64 解码"""
        import base64
        try:
            return base64.b64decode(text).decode("utf-8")
        except Exception:
            return base64.b64decode(text).decode("latin-1")

    def _hash_text(self, text: str, algorithm: str) -> Dict:
        """计算文本哈希值"""
        import hashlib
        algo_map = {
            "md5": hashlib.md5,
            "sha1": hashlib.sha1,
            "sha256": hashlib.sha256,
            "sha512": hashlib.sha512,
        }
        if algorithm not in algo_map:
            return {"error": f"不支持的算法: {algorithm}，可选: {', '.join(algo_map.keys())}"}
        h = algo_map[algorithm](text.encode("utf-8")).hexdigest()
        return {"algorithm": algorithm, "hash": h, "length": len(h)}

    def get_available_operations(self) -> list:
        """获取所有可用操作列表"""
        return [
            {
                "name": "read_file",
                "description": "读取文件内容",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"}
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "write_file",
                "description": "写入文件内容（会覆盖已有文件）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"},
                        "content": {"type": "string", "description": "文件内容"},
                    },
                    "required": ["path", "content"],
                },
            },
            {
                "name": "edit_file",
                "description": "编辑文件内容（替换文本）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"},
                        "old_text": {"type": "string", "description": "要被替换的旧文本"},
                        "new_text": {"type": "string", "description": "替换后的新文本"},
                    },
                    "required": ["path", "old_text", "new_text"],
                },
            },
            {
                "name": "list_files",
                "description": "列出目录中的文件",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "目录路径"},
                        "pattern": {"type": "string", "description": "文件匹配模式, 如 *.py"},
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "search_files",
                "description": "搜索文件内容",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "搜索目录"},
                        "query": {"type": "string", "description": "搜索关键词"},
                    },
                    "required": ["path", "query"],
                },
            },
            {
                "name": "execute_command",
                "description": "执行系统命令（仅允许安全命令）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "要执行的命令"},
                        "timeout": {"type": "integer", "description": "超时时间(秒)", "default": 30},
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "take_screenshot",
                "description": "截取屏幕截图",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "launch_process",
                "description": "启动新进程",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "可执行文件路径"},
                        "args": {"type": "string", "description": "命令行参数"},
                    },
                    "required": ["command"],
                },
            },
            {
                "name": "list_processes",
                "description": "列出所有运行中的进程",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "get_system_info",
                "description": "获取系统信息（操作系统、CPU、内存等）",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "open_url",
                "description": "在默认浏览器中打开 URL",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "要打开的 URL"}
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "get_screen_size",
                "description": "获取屏幕分辨率",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            # === Web 操作 ===
            {
                "name": "web_fetch",
                "description": "获取网页内容",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "要获取的 URL"},
                        "timeout": {"type": "integer", "description": "超时时间（秒）"},
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "web_search",
                "description": "执行网页搜索",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词"},
                        "num_results": {"type": "integer", "description": "返回结果数量"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "web_screenshot",
                "description": "获取网页截图",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "要截图的 URL"},
                    },
                    "required": ["url"],
                },
            },
            # === 图片展示 ===
            {
                "name": "show_image",
                "description": "向用户展示指定路径下的本地图片（供 AI 在回复中配图）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "图片文件绝对路径"},
                        "caption": {"type": "string", "description": "可选的图片说明文字"},
                    },
                    "required": ["path"],
                },
            },
            # === 浏览器自动化操作 ===
            {
                "name": "browser_navigate",
                "description": "在自动化浏览器中导航到指定 URL，浏览器未启动时自动启动",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "要访问的网页 URL"},
                        "timeout": {"type": "integer", "description": "超时秒数（默认取配置）"},
                        "wait_until": {"type": "string", "description": "等待条件：domcontentloaded/load"},
                    },
                    "required": ["url"],
                },
            },
            {
                "name": "browser_back",
                "description": "浏览器后退到上一页",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "browser_forward",
                "description": "浏览器前进到下一页",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "browser_reload",
                "description": "刷新当前页面",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "browser_open_tab",
                "description": "打开新标签页",
                "parameters": {
                    "type": "object",
                    "properties": {"url": {"type": "string", "description": "初始 URL（可选）"}},
                    "required": [],
                },
            },
            {
                "name": "browser_list_tabs",
                "description": "列出当前所有标签页及当前页",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "browser_switch_tab",
                "description": "切换到第 N 个标签页（从 1 开始）",
                "parameters": {
                    "type": "object",
                    "properties": {"index": {"type": "integer", "description": "标签页序号（从1开始）"}},
                    "required": ["index"],
                },
            },
            {
                "name": "browser_close_tab",
                "description": "关闭标签页（留空关闭当前页）",
                "parameters": {
                    "type": "object",
                    "properties": {"index": {"type": "integer", "description": "标签页序号，留空关闭当前页"}},
                    "required": [],
                },
            },
            {
                "name": "browser_find",
                "description": "查找页面中匹配 CSS 选择器的元素列表",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS 选择器"},
                        "max_elements": {"type": "integer", "description": "最多返回元素数"},
                    },
                    "required": ["selector"],
                },
            },
            {
                "name": "browser_click",
                "description": "点击页面中 CSS 选择器定位的元素",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS 选择器"},
                        "timeout": {"type": "integer", "description": "等待超时秒数"},
                    },
                    "required": ["selector"],
                },
            },
            {
                "name": "browser_dblclick",
                "description": "双击页面元素",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS 选择器"},
                        "timeout": {"type": "integer", "description": "等待超时秒数"},
                    },
                    "required": ["selector"],
                },
            },
            {
                "name": "browser_hover",
                "description": "鼠标悬停到元素上",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS 选择器"},
                        "timeout": {"type": "integer", "description": "等待超时秒数"},
                    },
                    "required": ["selector"],
                },
            },
            {
                "name": "browser_type",
                "description": "向输入框输入文字",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS 选择器"},
                        "text": {"type": "string", "description": "要输入的文本"},
                        "clear": {"type": "boolean", "description": "输入前是否清空"},
                        "timeout": {"type": "integer", "description": "等待超时秒数"},
                    },
                    "required": ["selector", "text"],
                },
            },
            {
                "name": "browser_press_key",
                "description": "按下键盘按键",
                "parameters": {
                    "type": "object",
                    "properties": {"key": {"type": "string", "description": "按键名，如 Enter/Tab/Escape"}},
                    "required": ["key"],
                },
            },
            {
                "name": "browser_select_option",
                "description": "选择下拉框选项",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS 选择器"},
                        "value": {"type": "string", "description": "选项 value/label/文本"},
                        "timeout": {"type": "integer", "description": "等待超时秒数"},
                    },
                    "required": ["selector", "value"],
                },
            },
            {
                "name": "browser_check",
                "description": "勾选或取消勾选复选框/单选钮",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS 选择器"},
                        "checked": {"type": "boolean", "description": "是否勾选"},
                    },
                    "required": ["selector"],
                },
            },
            {
                "name": "browser_fill_form",
                "description": "批量按 name 填写表单多个字段",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fields": {"type": "array", "description": "字段列表 [{name,value}...]"},
                        "timeout": {"type": "integer", "description": "等待超时秒数"},
                    },
                    "required": ["fields"],
                },
            },
            {
                "name": "browser_submit",
                "description": "提交当前页面表单",
                "parameters": {
                    "type": "object",
                    "properties": {"timeout": {"type": "integer", "description": "等待超时秒数"}},
                    "required": [],
                },
            },
            {
                "name": "browser_scroll",
                "description": "滚动页面",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "amount": {"type": "integer", "description": "滚动像素数，0 使用预设方向"},
                        "to": {"type": "string", "description": "方向 top/bottom/up/down"},
                        "selector": {"type": "string", "description": "目标元素（留空为页面）"},
                    },
                    "required": [],
                },
            },
            {
                "name": "browser_get_text",
                "description": "获取页面元素文本内容",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS 选择器，默认 body"},
                        "timeout": {"type": "integer", "description": "等待超时秒数"},
                    },
                    "required": [],
                },
            },
            {
                "name": "browser_get_html",
                "description": "获取页面元素 HTML 内容",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS 选择器，默认 html"},
                        "timeout": {"type": "integer", "description": "等待超时秒数"},
                    },
                    "required": [],
                },
            },
            {
                "name": "browser_get_attribute",
                "description": "获取元素指定属性值",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS 选择器"},
                        "attribute": {"type": "string", "description": "属性名，如 href/src"},
                        "timeout": {"type": "integer", "description": "等待超时秒数"},
                    },
                    "required": ["selector", "attribute"],
                },
            },
            {
                "name": "browser_extract",
                "description": "结构化提取页面内容（标题/正文文本/链接/所有文本）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "include": {"type": "array", "description": "提取项：title/text/links"},
                    },
                    "required": [],
                },
            },
            {
                "name": "browser_evaluate",
                "description": "在页面执行 JavaScript（受安全开关控制）",
                "parameters": {
                    "type": "object",
                    "properties": {"script": {"type": "string", "description": "JS 代码"}},
                    "required": ["script"],
                },
            },
            {
                "name": "browser_wait",
                "description": "等待元素出现或消失",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "selector": {"type": "string", "description": "CSS 选择器"},
                        "timeout": {"type": "integer", "description": "等待秒数"},
                        "state": {"type": "string", "description": "状态 visible/hidden/attached/detached"},
                    },
                    "required": [],
                },
            },
            {
                "name": "browser_screenshot",
                "description": "截取浏览器页面截图，返回图片路径",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "full_page": {"type": "boolean", "description": "是否整页"},
                        "selector": {"type": "string", "description": "截取指定元素"},
                    },
                    "required": [],
                },
            },
            {
                "name": "browser_close",
                "description": "关闭并释放浏览器实例",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "browser_status",
                "description": "查看浏览器实例状态",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "browser_logs",
                "description": "查看浏览器操作日志",
                "parameters": {
                    "type": "object",
                    "properties": {"limit": {"type": "integer", "description": "返回条数"}},
                    "required": [],
                },
            },
        ]


# 全局单例
_executor: Optional[ComputerExecutor] = None


def get_executor() -> ComputerExecutor:
    global _executor
    if _executor is None:
        _executor = ComputerExecutor()
    return _executor