"""
课件 / 作业文档解析（纯文本抽取）

支持格式:
- pdf             PyMuPDF（缺失时回退 pypdf）
- pptx            zip + XML，无需 python-pptx
- docx            zip + XML，无需 python-docx
- xlsx / xlsm     openpyxl
- txt / md / csv  多编码尝试（utf-8 / utf-8-sig / gbk / gb18030）

图片（png/jpg/…）依赖本地 OCR，属第三期能力，这里返回明确提示而非静默失败。
"""
from __future__ import annotations

import logging
import os
import re
import zipfile
from html import unescape
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("ZhiYanWeb.Edu")

PLAIN_EXTS = (".txt", ".md", ".markdown", ".csv", ".log")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tif", ".tiff")
SUPPORTED_EXTS = (".pdf", ".pptx", ".docx", ".xlsx", ".xlsm") + PLAIN_EXTS

_TEXT_ENCODINGS = ("utf-8", "utf-8-sig", "gbk", "gb18030", "big5", "latin-1")


def supported_extensions() -> List[str]:
    """返回支持的扩展名列表（含点号）"""
    return list(SUPPORTED_EXTS)


def extract_text(path: str) -> Dict[str, Any]:
    """抽取文档文本

    返回: {"success": bool, "text": str, "file_type": str, "pages": int, "message": str}
    """
    result: Dict[str, Any] = {
        "success": False, "text": "", "file_type": "", "pages": 0, "message": "",
    }
    if not path:
        result["message"] = "文件路径为空"
        return result
    if not os.path.exists(path):
        result["message"] = f"文件不存在: {path}"
        return result
    if os.path.isdir(path):
        result["message"] = "路径是文件夹，请指定具体文件"
        return result

    ext = os.path.splitext(path)[1].lower()
    result["file_type"] = ext.lstrip(".")

    if ext in IMAGE_EXTS:
        result["message"] = "图片题目需本地 OCR 识别（计划在第三期提供），当前请先转成文本或 PDF"
        return result
    if ext not in SUPPORTED_EXTS:
        result["message"] = f"暂不支持的格式: {ext or '未知'}，支持 {'/'.join(SUPPORTED_EXTS)}"
        return result

    try:
        if ext == ".pdf":
            text, pages = _pdf_text(path)
        elif ext == ".pptx":
            text, pages = _pptx_text(path)
        elif ext == ".docx":
            text, pages = _docx_text(path)
        elif ext in (".xlsx", ".xlsm"):
            text, pages = _xlsx_text(path)
        else:
            text, pages = _plain_text(path)
    except Exception as e:
        logger.warning(f"解析文档失败 {path}: {e}")
        result["message"] = f"解析失败: {e}"
        return result

    text = (text or "").strip()
    result["pages"] = pages
    if not text:
        result["message"] = "未提取到文本内容（可能是扫描件/纯图片文档）"
        return result

    result["success"] = True
    result["text"] = text
    result["message"] = f"提取成功，共 {len(text)} 字"
    return result


def _clean(text: str) -> str:
    """清理 XML 抽取出的文本（压缩空白、去首尾）"""
    return re.sub(r"[ \t\u3000]+", " ", text or "").strip()


def extract_pages(path: str) -> Dict[str, Any]:
    """按页/幻灯片抽取文本（快捷目录用）

    返回 {"success": bool, "pages": [{"page": int, "text": str}], "file_type": str, "message": str}
    """
    result: Dict[str, Any] = {"success": False, "pages": [], "file_type": "", "message": ""}
    if not path or not os.path.exists(path):
        result["message"] = "文件不存在"
        return result
    ext = os.path.splitext(path)[1].lower()
    result["file_type"] = ext.lstrip(".")

    try:
        if ext == ".pptx":
            pages = _pptx_pages(path)
        elif ext == ".pdf":
            pages = _pdf_pages(path)
        elif ext == ".docx":
            pages = _docx_pages(path)
        elif ext in (".xlsx", ".xlsm"):
            pages = _xlsx_pages(path)
        elif ext in PLAIN_EXTS:
            pages = _plain_pages(path)
        else:
            result["message"] = f"暂不支持按页解析: {ext or '未知'}"
            return result
    except Exception as e:
        logger.warning(f"按页解析失败 {path}: {e}")
        result["message"] = f"解析失败: {e}"
        return result

    pages = [p for p in pages if (p.get("text") or "").strip()]
    if not pages:
        result["message"] = "未提取到分页文本（可能是扫描件/纯图片文档）"
        return result
    for i, p in enumerate(pages, 1):
        p["page"] = i
    result["success"] = True
    result["pages"] = pages
    result["message"] = f"共 {len(pages)} 页"
    return result


def _pptx_pages(path: str) -> List[Dict[str, Any]]:
    """逐张幻灯片抽取文本"""
    tag = re.compile(r"<a:t[^>]*>(.*?)</a:t>", re.S)
    pages: List[Dict[str, Any]] = []
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)]
        names.sort(key=lambda n: int(re.search(r"slide(\d+)\.xml", n).group(1)))
        for name in names:
            xml = zf.read(name).decode("utf-8", "ignore")
            body = "\n".join(t for t in (_clean(unescape(x)) for x in tag.findall(xml)) if t)
            note_name = name.replace("ppt/slides/slide", "ppt/notesSlides/notesSlide")
            note = ""
            if note_name in zf.namelist():
                note = "\n".join(
                    t for t in (_clean(unescape(x)) for x in tag.findall(zf.read(note_name).decode("utf-8", "ignore"))) if t
                )
            pages.append({"text": body, "notes": note})
    return pages


def _pdf_pages(path: str) -> List[Dict[str, Any]]:
    doc = None
    try:
        import pymupdf
        doc = pymupdf.open(path)
    except ImportError:
        try:
            import fitz
            doc = fitz.open(path)
        except ImportError:
            doc = None
    except Exception:
        doc = None
    pages: List[Dict[str, Any]] = []
    if doc is not None:
        try:
            for page in doc:
                pages.append({"text": page.get_text() or ""})
            return pages
        finally:
            try:
                doc.close()
            except Exception:
                pass
    from pypdf import PdfReader
    reader = PdfReader(path)
    return [{"text": p.extract_text() or ""} for p in reader.pages]


def _docx_pages(path: str) -> List[Dict[str, Any]]:
    """DOCX 无固定分页，按空行/标题粗分为逻辑页"""
    text, _ = _docx_text(path)
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if not blocks:
        blocks = [text]
    return [{"text": b} for b in blocks]


def _xlsx_pages(path: str) -> List[Dict[str, Any]]:
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    pages: List[Dict[str, Any]] = []
    try:
        for ws in wb.worksheets:
            lines = [f"【{ws.title}】"]
            for row in ws.iter_rows(values_only=True):
                cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if cells:
                    lines.append("\t".join(cells))
            pages.append({"text": "\n".join(lines)})
    finally:
        try:
            wb.close()
        except Exception:
            pass
    return pages


def _plain_pages(path: str) -> List[Dict[str, Any]]:
    text, _ = _plain_text(path)
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    if not blocks:
        blocks = [text]
    return [{"text": b} for b in blocks]


# ---------- 各格式实现 ----------

def _pdf_text(path: str) -> Tuple[str, int]:
    """PDF 文本抽取：优先 PyMuPDF，失败回退 pypdf"""
    doc = None
    try:
        import pymupdf  # PyMuPDF 新版包名
        doc = pymupdf.open(path)
    except ImportError:
        try:
            import fitz  # PyMuPDF 旧包名
            doc = fitz.open(path)
        except ImportError:
            doc = None
    except Exception as e:
        logger.debug(f"PyMuPDF 打开失败，回退 pypdf: {e}")
        doc = None

    if doc is not None:
        try:
            parts = []
            for page in doc:
                parts.append(page.get_text() or "")
            pages = len(parts)
            return "\n".join(parts), pages
        finally:
            try:
                doc.close()
            except Exception:
                pass

    from pypdf import PdfReader
    reader = PdfReader(path)
    parts = [(p.extract_text() or "") for p in reader.pages]
    return "\n".join(parts), len(parts)


def _pptx_text(path: str) -> Tuple[str, int]:
    """PPTX 文本抽取：直接读 zip 内 ppt/slides/slideN.xml 的 <a:t>"""
    tag = re.compile(r"<a:t[^>]*>(.*?)</a:t>", re.S)
    slides: List[str] = []
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)]
        names.sort(key=lambda n: int(re.search(r"slide(\d+)\.xml", n).group(1)))

        def _slide_text(xml: str) -> str:
            return "\n".join(
                t for t in (_clean(unescape(x)) for x in tag.findall(xml)) if t
            )

        for name in names:
            xml = zf.read(name).decode("utf-8", "ignore")
            body = _slide_text(xml)
            # 备注页可能含答案/讲稿，一并提取（可选）
            note_name = name.replace("ppt/slides/slide", "ppt/notesSlides/notesSlide")
            note = ""
            if note_name in zf.namelist():
                note = _slide_text(zf.read(note_name).decode("utf-8", "ignore"))
            if note:
                body = f"{body}\n【备注】{note}" if body else f"【备注】{note}"
            if body:
                slides.append(body)
    return "\n\n".join(slides), len(slides)


def _docx_text(path: str) -> Tuple[str, int]:
    """DOCX 文本抽取：读 word/document.xml，按段落 <w:p> 断行"""
    with zipfile.ZipFile(path) as zf:
        if "word/document.xml" not in zf.namelist():
            raise ValueError("docx 缺少 word/document.xml")
        xml = zf.read("word/document.xml").decode("utf-8", "ignore")

    t_tag = re.compile(r"<w:t[^>]*>(.*?)</w:t>", re.S)
    lines: List[str] = []
    for para in re.split(r"</w:p>", xml):
        line = _clean("".join(unescape(t) for t in t_tag.findall(para)))
        if line:
            lines.append(line)
    return "\n".join(lines), len(lines)


def _xlsx_text(path: str) -> Tuple[str, int]:
    """XLSX 文本抽取（openpyxl）：按工作表逐行拼接"""
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    parts: List[str] = []
    rows = 0
    try:
        for ws in wb.worksheets:
            parts.append(f"【{ws.title}】")
            for row in ws.iter_rows(values_only=True):
                cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if cells:
                    parts.append("\t".join(cells))
                    rows += 1
    finally:
        try:
            wb.close()
        except Exception:
            pass
    return "\n".join(parts), rows


def _plain_text(path: str) -> Tuple[str, int]:
    """纯文本多编码尝试读取"""
    last_error = None
    for enc in _TEXT_ENCODINGS:
        try:
            with open(path, "r", encoding=enc) as f:
                text = f.read()
            return text, text.count("\n") + 1
        except (UnicodeDecodeError, UnicodeError) as e:
            last_error = e
            continue
    raise ValueError(f"文本编码识别失败: {last_error}")