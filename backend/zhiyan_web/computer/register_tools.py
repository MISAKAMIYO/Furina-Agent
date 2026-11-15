"""
电脑操控工具注册模块

将 ComputerExecutor 的工具、浏览器工具、CMD 工具、Skills 注册到 FunctionToolManager，
使 LLM 能够通过函数调用机制使用所有能力。

在 app.py 初始化时调用 register_computer_tools()。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .services.executor import get_executor
from .services.safety import get_safety_manager, RiskLevel

logger = logging.getLogger("ZhiYanWeb.Computer.Register")


def register_computer_tools() -> None:
    """将所有电脑操控工具注册到 FunctionToolManager"""
    try:
        from zhiyan_web.core import func_tool_manager
    except (ImportError, AttributeError):
        logger.warning("FunctionToolManager 不可用，跳过工具注册")
        return

    if func_tool_manager is None:
        logger.warning("func_tool_manager 为 None，跳过工具注册")
        return

    executor = get_executor()
    safety = get_safety_manager()

    # 计数器
    count = 0

    # ──── 核心工具定义 ────

    def _make_handler(op_name: str):
        """创建指向 executor 的 handler"""
        async def handler(**kwargs: Any) -> str:
            result = await executor.execute(op_name, kwargs)
            if result["success"]:
                data = result.get("result", "")
                if isinstance(data, (dict, list)):
                    return json.dumps(data, ensure_ascii=False, indent=2)
                return str(data)
            else:
                return f"错误: {result.get('error', '未知错误')}"
        return handler

    def _register(name: str, params: list, desc: str, handler, group: str = ""):
        """注册单个工具（group 用于 tool_groups 的场景裁剪）"""
        nonlocal count
        func_tool_manager.add_func(
            name=name,
            func_args=params,
            desc=desc,
            handler=handler,
            group=group,
        )
        count += 1
        logger.debug(f"注册电脑操控工具: {name}")

    # ════════════════════════════════════════════════════
    # 1. 文件操作工具
    # ════════════════════════════════════════════════════

    _register("read_file", [
        {"name": "path", "type": "string", "description": "文件路径，例如 C:\\Users\\...\\file.txt"},
    ], "读取指定文件的内容。支持文本文件和代码文件。", _make_handler("read_file"))

    _register("write_file", [
        {"name": "path", "type": "string", "description": "文件路径"},
        {"name": "content", "type": "string", "description": "要写入的文件内容"},
    ], "写入内容到指定文件。如果文件已存在则会覆盖。", _make_handler("write_file"))

    _register("edit_file", [
        {"name": "path", "type": "string", "description": "文件路径"},
        {"name": "old_text", "type": "string", "description": "要被替换的旧文本"},
        {"name": "new_text", "type": "string", "description": "替换后的新文本"},
    ], "编辑文件内容，替换指定文本。用于修改代码或配置文件。", _make_handler("edit_file"))

    _register("list_files", [
        {"name": "path", "type": "string", "description": "目录路径，留空为当前目录"},
        {"name": "pattern", "type": "string", "description": "文件匹配模式，如 *.py"},
    ], "列出指定目录中的文件和子目录。", _make_handler("list_files"))

    _register("search_files", [
        {"name": "path", "type": "string", "description": "搜索目录路径"},
        {"name": "query", "type": "string", "description": "要搜索的文本"},
    ], "在指定目录中搜索包含特定文本的文件。", _make_handler("search_files"))

    _register("get_file_info", [
        {"name": "path", "type": "string", "description": "文件或目录路径"},
    ], "获取文件或目录的详细信息，包括大小、修改时间等。", _make_handler("get_file_info"))

    _register("copy_file", [
        {"name": "source", "type": "string", "description": "源文件路径"},
        {"name": "destination", "type": "string", "description": "目标路径"},
    ], "复制文件到指定位置。", _make_handler("copy_file"))

    _register("move_file", [
        {"name": "source", "type": "string", "description": "源文件路径"},
        {"name": "destination", "type": "string", "description": "目标路径"},
    ], "移动文件到指定位置。", _make_handler("move_file"))

    _register("rename_file", [
        {"name": "old_path", "type": "string", "description": "原文件路径"},
        {"name": "new_path", "type": "string", "description": "新文件路径"},
    ], "重命名文件或目录。", _make_handler("rename_file"))

    _register("create_directory", [
        {"name": "path", "type": "string", "description": "要创建的目录路径"},
    ], "创建新目录。", _make_handler("create_directory"))

    # ──── 扩展文件工具（大文件按行读、正则检索、指纹、diff、目录树、CSV、压缩包、追加、解压、回收站删除）────
    from .tools import extra_ops as _extra

    def _extra_handler(fn):
        """把同步实现包成异步工具处理器：结果统一转 JSON，异常转「错误: ...」"""
        async def handler(**kwargs: Any) -> str:
            try:
                result = await asyncio.to_thread(fn, **kwargs)
                if isinstance(result, (dict, list)):
                    return json.dumps(result, ensure_ascii=False, indent=2)
                return str(result)
            except Exception as e:
                return f"错误: {e}"
        return handler

    _register("read_file_lines", [
        {"name": "path", "type": "string", "description": "文件路径"},
        {"name": "start", "type": "integer", "description": "起始行号（1 基，默认 1）"},
        {"name": "count", "type": "integer", "description": "读取行数（默认 80，上限 2000）"},
    ], "按行段读取文件，适合大文件。返回起始/结束行、总行数与内容。",
        _extra_handler(_extra.read_file_lines), group="core")

    _register("grep_files", [
        {"name": "pattern", "type": "string", "description": "正则表达式"},
        {"name": "path", "type": "string", "description": "搜索目录或文件，留空为当前目录"},
        {"name": "max_results", "type": "integer", "description": "最多返回多少条命中（默认 40）"},
        {"name": "case_sensitive", "type": "boolean", "description": "是否区分大小写（默认否）"},
    ], "按正则检索文件内容，返回命中的文件、行号与整行文本。比 search_files 更适合查代码。",
        _extra_handler(_extra.grep_files), group="core")

    _register("file_hash", [
        {"name": "path", "type": "string", "description": "文件路径"},
        {"name": "algorithm", "type": "string", "description": "md5 / sha1 / sha256（默认 sha256）"},
    ], "计算文件指纹，用于校验下载或比对文件是否一致。",
        _extra_handler(_extra.file_hash), group="core")

    _register("diff_files", [
        {"name": "path_a", "type": "string", "description": "第一个文件"},
        {"name": "path_b", "type": "string", "description": "第二个文件"},
    ], "比较两个文本文件，返回 unified diff 与新增/删除行数。",
        _extra_handler(_extra.diff_files), group="core")

    _register("list_tree", [
        {"name": "path", "type": "string", "description": "根目录，留空为当前目录"},
        {"name": "max_depth", "type": "integer", "description": "最大深度（默认 2，上限 6）"},
    ], "输出目录树（受限深度与条数），适合先了解项目结构。",
        _extra_handler(_extra.list_tree), group="core")

    _register("csv_preview", [
        {"name": "path", "type": "string", "description": "CSV 文件路径"},
        {"name": "rows", "type": "integer", "description": "预览行数（默认 10）"},
    ], "预览 CSV：表头、前 N 行与总行数。",
        _extra_handler(_extra.csv_preview), group="core")

    _register("zip_list", [
        {"name": "path", "type": "string", "description": "压缩包路径（.zip）"},
    ], "列出 zip 压缩包内的文件清单。",
        _extra_handler(_extra.zip_list), group="core")

    _register("append_file", [
        {"name": "path", "type": "string", "description": "文件路径"},
        {"name": "content", "type": "string", "description": "要追加的内容"},
    ], "在文件末尾追加内容（文件不存在会创建）。写操作，受工作区与审批限制。",
        _extra_handler(_extra.append_file), group="core")

    _register("unzip_file", [
        {"name": "path", "type": "string", "description": "压缩包路径"},
        {"name": "dest", "type": "string", "description": "解压目标目录（留空为压缩包同目录）"},
    ], "解压 zip 到指定目录（自动跳过目录穿越条目）。",
        _extra_handler(_extra.unzip_file), group="core")

    _register("delete_file", [
        {"name": "path", "type": "string", "description": "要删除的文件或目录"},
    ], "把文件/目录移到回收站（可还原），不做硬删除。",
        _extra_handler(_extra.delete_file), group="core")

    # ════════════════════════════════════════════════════
    # 2. 命令执行（通过 CMD 审批系统）
    # ════════════════════════════════════════════════════

    async def _command_prompt_handler(**kwargs: Any) -> str:
        """通过 CMD 审批系统执行命令"""
        command = kwargs.get("command", "")
        description = kwargs.get("description", "")
        timeout = kwargs.get("timeout", 60)
        cwd = kwargs.get("cwd", "")

        if not command:
            return "错误: 缺少命令参数"

        # 安全检查
        check = safety.check_command_safety(command)
        if not check["safe"]:
            return f"错误: {check['message']}"

        try:
            from .services.command_prompt import get_command_prompt_manager
            cpm = get_command_prompt_manager()
            cpm.start()

            # 请求执行
            result = await cpm.request_execution(
                command=command,
                description=description or f"AI 请求执行: {command[:50]}",
                requester="agent",
                timeout=timeout,
                cwd=cwd,
            )

            if not result.get("success"):
                return f"错误: {result.get('error', '执行失败')}"

            session_id = result["session_id"]
            risk_level = result.get("risk_level", "normal")

            # 安全命令自动执行，等待结果
            if risk_level == "safe":
                # 消费流式输出直到命令完成（stream_output 会在结束时发送 done 事件）
                async for _ in cpm.stream_output(session_id):
                    pass
                session_data = cpm.get_session(session_id)
                output = ""
                if session_data:
                    stdout = session_data.get("stdout", [])
                    stderr = session_data.get("stderr", [])
                    output = "\n".join(stdout[-100:])
                    if stderr:
                        output += "\n--- stderr ---\n" + "\n".join(stderr[-50:])
                    if not output:
                        output = "命令已执行（无输出）"
                # 返回结构化结果：session_id 供前端在 cmd 风格终端回放完整输出
                return json.dumps({
                    "status": "completed",
                    "session_id": session_id,
                    "command": command,
                    "output": output,
                }, ensure_ascii=False)

            # 需要用户确认
            return json.dumps({
                "status": "pending_approval",
                "message": "命令需要用户确认",
                "session_id": session_id,
                "command": command,
                "risk_level": risk_level,
            }, ensure_ascii=False)

        except Exception as e:
            logger.error(f"CMD 执行失败: {e}")
            return f"错误: {str(e)}"

    _register("command_prompt_execute", [
        {"name": "command", "type": "string", "description": "要执行的命令"},
        {"name": "description", "type": "string", "description": "命令用途说明"},
        {"name": "timeout", "type": "number", "description": "超时时间（秒），默认 60"},
        {"name": "cwd", "type": "string", "description": "工作目录（可选）"},
    ], "通过命令提示符执行系统命令。安全命令自动执行，危险命令需用户确认。", _command_prompt_handler)

    # 保留旧版 execute_command 用于简单场景
    _register("execute_command", [
        {"name": "command", "type": "string", "description": "要执行的命令"},
        {"name": "timeout", "type": "number", "description": "超时时间（秒），默认 30"},
    ], "执行系统命令。仅允许执行白名单中的安全命令。", _make_handler("execute_command"))

    # ════════════════════════════════════════════════════
    # 3. 系统信息工具
    # ════════════════════════════════════════════════════

    _register("get_system_info", [], "获取系统信息，包括操作系统、CPU、内存、磁盘等。", _make_handler("get_system_info"))

    _register("list_processes", [], "列出当前系统中运行的所有进程。", _make_handler("list_processes"))

    _register("open_url", [
        {"name": "url", "type": "string", "description": "要打开的 URL"},
    ], "在默认浏览器中打开指定 URL。", _make_handler("open_url"))

    _register("open_application", [
        {"name": "path", "type": "string", "description": "软件/应用/文档/文件夹的路径，例如 C:\\Program Files\\app\\app.exe 或 C:\\Users\\...\\myapp.exe"},
        {"name": "args", "type": "string", "description": "可选，附加给可执行程序的启动参数（仅对可执行文件生效）"},
    ], "打开本地软件或应用程序。接收软件路径参数，先验证路径存在与可执行性，再以系统默认方式启动该软件（支持 .exe/.bat/.cmd/.lnk 等，目录则直接打开文件夹），最后返回执行结果。当用户要求『打开某个软件/程序/应用/文件夹』时调用此工具。", _make_handler("open_application"))

    # ════════════════════════════════════════════════════
    # 4. 截图工具
    # ════════════════════════════════════════════════════

    _register("take_screenshot", [], "截取当前屏幕截图。返回截图文件路径。", _make_handler("take_screenshot"))

    # ════════════════════════════════════════════════════
    # 4.2 截屏录屏工具（对接 screencap 服务，支持全屏/窗口/录屏）
    # ════════════════════════════════════════════════════

    async def _capture_screen_handler(**kwargs: Any) -> str:
        """截屏（支持全屏/活动窗口）"""
        try:
            from zhiyan_web.screencap import service as screencap_service
            mode = str(kwargs.get("mode", "full") or "full").lower()
            if mode not in ("full", "window"):
                mode = "full"
            result = screencap_service.take_screenshot(mode=mode)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    async def _start_screen_recording_handler(**kwargs: Any) -> str:
        """开始录屏"""
        try:
            from zhiyan_web.screencap import service as screencap_service
            result = screencap_service.start_recording(
                fps=kwargs.get("fps"),
                audio=kwargs.get("audio"),
                quality=kwargs.get("quality"),
            )
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    async def _stop_screen_recording_handler(**kwargs: Any) -> str:
        """停止录屏"""
        try:
            from zhiyan_web.screencap import service as screencap_service
            result = screencap_service.stop_recording()
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    async def _screen_recording_status_handler(**kwargs: Any) -> str:
        """获取录屏状态"""
        try:
            from zhiyan_web.screencap import service as screencap_service
            result = screencap_service.get_recording_status()
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    _register("capture_screen", [
        {"name": "mode", "type": "string", "description": "截图模式: full 全屏 / window 当前活动窗口，默认 full"},
    ], "截取当前电脑屏幕（全屏或活动窗口），保存截图并返回可访问的图片地址。当用户要求『截图/截屏/截个图/把屏幕截下来』时调用此工具。", _capture_screen_handler)

    _register("start_screen_recording", [
        {"name": "fps", "type": "number", "description": "帧率，默认 20"},
        {"name": "audio", "type": "boolean", "description": "是否录制系统声音（需要虚拟音频设备），默认关"},
        {"name": "quality", "type": "string", "description": "x264 预设: ultrafast/superfast/veryfast/faster/fast/medium"},
    ], "开始屏幕录制（录制整个屏幕为视频）。当用户要求『录屏/开始录屏/录制屏幕/录个视频』时调用此工具。", _start_screen_recording_handler)

    _register("stop_screen_recording", [], "停止当前屏幕录制并保存视频文件。当用户要求『停止录屏/结束录屏/保存录像』时调用此工具。", _stop_screen_recording_handler)

    _register("get_screen_recording_status", [], "查询当前屏幕录制状态（是否正在录制、已录制时长等）。", _screen_recording_status_handler)

    # ════════════════════════════════════════════════════
    # 4.5 图片展示工具
    # ════════════════════════════════════════════════════

    async def _show_image_handler(**kwargs: Any) -> str:
        """展示本地图片给用户（返回带可访问 url 的 JSON，前端据此渲染图片）"""
        try:
            from .tools import image_ops
            result = image_ops.show_image(**kwargs)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    _register("show_image", [
        {"name": "path", "type": "string", "description": "图片文件的本地绝对路径"},
        {"name": "caption", "type": "string", "description": "可选的图片说明文字"},
    ], "向用户展示一张指定路径下的本地图片。当你需要让用户看到某个图片文件（如生成的图、截图、用户上传的图、指定路径下的图）时调用此工具，图片会直接显示在聊天界面上。支持 png/jpg/jpeg/gif/webp/bmp/svg 等常见格式。", _show_image_handler)

    # ════════════════════════════════════════════════════
    # 5. 桌面操作工具
    # ════════════════════════════════════════════════════

    _register("get_mouse_position", [], "获取鼠标当前屏幕坐标位置。", _make_handler("get_mouse_position"))

    _register("move_mouse", [
        {"name": "x", "type": "number", "description": "目标 X 坐标"},
        {"name": "y", "type": "number", "description": "目标 Y 坐标"},
    ], "移动鼠标到指定屏幕坐标位置。", _make_handler("move_mouse"))

    _register("click_mouse", [
        {"name": "button", "type": "string", "description": "要点击的鼠标按钮: left/right/middle"},
    ], "点击鼠标按钮。", _make_handler("click_mouse"))

    _register("type_text", [
        {"name": "text", "type": "string", "description": "要输入的文本内容"},
    ], "模拟键盘输入文本。", _make_handler("type_text"))

    _register("press_key", [
        {"name": "key", "type": "string", "description": "按键名称，如 enter, tab, escape, f5, ctrl+c"},
    ], "模拟按下键盘按键，支持组合键。", _make_handler("press_key"))

    _register("get_screen_size", [], "获取屏幕分辨率。", _make_handler("get_screen_size"))

    # ════════════════════════════════════════════════════
    # 6. Web 操作工具
    # ════════════════════════════════════════════════════

    _register("web_fetch", [
        {"name": "url", "type": "string", "description": "要获取的网页 URL"},
        {"name": "timeout", "type": "number", "description": "超时时间（秒），默认 15"},
    ], "获取网页内容。用于读取网页信息、文档等。", _make_handler("web_fetch"))

    _register("web_search", [
        {"name": "query", "type": "string", "description": "搜索关键词"},
        {"name": "num_results", "type": "number", "description": "返回结果数量，默认 5"},
    ], "执行网页搜索。用于查找网络上的信息。", _make_handler("web_search"))

    # ════════════════════════════════════════════════════
    # 7. Playwright 浏览器自动化工具（全量注册，统一下发 browser_execute）
    # ════════════════════════════════════════════════════

    def _make_browser_handler(op: str):
        """创建浏览器操作 handler，在闭包内捕获操作名，通过 browser_execute 分发"""
        async def handler(**kwargs: Any) -> str:
            try:
                from .tools.browser_ops import browser_execute
                result = await browser_execute(op, **kwargs)
            except Exception as e:
                return f"错误: {str(e)}"
            if isinstance(result, dict):
                # 截图只返回路径与缩略图，不回传超大 base64 给 LLM
                if isinstance(result.get("data"), dict) and "base64_full" in result["data"]:
                    result["data"] = {k: v for k, v in result["data"].items() if k != "base64_full"}
                return json.dumps(result, ensure_ascii=False, indent=2)
            return str(result)
        return handler

    # 每条：操作名 / 参数列表 / 描述
    _browser_tools = [
        ("browser_navigate", [
            {"name": "url", "type": "string", "description": "要访问的网页 URL"},
            {"name": "timeout", "type": "number", "description": "超时时间（秒），默认取配置"},
            {"name": "wait_until", "type": "string", "description": "等待条件: domcontentloaded/load"},
        ], "在自动化浏览器中导航到指定网页。浏览器未启动时会自动启动。"),
        ("browser_back", [], "浏览器后退到上一页。"),
        ("browser_forward", [], "浏览器前进到下一页。"),
        ("browser_reload", [], "刷新当前浏览器页面。"),
        ("browser_open_tab", [
            {"name": "url", "type": "string", "description": "新标签页初始 URL（可留空）"},
        ], "打开一个新的浏览器标签页。"),
        ("browser_list_tabs", [], "列出当前所有浏览器标签页及当前页面。"),
        ("browser_switch_tab", [
            {"name": "index", "type": "number", "description": "要切换到的标签页序号（从1开始）"},
        ], "切换到指定的浏览器标签页。"),
        ("browser_close_tab", [
            {"name": "index", "type": "number", "description": "要关闭的标签页序号（留空关闭当前页）"},
        ], "关闭指定的浏览器标签页。"),
        ("browser_find", [
            {"name": "selector", "type": "string", "description": "CSS 选择器"},
            {"name": "max_elements", "type": "number", "description": "最多返回的元素数量"},
        ], "查找页面上匹配指定 CSS 选择器的元素列表，用于定位可交互元素。"),
        ("browser_click", [
            {"name": "selector", "type": "string", "description": "CSS 选择器，如 #my-button, .btn-primary"},
            {"name": "timeout", "type": "number", "description": "等待超时（秒），默认 10"},
        ], "点击浏览器页面中由 CSS 选择器定位的元素。"),
        ("browser_dblclick", [
            {"name": "selector", "type": "string", "description": "CSS 选择器"},
            {"name": "timeout", "type": "number", "description": "等待超时（秒），默认 10"},
        ], "双击浏览器页面中的元素。"),
        ("browser_hover", [
            {"name": "selector", "type": "string", "description": "CSS 选择器"},
            {"name": "timeout", "type": "number", "description": "等待超时（秒），默认 10"},
        ], "将鼠标悬停到页面元素上。"),
        ("browser_type", [
            {"name": "selector", "type": "string", "description": "CSS 选择器，定位输入框"},
            {"name": "text", "type": "string", "description": "要输入的文字"},
            {"name": "clear", "type": "boolean", "description": "输入前是否清空现有内容"},
            {"name": "timeout", "type": "number", "description": "等待超时（秒），默认 10"},
        ], "在浏览器页面的输入框中输入文字。"),
        ("browser_press_key", [
            {"name": "key", "type": "string", "description": "按键名，如 Enter, Tab, Escape, ArrowDown"},
        ], "在浏览器页面中按下指定的键盘按键。"),
        ("browser_select_option", [
            {"name": "selector", "type": "string", "description": "下拉框 CSS 选择器"},
            {"name": "value", "type": "string", "description": "要选的选项值或显示文本"},
            {"name": "timeout", "type": "number", "description": "等待超时（秒），默认 10"},
        ], "选择浏览器页面中下拉框的选项。"),
        ("browser_check", [
            {"name": "selector", "type": "string", "description": "复选框/单选钮 CSS 选择器"},
            {"name": "checked", "type": "boolean", "description": "是否勾选（默认勾选）"},
        ], "勾选或取消勾选页面中的复选框/单选按钮。"),
        ("browser_fill_form", [
            {"name": "fields", "type": "string", "description": "JSON 数组，如 [{\"name\":\"email\",\"value\":\"a@b.com\"}]，按表单控件 name 批量填写"},
            {"name": "timeout", "type": "number", "description": "等待超时（秒），默认 10"},
        ], "按 name 批量填写表单中的多个字段。"),
        ("browser_submit", [
            {"name": "timeout", "type": "number", "description": "等待超时（秒），默认 10"},
        ], "提交当前浏览器页面中的表单。"),
        ("browser_scroll", [
            {"name": "amount", "type": "number", "description": "滚动像素数（0 则按方向预设滚动）"},
            {"name": "to", "type": "string", "description": "滚动方向: top/bottom/up/down"},
            {"name": "selector", "type": "string", "description": "要滚动的目标元素（留空为页面）"},
        ], "滚动浏览器页面到顶部/底部/指定方向或元素。"),
        ("browser_get_text", [
            {"name": "selector", "type": "string", "description": "CSS 选择器，默认获取 body 文本"},
            {"name": "timeout", "type": "number", "description": "等待超时（秒），默认 10"},
        ], "获取浏览器页面中指定元素的文本内容。"),
        ("browser_get_html", [
            {"name": "selector", "type": "string", "description": "CSS 选择器，默认获取整页 HTML"},
            {"name": "timeout", "type": "number", "description": "等待超时（秒），默认 10"},
        ], "获取浏览器页面中指定元素的 HTML 内容。"),
        ("browser_get_attribute", [
            {"name": "selector", "type": "string", "description": "CSS 选择器"},
            {"name": "attribute", "type": "string", "description": "属性名，如 href, src"},
            {"name": "timeout", "type": "number", "description": "等待超时（秒），默认 10"},
        ], "获取页面元素的指定属性值。"),
        ("browser_extract", [
            {"name": "include", "type": "string", "description": "需提取的内容，逗号分隔: title,text,links"},
        ], "结构化提取当前页面内容：标题、正文文本、链接等。"),
        ("browser_evaluate", [
            {"name": "script", "type": "string", "description": "要执行的 JavaScript 代码"},
        ], "在浏览器页面中执行 JavaScript，获取页面数据或操作页面（受安全配置控制）。"),
        ("browser_wait", [
            {"name": "selector", "type": "string", "description": "CSS 选择器"},
            {"name": "timeout", "type": "number", "description": "等待秒数，默认 10"},
            {"name": "state", "type": "string", "description": "状态: visible/hidden/attached/detached"},
        ], "等待页面中的元素出现或达到指定状态。"),
        ("browser_screenshot", [
            {"name": "full_page", "type": "boolean", "description": "是否截取完整页面（含滚动部分）"},
            {"name": "selector", "type": "string", "description": "只截取指定元素"},
        ], "截取当前浏览器页面的截图，返回图片路径与缩略图。"),
        ("browser_close", [], "关闭并释放浏览器实例，回收资源。"),
        ("browser_status", [], "查看当前浏览器实例的启动与运行状态。"),
        ("browser_logs", [
            {"name": "limit", "type": "number", "description": "返回的日志条数（默认 50）"},
        ], "查看浏览器自动化操作日志。"),
    ]

    for _bname, _bparams, _bdesc in _browser_tools:
        _register(_bname, _bparams, _bdesc, _make_browser_handler(_bname))

    # ════════════════════════════════════════════════════
    # 8. Skills 工具
    # ════════════════════════════════════════════════════

    # 注册通用的技能执行工具
    async def _skill_execute_handler(**kwargs: Any) -> str:
        """执行技能"""
        try:
            from zhiyan_web.skills import get_skill_manager
            sm = get_skill_manager()
            skill_name = kwargs.get("skill_name", "")
            if not skill_name:
                # 没有 skill_name 时返回技能列表
                skills = sm.get_skill_list()
                return json.dumps(skills, ensure_ascii=False, indent=2)
            # 移除技能名，其余作为参数
            skill_params = {k: v for k, v in kwargs.items() if k != "skill_name"}
            return await sm.execute_skill(skill_name, **skill_params)
        except Exception as e:
            return f"错误: {str(e)}"

    _register("execute_skill", [
        {"name": "skill_name", "type": "string", "description": "要执行的技能名称（不传则列出所有技能）"},
    ], "执行已安装的技能（Skill）。技能是预定义的自动化功能模块。", _skill_execute_handler,
        group="skills")

    # 获取技能列表
    _register("get_skill_list", [], "获取所有已安装的技能列表，包括名称、描述、版本信息。", _skill_execute_handler,
              group="skills")

    # 按需加载技能正文（渐进式披露：系统提示词只给技能清单，正文用这个工具拉）
    async def _skill_load_handler(**kwargs: Any) -> str:
        """加载技能：指令型返回完整正文，函数型返回参数说明。"""
        try:
            from zhiyan_web.skills.disclosure import load_skill
            result = await load_skill(str(kwargs.get("skill_name") or ""))
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"错误: {str(e)}"

    _register("load_skill", [
        {"name": "skill_name", "type": "string", "description": "要加载的技能名称"},
    ], ("加载指定技能的完整内容。指令型技能返回分步说明（照做即可），"
        "函数型技能返回参数说明（再按名称调用该工具执行）。"),
        _skill_load_handler, group="skills")

    # 注册各个技能为独立工具（由 skill_manager 完成）
    try:
        from zhiyan_web.skills import get_skill_manager
        sm = get_skill_manager()
        skill_count = sm.register_to_func_tool_manager()
        logger.info(f"已注册 {skill_count} 个技能工具")
    except Exception as e:
        logger.warning(f"注册技能工具失败: {e}")

    # ════════════════════════════════════════════════════
    # 8.2 子代理工具（spawn_subagent / delegate_to_agent / list_subagents）
    # ════════════════════════════════════════════════════
    try:
        from zhiyan_web.subagent import register_subagent_tools
        sub_count = register_subagent_tools(func_tool_manager)
        if sub_count:
            count += sub_count
    except Exception as e:
        logger.warning(f"注册子代理工具失败: {e}")

    # ════════════════════════════════════════════════════
    # 9. 记忆写入工具
    # ════════════════════════════════════════════════════

    async def _memory_write_handler(**kwargs: Any) -> str:
        """处理 AI 主动写入记忆"""
        try:
            from zhiyan_web.memory.auto_memory_writer import get_auto_memory_writer
            writer = get_auto_memory_writer()
            tags_raw = kwargs.get("tags", "")
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if isinstance(tags_raw, str) else []
            result = writer.write_memory(
                content=kwargs.get("content", ""),
                memory_type=kwargs.get("memory_type"),
                importance=kwargs.get("importance"),
                tags=tags,
            )
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    _register("write_memory", [
        {"name": "content", "type": "string", "description": "要记忆的内容，描述用户的事实、偏好、事件等"},
        {"name": "memory_type", "type": "string", "description": "记忆类型: fact/preference/event/concept（留空自动检测）"},
        {"name": "importance", "type": "number", "description": "重要性 0-1（留空自动计算）"},
        {"name": "tags", "type": "string", "description": "标签，逗号分隔"},
    ], "记住用户的重要信息。当你发现用户提到个人喜好、重要事实、习惯或关键信息时，调用此工具将信息写入记忆系统。注意：不要频繁调用，每条重要信息只记录一次。", _memory_write_handler)

    # ════════════════════════════════════════════════════
    # 10. 记忆回顾/总结工具
    # ════════════════════════════════════════════════════

    async def _memory_review_handler(**kwargs: Any) -> str:
        """查看待总结的近期记忆概况"""
        try:
            from zhiyan_web.memory.auto_memory_writer import get_auto_memory_writer
            writer = get_auto_memory_writer()
            result = writer.review_unsummarized()
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    _register("review_memory", [
        {"name": "query", "type": "string", "description": "要回顾的记忆关键词（留空查看所有待总结的记忆）"},
    ], "查看近期记忆概况。当你需要了解有哪些记忆尚未总结时调用此工具。", _memory_review_handler)

    async def _memory_summarize_handler(**kwargs: Any) -> str:
        """手动触发记忆总结"""
        try:
            from zhiyan_web.memory.auto_memory_writer import get_auto_memory_writer
            writer = get_auto_memory_writer()
            result = writer.summarize_memories()
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    _register("summarize_memories", [], "手动触发记忆总结，将近期积累的记忆进行归类总结并写入汇总记忆。", _memory_summarize_handler)

    # ════════════════════════════════════════════════════
    # 11. 知识库工具
    # ════════════════════════════════════════════════════

    async def _knowledge_search_handler(**kwargs: Any) -> str:
        """搜索知识库"""
        try:
            from zhiyan_web.knowledge.knowledge import get_knowledge_engine
            engine = get_knowledge_engine()
            results = engine.search(kwargs.get("query", ""), limit=kwargs.get("limit", 10))
            return json.dumps({"success": True, "data": results}, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    _register("search_knowledge", [
        {"name": "query", "type": "string", "description": "搜索关键词"},
        {"name": "limit", "type": "number", "description": "返回数量（默认10）"},
    ], "搜索知识库中的知识条目。当你需要查找特定信息时调用此工具。", _knowledge_search_handler)

    async def _knowledge_add_handler(**kwargs: Any) -> str:
        """向知识库添加知识"""
        try:
            from zhiyan_web.knowledge.knowledge import get_knowledge_engine
            engine = get_knowledge_engine()
            tags_raw = kwargs.get("tags", "")
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if isinstance(tags_raw, str) else []
            result = engine.add(
                content=kwargs.get("content", ""),
                tags=tags,
                priority=kwargs.get("priority", 5),
                source="ai",
            )
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    _register("add_knowledge", [
        {"name": "content", "type": "string", "description": "知识内容"},
        {"name": "tags", "type": "string", "description": "标签，逗号分隔"},
        {"name": "priority", "type": "number", "description": "优先级 1-10（默认5）"},
    ], "向知识库添加新的知识条目。", _knowledge_add_handler)

    logger.info(f"成功注册 {count} 个电脑操控工具到 FunctionToolManager")

    # ════════════════════════════════════════════════════
    # 12. 桌面高级操作工具
    # ════════════════════════════════════════════════════

    _register("scroll_mouse", [
        {"name": "clicks", "type": "number", "description": "滚动次数，正数向上，负数向下，默认1"},
    ], "滚动鼠标滚轮。", _make_handler("scroll_mouse"))

    _register("double_click", [], "双击鼠标左键。", _make_handler("double_click"))

    _register("drag_mouse", [
        {"name": "x", "type": "number", "description": "目标 X 坐标"},
        {"name": "y", "type": "number", "description": "目标 Y 坐标"},
    ], "拖拽鼠标到指定位置。", _make_handler("drag_mouse"))

    # ════════════════════════════════════════════════════
    # 13. 剪贴板工具
    # ════════════════════════════════════════════════════

    _register("get_clipboard", [], "获取剪贴板中的文本内容。", _make_handler("get_clipboard"))

    _register("set_clipboard", [
        {"name": "text", "type": "string", "description": "要写入剪贴板的文本"},
    ], "将文本写入剪贴板。", _make_handler("set_clipboard"))

    # ════════════════════════════════════════════════════
    # 14. 窗口管理工具
    # ════════════════════════════════════════════════════

    _register("list_windows", [], "列出所有打开的窗口及其标题。", _make_handler("list_windows"))

    _register("activate_window", [
        {"name": "title", "type": "string", "description": "窗口标题（支持模糊匹配）"},
    ], "激活并聚焦到指定窗口。", _make_handler("activate_window"))

    _register("minimize_window", [
        {"name": "title", "type": "string", "description": "窗口标题（支持模糊匹配）"},
    ], "最小化指定窗口。", _make_handler("minimize_window"))

    _register("close_window", [
        {"name": "title", "type": "string", "description": "窗口标题（支持模糊匹配）"},
    ], "关闭指定窗口。", _make_handler("close_window"))

    # ════════════════════════════════════════════════════
    # 15. 数据处理工具
    # ════════════════════════════════════════════════════

    _register("calculate", [
        {"name": "expression", "type": "string", "description": "数学表达式，如 1+2*3"},
    ], "执行数学计算。支持 +, -, *, /, (, ), %, ^ 等运算符。", _make_handler("calculate"))

    _register("format_json", [
        {"name": "text", "type": "string", "description": "要格式化的 JSON 字符串"},
    ], "格式化 JSON 字符串，使其更易读。", _make_handler("format_json"))

    _register("extract_json", [
        {"name": "text", "type": "string", "description": "包含 JSON 的文本"},
    ], "从文本中提取 JSON 对象。", _make_handler("extract_json"))

    _register("count_tokens", [
        {"name": "text", "type": "string", "description": "要估算 token 数的文本"},
    ], "估算文本的 token 数量（中文字符÷1.5 + 其他字符÷4）。", _make_handler("count_tokens"))

    _register("base64_encode", [
        {"name": "text", "type": "string", "description": "要编码的文本"},
    ], "将文本进行 Base64 编码。", _make_handler("base64_encode"))

    _register("base64_decode", [
        {"name": "text", "type": "string", "description": "Base64 编码的文本"},
    ], "将 Base64 编码的文本解码为原始文本。", _make_handler("base64_decode"))

    _register("generate_uuid", [], "生成一个 UUID v4 唯一标识符。", _make_handler("generate_uuid"))

    _register("get_current_time", [], "获取当前时间，包含日期时间戳和时区信息。", _make_handler("get_current_time"))

    _register("hash_text", [
        {"name": "text", "type": "string", "description": "要计算哈希的文本"},
        {"name": "algorithm", "type": "string", "description": "哈希算法: md5/sha1/sha256/sha512（默认md5）"},
    ], "计算文本的哈希值。", _make_handler("hash_text"))

    # ════════════════════════════════════════════════════
    # 16. 工具发现工具
    # ════════════════════════════════════════════════════

    async def _list_all_tools_handler(**kwargs: Any) -> str:
        """列出所有已注册的工具"""
        try:
            from zhiyan_web.core import func_tool_manager
            if func_tool_manager is None:
                return json.dumps({"success": False, "error": "func_tool_manager 不可用"})
            tools = func_tool_manager.list_funcs()
            tool_list = []
            for tool in tools:
                if hasattr(tool, 'to_dict'):
                    d = tool.to_dict()
                else:
                    d = {"name": getattr(tool, 'name', '?'), "description": getattr(tool, 'description', '')}
                tool_list.append(d)
            return json.dumps({"success": True, "total": len(tool_list), "tools": tool_list}, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    _register("list_available_tools", [], "列出所有可用的工具及其描述，帮助了解当前系统有哪些能力。", _list_all_tools_handler)