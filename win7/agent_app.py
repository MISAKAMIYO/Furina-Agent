# -*- coding: utf-8 -*-
"""
agent_app.py — Win7（Python 3.8.10）精简后端。

只挂 Agent 需要的路由：设置、会话/历史、流式对话、工具、记忆。
音乐、BetterGI、OCR、截屏、插件市场、DSH 等依赖重（或 Win7 不可用）的模块不加载。

用法：
    python win7/launch.py            # 自动挑端口、起 uvicorn、打开浏览器
    ZHIYAN_PORT=8001 python win7/launch.py
"""
from __future__ import annotations

import os
import sys

# ── 路径：backend/ 进 sys.path，win7/ 的上级进 sys.path（为了 import win7.compat38）
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_BACKEND = os.path.join(_ROOT, "backend")
for p in (_ROOT, _BACKEND):
    if p not in sys.path:
        sys.path.insert(0, p)

import win7.compat38  # noqa: F401  必须在任何 zhiyan_web 模块之前

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from zhiyan_web.config import FRONTEND_DIR, get_settings, load_settings, update_settings
from zhiyan_web.core import init_core

load_settings()
init_core()


def _init_llm_provider() -> None:
    """按当前设置创建 LLM 提供商并挂到 chat_manager（与 app.py 的 _init_llm_provider 等价）。"""
    from zhiyan_web.core import chat_manager
    from zhiyan_web.core.llm.providers import MockProvider, create_provider

    settings = get_settings()
    provider_type = (settings.get("provider", "mock") or "mock").lower()
    preset = (
        "deepseek", "zhipu", "qwen", "moonshot", "doubao", "minimax", "hunyuan", "ernie",
        "spark", "siliconflow", "together", "gemini", "anthropic", "xai", "mistral", "groq", "openrouter",
    )
    if provider_type == "mock":
        provider = MockProvider()
    else:
        key = settings.get("openrouter_api_key", "") or settings.get("api_key", "") if provider_type == "openrouter" else settings.get("api_key", "")
        model = settings.get("model", "gpt-3.5-turbo")
        api_url = settings.get("api_url", "https://api.openai.com/v1")
        kwargs = {"response_format": settings.get("response_format", "text")}
        if provider_type == "deepseek":
            kwargs["thinking_enabled"] = settings.get("deepseek_thinking_enabled", False)
            kwargs["reasoning_effort"] = settings.get("deepseek_reasoning_effort", "high")
        if not key:
            print("[win7] 未配置 API Key，回退到 mock")
            provider = MockProvider()
        elif provider_type in preset:
            provider = create_provider(provider_type, api_key=key, model=model, **kwargs)
        else:
            provider = create_provider("openai", api_key=key, model=model, base_url=api_url,
                                       provider_name=provider_type, **kwargs)
    if provider is None:
        provider = MockProvider()
    chat_manager.set_provider(provider)
    print(f"[win7] LLM provider: {provider.name}")


_init_llm_provider()

logger_name = "ZhiYanWeb.Win7"

app = FastAPI(title="Furina Agent (Win7 / Agent only)", version="1.0.0-win7")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 静态资源 ────────────────────────────────────────────────────────────
_static_dir = os.path.join(FRONTEND_DIR, "static")
if os.path.isdir(_static_dir):
    app.mount("/static", StaticFiles(directory=_static_dir), name="static")

_miao_img_dir = os.path.join(_ROOT, "miao_img")
if os.path.isdir(_miao_img_dir):
    app.mount("/miao_img", StaticFiles(directory=_miao_img_dir), name="miao_img")


# ── 路由：只挂 Agent 必需的 ─────────────────────────────────────────────
def _try_include(label, fn):
    try:
        fn()
        print(f"[win7] router ok: {label}")
    except Exception as exc:  # 单个路由失败不影响整体
        print(f"[win7] router skipped: {label} ({type(exc).__name__}: {exc})")


def _chat():
    from zhiyan_web.chat.routers import chat_router

    app.include_router(chat_router)


def _memory():
    from zhiyan_web.memory.routers.memory import router as memory_router

    app.include_router(memory_router)


def _likability():
    from zhiyan_web.likability.routers.likability import router as likability_router

    app.include_router(likability_router)


def _tools():
    from zhiyan_web.computer.register_tools import register_computer_tools

    register_computer_tools()


_try_include("chat", _chat)
_try_include("memory", _memory)
_try_include("likability", _likability)
_try_include("computer-tools", _tools)


# ── 设置 ────────────────────────────────────────────────────────────────
class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")


@app.get("/api/settings")
async def get_settings_api():
    return get_settings()


@app.post("/api/settings")
async def update_settings_api(data: SettingsUpdate):
    try:
        payload = {k: v for k, v in data.model_dump().items() if v is not None}
        if payload.pop("_reset", False):
            from zhiyan_web.config.settings import DEFAULT_SETTINGS
            import copy

            update_settings(copy.deepcopy(DEFAULT_SETTINGS))
            return {"status": "success", "message": "配置已重置为默认值"}
        update_settings(payload)
        return {"status": "success", "message": "配置已保存"}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(exc)})


@app.get("/api/health")
async def health():
    return {"status": "ok", "edition": "win7-agent", "python": sys.version.split()[0]}


# ── 页面 ────────────────────────────────────────────────────────────────
@app.get("/")
async def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))
