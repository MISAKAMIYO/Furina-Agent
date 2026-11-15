"""
电脑操控 API 路由
提供电脑操控的 REST API 接口
"""
from __future__ import annotations

import logging
from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..services.executor import get_executor
from ..services.audit import get_audit_logger

logger = logging.getLogger("ZhiYanWeb.Computer.Router")

router = APIRouter(prefix="/api/computer", tags=["computer"])

executor = get_executor()
audit_logger = get_audit_logger()


class ExecuteRequest(BaseModel):
    operation: str
    params: Dict[str, Any] = {}
    session_id: str = "default"
    user_id: str = "agent"


class FileOperation(BaseModel):
    path: str
    content: Optional[str] = None
    pattern: Optional[str] = "*"
    query: Optional[str] = None
    old_text: Optional[str] = None
    new_text: Optional[str] = None


@router.post("/execute")
async def execute_operation(request: ExecuteRequest):
    """执行电脑操控操作"""
    try:
        result = await executor.execute(
            operation=request.operation,
            params=request.params,
            session_id=request.session_id,
            user_id=request.user_id,
        )
        return result
    except Exception as e:
        logger.error(f"执行操作失败: {e}")
        return {"success": False, "error": str(e)}


@router.post("/file/read")
async def read_file(data: FileOperation):
    """读取文件"""
    try:
        result = await executor.execute("read_file", {"path": data.path})
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/file/write")
async def write_file(data: FileOperation):
    """写入文件"""
    if not data.content:
        return {"success": False, "error": "content 不能为空"}
    try:
        result = await executor.execute("write_file", {"path": data.path, "content": data.content})
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/file/edit")
async def edit_file(data: FileOperation):
    """编辑文件"""
    if not data.old_text or not data.new_text:
        return {"success": False, "error": "old_text 和 new_text 不能为空"}
    try:
        result = await executor.execute(
            "edit_file",
            {"path": data.path, "old_text": data.old_text, "new_text": data.new_text},
        )
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/file/info")
async def get_file_info(path: str = Query(..., description="文件路径")):
    """获取文件信息"""
    try:
        result = await executor.execute("get_file_info", {"path": path})
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/file/list")
async def list_files(
    path: str = Query(".", description="目录路径"),
    pattern: str = Query("*", description="匹配模式"),
):
    """列出文件"""
    try:
        result = await executor.execute("list_files", {"path": path, "pattern": pattern})
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/command/execute")
async def execute_command(
    command: str = Query(..., description="要执行的命令"),
    timeout: int = Query(30, description="超时时间(秒)"),
):
    """执行系统命令"""
    try:
        result = await executor.execute("execute_command", {"command": command, "timeout": timeout})
        return result
    except PermissionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/system/info")
async def system_info():
    """获取系统信息"""
    try:
        result = await executor.execute("get_system_info", {})
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/process/list")
async def list_processes():
    """列出进程"""
    try:
        result = await executor.execute("list_processes", {})
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/screenshot")
async def take_screenshot():
    """截取屏幕截图"""
    try:
        result = await executor.execute("take_screenshot", {})
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════
# 浏览器自动化 API（对接 browser_ops）
# ════════════════════════════════════════════════════

class BrowserRequest(BaseModel):
    op: str                                 # 操作名（browser_navigate / navigate 均可）
    params: Dict[str, Any] = {}


@router.post("/browser/execute")
async def browser_execute(request: BrowserRequest):
    """执行浏览器自动化操作"""
    try:
        from ..tools import browser_ops
        result = await browser_ops.browser_execute(request.op, **request.params)
        return {"success": result.get("success", True), "data": result}
    except Exception as e:
        logger.error(f"浏览器操作失败 {request.op}: {e}")
        return {"success": False, "error": str(e)}


@router.get("/browser/status")
async def browser_status():
    """查看浏览器实例状态"""
    try:
        from ..tools import browser_ops
        result = await browser_ops.get_browser_status()
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/browser/logs")
async def browser_logs(limit: int = Query(50, description="返回条数")):
    """查看浏览器操作日志"""
    try:
        from ..tools import browser_ops
        result = await browser_ops.get_browser_logs(limit)
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/browser/capabilities")
async def browser_capabilities():
    """查看浏览器工具能力清单"""
    try:
        from ..tools import browser_ops
        ops = browser_ops.available_operations()
        return {
            "success": True,
            "data": {
                "available": browser_ops._HAS_PLAYWRIGHT,
                "operations": ops,
                "count": len(ops),
            },
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/screenshot/view")
async def view_screenshot(path: str = Query(..., description="截图文件路径")):
    """查看截图（返回文件）"""
    import os
    if not os.path.exists(path):
        return {"success": False, "error": "截图文件不存在"}
    from fastapi.responses import FileResponse
    return FileResponse(path, media_type="image/png")


@router.get("/image/view")
async def view_image(path: str = Query(..., description="图片文件路径")):
    """查看/展示指定路径的图片（供 AI 调用 show_image 后在聊天界面渲染）"""
    import os
    try:
        from ..tools import image_ops
    except Exception as e:
        return {"success": False, "error": str(e)}
    abspath = image_ops._resolve_view_path(path)
    if not abspath:
        return {"success": False, "error": "图片文件不存在或不是受支持的图片格式"}
    ext = os.path.splitext(abspath)[1].lower()
    mime = image_ops._MIME_MAP.get(ext, "image/png")
    from fastapi.responses import FileResponse
    return FileResponse(abspath, media_type=mime)


@router.get("/tools")
async def get_available_tools():
    """获取所有可用工具列表"""
    try:
        tools = executor.get_available_operations()
        return {"success": True, "data": tools}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/audit/logs")
async def get_audit_logs(
    operation: Optional[str] = Query(None, description="操作类型"),
    hours: int = Query(24, description="查询最近多少小时"),
    limit: int = Query(100, description="返回条数"),
    only_dangerous: bool = Query(False, description="只显示危险操作"),
):
    """查询审计日志"""
    try:
        logs = audit_logger.query(
            operation=operation,
            hours=hours,
            limit=limit,
            only_dangerous=only_dangerous,
        )
        return {"success": True, "data": logs}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/audit/stats")
async def get_audit_stats(hours: int = Query(24, description="统计最近多少小时")):
    """获取审计统计"""
    try:
        stats = audit_logger.get_stats(hours=hours)
        return {"success": True, "data": stats}
    except Exception as e:
        return {"success": False, "error": str(e)}