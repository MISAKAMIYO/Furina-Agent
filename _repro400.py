# -*- coding: utf-8 -*-
"""用完整服务器调用链复现 400，捕获响应体"""
import sys, asyncio, io, logging
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
logging.basicConfig(level=logging.ERROR, stream=sys.stdout, format='%(levelname)s %(message)s')
sys.path.insert(0, r'D:\Python\NEW_start\ZHIYAN AGENT WEB\backend')

from zhiyan_web.core.llm.chat_manager import ChatManager
from zhiyan_web.core.llm.providers import OpenAIProvider, LLMMessage
from zhiyan_web.config.settings import get_settings

settings = get_settings()
llm = settings.get("llm", {})
KEY = llm.get("api_key") or settings.get("api_key", "")
MODEL = llm.get("model") or settings.get("model", "deepseek-chat")
BASE = llm.get("base_url") or "https://api.deepseek.com"
print(f"== key={KEY[:8]}... model={MODEL} base={BASE} ==")

provider = OpenAIProvider(
    base_url=BASE,
    provider_name="deepseek",
    api_key=KEY,
    model=MODEL,
    thinking_enabled=settings.get("deepseek_thinking_enabled", False),
    reasoning_effort=settings.get("deepseek_reasoning_effort", "high"),
)

cm = ChatManager(provider=provider)

async def main():
    cm.add_message("user", "帮我写一个python脚本统计文件夹里文件数量")
    tools = cm._get_registered_tools()
    print("工具数量:", len(tools))
    sp = cm._get_system_prompt()
    agent_prompt = await cm._build_agent_prompt(sp, "帮我写一个python脚本统计文件夹里文件数量", tools)
    kwargs = {
        "system_prompt": agent_prompt,
        "tools": cm._build_openai_tools(tools),
    }
    dc = cm._get_deepseek_config()
    kwargs.setdefault("thinking_enabled", dc["thinking_enabled"])
    kwargs.setdefault("reasoning_effort", dc["reasoning_effort"])
    kwargs.setdefault("response_format", dc["response_format"])
    print("thinking_enabled:", dc["thinking_enabled"], "| reasoning_effort:", dc["reasoning_effort"])
    print("system_prompt 长度:", len(agent_prompt))
    resp = await provider.chat(cm._conversations["default"].messages, **kwargs)
    print("RESULT:", resp.content[:500])

asyncio.run(main())
