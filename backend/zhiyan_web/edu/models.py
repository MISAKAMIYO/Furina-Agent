"""
教辅子系统（edu）数据模型与数据库表结构

表:
- edu_courses    课件/资料（导入的 PDF/PPTX/DOCX/TXT/XLSX 等）
- edu_questions  题目（题干/选项/答案/解析/知识点/难度）

说明：沿用 zhiyan_web.db.database 的 SQLite 帮助函数，库文件为 data/zhiyan.db。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from zhiyan_web.config.settings import DATA_DIR
from zhiyan_web.db.database import get_db

logger = logging.getLogger("ZhiYanWeb.Edu")

# 教辅数据目录：data/edu/（导入的原始文件与导出文件都放这里）
EDU_DIR = os.path.join(DATA_DIR, "edu")
EDU_FILES_DIR = os.path.join(EDU_DIR, "files")
# 授课子系统产物目录
EDU_SLIDES_DIR = os.path.join(EDU_DIR, "slides")     # 生成的 pptx / html / seewo
EDU_FLOWS_DIR = os.path.join(EDU_DIR, "flows")       # 授课流程 JSON / Markdown
EDU_CATALOG_DIR = os.path.join(EDU_DIR, "catalogs")  # 课件纲要 JSON
EDU_VOICE_DIR = os.path.join(EDU_DIR, "voice")       # 语音问答缓存

try:
    os.makedirs(EDU_FILES_DIR, exist_ok=True)
    os.makedirs(EDU_SLIDES_DIR, exist_ok=True)
    os.makedirs(EDU_FLOWS_DIR, exist_ok=True)
    os.makedirs(EDU_CATALOG_DIR, exist_ok=True)
    os.makedirs(EDU_VOICE_DIR, exist_ok=True)
except Exception as _e:  # 目录创建失败不阻断导入（可能只读）
    logger.warning(f"创建教辅数据目录失败: {_e}")

# 题型取值: choice=选择题 / fill=填空题 / judge=判断题 / answer=解答题
QTYPES = ("choice", "fill", "judge", "answer")

_QTYPE_ALIASES = {
    "choice": "choice", "选择题": "choice", "单选": "choice", "多选": "choice", "选择": "choice",
    "fill": "fill", "填空题": "fill", "填空": "fill",
    "judge": "judge", "判断题": "judge", "判断": "judge", "对错": "judge",
    "answer": "answer", "解答题": "answer", "解答": "answer", "计算题": "answer",
    "计算": "answer", "证明题": "answer", "证明": "answer", "应用题": "answer",
    "简答": "answer", "问答题": "answer", "综合题": "answer",
}

_CREATE_COURSES = """
CREATE TABLE IF NOT EXISTS edu_courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    subject TEXT DEFAULT '',
    grade TEXT DEFAULT '',
    filename TEXT DEFAULT '',
    file_type TEXT DEFAULT '',
    stored_path TEXT DEFAULT '',
    raw_text TEXT DEFAULT '',
    question_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'ok',
    source TEXT DEFAULT 'local',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

_CREATE_QUESTIONS = """
CREATE TABLE IF NOT EXISTS edu_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER DEFAULT 0,
    subject TEXT DEFAULT '',
    grade TEXT DEFAULT '',
    qtype TEXT DEFAULT 'answer',
    stem TEXT NOT NULL,
    options TEXT DEFAULT '[]',
    answer TEXT DEFAULT '',
    analysis TEXT DEFAULT '',
    knowledge_points TEXT DEFAULT '[]',
    difficulty INTEGER DEFAULT 3,
    source TEXT DEFAULT 'manual',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# 错题本：一道错题一条记录（可由题库题目一键转入）
_CREATE_MISTAKES = """
CREATE TABLE IF NOT EXISTS edu_mistakes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER DEFAULT 0,
    subject TEXT DEFAULT '',
    grade TEXT DEFAULT '',
    qtype TEXT DEFAULT 'answer',
    stem TEXT NOT NULL,
    options TEXT DEFAULT '[]',
    wrong_answer TEXT DEFAULT '',
    correct_answer TEXT DEFAULT '',
    analysis TEXT DEFAULT '',
    knowledge_points TEXT DEFAULT '[]',
    difficulty INTEGER DEFAULT 3,
    note TEXT DEFAULT '',
    status TEXT DEFAULT 'open',
    review_count INTEGER DEFAULT 0,
    correct_streak INTEGER DEFAULT 0,
    source TEXT DEFAULT 'manual',
    last_review_at TEXT DEFAULT '',
    mastered_at TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# 试卷：一次组卷的快照（题目与配分以 JSON 存下，避免题库变动影响历史卷）
_CREATE_EXAMS = """
CREATE TABLE IF NOT EXISTS edu_exams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    subject TEXT DEFAULT '',
    grade TEXT DEFAULT '',
    question_count INTEGER DEFAULT 0,
    total_score INTEGER DEFAULT 100,
    duration INTEGER DEFAULT 60,
    question_ids TEXT DEFAULT '[]',
    config TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# 错题状态: open=待复习 / reviewing=复习中 / mastered=已掌握
MISTAKE_STATUSES = ("open", "reviewing", "mastered")

# ---- AI 授课子系统（teaching）表结构 ----

# 教学幻灯片：一次 AI 创作的课件（大纲 JSON + pptx/html 文件路径）
_CREATE_SLIDES = """
CREATE TABLE IF NOT EXISTS edu_slides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    subject TEXT DEFAULT '',
    grade TEXT DEFAULT '',
    topic TEXT DEFAULT '',
    slide_count INTEGER DEFAULT 0,
    outline TEXT DEFAULT '{}',
    pptx_path TEXT DEFAULT '',
    html_path TEXT DEFAULT '',
    seewo_path TEXT DEFAULT '',
    status TEXT DEFAULT 'ok',
    source TEXT DEFAULT 'ai',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# 授课流程：读取课件后由 AI 总结的授课流程（结构 JSON + 落盘文件路径）
_CREATE_FLOWS = """
CREATE TABLE IF NOT EXISTS edu_flows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER DEFAULT 0,
    title TEXT NOT NULL,
    subject TEXT DEFAULT '',
    grade TEXT DEFAULT '',
    summary TEXT DEFAULT '{}',
    file_path TEXT DEFAULT '',
    question_count INTEGER DEFAULT 0,
    answered_count INTEGER DEFAULT 0,
    source_path TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# 快捷目录：课件纲要 + 每页元素记录（供悬浮球快捷跳转）
_CREATE_CATALOGS = """
CREATE TABLE IF NOT EXISTS edu_catalogs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id INTEGER DEFAULT 0,
    title TEXT NOT NULL,
    subject TEXT DEFAULT '',
    page_count INTEGER DEFAULT 0,
    outline TEXT DEFAULT '[]',
    source_path TEXT DEFAULT '',
    file_type TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# 随机点名：名单分组
_CREATE_ROLLCALL_GROUPS = """
CREATE TABLE IF NOT EXISTS edu_rollcall_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# 随机点名：成员（可调权重）
_CREATE_ROLLCALL_MEMBERS = """
CREATE TABLE IF NOT EXISTS edu_rollcall_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    active INTEGER DEFAULT 1,
    drawn_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# 随机点名：抽取记录（时间戳伪随机 + 去重依据）
_CREATE_ROLLCALL_LOGS = """
CREATE TABLE IF NOT EXISTS edu_rollcall_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    member_id INTEGER DEFAULT 0,
    name TEXT DEFAULT '',
    seed TEXT DEFAULT '',
    batch TEXT DEFAULT '',
    algorithm TEXT DEFAULT 'timestamp',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# 网络试题：一次获取/生成的记录（来源 + 结果快照）
_CREATE_WEB_QUESTIONS = """
CREATE TABLE IF NOT EXISTS edu_web_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT DEFAULT 'ai_generate',
    query TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    grade TEXT DEFAULT '',
    payload TEXT DEFAULT '[]',
    saved_ids TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def init_edu_tables() -> None:
    """初始化教辅子系统数据库表"""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(_CREATE_COURSES)
        cursor.execute(_CREATE_QUESTIONS)
        cursor.execute(_CREATE_MISTAKES)
        cursor.execute(_CREATE_EXAMS)
        cursor.execute(_CREATE_SLIDES)
        cursor.execute(_CREATE_FLOWS)
        cursor.execute(_CREATE_CATALOGS)
        cursor.execute(_CREATE_ROLLCALL_GROUPS)
        cursor.execute(_CREATE_ROLLCALL_MEMBERS)
        cursor.execute(_CREATE_ROLLCALL_LOGS)
        cursor.execute(_CREATE_WEB_QUESTIONS)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_edu_q_subject ON edu_questions(subject)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_edu_q_course ON edu_questions(course_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_edu_m_subject ON edu_mistakes(subject)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_edu_m_status ON edu_mistakes(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_edu_m_question ON edu_mistakes(question_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_edu_e_subject ON edu_exams(subject)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_edu_slide_subject ON edu_slides(subject)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_edu_flow_course ON edu_flows(course_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_edu_catalog_course ON edu_catalogs(course_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_edu_rc_member_group ON edu_rollcall_members(group_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_edu_rc_log_group ON edu_rollcall_logs(group_id)")
        conn.commit()
        logger.info("教辅子系统数据库表初始化完成")
    except Exception as e:
        logger.error(f"教辅子系统数据库表初始化失败: {e}")
        conn.rollback()
    finally:
        conn.close()


def normalize_qtype(value: Any) -> str:
    """把各种题型写法归一化为 choice/fill/judge/answer"""
    if not value:
        return "answer"
    key = str(value).strip().lower()
    if key in _QTYPE_ALIASES:
        return _QTYPE_ALIASES[key]
    for alias, target in _QTYPE_ALIASES.items():
        if alias in key:
            return target
    return "answer"


def normalize_difficulty(value: Any) -> int:
    """难度归一化为 1-5 的整数"""
    try:
        if isinstance(value, str):
            mapping = {"易": 1, "简单": 1, "容易": 1, "较易": 2, "中": 3, "中等": 3,
                       "较难": 4, "难": 5, "困难": 5, "很难": 5}
            text = value.strip()
            if text in mapping:
                return mapping[text]
            value = float("".join(ch for ch in text if ch.isdigit() or ch == ".") or 3)
        num = int(float(value))
    except Exception:
        return 3
    return max(1, min(5, num))


# ============= 请求 / 响应模型（按 Python 3.8 兼容：使用 typing 泛型） =============

class QuestionIn(BaseModel):
    """新增题目"""
    stem: str
    options: List[str] = []
    answer: str = ""
    analysis: str = ""
    qtype: str = "answer"
    knowledge_points: List[str] = []
    difficulty: int = 3
    subject: str = ""
    grade: str = ""
    course_id: Optional[int] = None
    source: str = "manual"


class QuestionUpdate(BaseModel):
    """更新题目（仅更新传入字段）"""
    stem: Optional[str] = None
    options: Optional[List[str]] = None
    answer: Optional[str] = None
    analysis: Optional[str] = None
    qtype: Optional[str] = None
    knowledge_points: Optional[List[str]] = None
    difficulty: Optional[int] = None
    subject: Optional[str] = None
    grade: Optional[str] = None


class ImportPathRequest(BaseModel):
    """按本地路径导入课件/作业"""
    path: str
    subject: str = ""
    grade: str = ""
    title: str = ""
    max_questions: int = 30


class ExtractPreviewRequest(BaseModel):
    """解析预览（只抽取不入库）"""
    text: str = ""
    path: str = ""
    subject: str = ""
    grade: str = ""
    max_questions: int = 20


class SolveRequest(BaseModel):
    """解题讲解请求"""
    question_id: Optional[int] = None
    text: str = ""
    subject: str = ""
    save: bool = False


# ============= 第二期：错题本 / 组卷 / 推题 =============

class MistakeIn(BaseModel):
    """新增错题（可来自题库题目，也可纯手写）"""
    question_id: Optional[int] = None
    stem: str = ""
    options: List[str] = []
    subject: str = ""
    grade: str = ""
    qtype: str = "answer"
    wrong_answer: str = ""
    correct_answer: str = ""
    analysis: str = ""
    knowledge_points: List[str] = []
    difficulty: Optional[int] = None
    note: str = ""
    source: str = "manual"


class MistakeFromRequest(BaseModel):
    """由题库题目转入错题本"""
    question_id: int
    wrong_answer: str = ""
    note: str = ""
    source: str = "manual"


class MistakeUpdate(BaseModel):
    """更新错题（仅更新传入字段）"""
    subject: str = ""
    grade: str = ""
    wrong_answer: Optional[str] = None
    correct_answer: Optional[str] = None
    analysis: Optional[str] = None
    knowledge_points: Optional[List[str]] = None
    difficulty: Optional[int] = None
    note: Optional[str] = None
    status: Optional[str] = None


class ReviewRequest(BaseModel):
    """错题复习打卡"""
    result: str = "correct"        # correct=做对了 / wrong=又做错了
    note: str = ""                 # 本次复习备注


class ExamRequest(BaseModel):
    """组卷请求"""
    title: str = ""
    subject: str = ""
    grade: str = ""
    question_count: int = 10
    duration: int = 60             # 考试时长（分钟）
    total_score: int = 100         # 卷面总分
    qtypes: List[str] = []         # 限定题型，空为不限
    difficulty: Optional[int] = None      # 目标难度（1-5），None 为不限
    difficulty_mix: Optional[Dict[str, int]] = None   # 各难度题数配比，如 {"1":2,"3":5,"5":3}
    knowledge_points: List[str] = []      # 限定/优先知识点
    include_mistakes: bool = False        # 是否优先抽错题本中的薄弱题
    course_id: Optional[int] = None
    seed: Optional[int] = None     # 固定随机种子，便于复现同一份卷
    save: bool = False             # 生成后是否直接存入「我的试卷」


# ============= AI 授课子系统：请求模型 =============

class SlideGenerateRequest(BaseModel):
    """AI 教学幻灯片生成请求"""
    topic: str = ""                       # 教学主题（必填）
    subject: str = ""
    grade: str = ""
    objectives: str = ""                  # 教学目标（可留空由 AI 拟定）
    outline: str = ""                     # 教学提纲（可留空由 AI 生成）
    content: str = ""                     # 已有教学内容/知识点原文（可选）
    slide_count: int = 12
    export_pptx: bool = True
    export_html: bool = True
    theme: str = "default"

    class Config:
        extra = "ignore"


class SlideUpdate(BaseModel):
    """幻灯片记录更新"""
    title: Optional[str] = None
    subject: Optional[str] = None
    grade: Optional[str] = None
    topic: Optional[str] = None
    outline: Optional[Dict[str, Any]] = None


class FlowRequest(BaseModel):
    """授课流程总结请求"""
    path: str = ""                        # 本地课件路径
    course_id: Optional[int] = None       # 或已有课件 id
    text: str = ""                        # 或直接给原文
    subject: str = ""
    grade: str = ""
    title: str = ""
    auto_answer: bool = True              # 试题无答案时由 AI 补答案
    save_file: bool = True                # 是否落盘保存为本地文件


class RollcallGroupIn(BaseModel):
    """点名名单分组"""
    name: str
    description: str = ""


class RollcallMemberIn(BaseModel):
    """点名成员"""
    name: str
    weight: float = 1.0
    active: bool = True


class RollcallMemberBulk(BaseModel):
    """批量导入成员（每行一个名字，支持「名字,权重」）"""
    group_id: int
    text: str = ""
    names: List[str] = []
    default_weight: float = 1.0
    replace: bool = False                 # 是否先清空原成员


class RollcallDrawRequest(BaseModel):
    """点名抽取请求"""
    group_id: int
    count: int = 1                        # 本次抽几人
    no_repeat_window: Optional[int] = None  # 去重窗口，None 取配置
    algorithm: str = ""                   # timestamp / random，空取配置
    seed: str = ""                        # 可复现种子（留空用当前时间戳）


class CatalogBuildRequest(BaseModel):
    """快捷目录构建请求"""
    path: str = ""
    course_id: Optional[int] = None
    text: str = ""
    subject: str = ""
    title: str = ""
    with_ocr: bool = False                # 扫描件/图片页是否 OCR 后建目录


class CatalogMatchRequest(BaseModel):
    """按屏幕 OCR 文本匹配课件页面（悬浮球跳转）"""
    catalog_id: int
    text: str = ""                        # OCR 识别出的当前页文字
    image_path: str = ""                  # 或给截图路径（走 OCR）
    threshold: Optional[float] = None


class OcrRequest(BaseModel):
    """通用 OCR 请求"""
    image_path: str = ""
    image_base64: str = ""
    backend: str = "auto"                 # auto / local / vision
    lang: str = "ch"
    prompt: str = ""                      # 视觉模型提示词（题目识别场景可覆盖）


class VoiceAskRequest(BaseModel):
    """语音/文本问答请求（实时问答链路）"""
    text: str = ""                        # 直接文本提问（可空，走 ASR）
    audio_base64: str = ""                # 音频（base64，wav/mp3）
    audio_path: str = ""
    engine: str = "auto"                  # asr 引擎 auto / local / cloud
    language: str = "zh"
    subject: str = ""
    speak: bool = True                    # 是否 TTS 播报
    save_memory: bool = True              # 是否写入连续记忆
    session_id: str = ""                  # 会话 id（连续记忆上下文）


class AsrRequest(BaseModel):
    """纯语音识别请求"""
    audio_base64: str = ""
    audio_path: str = ""
    engine: str = "auto"
    language: str = "zh"


class WebQuestionRequest(BaseModel):
    """网络试题请求"""
    mode: str = "auto"                    # auto / api / ai_generate / ai_web
    query: str = ""                       # 知识点 / 关键词 / 要求
    subject: str = ""
    grade: str = ""
    count: int = 5
    qtype: str = ""                       # 限定题型，空为不限
    difficulty: Optional[int] = None
    save_to_bank: bool = True
    api_url: str = ""                     # 覆盖配置的第三方 API


# ============= 表单字段校验（Python 3.8 兼容：不用 PEP604） =============

class CatalogPageNote(BaseModel):
    """课件页面元素记录（目录用）"""
    page: int
    title: str = ""
    elements: List[str] = []
    text: str = ""


def teaching_settings(section: str = "") -> Dict[str, Any]:
    """读取 edu.teaching 配置（可按子节读取）。

    section 为空返回整个 teaching 配置字典；否则返回 teaching[section]（缺失返回 {}）。
    """
    try:
        from zhiyan_web.config import get_settings
        teaching = (get_settings().get("edu") or {}).get("teaching") or {}
    except Exception:
        teaching = {}
    if not section:
        return teaching if isinstance(teaching, dict) else {}
    sub = teaching.get(section)
    return sub if isinstance(sub, dict) else {}