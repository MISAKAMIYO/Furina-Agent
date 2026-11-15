"""
代码编辑器路由模块
提供代码编辑器相关的 REST API 端点

端点:
- GET /api/editor/projects - 列出项目
- POST /api/editor/projects - 创建项目
- DELETE /api/editor/projects/{name} - 删除项目
- GET /api/editor/files - 列出文件
- GET /api/editor/files/read - 读取文件
- POST /api/editor/files/save - 保存文件
- POST /api/editor/files/create - 创建文件
- POST /api/editor/files/folder - 创建目录
- POST /api/editor/files/delete - 删除文件
- POST /api/editor/files/rename - 重命名
- POST /api/editor/execute - 执行代码
- GET /api/editor/templates - 列出模板
- GET /api/editor/templates/{id} - 获取模板
- GET /api/editor/languages - 列出支持的语言
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Body
from pydantic import BaseModel
from typing import Optional, Dict, Any
import os

from zhiyan_web.code_editor.services.editor_service import (
    list_projects,
    create_project,
    delete_project,
    list_files,
    list_files_tree,
    read_file,
    save_file,
    create_file,
    create_folder,
    delete_file,
    rename_item,
    move_item,
    execute_code,
    list_templates,
    get_template,
    list_system_drives,
    LANGUAGE_EXEC_MAP,
    MONACO_LANG_MAP,
)

router = APIRouter(prefix="/api/editor", tags=["code_editor"])


# ──── 请求模型 ────

class CreateProjectRequest(BaseModel):
    name: str
    description: str = ""
    language: str = ""


class SaveFileRequest(BaseModel):
    project: str
    path: str
    content: str


class CreateFileRequest(BaseModel):
    project: str
    path: str
    content: str = ""
    template: Optional[str] = None


class CreateFolderRequest(BaseModel):
    project: str
    path: str


class DeleteFileRequest(BaseModel):
    project: str
    path: str


class RenameRequest(BaseModel):
    project: str
    path: str
    new_name: str


class MoveRequest(BaseModel):
    project: str
    path: str
    target_dir: str


class ExecuteRequest(BaseModel):
    project: str
    path: str = ""
    code: Optional[str] = None


# ──── 项目 API ────

@router.get("/projects")
async def api_list_projects():
    """列出所有项目"""
    return list_projects()


@router.post("/projects")
async def api_create_project(request: CreateProjectRequest):
    """创建新项目"""
    result = create_project(request.name, request.description, request.language)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "创建失败"))
    return result


@router.delete("/projects/{name}")
async def api_delete_project(name: str):
    """删除项目"""
    result = delete_project(name)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "项目不存在"))
    return result


@router.get("/drives")
async def api_list_drives():
    """获取系统可用根路径（Windows 盘符 / Unix 根目录），用于本地目录浏览"""
    return {
        "success": True,
        "data": {
            "drives": list_system_drives(),
            "is_windows": os.name == "nt",
        }
    }


# ──── 文件操作 API ────

@router.get("/files")
async def api_list_files(
    project: str = Query(..., description="项目名称"),
    path: str = Query("", description="目录路径，空为根目录"),
):
    """列出项目文件"""
    return list_files(project, path)


@router.get("/files/tree")
async def api_list_files_tree(
    project: str = Query(..., description="项目名称"),
    max_depth: int = Query(20, description="最大递归深度"),
):
    """递归列出整个项目文件树（用于搜索与统计）"""
    return list_files_tree(project, max_depth)


@router.get("/files/read")
async def api_read_file(
    project: str = Query(..., description="项目名称"),
    path: str = Query(..., description="文件路径"),
):
    """读取文件内容"""
    return read_file(project, path)


@router.get("/files/raw")
async def api_raw_file(
    project: str = Query(..., description="项目名称"),
    path: str = Query(..., description="文件路径"),
):
    """以原始字节返回文件内容（用于图片等二进制预览）"""
    import mimetypes
    from fastapi.responses import FileResponse
    from zhiyan_web.code_editor.services.editor_service import resolve_path
    try:
        filepath = resolve_path(project, path)
        if not os.path.isfile(filepath):
            raise HTTPException(status_code=404, detail="文件不存在")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    mime = mimetypes.guess_type(filepath)[0] or "application/octet-stream"
    return FileResponse(filepath, media_type=mime)


@router.post("/files/save")
async def api_save_file(request: SaveFileRequest):
    """保存文件"""
    return save_file(request.project, request.path, request.content)


@router.post("/files/create")
async def api_create_file(request: CreateFileRequest):
    """创建新文件"""
    if request.template:
        tmpl = get_template(request.template)
        if tmpl.get("success"):
            request.content = tmpl["data"]["content"]
    return create_file(request.project, request.path, request.content)


@router.post("/files/folder")
async def api_create_folder(request: CreateFolderRequest):
    """创建目录"""
    return create_folder(request.project, request.path)


@router.post("/files/delete")
async def api_delete_file(request: DeleteFileRequest):
    """删除文件或目录"""
    return delete_file(request.project, request.path)


@router.post("/files/rename")
async def api_rename_file(request: RenameRequest):
    """重命名文件或目录"""
    return rename_item(request.project, request.path, request.new_name)


@router.post("/files/move")
async def api_move_file(request: MoveRequest):
    """移动文件或目录到目标目录"""
    return move_item(request.project, request.path, request.target_dir)


# ──── 代码执行 API ────

@router.post("/execute")
async def api_execute_code(request: ExecuteRequest):
    """执行代码"""
    return execute_code(request.project, request.path, request.code)


# ──── 模板 API ────

@router.get("/templates")
async def api_list_templates():
    """列出文件模板"""
    return list_templates()


@router.get("/templates/{template_id}")
async def api_get_template(template_id: str):
    """获取模板详情"""
    result = get_template(template_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail="模板不存在")
    return result


# ──── 语言支持 API ────

@router.get("/languages")
async def api_list_languages():
    """列出支持的语言和执行能力"""
    languages = {}
    for ext, config in LANGUAGE_EXEC_MAP.items():
        lang = config["name"]
        if lang not in languages:
            languages[lang] = {
                "name": lang,
                "extensions": [],
                "executable": False,
                "monaco_language": None,
            }
        languages[lang]["extensions"].append(ext)
        if config["command"]:
            languages[lang]["executable"] = True
        languages[lang]["monaco_language"] = MONACO_LANG_MAP.get(ext)

    return {
        "success": True,
        "data": {
            "languages": list(languages.values()),
            "total": len(languages),
        }
    }