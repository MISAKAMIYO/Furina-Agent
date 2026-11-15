# -*- coding: utf-8 -*-
"""验证普通模式与 Agent 模式的提示词"""
import sys, asyncio, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r'D:\Python\NEW_start\ZHIYAN AGENT WEB\backend')

from zhiyan_web.core.llm.chat_manager import ChatManager
from zhiyan_web.config.settings import get_settings

cm = ChatManager()

print("=== 普通模式 system_prompt（前 300 字） ===")
sp = cm._get_system_prompt()
print(sp[:300])
print()
print("普通模式含『智言』:", "智言" in sp, "| 含『智言妹妹』:", "智言妹妹" in sp)
print()

async def main():
    agent_prompt = await cm._build_agent_prompt(sp, "帮我写个python脚本", [])
    print("=== Agent 模式提示词（最后 600 字） ===")
    print(agent_prompt[-600:])
    print()
    print("Agent 模式含『智言』:", "智言" in agent_prompt)
    print("Agent 模式含『任务执行模式』指南:", "任务执行模式" in agent_prompt)
    print("Agent 模式含『聚焦任务本身』:", "聚焦任务本身" in agent_prompt)
    print("Agent 模式含『避免过多情绪化』:", "避免过多情绪化" in agent_prompt)

asyncio.run(main())
