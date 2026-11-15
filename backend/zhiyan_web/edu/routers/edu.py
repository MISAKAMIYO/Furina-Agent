"""
AI 教辅 API 路由

端点:
- GET    /api/edu/stats                     题库总览统计
- GET    /api/edu/subjects                  学科列表
- GET    /api/edu/tags                      知识点标签
- GET    /api/edu/courses                   课件列表
- POST   /api/edu/courses/import            上传文件导入（multipart）
- POST   /api/edu/courses/import-path       按本地路径导入
- DELETE /api/edu/courses/{course_id}       删除课件
- GET    /api/edu/questions                 题库检索
- GET    /api/edu/questions/{qid}           题目详情
- POST   /api/edu/questions                 新增题目
- PUT    /api/edu/questions/{qid}           更新题目
- DELETE /api/edu/questions/{qid}           删除题目
- POST   /api/edu/extract-preview           解析预览（只抽取不入库）
- POST   /api/edu/solve                     解题讲解

第二期:
- GET    /api/edu/mistakes                  错题本检索
- GET    /api/edu/mistakes/stats            错题本统计
- GET    /api/edu/mistakes/weak-points      薄弱知识点
- POST   /api/edu/mistakes                  新增错题
- POST   /api/edu/mistakes/from-question    由题库题目转入错题本
- GET    /api/edu/mistakes/{mid}            错题详情
- PUT    /api/edu/mistakes/{mid}            更新错题
- DELETE /api/edu/mistakes/{mid}            删除错题
- POST   /api/edu/mistakes/{mid}/review     复习打卡
- POST   /api/edu/exams/generate            生成试卷
- POST   /api/edu/exams                     保存试卷快照
- GET    /api/edu/exams                     试卷列表
- GET    /api/edu/exams/{eid}               试卷详情
- DELETE /api/edu/exams/{eid}               删除试卷
- GET    /api/edu/exams/{eid}/export        导出 HTML 试卷
- GET    /api/edu/recommend                 针对性推题
- GET    /api/edu/mastery                   掌握度概览
- POST   /api/edu/practice                  推题练习包（含学习建议）
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse

from .. import bank, exam, mistake, recommend
from ..models import (
    ExamRequest,
    ExtractPreviewRequest,
    ImportPathRequest,
    MistakeFromRequest,
    MistakeIn,
    MistakeUpdate,
    QuestionIn,
    QuestionUpdate,
    ReviewRequest,
    SolveRequest,
    init_edu_tables,
)
from ..solver import solve_question

logger = logging.getLogger("ZhiYanWeb.Edu")

router = APIRouter(prefix="/api/edu", tags=["edu"])

__all__ = ["router", "init_edu_tables"]


# ============= 统计 / 元数据 =============

@router.get("/stats")
async def edu_stats():
    """题库总览统计"""
    try:
        return {"success": True, "data": bank.stats()}
    except Exception as e:
        logger.error(f"获取教辅统计失败: {e}")
        return {"success": False, "data": {}, "message": str(e)}


@router.get("/subjects")
async def edu_subjects():
    """已有学科列表"""
    try:
        return {"success": True, "data": bank.subjects()}
    except Exception as e:
        return {"success": False, "data": [], "message": str(e)}


@router.get("/tags")
async def edu_tags(limit: int = Query(60, ge=1, le=500)):
    """知识点标签云"""
    try:
        return {"success": True, "data": bank.tags(limit=limit)}
    except Exception as e:
        return {"success": False, "data": [], "message": str(e)}


# ============= 课件 =============

@router.get("/courses")
async def edu_list_courses(limit: int = Query(200, ge=1, le=1000)):
    """课件列表"""
    try:
        return {"success": True, "data": bank.list_courses(limit=limit)}
    except Exception as e:
        return {"success": False, "data": [], "message": str(e)}


@router.post("/courses/import")
async def edu_import_course_file(
    file: UploadFile = File(...),
    subject: str = Form(""),
    grade: str = Form(""),
    title: str = Form(""),
    max_questions: int = Form(30),
):
    """上传课件/作业文件并导入题库"""
    try:
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="上传文件为空")
        path = bank.save_upload(data, file.filename or "upload.bin")
        # 标题优先取用户填写，其次取原始文件名（避免用带时间戳的落盘名）
        course_title = title or os.path.splitext(os.path.basename(file.filename or ""))[0]
        result = await bank.import_document(
            path, subject=subject, grade=grade, title=course_title,
            max_questions=max_questions, source="upload",
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"导入课件失败: {e}")
        return {"success": False, "message": f"导入失败: {e}", "question_count": 0}


@router.post("/courses/import-path")
async def edu_import_course_path(request: ImportPathRequest):
    """按本地路径导入课件/作业（桌面端常用）"""
    try:
        return await bank.import_document(
            request.path, subject=request.subject, grade=request.grade,
            title=request.title, max_questions=request.max_questions, source="local",
        )
    except Exception as e:
        logger.error(f"导入课件失败: {e}")
        return {"success": False, "message": f"导入失败: {e}", "question_count": 0}


@router.delete("/courses/{course_id}")
async def edu_delete_course(course_id: int, keep_questions: bool = Query(False)):
    """删除课件（默认连同其题目一起删除）"""
    if course_id <= 0:
        raise HTTPException(status_code=400, detail="无效的课件 ID")
    return bank.delete_course(course_id, delete_questions=not keep_questions)


# ============= 题目 =============

@router.get("/questions")
async def edu_list_questions(
    subject: str = Query(""),
    qtype: str = Query(""),
    difficulty: Optional[int] = Query(None, ge=1, le=5),
    keyword: str = Query(""),
    course_id: Optional[int] = Query(None, ge=1),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """题库检索"""
    try:
        data = bank.list_questions(
            subject=subject, qtype=qtype, difficulty=difficulty,
            keyword=keyword, course_id=course_id, limit=limit, offset=offset,
        )
        return {"success": True, "data": data["items"], "total": data["total"]}
    except Exception as e:
        logger.error(f"检索题目失败: {e}")
        return {"success": False, "data": [], "total": 0, "message": str(e)}


@router.get("/questions/{question_id}")
async def edu_get_question(question_id: int):
    """题目详情"""
    item = bank.get_question(question_id)
    if not item:
        raise HTTPException(status_code=404, detail="题目不存在")
    return {"success": True, "data": item}


@router.post("/questions")
async def edu_add_question(request: QuestionIn):
    """手动新增题目"""
    if not request.stem.strip():
        raise HTTPException(status_code=400, detail="题干不能为空")
    qid = bank.add_question(request.dict())
    if not qid:
        return {"success": False, "message": "新增失败"}
    if request.course_id:
        bank.update_course_stats(int(request.course_id))
    return {"success": True, "id": qid, "message": "题目已添加"}


@router.put("/questions/{question_id}")
async def edu_update_question(question_id: int, request: QuestionUpdate):
    """更新题目"""
    patch = {k: v for k, v in request.dict().items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    ok = bank.update_question(question_id, patch)
    if not ok:
        return {"success": False, "message": "更新失败或题目不存在"}
    return {"success": True, "message": "题目已更新"}


@router.delete("/questions/{question_id}")
async def edu_delete_question(question_id: int):
    """删除题目"""
    if question_id <= 0:
        raise HTTPException(status_code=400, detail="无效的题目 ID")
    ok = bank.delete_question(question_id)
    return {"success": ok, "message": "题目已删除" if ok else "题目不存在"}


# ============= 解析预览 / 解题 =============

@router.post("/extract-preview")
async def edu_extract_preview(request: ExtractPreviewRequest):
    """解析预览：只做题目抽取，不写入题库"""
    from ..parsers.document import extract_text
    from ..parsers.extractor import extract_questions

    text = (request.text or "").strip()
    if not text and request.path:
        parsed = extract_text(request.path)
        if not parsed.get("success"):
            return {"success": False, "questions": [], "message": parsed.get("message")}
        text = parsed["text"]
    if not text:
        raise HTTPException(status_code=400, detail="请提供文本或本地文件路径")

    result = await extract_questions(
        text, subject=request.subject, grade=request.grade, max_questions=request.max_questions
    )
    return result


@router.post("/solve")
async def edu_solve(request: SolveRequest):
    """解题讲解：按题目 ID 或直接文本求解"""
    question = None
    if request.question_id:
        question = bank.get_question(int(request.question_id))
        if not question:
            raise HTTPException(status_code=404, detail="题目不存在")
    elif not (request.text or "").strip():
        raise HTTPException(status_code=400, detail="请提供题目 ID 或题目文本")

    result = await solve_question(
        question=question, text=request.text, subject=request.subject
    )
    # 保存开关：请求显式指定优先，否则取配置 edu.auto_save_analysis
    save = request.save
    if not save:
        from zhiyan_web.config import get_settings
        save = bool((get_settings().get("edu") or {}).get("auto_save_analysis"))
    if result.get("success") and save and request.question_id:
        patch = {}
        if result.get("analysis"):
            patch["analysis"] = result["analysis"]
        if result.get("answer"):
            patch["answer"] = result["answer"]
        if result.get("knowledge_points"):
            patch["knowledge_points"] = result["knowledge_points"]
        if patch:
            bank.update_question(int(request.question_id), patch)
            result["saved"] = True
    return result


# ============= 第二期：错题本 =============

@router.get("/mistakes")
async def edu_list_mistakes(
    subject: str = Query(""),
    status: str = Query(""),
    keyword: str = Query(""),
    knowledge_point: str = Query(""),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """错题本检索"""
    try:
        data = mistake.list_mistakes(
            subject=subject, status=status, keyword=keyword,
            knowledge_point=knowledge_point, limit=limit, offset=offset,
        )
        return {"success": True, "data": data["items"], "total": data["total"]}
    except Exception as e:
        logger.error(f"检索错题失败: {e}")
        return {"success": False, "data": [], "total": 0, "message": str(e)}


@router.get("/mistakes/stats")
async def edu_mistake_stats():
    """错题本统计"""
    try:
        return {"success": True, "data": mistake.stats()}
    except Exception as e:
        logger.error(f"错题统计失败: {e}")
        return {"success": False, "data": {}, "message": str(e)}


@router.get("/mistakes/weak-points")
async def edu_weak_points(
    subject: str = Query(""),
    limit: int = Query(10, ge=1, le=100),
):
    """薄弱知识点"""
    try:
        return {"success": True, "data": mistake.weak_points(limit=limit, subject=subject)}
    except Exception as e:
        return {"success": False, "data": [], "message": str(e)}


@router.post("/mistakes")
async def edu_add_mistake(request: MistakeIn):
    """新增错题（可只填题干，也可指定题库题目 id）"""
    data = request.dict()
    if not (request.stem or "").strip() and request.question_id:
        question = bank.get_question(int(request.question_id))
        if not question:
            raise HTTPException(status_code=404, detail="题目不存在")
        data.update({
            "stem": question.get("stem") or "",
            "options": question.get("options") or [],
            "subject": question.get("subject") or request.subject,
            "grade": question.get("grade") or request.grade,
            "qtype": question.get("qtype") or request.qtype,
            "correct_answer": question.get("answer") or request.correct_answer,
            "analysis": question.get("analysis") or request.analysis,
            "knowledge_points": question.get("knowledge_points") or request.knowledge_points,
            "difficulty": question.get("difficulty") or request.difficulty,
        })
    mid = mistake.add_mistake(data)
    if not mid:
        return {"success": False, "message": "题干不能为空，或写入失败"}
    return {"success": True, "id": mid, "message": "已加入错题本"}


@router.post("/mistakes/from-question")
async def edu_mistake_from_question(request: MistakeFromRequest):
    """把题库中的题目一键转入错题本"""
    return mistake.add_from_question(
        request.question_id, wrong_answer=request.wrong_answer,
        note=request.note, source=request.source,
    )


@router.get("/mistakes/{mistake_id}")
async def edu_get_mistake(mistake_id: int):
    """错题详情"""
    item = mistake.get_mistake(mistake_id)
    if not item:
        raise HTTPException(status_code=404, detail="错题不存在")
    return {"success": True, "data": item}


@router.put("/mistakes/{mistake_id}")
async def edu_update_mistake(mistake_id: int, request: MistakeUpdate):
    """更新错题"""
    patch = {k: v for k, v in request.dict().items() if v is not None}
    if not patch:
        raise HTTPException(status_code=400, detail="没有需要更新的字段")
    ok = mistake.update_mistake(mistake_id, patch)
    return {"success": ok, "message": "错题已更新" if ok else "更新失败或错题不存在"}


@router.delete("/mistakes/{mistake_id}")
async def edu_delete_mistake(mistake_id: int):
    """删除错题"""
    if mistake_id <= 0:
        raise HTTPException(status_code=400, detail="无效的错题 ID")
    ok = mistake.delete_mistake(mistake_id)
    return {"success": ok, "message": "错题已删除" if ok else "错题不存在"}


@router.post("/mistakes/{mistake_id}/review")
async def edu_review_mistake(mistake_id: int, request: ReviewRequest):
    """复习打卡：做对连对两次判为已掌握，做错回到待复习"""
    return mistake.review_mistake(mistake_id, result=request.result, note=request.note)


# ============= 第二期：组卷 =============

@router.post("/exams/generate")
async def edu_generate_exam(request: ExamRequest):
    """生成试卷（save=true 时同时存入「我的试卷」）"""
    try:
        return exam.generate_exam(request.dict())
    except Exception as e:
        logger.error(f"组卷失败: {e}")
        return {"success": False, "message": f"组卷失败: {e}", "paper": None}


@router.post("/exams")
async def edu_save_exam(paper: Dict[str, Any] = Body(...)):
    """保存一份试卷快照"""
    return exam.save_exam(paper)


@router.get("/exams")
async def edu_list_exams(limit: int = Query(100, ge=1, le=500)):
    """试卷列表"""
    try:
        return {"success": True, "data": exam.list_exams(limit=limit)}
    except Exception as e:
        return {"success": False, "data": [], "message": str(e)}


@router.get("/exams/{exam_id}")
async def edu_get_exam(exam_id: int):
    """试卷详情（含题目与配分）"""
    item = exam.get_exam(exam_id)
    if not item:
        raise HTTPException(status_code=404, detail="试卷不存在")
    return {"success": True, "data": item}


@router.delete("/exams/{exam_id}")
async def edu_delete_exam(exam_id: int):
    """删除试卷"""
    if exam_id <= 0:
        raise HTTPException(status_code=400, detail="无效的试卷 ID")
    ok = exam.delete_exam(exam_id)
    return {"success": ok, "message": "试卷已删除" if ok else "试卷不存在"}


@router.get("/exams/{exam_id}/export")
async def edu_export_exam(exam_id: int, with_answer: bool = Query(False)):
    """导出 HTML 试卷（浏览器可直接打印为 PDF）"""
    result = exam.export_exam_html(exam_id, with_answer=with_answer)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("message") or "导出失败")
    return HTMLResponse(content=result["html"])


# ============= 第二期：推题 / 掌握度 =============

@router.get("/recommend")
async def edu_recommend(
    subject: str = Query(""),
    limit: int = Query(5, ge=1, le=30),
    knowledge_point: str = Query(""),
    difficulty: Optional[int] = Query(None, ge=1, le=5),
):
    """针对性推题（基于错题本薄弱知识点）"""
    try:
        points = [p.strip() for p in knowledge_point.split(",") if p.strip()]
        data = recommend.recommend_questions(
            subject=subject, limit=limit, knowledge_points=points, difficulty=difficulty
        )
        return {"success": True, "data": data["items"], "weak_points": data["weak_points"],
                "focus_points": data["focus_points"], "reason": data["reason"]}
    except Exception as e:
        logger.error(f"推题失败: {e}")
        return {"success": False, "data": [], "message": str(e)}


@router.post("/practice")
async def edu_practice(
    subject: str = Query(""),
    limit: int = Query(5, ge=1, le=30),
):
    """推题练习包：练习题 + 薄弱知识点 + 学习建议"""
    try:
        return await recommend.build_practice(subject=subject, limit=limit)
    except Exception as e:
        logger.error(f"生成练习包失败: {e}")
        return {"success": False, "items": [], "message": str(e)}


@router.get("/mastery")
async def edu_mastery(subject: str = Query("")):
    """掌握度概览（整体 + 分学科）"""
    try:
        return {"success": True, "data": recommend.mastery(subject=subject)}
    except Exception as e:
        logger.error(f"获取掌握度失败: {e}")
        return {"success": False, "data": {}, "message": str(e)}