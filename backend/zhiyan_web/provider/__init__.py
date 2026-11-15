"""
提供商管理模块

提供 LLM/STT/TTS/Embedding 提供商管理和函数工具管理。

模块结构:
- func_tool_manager.py  - 函数工具管理器（工具注册 + MCP 客户端）
- manager.py            - 提供商管理器（多实例管理、会话隔离）
"""
from __future__ import annotations