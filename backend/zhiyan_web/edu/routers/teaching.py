"""
AI 授课子系统 API 路由（teaching）

挂载前缀: /api/edu/teach（与教辅路由共用 /api/edu，前端统一用 eduApi 访问）

端点分组:
- /status                     子系统状态（幻灯片/OCR/ASR 引擎可用性）
- /config                     读取/更新授课配置
- /slides*                    教学幻灯片：生成/列表/详情/编辑/删除/再导出/文件下载
- /flows*                     授课流程：总结/列表/详情/删除/文件下载
- /rollcall*                  随机点名：分组/成员/抽取/历史/重置
- /catalog*                   快捷目录：构建/列表/详情/删除/匹配/跳转
- /ocr*                       OCR：通用/屏幕/题目/引擎
- /voice*                     语音问答：问答/识别/合成/信息/音频
- /memory*                    连续记忆：历史/写入/清空/统计
- /chat                       豆包式对话问答（文本）
- /web-questions*             网络试题：获取/记录
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse

from zhiyan_web.config import DATA_DIR, get_settings, update_settings

from .. import asr, catalog, flow, memory_bridge, ocr, rollcall, voice, web_questions
from ..models import (
    AsrRequest,
    CatalogBuildRequest,
    CatalogMatchRequest,
    FlowRequest,
    OcrRequest,
    RollcallDrawRequest,
    RollcallGroupIn,
    RollcallMemberBulk,
    RollcallMemberIn,
    SlideGenerateRequest,
    SlideUpdate,
    VoiceAskRequest,
    WebQuestionRequest,
    teaching_settings,
)
from ..slides import service as slides_service

logger = logging.getLogger("ZhiYanWeb.Edu")

router = APIRouter(prefix="/api/edu/teach", tags=["edu-teach"])

__all__ = ["router"]


def _guard_path(path: str, allowed_dirs: List[str]) -> str:
    """限制文件下载只能在教辅数据目录内，避免任意文件读取"""
    if not path:
        raise HTTPException(status_code=400, detail="缺少文件路径")
    target = os.path.abspath(path)
    for d in allowed_dirs:
        base = os.path.abspath(d)
        if target == base or target.startswith(base + os.sep):
            if os.path.exists(target):
                return target
            raise HTTPException(status_code=404, detail="文件不存在")
    raise HTTPException(status_code=403, detail="不允许访问该路径")


# ============= 状态 / 配置 =============

@router.get("/status")
async def teach_status():
    """子系统状态：各能力可用性"""
    ppt_ok = True
    try:
        import pptx  # noqa: F401
    except Exception:
        ppt_ok = False
    return {
        "success": True,
        "data": {
            "enabled": bool((get_settings().get("edu") or {}).get("enabled", True)),
            "pptx": ppt_ok,
            "ocr": ocr.available_engines(),
            "asr": asr.available_engines(),
            "voice": voice.info(),
        },
    }


@router.get("/config")
async def teach_config():
    """读取授课配置"""
    return {"success": True, "data": teaching_settings()}


@router.post("/config")
async def teach_config_update(patch: Dict[str, Any] = Body(...)):
    """更新授课配置（深合并）"""
    try:
        update_settings({"edu": {"teaching": patch}})
        return {"success": True, "data": teaching_settings()}
    except Exception as e:
        logger.error(f"更新授课配置失败: {e}")
        return {"success": False, "message": str(e)}


# ============= 教学幻灯片 =============

@router.get("/slides")
async def list_slides(limit: int = Query(100, ge=1, le=500)):
    return {"success": True, "data": slides_service.list_slides(limit=limit)}


@router.post("/slides/generate")
async def generate_slides(request: SlideGenerateRequest):
    try:
        return await slides_service.generate_slides(request.dict())
    except Exception as e:
        logger.error(f"生成幻灯片失败: {e}")
        return {"success": False, "message": f"生成失败: {e}", "data": None}


@router.get("/slides/{slide_id}")
async def get_slide(slide_id: int):
    item = slides_service.get_slide(slide_id)
    if not item:
        raise HTTPException(status_code=404, detail="幻灯片不存在")
    return {"success": True, "data": item}


@router.put("/slides/{slide_id}")
async def update_slide(slide_id: int, request: SlideUpdate):
    patch = {k: v for k, v in request.dict().items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    ok = slides_service.update_slide(slide_id, patch)
    return {"success": ok, "message": "已更新" if ok else "更新失败", "data": slides_service.get_slide(slide_id)}


@router.delete("/slides/{slide_id}")
async def delete_slide(slide_id: int, remove_files: bool = Query(False)):
    return slides_service.delete_slide(slide_id, remove_files=remove_files)


@router.post("/slides/{slide_id}/export")
async def export_slide(slide_id: int):
    return slides_service.regenerate_files(slide_id)


@router.get("/slides/{slide_id}/file")
async def slide_file(slide_id: int, kind: str = Query("html")):
    from ..models import EDU_SLIDES_DIR
    item = slides_service.get_slide(slide_id)
    if not item:
        raise HTTPException(status_code=404, detail="幻灯片不存在")
    path = {"pptx": item.get("pptx_path"), "html": item.get("html_path"), "seewo": item.get("seewo_path")}.get(kind)
    if not path:
        raise HTTPException(status_code=404, detail=f"未生成 {kind} 文件（可能缺少 python-pptx 依赖）")
    target = _guard_path(path, [EDU_SLIDES_DIR])
    # HTML 用浏览器内联预览，其它走下载
    if kind == "html":
        return FileResponse(target, media_type="text/html; charset=utf-8")
    return FileResponse(target, filename=os.path.basename(target))


# ============= 授课流程 =============

@router.get("/flows")
async def list_flows(limit: int = Query(100, ge=1, le=500)):
    return {"success": True, "data": flow.list_flows(limit=limit)}


@router.post("/flows/summarize")
async def summarize_flow(request: FlowRequest):
    try:
        return await flow.summarize_flow(request.dict())
    except Exception as e:
        logger.error(f"授课流程总结失败: {e}")
        return {"success": False, "message": f"总结失败: {e}", "data": None}


@router.get("/flows/{flow_id}")
async def get_flow(flow_id: int):
    item = flow.get_flow(flow_id)
    if not item:
        raise HTTPException(status_code=404, detail="授课流程不存在")
    return {"success": True, "data": item}


@router.delete("/flows/{flow_id}")
async def delete_flow(flow_id: int, remove_file: bool = Query(False)):
    return flow.delete_flow(flow_id, remove_file=remove_file)


@router.get("/flows/{flow_id}/file")
async def flow_file(flow_id: int):
    from ..models import EDU_FLOWS_DIR
    item = flow.get_flow(flow_id)
    if not item:
        raise HTTPException(status_code=404, detail="授课流程不存在")
    target = _guard_path(item.get("file_path") or "", [EDU_FLOWS_DIR])
    return FileResponse(target, filename=os.path.basename(target))


@router.get("/flows/{flow_id}/markdown")
async def flow_markdown(flow_id: int):
    from ..models import EDU_FLOWS_DIR
    item = flow.get_flow(flow_id)
    if not item:
        raise HTTPException(status_code=404, detail="授课流程不存在")
    json_path = item.get("file_path") or ""
    md_path = os.path.splitext(json_path)[0] + ".md"
    target = _guard_path(md_path, [EDU_FLOWS_DIR])
    with open(target, "r", encoding="utf-8") as f:
        return PlainTextResponse(f.read(), media_type="text/plain; charset=utf-8")


# ============= 随机点名 =============

@router.get("/rollcall/groups")
async def rc_groups():
    return {"success": True, "data": rollcall.list_groups()}


@router.post("/rollcall/groups")
async def rc_create_group(request: RollcallGroupIn):
    return rollcall.create_group(request.name, request.description)


@router.delete("/rollcall/groups/{group_id}")
async def rc_delete_group(group_id: int):
    return rollcall.delete_group(group_id)


@router.get("/rollcall/groups/{group_id}/members")
async def rc_members(group_id: int):
    return {"success": True, "data": rollcall.list_members(group_id)}


@router.post("/rollcall/groups/{group_id}/members")
async def rc_add_member(group_id: int, request: RollcallMemberIn):
    return rollcall.add_member(group_id, request.name, request.weight, request.active)


@router.post("/rollcall/members/bulk")
async def rc_bulk_members(request: RollcallMemberBulk):
    return rollcall.bulk_members(
        request.group_id, text=request.text, names=request.names,
        default_weight=request.default_weight, replace=request.replace,
    )


@router.put("/rollcall/members/{member_id}")
async def rc_update_member(member_id: int, patch: Dict[str, Any] = Body(...)):
    ok = rollcall.update_member(member_id, patch)
    return {"success": ok, "message": "已更新" if ok else "更新失败"}


@router.delete("/rollcall/members/{member_id}")
async def rc_delete_member(member_id: int):
    ok = rollcall.delete_member(member_id)
    return {"success": ok, "message": "已删除" if ok else "删除失败"}


@router.post("/rollcall/draw")
async def rc_draw(request: RollcallDrawRequest):
    return rollcall.draw(request.dict())


@router.get("/rollcall/groups/{group_id}/history")
async def rc_history(group_id: int, limit: int = Query(50, ge=1, le=500)):
    return {"success": True, "data": rollcall.history(group_id, limit=limit)}


@router.post("/rollcall/groups/{group_id}/reset")
async def rc_reset(group_id: int):
    return rollcall.reset_logs(group_id)


# ============= 快捷目录 =============

@router.get("/catalog/list")
async def catalog_list(limit: int = Query(100, ge=1, le=500)):
    return {"success": True, "data": catalog.list_catalogs(limit=limit)}


@router.post("/catalog/build")
async def catalog_build(request: CatalogBuildRequest):
    try:
        return await catalog.build_catalog(request.dict())
    except Exception as e:
        logger.error(f"构建快捷目录失败: {e}")
        return {"success": False, "message": f"构建失败: {e}", "data": None}


@router.get("/catalog/{catalog_id}")
async def catalog_get(catalog_id: int):
    item = catalog.get_catalog(catalog_id)
    if not item:
        raise HTTPException(status_code=404, detail="目录不存在")
    return {"success": True, "data": item}


@router.delete("/catalog/{catalog_id}")
async def catalog_delete(catalog_id: int):
    return catalog.delete_catalog(catalog_id)


@router.post("/catalog/match")
async def catalog_match(request: CatalogMatchRequest):
    """用 OCR 文本（或截图）匹配课件页码"""
    item = catalog.get_catalog(request.catalog_id)
    if not item:
        raise HTTPException(status_code=404, detail="目录不存在")
    text = str(request.text or "")
    if not text and request.image_path:
        res = await ocr.ocr_image(image_path=request.image_path, backend="auto")
        if not res.get("success"):
            return {"success": False, "message": res.get("message") or "OCR 失败"}
        text = res.get("text") or ""
    if not text:
        raise HTTPException(status_code=400, detail="请提供 OCR 文本或截图路径")
    matched = catalog.match_text(item, text, threshold=request.threshold)
    matched["success"] = True
    return matched


@router.post("/catalog/match-screen")
async def catalog_match_screen(request: Dict[str, Any] = Body(...)):
    """截屏 OCR 后匹配页码（悬浮球用）"""
    catalog_id = int(request.get("catalog_id") or 0)
    item = catalog.get_catalog(catalog_id)
    if not item:
        raise HTTPException(status_code=404, detail="目录不存在")
    shot = await ocr.ocr_screen(region=request.get("region"), backend="auto")
    if not shot.get("success"):
        return {"success": False, "message": shot.get("message") or "屏幕 OCR 失败"}
    matched = catalog.match_text(item, shot.get("text") or "", threshold=request.get("threshold"))
    matched["success"] = True
    matched["ocr_text"] = (shot.get("text") or "")[:500]
    return matched


@router.post("/catalog/jump")
async def catalog_jump(request: Dict[str, Any] = Body(...)):
    """相对当前页跳转（向 WPS/希沃发送翻页键）"""
    try:
        current = int(request.get("current_page") or 0)
        target = int(request.get("target_page") or 0)
    except Exception:
        raise HTTPException(status_code=400, detail="页码无效")
    if target <= 0:
        raise HTTPException(status_code=400, detail="目标页码无效")
    delta = target - current if current > 0 else 0
    if current <= 0:
        # 无当前页信息时，从第 1 页开始：发送足够多的 PageUp 回到开头再前进
        keys = catalog.jump_by_delta(-99)
        logger.debug(f"回到首页: {keys.get('message')}")
        delta = target - 1
    return catalog.jump_by_delta(delta)


# ============= OCR =============

@router.post("/ocr")
async def ocr_general(request: OcrRequest):
    return await ocr.ocr_image(
        image_path=request.image_path, image_base64=request.image_base64,
        backend=request.backend, lang=request.lang, prompt=request.prompt,
    )


@router.post("/ocr/screen")
async def ocr_screen(request: Dict[str, Any] = Body(default={})):
    return await ocr.ocr_screen(region=(request or {}).get("region"), backend=(request or {}).get("backend", "auto"))


@router.post("/ocr/question")
async def ocr_question(request: Dict[str, Any] = Body(...)):
    return await ocr.ocr_question(
        image_path=request.get("image_path", ""),
        image_base64=request.get("image_base64", ""),
        backend=request.get("backend", "auto"),
    )


@router.get("/ocr/engines")
async def ocr_engines():
    return {"success": True, "data": ocr.available_engines()}


# ============= 语音问答 =============

@router.get("/voice/info")
async def voice_info():
    return {"success": True, "data": voice.info()}


@router.post("/voice/ask")
async def voice_ask(request: VoiceAskRequest):
    try:
        return await voice.ask(request.dict())
    except Exception as e:
        logger.error(f"语音问答失败: {e}")
        return {"success": False, "message": f"问答失败: {e}", "question": "", "answer": ""}


@router.post("/voice/asr")
async def voice_asr(request: AsrRequest):
    return await voice.asr_only(request.dict())


@router.post("/voice/tts")
async def voice_tts(request: Dict[str, Any] = Body(...)):
    path = await voice.synthesize(str(request.get("text") or ""))
    if not path:
        return {"success": False, "message": "TTS 不可用（未配置语音引擎）"}
    return {"success": True, "audio_path": path, "audio_url": voice._to_url(path)}


@router.get("/voice/audio")
async def voice_audio(path: str = Query(...)):
    from ..models import EDU_VOICE_DIR
    dirs = [EDU_VOICE_DIR]
    # TTS 引擎产物目录也允许（便于直接播放合成结果）
    try:
        from zhiyan_web.config import TTS_DIR
        dirs.append(TTS_DIR)
    except Exception:
        pass
    target = _guard_path(path, dirs)
    media = "audio/mpeg" if target.lower().endswith(".mp3") else "audio/wav"
    return FileResponse(target, media_type=media)


# ============= 连续记忆 / 对话 =============

@router.get("/memory/history")
async def memory_history(session_id: str = Query("default"), limit: int = Query(40, ge=1, le=200)):
    return {"success": True, "data": memory_bridge.get_history(session_id, limit=limit)}


@router.post("/memory/remember")
async def memory_remember(request: Dict[str, Any] = Body(...)):
    return memory_bridge.remember(
        str(request.get("session_id") or "default"),
        str(request.get("content") or ""),
        memory_type=str(request.get("memory_type") or "fact"),
        importance=float(request.get("importance") or 0.6),
        tags=request.get("tags") or None,
    )


@router.get("/memory/search")
async def memory_search(query: str = Query(""), top_k: int = Query(6, ge=1, le=30)):
    return {"success": True, "data": memory_bridge.search(query, top_k=top_k)}


@router.post("/memory/clear")
async def memory_clear(request: Dict[str, Any] = Body(...)):
    return memory_bridge.clear_session(str(request.get("session_id") or "default"))


@router.get("/memory/stats")
async def memory_stats():
    return {"success": True, "data": memory_bridge.stats()}


@router.post("/chat")
async def teach_chat(request: Dict[str, Any] = Body(...)):
    """豆包式对话问答（文本；默认不播报，可选 speak）"""
    payload = {
        "text": request.get("text") or "",
        "session_id": request.get("session_id") or "chat",
        "subject": request.get("subject") or "",
        "speak": bool(request.get("speak", False)),
        "save_memory": bool(request.get("save_memory", True)),
    }
    try:
        return await voice.ask(payload)
    except Exception as e:
        logger.error(f"对话问答失败: {e}")
        return {"success": False, "message": f"问答失败: {e}", "answer": ""}


# ============= 网络试题 =============

@router.post("/web-questions/fetch")
async def web_questions_fetch(request: WebQuestionRequest):
    try:
        return await web_questions.fetch_questions(request.dict())
    except Exception as e:
        logger.error(f"获取网络试题失败: {e}")
        return {"success": False, "message": f"获取失败: {e}", "questions": []}


@router.get("/web-questions/records")
async def web_questions_records(limit: int = Query(50, ge=1, le=500)):
    return {"success": True, "data": web_questions.list_records(limit=limit)}


@router.get("/web-questions/records/{record_id}")
async def web_questions_record(record_id: int):
    item = web_questions.get_record(record_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True, "data": item}


@router.delete("/web-questions/records/{record_id}")
async def web_questions_delete(record_id: int):
    ok = web_questions.delete_record(record_id)
    return {"success": ok, "message": "已删除" if ok else "删除失败"}