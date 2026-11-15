"""教辅文档解析与题目抽取"""
from __future__ import annotations

from .document import extract_pages, extract_text, supported_extensions
from .extractor import extract_questions

__all__ = ["extract_text", "extract_pages", "supported_extensions", "extract_questions"]