"""
代码编写 API 路由
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..services.code_writer import get_code_writer

logger = logging.getLogger("ZhiYanWeb.Computer.CodeWriter")
router = APIRouter(prefix="/api/code", tags=["code_writer"])

writer = get_code_writer()


class CreateProjectRequest(BaseModel):
    name: str
    template: str = "python-script"
    description: str = ""
    files: Optional[Dict[str, str]] = None


class FileRequest(BaseModel):
    project_id: str
    filename: str
    content: str


class CodeReviewRequest(BaseModel):
    code: str
    language: str = "python"


@router.post("/project/create")
async def create_project(request: CreateProjectRequest):
    """创建代码项目"""
    try:
        result = writer.create_project(
            name=request.name,
            template=request.template,
            description=request.description,
            files=request.files,
        )
        return {"success": True, "data": result}
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"创建项目失败: {e}")
        return {"success": False, "error": str(e)}


@router.post("/project/add-file")
async def add_file(request: FileRequest):
    """向项目添加文件"""
    try:
        result = writer.add_file(request.project_id, request.filename, request.content)
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/project/edit-file")
async def edit_file(request: FileRequest):
    """编辑项目文件"""
    try:
        result = writer.edit_file(request.project_id, request.filename, request.content)
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/project/read")
async def read_file(
    project_id: str = Query(..., description="项目 ID"),
    filename: str = Query(..., description="文件名"),
):
    """读取项目文件"""
    try:
        result = writer.read_file(project_id, filename)
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/project/list")
async def list_projects():
    """列出所有项目"""
    try:
        projects = writer.list_projects()
        return {"success": True, "data": projects}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/project/{project_id}")
async def get_project(project_id: str):
    """获取项目详情"""
    try:
        project = writer.get_project(project_id)
        return {"success": True, "data": project}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.delete("/project/{project_id}")
async def delete_project(project_id: str):
    """删除项目"""
    try:
        result = writer.delete_project(project_id)
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/templates")
async def get_templates():
    """获取可用模板"""
    try:
        templates = writer.get_available_templates()
        return {"success": True, "data": templates}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/review")
async def review_code(request: CodeReviewRequest):
    """代码审查"""
    try:
        result = writer.review_code(request.code, request.language)
        return {"success": True, "data": result}
    except Exception as e:
        return {"success": False, "error": str(e)}