"""
PPTX 幻灯片写出

用 python-pptx 把教学大纲写成可编辑的 .pptx（16:9）。
缺失 python-pptx 时返回 {success: False, message: 需安装 python-pptx}，由 service 决定是否降级为纯 HTML。

对外接口: write_pptx(outline, out_path) -> dict
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger("ZhiYanWeb.Edu")

# 主题配色（RGB）
_THEMES = {
    "default": {"bg": (255, 255, 255), "title": (31, 41, 55), "body": (55, 65, 81), "accent": (61, 139, 253)},
    "dark": {"bg": (24, 28, 38), "title": (240, 244, 250), "body": (206, 214, 226), "accent": (96, 165, 250)},
    "fresh": {"bg": (246, 252, 249), "title": (22, 78, 62), "body": (45, 90, 75), "accent": (16, 185, 129)},
    "academic": {"bg": (250, 248, 244), "title": (74, 51, 34), "body": (94, 74, 58), "accent": (180, 83, 9)},
}


def _rgb(color: Any):
    from pptx.dml.color import RGBColor
    return RGBColor(*color)


def _set_run_font(run, size_pt: int, bold: bool, color: Any, name: str = "微软雅黑") -> None:
    """设置字号/粗体/颜色，并同时设置中文字体（ea typeface）"""
    from pptx.util import Pt
    run.font.size = Pt(size_pt)
    run.font.bold = bold
    run.font.name = name
    run.font.color.rgb = _rgb(color)
    # 中文字体需要额外设置 rPr 的 ea / cs 字体
    try:
        rPr = run._r.get_or_add_rPr()
        from pptx.oxml.ns import qn
        ea = rPr.find(qn("a:ea"))
        if ea is None:
            from lxml import etree
            ea = etree.SubElement(rPr, qn("a:ea"))
        ea.set("typeface", name)
    except Exception:
        pass


def write_pptx(outline: Dict[str, Any], out_path: str) -> Dict[str, Any]:
    """把大纲写成 .pptx，返回 {"success", "path", "message"}"""
    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt
    except ImportError:
        return {"success": False, "path": "", "message": "未安装 python-pptx，无法生成 PPTX（可仅使用 HTML 幻灯片）"}

    try:
        theme_name = str(outline.get("theme") or "default")
        theme = _THEMES.get(theme_name, _THEMES["default"])
        prs = Presentation()
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)
        blank = prs.slide_layouts[6]

        slides: List[Dict[str, Any]] = outline.get("slides") or []
        for page in slides:
            slide = prs.slides.add_slide(blank)
            stype = page.get("type") or "content"
            title = str(page.get("title") or "")
            bullets = page.get("bullets") or []
            notes = str(page.get("notes") or "")

            # 背景
            try:
                from pptx.oxml.ns import qn
                bg = slide.background.fill
                bg.solid()
                bg.fore_color.rgb = _rgb(theme["bg"])
            except Exception:
                pass

            if stype == "cover":
                # 封面：居中大标题 + 副标题
                tb = slide.shapes.add_textbox(Inches(1.0), Inches(2.6), Inches(11.33), Inches(1.6))
                tf = tb.text_frame
                tf.word_wrap = True
                para = tf.paragraphs[0]
                para.alignment = 1  # CENTER
                run = para.add_run()
                run.text = title
                _set_run_font(run, 40, True, theme["title"])
                if bullets:
                    sub = slide.shapes.add_textbox(Inches(1.0), Inches(4.3), Inches(11.33), Inches(1.2))
                    stf = sub.text_frame
                    stf.word_wrap = True
                    for i, b in enumerate(bullets):
                        p = stf.paragraphs[0] if i == 0 else stf.add_paragraph()
                        p.alignment = 1
                        r = p.add_run()
                        r.text = str(b)
                        _set_run_font(r, 18, False, theme["body"])
            else:
                # 标题
                tb = slide.shapes.add_textbox(Inches(0.7), Inches(0.5), Inches(11.9), Inches(1.0))
                tf = tb.text_frame
                tf.word_wrap = True
                run = tf.paragraphs[0].add_run()
                run.text = title
                _set_run_font(run, 28, True, theme["title"])
                # 标题下装饰线
                try:
                    from pptx.enum.shapes import MSO_SHAPE
                    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.72), Inches(1.42), Inches(1.6), Pt(4))
                    bar.fill.solid()
                    bar.fill.fore_color.rgb = _rgb(theme["accent"])
                    bar.line.fill.background()
                except Exception:
                    pass

                # 正文
                body_top = Inches(1.75)
                if stype == "example":
                    body_top = Inches(1.7)
                body = slide.shapes.add_textbox(Inches(0.85), body_top, Inches(11.6), Inches(5.2))
                btf = body.text_frame
                btf.word_wrap = True
                first = True
                for b in bullets:
                    p = btf.paragraphs[0] if first else btf.add_paragraph()
                    first = False
                    r = p.add_run()
                    r.text = "• " + str(b)
                    _set_run_font(r, 18, False, theme["body"])

                if stype == "example":
                    ex = page.get("example") or {}
                    q = str(ex.get("question") or "").strip()
                    opts = ex.get("options") or []
                    ans = str(ex.get("answer") or "").strip()
                    ana = str(ex.get("analysis") or "").strip()
                    if q:
                        p = btf.add_paragraph()
                        r = p.add_run()
                        r.text = ""
                        r.text = f"例题：{q}"
                        _set_run_font(r, 18, True, theme["title"])
                    for i, o in enumerate(opts):
                        p = btf.add_paragraph()
                        r = p.add_run()
                        label = "ABCDEFGH"[i] if i < 8 else "?"
                        r.text = f"{label}. {o}"
                        _set_run_font(r, 16, False, theme["body"])
                    if ans:
                        p = btf.add_paragraph()
                        r = p.add_run()
                        r.text = f"答案：{ans}"
                        _set_run_font(r, 16, True, theme["accent"])
                    if ana:
                        p = btf.add_paragraph()
                        r = p.add_run()
                        r.text = f"解析：{ana}"
                        _set_run_font(r, 15, False, theme["body"])

            # 讲稿备注
            if notes:
                try:
                    slide.notes_slide.notes_text_frame.text = notes
                except Exception:
                    pass

        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        prs.save(out_path)
        return {"success": True, "path": out_path, "message": f"已生成 PPTX（{len(slides)} 页）"}
    except Exception as e:
        logger.error(f"生成 PPTX 失败: {e}")
        return {"success": False, "path": "", "message": f"生成 PPTX 失败: {e}"}