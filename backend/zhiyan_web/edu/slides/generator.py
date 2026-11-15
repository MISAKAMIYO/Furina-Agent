"""
教学幻灯片内容生成

把教学主题 / 教学目标 / 教学提纲 / 已有内容原文交给 LLM，生成结构化的幻灯片大纲：
- 封面、教学目标、教学提纲、知识点详解（多页）、例题演练（含答案与讲解）、课堂小结

输出结构（dict）:
{
  "title": str, "subject": str, "grade": str,
  "objectives": [str],            # 教学目标
  "outline": [str],               # 教学提纲
  "slides": [
     {"type": "cover|objectives|outline|content|example|summary",
      "title": str, "bullets": [str], "notes": str,
      "example": {"question": str, "options": [str], "answer": str, "analysis": str}   # 仅 example
     }
  ]
}
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..llm_utils import one_shot, parse_object

logger = logging.getLogger("ZhiYanWeb.Edu")

_SYSTEM_PROMPT = """你是一位资深一线教师与教学设计专家。请根据用户给出的教学信息，设计一份完整的教学授课幻灯片大纲，输出 JSON。

要求：
1. 只输出 JSON，不要任何解释文字，不要 markdown 代码块。
2. JSON 字段：
   - title: 课件标题（简洁，如「一元二次方程的解法」）
   - objectives: 教学目标数组（3-5 条，用「学生能够…」的表述）
   - outline: 教学提纲数组（4-8 条，按授课顺序）
   - slides: 幻灯片页面数组，按授课顺序排列，每页字段：
       * type: 页面类型，只能是 cover(封面) / objectives(教学目标) / outline(教学提纲) /
               content(知识点详解) / example(例题演练) / summary(课堂小结)
       * title: 页面标题
       * bullets: 该页要点数组（cover 页可给副标题；content 页 3-6 条要点，每条一句话讲清）
       * notes: 讲稿备注（老师怎么讲，60-150 字）
       * example: 仅当 type=example 时给出，字段 question(题干)、options(选项数组，非选择题给 [])、
                  answer(参考答案)、analysis(解题过程与讲解)
3. 页面结构要求（必须包含）：
   - 第 1 页 type=cover
   - 第 2 页 type=objectives（教学目标）
   - 第 3 页 type=outline（教学提纲）
   - 中间若干页 type=content，把知识点讲清楚，一个知识点一页，条理清晰
   - 至少 2 页 type=example（例题演练），题目要有代表性，必须给出答案与详细解析
   - 最后一页 type=summary（课堂小结，回顾重点 + 布置作业建议）
4. 内容要贴合用户给定的学科与年级，语言口语化、便于直接上课使用；不要编造与主题无关的内容。
5. 数学公式用纯文本或 LaTeX 行内形式（$...$）。
"""


def _fallback_outline(topic: str, subject: str, grade: str, objectives: str,
                      outline: str, content: str, slide_count: int) -> Dict[str, Any]:
    """LLM 不可用时的兜底大纲：按标准教学结构给出骨架，内容留待人工补充"""
    obj_list = [s.strip(" -•·\t") for s in (objectives or "").replace("；", "\n").split("\n") if s.strip()]
    out_list = [s.strip(" -•·\t") for s in (outline or "").replace("；", "\n").split("\n") if s.strip()]
    if not obj_list:
        obj_list = [f"学生能够理解{topic or '本课内容'}的核心概念",
                    f"学生能够运用{topic or '本课内容'}解决基础问题",
                    "学生能够形成规范的分析与表达习惯"]
    if not out_list:
        out_list = ["导入与复习", "新知探究", "例题精讲", "当堂练习", "课堂小结与作业"]

    body = (content or "").strip()
    slides: List[Dict[str, Any]] = [
        {"type": "cover", "title": topic or "新授课", "bullets": [f"{subject or ''} {grade or ''}".strip()], "notes": "开场，明确本课主题与本课目标。"},
        {"type": "objectives", "title": "教学目标", "bullets": obj_list, "notes": "向学生说明本课要达成的目标。"},
        {"type": "outline", "title": "教学提纲", "bullets": out_list, "notes": "交代本课的整体流程与节奏。"},
    ]
    if body:
        paragraphs = [p.strip() for p in body.split("\n") if p.strip()]
        slides.append({"type": "content", "title": "知识点详解", "bullets": paragraphs[:6], "notes": "结合原文讲解本课核心知识点。"})
    else:
        for i, item in enumerate(out_list[:3], 1):
            slides.append({"type": "content", "title": item, "bullets": [f"围绕「{item}」展开讲解（待补充要点）"], "notes": "按要点讲解，注意互动。"})
    slides.append({
        "type": "example", "title": "例题演练",
        "bullets": ["先独立思考，再对照讲解"],
        "notes": "请学生先作答，再逐步讲解。",
        "example": {"question": "（例题待补充）", "options": [], "answer": "", "analysis": "未配置模型，无法自动生成例题，请手动补充。"},
    })
    slides.append({"type": "summary", "title": "课堂小结", "bullets": ["回顾本课重点", "整理易错点", "完成课后作业"], "notes": "带领学生回顾，布置作业。"})
    # 按请求页数补/裁
    target = max(6, min(int(slide_count or 12), 40))
    while len(slides) < target:
        slides.insert(-1, {"type": "content", "title": "知识点补充", "bullets": ["（待补充）"], "notes": "补充讲解。"})
    return {
        "title": topic or "新授课",
        "subject": subject,
        "grade": grade,
        "objectives": obj_list,
        "outline": out_list,
        "slides": slides[:target],
        "engine": "fallback",
    }


def _normalize_slide(item: Dict[str, Any], index: int) -> Dict[str, Any]:
    """归一化单页"""
    stype = str(item.get("type") or item.get("页面类型") or "content").strip().lower()
    alias = {"封面": "cover", "目标": "objectives", "教学目标": "objectives", "提纲": "outline",
             "教学提纲": "outline", "内容": "content", "知识点": "content", "知识点详解": "content",
             "例题": "example", "例题演练": "example", "练习": "example", "小结": "summary", "总结": "summary"}
    stype = alias.get(stype, stype)
    if stype not in ("cover", "objectives", "outline", "content", "example", "summary"):
        stype = "content"

    bullets = item.get("bullets") or item.get("points") or item.get("要点") or []
    if isinstance(bullets, str):
        bullets = [b.strip() for b in bullets.replace("；", "\n").split("\n") if b.strip()]
    if not isinstance(bullets, list):
        bullets = []
    bullets = [str(b).strip() for b in bullets if str(b).strip()]

    page: Dict[str, Any] = {
        "index": index,
        "type": stype,
        "title": str(item.get("title") or item.get("标题") or "").strip(),
        "bullets": bullets,
        "notes": str(item.get("notes") or item.get("讲稿") or item.get("备注") or "").strip(),
    }

    example = item.get("example") or item.get("例题") or None
    if stype == "example":
        if not isinstance(example, dict):
            example = {}
        options = example.get("options") or example.get("选项") or []
        if isinstance(options, str):
            options = [o.strip() for o in options.split("\n") if o.strip()]
        if not isinstance(options, list):
            options = []
        page["example"] = {
            "question": str(example.get("question") or example.get("题干") or "").strip(),
            "options": [str(o).strip() for o in options if str(o).strip()],
            "answer": str(example.get("answer") or example.get("答案") or "").strip(),
            "analysis": str(example.get("analysis") or example.get("解析") or "").strip(),
        }
    return page


async def generate_outline(
    topic: str,
    subject: str = "",
    grade: str = "",
    objectives: str = "",
    outline: str = "",
    content: str = "",
    slide_count: int = 12,
) -> Dict[str, Any]:
    """生成教学幻灯片大纲

    返回 {"success", "outline": {...}, "engine": "llm"/"fallback", "message"}
    """
    topic = (topic or "").strip()
    if not topic:
        return {"success": False, "outline": None, "message": "请先填写教学主题"}

    slide_count = max(6, min(int(slide_count or 12), 40))
    user_content = (
        f"学科：{subject or '未指定'}\n年级：{grade or '未指定'}\n"
        f"教学主题：{topic}\n"
        f"教学目标（可补充/完善）：{objectives or '（未提供，请你拟定）'}\n"
        f"教学提纲（可补充/完善）：{outline or '（未提供，请你拟定）'}\n"
        f"计划页数：约 {slide_count} 页\n"
    )
    if content.strip():
        user_content += f"\n已有教学内容/知识点原文（请据此组织知识点详解与例题）：\n{content[:8000]}"

    raw = await one_shot(_SYSTEM_PROMPT, user_content, temperature=0.5, max_tokens=6000)
    data = parse_object(raw) if raw else None

    if not data or not isinstance(data.get("slides"), list) or not data["slides"]:
        logger.info("幻灯片生成回退骨架（LLM 不可用或输出非结构化）")
        fallback = _fallback_outline(topic, subject, grade, objectives, outline, content, slide_count)
        fallback["message"] = "未配置模型或模型未返回结构化内容，已生成可编辑的教学骨架，请手动补充知识点与例题。"
        return {"success": True, "outline": fallback, "engine": "fallback", "message": fallback["message"]}

    slides = [_normalize_slide(s, i + 1) for i, s in enumerate(data["slides"]) if isinstance(s, dict)]
    slides = [s for s in slides if s.get("title") or s.get("bullets")]

    obj = data.get("objectives") or data.get("教学目标") or []
    if isinstance(obj, str):
        obj = [s.strip() for s in obj.split("\n") if s.strip()]
    out = data.get("outline") or data.get("教学提纲") or []
    if isinstance(out, str):
        out = [s.strip() for s in out.split("\n") if s.strip()]

    result = {
        "title": str(data.get("title") or topic).strip() or topic,
        "subject": subject,
        "grade": grade,
        "objectives": [str(o).strip() for o in obj if str(o).strip()] if isinstance(obj, list) else [],
        "outline": [str(o).strip() for o in out if str(o).strip()] if isinstance(out, list) else [],
        "slides": slides,
        "engine": "llm",
    }
    return {"success": True, "outline": result, "engine": "llm", "message": f"已生成 {len(slides)} 页教学幻灯片"}