"""
Furina Agent Web - FastAPI 应用主入口

负责:
- 创建 FastAPI 应用实例
- 挂载静态文件
- 注册所有路由
- 初始化全局状态
"""
from __future__ import annotations

import os
import json
import asyncio
import logging
import sys
from datetime import datetime

# Windows 事件循环修复：确保子进程支持（Playwright 需要 ProactorEventLoop）
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except AttributeError:
        pass

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ============= 导入核心模块 =============
from zhiyan_web.config import (
    FRONTEND_DIR,
    DATA_DIR,
    PROJECT_ROOT,
    TTS_DIR,
    load_settings,
    get_settings,
    update_settings,
)
from zhiyan_web.core import init_core
from zhiyan_web.core.llm.providers import MockProvider, create_provider

# 延迟获取核心实例的辅助函数
def get_chat_manager():
    from zhiyan_web.core import chat_manager
    return chat_manager

def get_tts_engine():
    from zhiyan_web.core import tts_engine
    return tts_engine

# ============= 配置日志 =============
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("ZhiYanWeb")

# ============= 创建 FastAPI 应用 =============
app = FastAPI(title="Furina Agent Web", version="1.0.0")

# ============= CORS 中间件配置 =============
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============= 初始化核心模块 =============
load_settings()
init_core()

# ============= 挂载静态文件 =============
app.mount("/static", StaticFiles(directory=os.path.join(FRONTEND_DIR, 'static')), name="static")

# miao-plugin 出图目录静态挂载：网页端用 /miao_img/<name> 直接展示图片
_miao_img_dir = os.path.join(DATA_DIR, "miao_node")
try:
    os.makedirs(_miao_img_dir, exist_ok=True)
    app.mount("/miao_img", StaticFiles(directory=_miao_img_dir), name="miao_img")
except Exception as e:
    print(f"[app] 挂载 /miao_img 失败: {e}")

# ============= 导入并注册路由（延迟导入，确保核心模块已初始化） =============
from zhiyan_web.chat.routers import chat_router, avatar_router
from zhiyan_web.music.routers import music_router, download_router, netease_router, kugou_router, qq_router
from zhiyan_web.economy.routers import economy_router
from zhiyan_web.bottle.routers import bottle_router
from zhiyan_web.gomoku.routers import gomoku_router
from zhiyan_web.blindbox.routers import blindbox_router
from zhiyan_web.wheel.routers import wheel_router
from zhiyan_web.imagegen.routers import imagegen_router
from zhiyan_web.codeexec.routers import codeexec_router
from zhiyan_web.emotion.routers.emotion import emotion_router, set_emotion_engine, set_bond_engine, set_drift_detector
from zhiyan_web.proactive.routers.proactive import proactive_router, set_proactive_engine, set_scheduler_engine
from zhiyan_web.plugins.routers.plugins import plugin_router, set_plugin_manager
from zhiyan_web.translate.routers.translate import router as translate_router
from zhiyan_web.memory.routers.memory import router as memory_router, init_memory_table
from zhiyan_web.knowledge.routers.knowledge import router as knowledge_router, init_knowledge_table
from zhiyan_web.likability.routers.likability import router as likability_router, init_likability_data
from zhiyan_web.agents.routers.agents import agent_router
from zhiyan_web.projects.routers.projects import project_router
from zhiyan_web.computer.routers.computer import router as computer_router
from zhiyan_web.computer.routers.code_writer import router as code_writer_router
from zhiyan_web.computer.routers.planner import router as planner_router
from zhiyan_web.computer.routers.command_prompt import router as command_prompt_router
from zhiyan_web.skills.routers import router as skills_router
from zhiyan_web.computer.register_tools import register_computer_tools
from zhiyan_web.platform.register_qq_tools import register_qq_tools
from zhiyan_web.bettergi.register_tools import register_bettergi_tools
from zhiyan_web.code_editor.routers.editor import router as editor_router
from zhiyan_web.ffmpeg.routers.ffmpeg import router as ffmpeg_router
from zhiyan_web.plugin_files import router as plugin_files_router
from zhiyan_web.github_proxy.routers import router as github_proxy_router
from zhiyan_web.dsh import router as dsh_router
from zhiyan_web.screencap.routers import router as screencap_router
from zhiyan_web.files.routers import router as files_router
from zhiyan_web.context_compress.routers import router as context_compress_router
from zhiyan_web.usage.routers import router as usage_router
from zhiyan_web.platform.routers import router as qq_platform_router
from zhiyan_web.platform.official_routers import router as qq_official_router
from zhiyan_web.edu.routers import edu_router, teach_router, init_edu_tables
from zhiyan_web.mcp.routers import router as mcp_router
from zhiyan_web.extensions.routers import router as extensions_router
from zhiyan_web.subagent.routers import router as subagent_router
from zhiyan_web.bettergi.routers import router as bettergi_router

app.include_router(chat_router)
app.include_router(avatar_router)
app.include_router(music_router)
app.include_router(download_router)
app.include_router(netease_router)
app.include_router(kugou_router)
app.include_router(qq_router)
app.include_router(economy_router)
app.include_router(bottle_router)
app.include_router(gomoku_router)
app.include_router(blindbox_router)
app.include_router(wheel_router)
app.include_router(imagegen_router)
app.include_router(codeexec_router)
app.include_router(emotion_router)
app.include_router(proactive_router)
app.include_router(plugin_router)
app.include_router(translate_router)
app.include_router(memory_router)
app.include_router(knowledge_router)
app.include_router(likability_router)
app.include_router(agent_router)
app.include_router(project_router)
app.include_router(computer_router)
app.include_router(code_writer_router)
app.include_router(planner_router)
app.include_router(command_prompt_router)
app.include_router(skills_router)
app.include_router(editor_router)
app.include_router(ffmpeg_router)
app.include_router(plugin_files_router)
app.include_router(github_proxy_router)
app.include_router(dsh_router)
app.include_router(screencap_router)
app.include_router(files_router)
app.include_router(context_compress_router)
app.include_router(usage_router)
app.include_router(qq_platform_router)
app.include_router(qq_official_router)
app.include_router(edu_router)
app.include_router(teach_router)
app.include_router(mcp_router)
app.include_router(extensions_router)
app.include_router(subagent_router)
app.include_router(bettergi_router)

from zhiyan_web.system.elevation import is_admin as _is_admin, restart_as_admin as _restart_as_admin, build_restart_command as _build_restart_command


@app.get("/api/system/elevation")
async def system_elevation():
    """当前进程权限状态 + 提权重启命令预览（供 UI 提示）"""
    return {"success": True, "is_admin": _is_admin(), "restart": _build_restart_command()}


@app.post("/api/system/restart-as-admin")
async def system_restart_as_admin(payload: Dict[str, Any] = None):
    """以管理员身份重启本程序。

    安全策略：默认只返回「将要执行的命令」，必须带 {"confirm": true} 才会真的弹 UAC 重启，
    避免误调用把正在运行的程序弹掉。
    """
    data = payload if isinstance(payload, dict) else {}
    confirm = bool(data.get("confirm"))
    if not confirm:
        return {"success": True, "data": _restart_as_admin(dry_run=True),
                "hint": "预览模式：要真正重启请传 {\"confirm\": true}"}
    return {"success": True, "data": _restart_as_admin(dry_run=False)}


# ============= 初始化各模块数据库表 =============
try:
    init_memory_table()
    init_knowledge_table()
    init_likability_data()
    init_edu_tables()
    logger.info("各功能模块数据库表初始化完成")
except Exception as e:
    logger.warning(f"部分模块数据库表初始化失败: {e}")

# ============= 注册电脑操控工具到 FunctionToolManager =============
try:
    register_computer_tools()
    logger.info("电脑操控工具注册完成")
except Exception as e:
    logger.warning(f"电脑操控工具注册失败: {e}")

# ============= 加载声明式扩展清单（extension.json 贡献的子代理/工具分组/钩子）=============
# 放在工具注册之后：扩展可能追加工具分组，也可能注册子代理类型与钩子。
try:
    from zhiyan_web.extensions.registry import load_manifests
    _ext_summary = load_manifests()
    if _ext_summary.get("loaded"):
        logger.info(
            "扩展清单加载完成: %d 份（工具分组 %d，子代理 %d，钩子 %d）",
            len(_ext_summary["loaded"]), _ext_summary["tool_groups"],
            _ext_summary["subagents"], _ext_summary["hooks"],
        )
    for _err in _ext_summary.get("errors") or []:
        logger.debug("扩展清单告警: %s", _err)
except Exception as e:
    logger.warning(f"加载扩展清单失败: {e}")

# ============= 回灌自定义子代理类型（data/subagent_types.json）=============
try:
    from zhiyan_web.subagent.types_store import load_into_registry
    load_into_registry()
except Exception as e:
    logger.warning(f"加载自定义子代理类型失败: {e}")

# ============= 注册 QQ 平台工具到 FunctionToolManager =============
try:
    register_qq_tools()
    logger.info("QQ 平台工具注册完成")
except Exception as e:
    logger.warning(f"QQ 平台工具注册失败: {e}")

# ============= 注册 BetterGI 托管工具到 FunctionToolManager =============
try:
    register_bettergi_tools()
except Exception as e:
    logger.warning(f"BetterGI 工具注册失败: {e}")

# ============= 初始化 Skills 系统 =============
try:
    from zhiyan_web.skills import get_skill_manager
    import os
    skills_dir = os.path.join(DATA_DIR, "skills")
    os.makedirs(skills_dir, exist_ok=True)
    skill_manager = get_skill_manager()
    skill_manager.set_skills_dir(skills_dir)
    # 异步加载技能
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(skill_manager.load_all())
    except RuntimeError:
        pass
    logger.info(f"Skills 系统已初始化，技能目录: {skills_dir}")
except Exception as e:
    logger.warning(f"Skills 系统初始化失败: {e}")

# ============= 初始化上下文压缩系统（连续压缩 + 历史定期压缩） =============
try:
    from zhiyan_web.context_compress.manager import get_context_compress_manager
    context_compress_manager = get_context_compress_manager()
    logger.info("上下文压缩系统已初始化")
except Exception as e:
    logger.warning(f"上下文压缩系统初始化失败: {e}")

@app.on_event("startup")
async def _start_context_compress_task():
    """启动上下文压缩后台任务（历史对话定期压缩）"""
    try:
        from zhiyan_web.context_compress.manager import get_context_compress_manager
        get_context_compress_manager().start_background_task()
        logger.info("上下文压缩后台任务已启动")
    except Exception as e:
        logger.warning(f"上下文压缩后台任务启动失败: {e}")


@app.on_event("startup")
async def _auto_start_yunzai():
    """按 miao_plugin.auto_start 自动拉起 TRSS-Yunzai 原生进程。

    - auto_start 开启：启动 TRSS-Yunzai（默认端口 2536，NapCat 反向 WS 接入）。
    - auto_start 关闭：不自动启动。
    """
    try:
        from zhiyan_web.config import get_settings
        from zhiyan_web.platform.yunzai_manager import yunzai_status, ensure_yunzai

        mp = get_settings().get("miao_plugin") or {}
        if not mp.get("auto_start"):
            logger.info("[yunzai] auto_start 未开启，跳过自动启动（可到设置页手动拉起）")
            return
        if yunzai_status().get("running"):
            logger.info("[yunzai] TRSS-Yunzai 已在运行，跳过启动")
            return
        logger.info("[yunzai] auto_start 开启，后台自动启动 TRSS-Yunzai…")

        async def _worker(fn):
            ok, msg = await asyncio.to_thread(fn)
            if ok:
                logger.info(f"[yunzai] 自动启动完成: {msg}")
            else:
                logger.warning(f"[yunzai] 自动启动失败: {msg}")

        asyncio.get_running_loop().create_task(_worker(ensure_yunzai))
    except Exception as e:
        logger.warning(f"[yunzai] 自动启动异常: {e}")

# 说明：QQ 平台（NapCat）不再随应用启动自动连接，
# 由用户在「设置 → QQ 平台」页面点击「启动 / 停止」手动控制。


@app.on_event("startup")
async def _auto_start_bettergi_monitor():
    """按 bettergi.auto_monitor 自动开始托管监控（默认关闭）"""
    try:
        from zhiyan_web.bettergi.service import get_bettergi_manager

        mgr = get_bettergi_manager()
        cfg = mgr.get_config()
        if cfg.get("enabled") and cfg.get("auto_monitor"):
            await mgr.start_monitor()
            logger.info("[bettergi] 已按配置自动开始监控")
        else:
            logger.info("[bettergi] 未开启自动监控，跳过")

        # 通知端点跟随当前服务端口同步（桌面壳端口每次可能不同）
        if cfg.get("auto_setup_notify"):
            port = os.environ.get("ZHIYAN_PORT", "8001")
            base_url = f"http://127.0.0.1:{port}"
            result = await asyncio.to_thread(mgr.sync_notification_endpoint, base_url, cfg.get("notify_transport"))
            if result.get("changed"):
                logger.info(f"[bettergi] 通知端点已同步: {result.get('changes', {}).get('webhookEndpoint')}")
            else:
                logger.info(f"[bettergi] 通知端点无需更新: {result.get('message')}")

        # 定时任务自动启动
        if cfg.get("schedule_enabled"):
            await mgr.start_scheduler()
            logger.info("[bettergi] 定时任务调度已自动启动")

        # 开机自启状态兜底（配置开了但注册表缺失时补写）
        if cfg.get("auto_start_on_boot") and not mgr.get_autostart().get("enabled"):
            try:
                mgr.set_autostart(True)
            except Exception as e:
                logger.warning(f"[bettergi] 写入开机自启失败: {e}")

        # 开机任务计划（默认关闭）：BGI 未运行且上次非任务异常中止时检查更新
        if cfg.get("boot_task_plan"):
            result = await mgr.boot_task_plan_startup()
            logger.info(f"[bettergi] 开机任务计划: {result.get('message')}")
    except Exception as e:
        logger.warning(f"[bettergi] 自动监控/通知同步失败: {e}")


@app.on_event("startup")
async def _auto_start_qq_official_ws():
    """QQ 官方机器人：启用且配置完成时自动启动 WebSocket 接收"""
    try:
        from zhiyan_web.config import get_settings
        from zhiyan_web.platform import qq_official

        cfg = get_settings().get("qq_official") or {}
        if cfg.get("enabled") and cfg.get("ws_enabled", True) and qq_official.configured():
            result = await qq_official.get_client().start()
            logger.info("[qq-official] %s", result.get("message"))
        else:
            logger.info("[qq-official] 未启用或未配置，跳过 WebSocket 接收")
    except Exception as e:
        logger.warning(f"[qq-official] 自动启动接收失败: {e}")


@app.on_event("shutdown")
async def _stop_qq_official_ws():
    try:
        from zhiyan_web.platform import qq_official

        await qq_official.get_client().stop()
    except Exception:
        pass


# ============= TTS 路由 =============

class TTSRequest(BaseModel):
    text: str
    emotion: str = "default"
    text_lang: str = "zh"
    prompt_lang: str = "ja"
    speed: float = 1.0


@app.post("/api/tts/synthesize")
async def synthesize_tts(request: TTSRequest):
    """
    合成语音
    
    参数:
        request: TTS 请求对象
    
    返回:
        dict: 合成结果
    """
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="文本内容不能为空")
    
    result = {"success": False, "error": None, "audio_url": None}
    event = asyncio.Event()
    
    # 根据请求参数更新引擎配置
    engine = get_tts_engine()
    if request.text_lang:
        engine.gpt_sovits_text_language = request.text_lang
    if request.prompt_lang:
        engine.gpt_sovits_prompt_language = request.prompt_lang
    if request.speed:
        engine.gpt_sovits_speed = request.speed
    
    def on_complete(file_path):
        nonlocal result
        result["success"] = True
        result["audio_url"] = f"/api/tts/audio/{os.path.basename(file_path)}"
        # 时长校验：GPT-SoVITS 异常时常返回极短音频，需明确提示以免误判为播放故障
        try:
            result["duration"] = None
            try:
                import wave
                with wave.open(file_path, 'rb') as wf:
                    result["duration"] = wf.getnframes() / float(wf.getframerate() or 1)
            except Exception:
                result["duration"] = None
            n_text = len(request.text.strip())
            if result["duration"] is not None and result["duration"] < 0.6 and n_text > 2:
                result["warning"] = (
                    f"合成结果异常过短（仅 {result['duration']:.2f} 秒），疑似 GPT-SoVITS 引擎异常。"
                    "建议检查 GPT-SoVITS 服务/模型加载，或切换其它合成引擎后重试。"
                )
        except Exception as e:
            logger.warning(f"TTS 时长校验失败: {e}")
        event.set()
    
    def on_error(error):
        nonlocal result
        result["error"] = error
        event.set()
    
    try:
        if request.emotion and request.emotion != "default":
            engine.synthesize_with_emotion(request.text, emotion=request.emotion, on_complete=on_complete, on_error=on_error)
        else:
            engine.synthesize(request.text, on_complete=on_complete, on_error=on_error)
        
        await asyncio.wait_for(event.wait(), timeout=180)
        
        if result["success"]:
            # 保存历史记录（使用实际文件名）
            try:
                actual_filename = os.path.basename(result["audio_url"])
                _save_tts_history(request, actual_filename)
            except (OSError, KeyError) as e:
                logger.warning(f"保存 TTS 历史记录失败: {e}")
            return result
        else:
            raise HTTPException(status_code=500, detail=result["error"])
    
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="TTS 合成超时")
    except Exception as e:
        logger.error(f"TTS 合成错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _save_tts_history(request, actual_filename: str = None):
    """保存 TTS 合成历史记录"""
    history_file = os.path.join(TTS_DIR, "history.json")
    try:
        history = []
        if os.path.exists(history_file):
            with open(history_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
        
        import datetime
        now = datetime.datetime.now()
        timestamp = now.strftime("%H:%M")
        # 使用实际生成的文件名，确保历史播放能正确找到音频文件
        filename = actual_filename if actual_filename else f"tts_{now.strftime('%Y%m%d_%H%M%S')}.wav"
        entry = {
            "time": timestamp,
            "text": request.text[:100],
            "emotion": request.emotion,
            "text_lang": request.text_lang,
            "speed": request.speed,
            "filename": filename,
            "size": "",
            "url": ""
        }
        history.insert(0, entry)
        
        # 最多保留 100 条
        if len(history) > 100:
            history = history[:100]
        
        os.makedirs(os.path.dirname(history_file), exist_ok=True)
        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存 TTS 历史失败: {e}")


@app.get("/api/tts/history")
async def get_tts_history():
    """
    获取 TTS 合成历史
    
    返回:
        dict: 历史记录列表
    """
    history_file = os.path.join(TTS_DIR, "history.json")
    history = []
    if os.path.exists(history_file):
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                saved = json.load(f)
            # 补充文件信息
            for item in saved:
                filename = item.get("filename", "")
                if filename:
                    filepath = os.path.join(TTS_DIR, filename)
                    if os.path.exists(filepath):
                        size = os.path.getsize(filepath)
                        size_str = f"{size/1024:.1f} KB" if size < 1024*1024 else f"{size/1024/1024:.1f} MB"
                        item["size"] = size_str
                        item["url"] = f"/api/tts/audio/{filename}"
                # 确保 text 字段存在
                if "text" not in item:
                    item["text"] = ""
                history.append(item)
        except Exception as e:
            logger.warning(f"读取 TTS 历史失败: {e}")
    
    return {"history": history}


@app.post("/api/tts/clear-history")
async def clear_tts_history():
    """
    清空 TTS 合成历史
    
    返回:
        dict: 操作结果
    """
    history_file = os.path.join(TTS_DIR, "history.json")
    try:
        if os.path.exists(history_file):
            os.remove(history_file)
        return {"status": "success", "message": "历史记录已清空"}
    except Exception as e:
        logger.warning(f"清空 TTS 历史失败: {e}")
        raise HTTPException(status_code=500, detail=f"清空历史记录失败: {str(e)}")


@app.get("/api/tts/audio/{filename}")
async def get_tts_audio(filename: str):
    """
    获取 TTS 音频文件
    
    参数:
        filename: 音频文件名
    
    返回:
        FileResponse: 音频文件响应
    """
    audio_path = os.path.join(TTS_DIR, filename)
    
    if not os.path.exists(audio_path):
        raise HTTPException(status_code=404, detail="音频文件不存在")
    
    ext = os.path.splitext(filename)[1].lower()
    mime_types = {
        '.mp3': 'audio/mpeg',
        '.wav': 'audio/wav',
    }
    
    return FileResponse(audio_path, media_type=mime_types.get(ext, 'audio/wav'))


@app.get("/api/tts/emotions")
async def get_tts_emotions():
    """
    获取支持的情绪列表
    
    返回:
        dict: 包含 emotions 字段
    """
    emotions = []
    for key, config in get_tts_engine().gpt_sovits_emotions.items():
        emotions.append({
            "key": key,
            "name": config.get("name", key),
            "keywords": config.get("keywords", []),
        })
    return {"emotions": emotions}


@app.get("/api/tts/voices")
async def get_tts_voices():
    """
    获取可用的 TTS 语音列表
    
    返回:
        dict: 包含 voices 列表
    """
    try:
        import edge_tts
        voices = await edge_tts.list_voices()
        result = []
        for v in voices:
            result.append({
                "name": v.get("ShortName", v.get("Name", "")),
                "locale": v.get("Locale", ""),
                "gender": v.get("Gender", ""),
                "local_name": v.get("LocalName", ""),
            })
        return {"voices": result}
    except Exception as e:
        logger.warning(f"获取语音列表失败: {e}")
        return {"voices": [], "error": str(e)}


@app.get("/api/tts/gpt-sovits/status")
async def get_gpt_sovits_status():
    """
    测试 GPT-SoVITS 连接状态（支持 API v2）
    
    返回:
        dict: 连接状态和详细信息
    """
    try:
        import aiohttp
        config = get_settings().get("gpt_sovits", {})
        host = config.get("host", "127.0.0.1")
        port = config.get("port", 9880)
        
        # 尝试连接 /status 接口
        status_url = f"http://{host}:{port}/status"
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(status_url, timeout=3) as response:
                    if response.status == 200:
                        try:
                            info = await response.json()
                        except (json.JSONDecodeError, aiohttp.ContentTypeError):
                            info = {}
                        return {
                            "status": "connected",
                            "message": "GPT-SoVITS 服务正常",
                            "api_version": "v2",
                            "host": host,
                            "port": port,
                            "info": info
                        }
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            
            # 如果 /status 不可用，尝试 /tts 接口（API v2 方式）
            tts_url = f"http://{host}:{port}/tts"
            try:
                async with session.post(tts_url, json={"text": "test", "text_lang": "zh"}, timeout=3) as response:
                    if response.status in [200, 422, 400]:
                        return {
                            "status": "connected",
                            "message": "GPT-SoVITS 服务正常（API v2）",
                            "api_version": "v2",
                            "host": host,
                            "port": port
                        }
            except (aiohttp.ClientError, asyncio.TimeoutError):
                pass
            
            return {
                "status": "disconnected",
                "message": f"无法连接 GPT-SoVITS ({host}:{port})，请确认服务已启动",
                "host": host,
                "port": port
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"连接检查失败: {str(e)}"
        }


@app.get("/api/tts/gpt-sovits/info")
async def get_gpt_sovits_config_info():
    """
    获取 GPT-SoVITS 当前完整配置信息
    
    返回:
        dict: 当前配置信息
    """
    config = get_settings().get("gpt_sovits", {})
    return {
        "config": config,
        "engine": get_tts_engine().get_engine_info()
    }


# ============= 设置路由 =============

# 插件列表图片目录
PLUGIN_LIST_DIR = os.path.join(DATA_DIR, 'plugin_list_images')
os.makedirs(PLUGIN_LIST_DIR, exist_ok=True)


@app.get("/api/plugin_list/image/{filename}")
async def get_plugin_list_image(filename: str):
    """
    获取插件列表图片

    参数:
        filename: 图片文件名

    返回:
        FileResponse: 图片文件响应
    """
    image_path = os.path.join(PLUGIN_LIST_DIR, filename)

    if not os.path.exists(image_path):
        raise HTTPException(status_code=404, detail="图片文件不存在")

    ext = os.path.splitext(filename)[1].lower()
    mime_types = {
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.webp': 'image/webp',
        '.gif': 'image/gif',
    }

    return FileResponse(image_path, media_type=mime_types.get(ext, 'image/png'))


# ============= 喵喵插件(miao-plugin)图片服务 =============

MIAO_IMAGE_DIR = os.path.join(DATA_DIR, 'miao', 'images')
os.makedirs(MIAO_IMAGE_DIR, exist_ok=True)


@app.get("/api/miao/image/{filename}")
async def get_miao_image(filename: str):
    """
    获取喵喵插件生成的图片卡片（miao-plugin 动态渲染的图片）

    参数:
        filename: 图片文件名

    返回:
        FileResponse: 图片文件响应
    """
    # 目录穿越防护：确保文件名仅为合法文件名
    base = os.path.basename(filename)
    if base != filename:
        raise HTTPException(status_code=400, detail="非法文件名")

    image_path = os.path.join(MIAO_IMAGE_DIR, base)
    if not os.path.exists(image_path):
        raise HTTPException(status_code=404, detail="图片文件不存在")

    ext = os.path.splitext(base)[1].lower()
    mime_types = {
        '.png': 'image/png',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.webp': 'image/webp',
        '.gif': 'image/gif',
    }
    return FileResponse(image_path, media_type=mime_types.get(ext, 'image/png'))


class SettingsUpdate(BaseModel):
    # LLM 配置
    provider: str = None
    api_key: str = None
    model: str = None
    api_url: str = None
    openrouter_api_key: str = None
    
    # LLM 模型参数
    max_tokens: int = None
    temperature: float = None
    top_p: float = None
    frequency_penalty: float = None
    presence_penalty: float = None
    system_prompt: str = None
    response_format: str = None
    n: int = None
    best_of: int = None
    stream: bool = None
    
    # DeepSeek 思考模式
    deepseek_thinking_enabled: bool = None
    deepseek_reasoning_effort: str = None
    
    # Agent 配置
    max_tool_iterations: int = None
    agent_workspace: str = None
    # Agent 工具场景裁剪（full / chat / coding / research / computer / lightweight）
    agent_tool_scenario: str = None
    # 子代理能力边界
    subagent_max_depth: int = None
    subagent_max_runs: int = None
    subagent_max_iterations: int = None
    subagent_concurrency: int = None

    # 对话回复引擎配置项（normal / agent / dsh），默认 agent，保存后对后续对话立即生效
    reply_engine: str = None
    # dsh 引擎安全管控：删除操作走回收站
    dsh_safe_delete: bool = None

    # GitHub 加速代理（结合 astrbot2github）
    github_proxy: str = None

    # 聊天主区域背景图（芙宁娜主题）
    chat_bg: dict = None
    
    # 上下文压缩配置
    context_compress: dict = None
    
    # DSH (DeepSeek Harness) 内核对话引擎
    dsh: dict = None
    
    # TTS 配置
    tts_enabled: bool = None
    tts_engine: str = None
    tts_voice: str = None
    tts_rate: int = None
    tts_volume: float = None
    tts_pitch: int = None
    tts_language: str = None
    tts_auto_clean: bool = None
    tts_max_length: int = None
    tts_cache_enabled: bool = None
    tts_prompt_on_startup: bool = None
    tts_emotion: str = None
    
    # GPT-SoVITS 配置
    gpt_sovits: dict = None
    
    # Bilibili 配置
    bilibili_cookie: str = None
    
    # 歌词设置
    auto_search_lyrics: bool = None
    
    # 特殊操作
    _reset: bool = None

    # 安全管控模式
    security_mode: str = None

    # QQ 平台接入（NapCat OneBot 11）
    qq_platform: dict = None

    # TRSS-Yunzai 运行配置（auto_start / 端口 / redis 路径）
    miao_plugin: dict = None

    # BetterGI 托管配置
    bettergi: dict = None

    # AI 教辅 / 授课子系统配置（edu.teaching 等）
    edu: dict = None


# ---------- TRSS-Yunzai 原生进程管理 ----------
@app.get("/api/yunzai/status")
async def yunzai_status_api():
    """查询 TRSS-Yunzai 运行状态。"""
    from zhiyan_web.platform.yunzai_manager import yunzai_status
    return {"status": "success", "data": yunzai_status()}


@app.post("/api/yunzai/start")
async def yunzai_start_api():
    """启动 TRSS-Yunzai（必要时先拉起 Redis）。"""
    from zhiyan_web.platform.yunzai_manager import ensure_yunzai
    ok, msg = await asyncio.to_thread(ensure_yunzai)
    return {"status": "success" if ok else "error", "message": msg}


@app.post("/api/yunzai/stop")
async def yunzai_stop_api():
    """停止 TRSS-Yunzai。"""
    from zhiyan_web.platform.yunzai_manager import stop_yunzai
    ok, msg = await asyncio.to_thread(stop_yunzai)
    return {"status": "success" if ok else "error", "message": msg}
# ---------- / TRSS-Yunzai 原生进程管理 ----------


@app.get("/api/settings")
async def get_settings_api():
    """
    获取当前配置
    
    返回:
        dict: 当前配置
    """
    return get_settings()


@app.post("/api/settings")
async def update_settings_api(data: SettingsUpdate):
    """
    更新配置
    
    参数:
        data: 新配置数据
    
    返回:
        dict: 操作结果
    """
    try:
        # 处理重置操作
        if data._reset:
            from zhiyan_web.config.settings import DEFAULT_SETTINGS
            import copy
            reset_data = copy.deepcopy(DEFAULT_SETTINGS)
            update_settings(reset_data)
            logger.info("配置已重置为默认值")
            return {"status": "success", "message": "配置已重置为默认值"}
        
        # 构建更新字典，只包含非 None 值
        update_data = {}
        for field, value in data.model_dump().items():
            if value is not None and field != '_reset':
                update_data[field] = value
        
        # 更新配置
        update_settings(update_data)
        
        # 如果更新了 TTS 引擎，重新初始化
        if data.tts_engine:
            from zhiyan_web.core import tts_engine as core_tts_engine
            settings = get_settings()
            # 创建新的 TTS 引擎实例
            new_tts_engine = __import__('zhiyan_web.core.tts.engine').core.tts.engine.TTSEngine(
                engine_type=data.tts_engine,
                voice=settings.get("tts_voice", "zh-CN-XiaoxiaoNeural"),
                rate=settings.get("tts_rate", 200),
                volume=settings.get("tts_volume", 1.0),
                config=settings
            )
            # 更新全局实例
            import zhiyan_web.core
            zhiyan_web.core.tts_engine = new_tts_engine
            logger.info(f"TTS 引擎已更新: {data.tts_engine}")
        
        # 如果更新了 LLM 相关配置，重新初始化提供商
        if any([data.provider, data.api_key, data.model, data.api_url, data.openrouter_api_key]):
            _init_llm_provider()
        
        # 如果更新了 Agent 配置，初始化工作区
        if data.agent_workspace is not None:
            try:
                from zhiyan_web.computer.services.workspace_manager import (
                    ensure_workspace_exists, get_workspace_info
                )
                success, msg = ensure_workspace_exists()
                if success:
                    logger.info(f"Agent 工作区已就绪: {msg}")
                else:
                    logger.warning(f"Agent 工作区初始化失败: {msg}")
            except Exception as e:
                logger.warning(f"初始化工作区失败: {e}")

        # 处理安全管控模式
        if data.security_mode is not None:
            from zhiyan_web.computer.services.safety_manager import set_security_mode
            success, msg, warning = set_security_mode(data.security_mode)
            logger.info(f"安全管控模式设置: {data.security_mode} - {msg}")

        # 处理 QQ 平台配置变更（依据 enabled 触发启动/停止/重启）
        if data.qq_platform is not None:
            try:
                # 令牌脱敏防护：忽略脱敏占位 token，避免把脱敏串写回配置导致鉴权失败
                qp = dict(data.qq_platform)
                tok = qp.get("access_token")
                if tok and "****" in str(tok):
                    qp.pop("access_token")
                from zhiyan_web.platform.manager import get_qq_platform_manager
                mgr = get_qq_platform_manager()
                running = mgr.get_status()["running"]
                enabled = qp.get("enabled")
                if enabled is True and not running:
                    await mgr.start()
                elif enabled is False and running:
                    await mgr.stop()
                elif running:
                    mgr.reconfigure()
            except Exception as e:
                logger.warning(f"应用 QQ 平台配置失败: {e}")

        # TRSS-Yunzai：auto_start 时确保进程在运行
        if data.miao_plugin is not None:
            try:
                mp = dict(data.miao_plugin)
                if mp.get("auto_start"):
                    from zhiyan_web.platform.yunzai_manager import ensure_yunzai
                    ok, msg = await asyncio.to_thread(ensure_yunzai)
                    if not ok:
                        logger.warning(f"[yunzai] 自动启动失败: {msg}")
            except Exception as e:
                logger.warning(f"自动启动 Yunzai 进程失败: {e}")

        # BetterGI 托管：配置已由 update_settings 落盘，这里让管理器重新读取
        if data.bettergi is not None:
            try:
                from zhiyan_web.bettergi.service import get_bettergi_manager
                get_bettergi_manager().reload()
            except Exception as e:
                logger.warning(f"[bettergi] 重新加载配置失败: {e}")

        return {"status": "success", "message": "配置已更新"}
    except Exception as e:
        logger.error(f"保存配置失败: {e}", exc_info=True)
        return {"status": "error", "message": f"保存配置失败: {str(e)}"}


@app.get("/api/llm-providers/presets")
async def get_llm_provider_presets():
    """
    获取 LLM 提供商预设列表
    
    返回:
        dict: 提供商预设列表
    """
    from zhiyan_web.config.settings import LLM_PROVIDER_PRESETS
    return {"presets": LLM_PROVIDER_PRESETS}


# ============= Agent 工作区 API =============

@app.get("/api/workspace/info")
async def get_workspace_info_api():
    """获取工作区信息"""
    try:
        from zhiyan_web.computer.services.workspace_manager import get_workspace_info
        return get_workspace_info()
    except Exception as e:
        return {"configured": False, "error": str(e)}


@app.post("/api/workspace/init")
async def init_workspace_api():
    """初始化工作区目录"""
    try:
        from zhiyan_web.computer.services.workspace_manager import ensure_workspace_exists
        success, msg = ensure_workspace_exists()
        return {"success": success, "message": msg}
    except Exception as e:
        return {"success": False, "message": str(e)}


# ============= 安全管控 API =============

@app.get("/api/security/info")
async def get_security_info_api():
    """获取安全管控信息"""
    try:
        from zhiyan_web.computer.services.safety_manager import get_security_info
        return get_security_info()
    except Exception as e:
        return {"mode": "normal", "error": str(e)}


@app.post("/api/security/set-mode")
async def set_security_mode_api(data: dict):
    """
    设置安全管控模式

    参数:
        data: {"mode": "normal" | "full_control" | "high_security"}
    """
    try:
        mode = data.get("mode", "")
        from zhiyan_web.computer.services.safety_manager import set_security_mode
        success, message, warning = set_security_mode(mode)
        return {
            "success": success,
            "message": message,
            "warning": warning,
            "mode": mode,
        }
    except Exception as e:
        return {"success": False, "message": str(e)}


# ============= TTS 配置导入（从 zhiyan data/settings.json） =============

@app.get("/api/tts/import-zhiyan-config")
async def import_zhiyan_tts_config():
    """
    从 zhiyan 软件的 data/settings.json 导入 TTS 配置
    
    返回:
        dict: 导入的配置信息
    """
    from zhiyan_web.config import DATA_DIR, PROJECT_ROOT
    
    # 调试：检查路径
    debug_info = {
        "project_root": PROJECT_ROOT,
        "data_dir": DATA_DIR,
    }
    
    # 尝试多个可能的路径加载 zhiyan 配置
    zhiyan_config_paths = [
        os.path.join(PROJECT_ROOT, '..', '..', '..', 'zhiyan agent', 'data', 'settings.json'),
        os.path.join(PROJECT_ROOT, 'data', 'settings.json'),
        os.path.join(DATA_DIR, 'settings.json'),
    ]
    
    zhiyan_settings = None
    loaded_path = None
    search_results = []
    for path in zhiyan_config_paths:
        abs_path = os.path.abspath(path)
        exists = os.path.exists(abs_path)
        search_results.append({"path": abs_path, "exists": exists})
        if exists:
            try:
                with open(abs_path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    has_gpt = bool(loaded.get("gpt_sovits"))
                    has_tts = bool(loaded.get("tts_engine"))
                    search_results[-1]["has_gpt_sovits"] = has_gpt
                    search_results[-1]["has_tts_engine"] = has_tts
                    if has_gpt or has_tts:
                        zhiyan_settings = loaded
                        loaded_path = abs_path
                        break
            except Exception as e:
                search_results[-1]["error"] = str(e)
                continue
    
    if not zhiyan_settings:
        # 尝试直接从 zhiyan agent web 的 data/settings.json 读取
        web_data_settings = os.path.join(PROJECT_ROOT, 'data', 'settings.json')
        abs_web = os.path.abspath(web_data_settings)
        search_results.append({"path": abs_web, "fallback": True})
        if os.path.exists(abs_web):
            try:
                with open(abs_web, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    search_results[-1]["has_gpt_sovits"] = bool(loaded.get("gpt_sovits"))
                    if loaded.get("gpt_sovits"):
                        zhiyan_settings = loaded
                        loaded_path = abs_web
            except Exception as e:
                search_results[-1]["error"] = str(e)
    
    if not zhiyan_settings:
        return {"status": "error", "message": "未找到 zhiyan 配置文件", "debug": debug_info, "searched_paths": search_results}
    
    # 提取 TTS 配置
    tts_config = {
        "tts_engine": zhiyan_settings.get("tts_engine", "GPT-SoVITS"),
        "tts_enabled": zhiyan_settings.get("tts_enabled", True),
        "tts_voice": zhiyan_settings.get("tts_voice", "zh-CN-XiaoyiNeural"),
        "tts_prompt_on_startup": zhiyan_settings.get("tts_prompt_on_startup", False),
        "gpt_sovits": zhiyan_settings.get("gpt_sovits", {}),
    }
    
    # 更新配置
    update_settings(tts_config)
    
    # 重新初始化 TTS 引擎
    new_tts_engine = __import__('zhiyan_web.core.tts.engine').core.tts.engine.TTSEngine(
        engine_type=tts_config["tts_engine"],
        voice=tts_config["tts_voice"],
        rate=200,
        volume=1.0,
        config=get_settings()
    )
    import zhiyan_web.core
    zhiyan_web.core.tts_engine = new_tts_engine
    
    logger.info(f"已从 zhiyan 配置导入 TTS 设置: {loaded_path}")
    
    return {
        "status": "success",
        "message": f"已从 {loaded_path} 导入 TTS 配置",
        "config": tts_config
    }


# ============= 状态路由 =============

@app.get("/api/status")
async def get_status():
    """
    获取系统状态
    
    返回:
        dict: 系统状态信息
    """
    provider = get_chat_manager().get_provider()
    return {
        "status": "running",
        "version": "1.0.0",
        "provider": provider.name if provider else "none",
        "model": provider._model if provider and hasattr(provider, '_model') else "none",
        "tts_enabled": get_settings().get("tts_enabled", False),
        "tts_engine": get_tts_engine().engine_type,
        "timestamp": datetime.now().isoformat()
    }


# ============= 页面路由 =============

@app.get("/")
async def root():
    """
    主页路由
    
    返回:
        FileResponse: 主页 HTML 文件
    """
    return FileResponse(os.path.join(FRONTEND_DIR, 'index.html'))


@app.get("/music")
async def music_player():
    """
    音乐播放器页面路由
    
    返回:
        FileResponse: 音乐播放器 HTML 文件
    """
    return FileResponse(os.path.join(FRONTEND_DIR, 'music.html'))


@app.get("/music/mini")
async def music_mini_player():
    """独立置顶迷你播放器（桌面壳小窗加载）"""
    return FileResponse(os.path.join(FRONTEND_DIR, 'music_mini.html'))


@app.get("/dsh")
async def dsh_panel_page():
    """
    DSH (DeepSeek Harness) 内核管理面板路由

    返回:
        FileResponse: DSH 管理面板 HTML 文件
    """
    return FileResponse(os.path.join(FRONTEND_DIR, 'dsh.html'))


@app.get("/screen")
async def screencap_page():
    """
    截屏录屏管理页面路由

    返回:
        FileResponse: 截屏录屏管理 HTML 文件
    """
    return FileResponse(os.path.join(FRONTEND_DIR, 'screen.html'))


@app.get("/float_ball")
async def float_ball_page():
    """AI 授课悬浮球页面（独立置顶小窗 / 浏览器预览）"""
    return FileResponse(os.path.join(FRONTEND_DIR, 'float_ball.html'))


# ============= 初始化函数 =============

def _init_llm_provider():
    """
    初始化 LLM 提供商
    
    根据配置创建并设置 LLM 提供商，同时注册到 ProviderManager。
    支持: mock, deepseek, openai, zhipu, qwen, moonshot, doubao, minimax, hunyuan,
          ernie, spark, siliconflow, together, gemini, anthropic, xai, mistral,
          groq, openrouter, custom（完整清单见 config/settings.py 的 LLM_PROVIDER_PRESETS）
    """
    settings = get_settings()
    provider_type = (settings.get("provider", "mock") or "mock").lower()

    # 内置 base_url 的服务商（与 core/llm/providers.py 的 create_provider 注册表保持一致，
    # 这些厂商无需读取用户填写的 api_url，直接走内置官方地址）。
    # 注意：openai 故意不在此列——它历史上允许用户把 api_url 指向自建/代理的
    # OpenAI 兼容服务，纳入白名单会让自定义地址失效。
    preset_providers = (
        "deepseek",
        "zhipu", "qwen", "moonshot", "doubao", "minimax", "hunyuan", "ernie", "spark",
        "siliconflow", "together",
        "gemini", "anthropic", "xai", "mistral", "groq", "openrouter",
    )

    if provider_type == "mock":
        provider = MockProvider()
    else:
        # OpenRouter 使用专用密钥，其他提供商使用通用 api_key
        if provider_type == "openrouter":
            api_key = settings.get("openrouter_api_key", "") or settings.get("api_key", "")
        else:
            api_key = settings.get("api_key", "")
        model = settings.get("model", "gpt-3.5-turbo")
        api_url = settings.get("api_url", "https://api.openai.com/v1")

        if not api_key:
            logger.warning(f"提供商 {provider_type} 未配置 API Key，回退到模拟模式")
            provider = MockProvider()
        else:
            # 传递 DeepSeek 思考模式参数和 response_format
            provider_kwargs = {}
            provider_kwargs["response_format"] = settings.get("response_format", "text")
            if provider_type == "deepseek":
                provider_kwargs["thinking_enabled"] = settings.get("deepseek_thinking_enabled", False)
                provider_kwargs["reasoning_effort"] = settings.get("deepseek_reasoning_effort", "high")

            if provider_type in preset_providers:
                provider = create_provider(
                    provider_type,
                    api_key=api_key,
                    model=model,
                    **provider_kwargs,
                )
            else:
                # openai / custom / 未知类型：使用配置的 api_url
                provider = create_provider(
                    "openai",
                    api_key=api_key,
                    model=model,
                    base_url=api_url,
                    provider_name=provider_type,
                    **provider_kwargs,
                )

    if provider:
        get_chat_manager().set_provider(provider)

        # 注册到 ProviderManager
        from zhiyan_web.core import provider_manager
        if provider_manager:
            provider_id = f"provider_{provider_type}"
            provider_manager.register_provider(provider_id, provider)
            provider_manager.set_curr_provider(provider_id)

        logger.info(f"LLM 提供商已设置: {provider.name}")
    else:
        get_chat_manager().set_provider(MockProvider())


# ============= 应用初始化 =============

# 初始化 LLM 提供商
_init_llm_provider()

# 初始化各模块数据库
try:
    from zhiyan_web.economy.services import init_economy_db
    from zhiyan_web.bottle.services import init_bottle_db
    from zhiyan_web.gomoku.services import init_gomoku_db
    from zhiyan_web.blindbox.services import init_blindbox_db
    from zhiyan_web.imagegen.services import init_imagegen_db
    from zhiyan_web.codeexec.services import init_codeexec_db
    
    init_economy_db()
    init_bottle_db()
    init_gomoku_db()
    init_blindbox_db()
    init_imagegen_db()
    init_codeexec_db()
    
    logger.info("各模块数据库初始化完成")
except Exception as e:
    logger.warning(f"部分模块数据库初始化失败: {e}")

# ============= 初始化情感系统、主动交互、插件系统 =============
try:
    from zhiyan_web.emotion import EmotionEngine, BondEngine, DriftDetector
    from zhiyan_web.proactive import ProactiveEngine, SchedulerEngine
    from zhiyan_web.star import Context, PluginManager
    from zhiyan_web.plugins import PluginContext as LegacyPluginContext
    from asyncio import Queue

    # 初始化情感引擎
    emotion_engine = EmotionEngine()
    bond_engine = BondEngine()
    drift_detector = DriftDetector()
    set_emotion_engine(emotion_engine)
    set_bond_engine(bond_engine)
    set_drift_detector(drift_detector)
    logger.info("情感系统已初始化")

    # 初始化主动交互引擎
    proactive_engine = ProactiveEngine(emotion_engine=emotion_engine, bond_engine=bond_engine)
    scheduler_engine = SchedulerEngine()
    set_proactive_engine(proactive_engine)
    set_scheduler_engine(scheduler_engine)
    logger.info("主动交互系统已初始化")

    # ===== 初始化插件系统（使用 AstrBot 兼容的 star 模块） =====
    # 创建 Star Context（插件上下文 API）
    star_context = Context()
    star_context.set_config(get_settings())
    star_context.set_chat_manager(get_chat_manager())
    star_context.set_tts_engine(get_tts_engine())

    # 注入 ProviderManager
    from zhiyan_web.core import provider_manager
    if provider_manager:
        star_context.set_provider_manager(provider_manager)

    # 将 star_context 注入到 core 模块，供兼容层查找
    import zhiyan_web.core
    zhiyan_web.core.star_context = star_context

    # 创建旧版 PluginContext 供向后兼容
    legacy_plugin_context = LegacyPluginContext(event_queue=Queue(), config=get_settings())

    # 创建 Star PluginManager（AstrBot 完全兼容）
    plugin_manager = PluginManager(context=star_context, config=get_settings())
    set_plugin_manager(plugin_manager)
    logger.info("插件系统已初始化（AstrBot 兼容模式）")

    # 管道模块初始化（将以上模块注入管道上下文）
    from zhiyan_web.core import pipeline_context, init_pipeline_modules

    # 直接设置管道上下文属性
    if pipeline_context:
        pipeline_context.emotion_engine = emotion_engine
        pipeline_context.bond_engine = bond_engine
        pipeline_context.proactive_engine = proactive_engine
        pipeline_context.scheduler_engine = scheduler_engine
        pipeline_context.star_context = star_context
        pipeline_context.plugin_manager = plugin_manager
        pipeline_context.plugin_context = legacy_plugin_context

    # 初始化智能体上下文管理器等内部模块
    init_pipeline_modules()

    # ===== 加载插件 =====
    try:
        logger.info("正在加载插件...")
        # 检测是否已有运行中的事件循环（uvicorn 模式下）
        try:
            running_loop = asyncio.get_running_loop()
            # 已有运行中的循环，使用 create_task 异步执行
            async def _load_plugins_async():
                try:
                    success, failed = await plugin_manager.load()
                    loaded_count = len(plugin_manager.get_plugin_status())
                    failed_count = len(failed) if failed else 0
                    logger.info(f"插件加载完成: 共 {loaded_count} 个, 失败 {failed_count} 个")
                    if failed:
                        for name, info in failed.items():
                            logger.warning(f"插件加载失败: {name} - {info.get('error', '未知错误')}")
                except Exception as ex:
                    logger.warning(f"插件加载过程异常: {ex}")
            running_loop.create_task(_load_plugins_async())
        except RuntimeError:
            # 没有运行中的循环，创建持久化事件循环执行插件加载
            # 不使用 asyncio.run() 因为它会关闭循环，导致后台任务（如 Playwright）被遗弃
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                success, failed = loop.run_until_complete(plugin_manager.load())
                loaded_count = len(plugin_manager.get_plugin_status())
                failed_count = len(failed) if failed else 0
                logger.info(f"插件加载完成: 共 {loaded_count} 个, 失败 {failed_count} 个")
                if failed:
                    for name, info in failed.items():
                        logger.warning(f"插件加载失败: {name} - {info.get('error', '未知错误')}")
                # 不要关闭事件循环，保持后台任务（如 Playwright）继续运行
            except Exception as e:
                logger.warning(f"插件加载异常: {e}")
    except Exception as e:
        logger.warning(f"插件加载失败: {e}")

except Exception as e:
    logger.warning(f"部分扩展模块初始化失败: {e}")

# ============= 初始化 Agent 工作区 =============
try:
    from zhiyan_web.computer.services.workspace_manager import (
        ensure_workspace_exists, get_workspace_info
    )
    success, msg = ensure_workspace_exists()
    if success:
        logger.info(f"Agent 工作区已初始化: {msg}")
    else:
        logger.info(f"Agent 工作区未配置（{msg}），可以在设置中配置")
except Exception as e:
    logger.warning(f"Agent 工作区初始化失败: {e}")

# ============= 初始化截屏录屏全局热键 =============
try:
    from zhiyan_web.screencap import service as screencap_service
    from zhiyan_web.screencap.hotkey import get_hotkey_manager

    def _on_screencap_hotkey(action: str) -> None:
        """全局热键回调（运行在热键线程）"""
        screencap_service.handle_hotkey_action(action)

    hotkey_cfg = screencap_service.get_hotkey_config()
    hotkey_mgr = get_hotkey_manager()
    hotkey_mgr.set_callback(_on_screencap_hotkey)
    if hotkey_cfg.get("enabled"):
        hotkey_mgr.start(hotkey_cfg.get("hotkeys", {}), enabled=True)
        logger.info("截屏录屏全局热键已注册")
    else:
        logger.info("截屏录屏全局热键未启用（可在管理页面开启）")
except Exception as e:
    logger.warning(f"截屏录屏热键注册失败: {e}")

logger.info("Furina Agent Web 服务器启动完成")
