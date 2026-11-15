"""
AI 教学幻灯片（slides）子模块

能力:
- generator.py   AI 生成教学幻灯片结构化大纲（教学目标/提纲/知识点详解/例题演练）
- pptx_writer.py 用 python-pptx 生成 .pptx（缺失依赖时降级）
- html_render.py 生成无依赖 HTML 幻灯片（可全屏放映，适配希沃浏览器/白板）
- seewo.py       希沃原生格式框架（私有格式，预留）
- service.py     编排：生成 → 入库 → 导出文件

对外主接口: service.generate_slides(request) -> dict
"""
from __future__ import annotations

from .service import generate_slides

__all__ = ["generate_slides"]