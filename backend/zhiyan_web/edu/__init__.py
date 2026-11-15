"""
AI 教辅子系统（edu）

第一期能力：
- 课件/作业解析入库（PDF/PPTX/DOCX/XLSX/TXT/MD）
- 题库（结构化题目存储、标签、检索、统计）
- 解题讲解（基于 LLM 的一次性调用，输出解题步骤/答案/易错点）

目录结构:
- models.py          数据模型与数据库表
- bank.py            题库服务（增删改查/统计/导入编排）
- solver.py          解题与讲解
- parsers/           文档解析与题目结构化抽取
- routers/           FastAPI 路由
"""
from __future__ import annotations

from .models import init_edu_tables, normalize_difficulty, normalize_qtype

__all__ = ["init_edu_tables", "normalize_qtype", "normalize_difficulty"]