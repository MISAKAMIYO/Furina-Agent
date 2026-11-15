"""
聊天 WebSocket 路由
提供流式聊天的 WebSocket 接口

接口列表:
- WS /api/chat/ws    - 流式聊天 WebSocket
"""
from __future__ import annotations

import os
import asyncio
import logging
import re
from datetime import datetime

from fastapi import WebSocket, WebSocketDisconnect

from zhiyan_web.core.websocket.connection import ConnectionManager
from zhiyan_web.config import get_settings, TTS_DIR, DATA_DIR

logger = logging.getLogger("ZhiYanWeb")

manager = ConnectionManager()

# 延迟获取核心实例（在使用时获取）
def get_chat_manager():
    from zhiyan_web.core import chat_manager
    return chat_manager

def get_tts_engine():
    from zhiyan_web.core import tts_engine
    return tts_engine


async def handle_coordinator_message(message: str, websocket: WebSocket) -> str:
    """通过 Coordinator 处理消息（多 Agent 协作）"""
    try:
        from zhiyan_web.agents.coordinator import Coordinator
        from zhiyan_web.agents.registry import get_registry

        coordinator = Coordinator()
        registry = get_registry()
        coordinator_agent = registry.get_coordinator()

        async def on_chunk(chunk: str):
            await manager.broadcast({
                "type": "ai_chunk",
                "chunk": chunk,
            })

        # 处理消息
        result = await coordinator.process(
            user_input=message,
            chat_history=[],
            on_chunk=on_chunk,
            on_decision=None,
        )

        return result
    except Exception as e:
        logger.error(f"Coordinator 处理失败，回退到直接 LLM: {e}")
        # 回退到直接 LLM
        full_response = ""
        def callback(chunk):
            nonlocal full_response
            # 过滤 __REASONING__: 前缀（DeepSeek 思考模式）
            if chunk.startswith("__REASONING__:"):
                reasoning_text = chunk[14:]
                if reasoning_text:
                    asyncio.run_coroutine_threadsafe(
                        manager.broadcast({"type": "reasoning_chunk", "content": reasoning_text}),
                        asyncio.get_event_loop()
                    )
                return
            full_response += chunk
            asyncio.run_coroutine_threadsafe(
                manager.broadcast({"type": "ai_chunk", "chunk": chunk}),
                asyncio.get_event_loop()
            )
        await get_chat_manager().chat_stream(message, callback=callback)
        return full_response


async def handle_say_tts_command(text: str, websocket: WebSocket):
    """
    处理 /say 指令
    
    格式: /say 语音内容
    
    行为:
    - 当 TTS 启用时：触发语音合成，广播语音消息
    - 当 TTS 禁用时：将文本保存到文件，广播文本消息
    
    参数:
        text: 要处理的指令文本（含 /say 前缀）
        websocket: WebSocket 连接（用于单播响应）
    """
    try:
        # 提取 /say 后的所有文本
        match = re.match(r'^/say\s+(.+)$', text, re.DOTALL)
        if not match:
            await websocket.send_json({
                "type": "error",
                "message": "指令格式错误，正确格式：/say 语音内容"
            })
            return
        
        say_text = match.group(1).strip()
        if not say_text:
            await websocket.send_json({
                "type": "error",
                "message": "语音内容不能为空"
            })
            return
        
        if len(say_text) > 500:
            say_text = say_text[:500]
        
        settings = get_settings()
        tts_enabled = settings.get("tts_enabled", False)
        
        if tts_enabled:
            # ====== TTS 启用：语音合成 ======
            await handle_tts_synthesis(say_text, websocket)
        else:
            # ====== TTS 禁用：保存文本到文件 ======
            await handle_say_text_save(say_text, websocket)
            
    except Exception as e:
        logger.error(f"语音指令处理错误: {e}")
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"处理失败: {str(e)}"
            })
        except WebSocketDisconnect:
            pass


async def handle_tts_synthesis(say_text: str, websocket: WebSocket):
    """
    TTS 启用时：合成语音并广播语音消息
    
    参数:
        say_text: 要合成的文本
        websocket: WebSocket 连接
    """
    # 通知前端正在合成
    await websocket.send_json({
        "type": "voice_synthesizing",
        "text": say_text
    })
    
    tts = get_tts_engine()
    if not tts or tts.engine_type == "none":
        await websocket.send_json({
            "type": "error",
            "message": "TTS 引擎不可用，请先在设置中配置 TTS"
        })
        return
    
    clean_text = tts._clean_text_for_tts(say_text)
    emotion = tts.detect_emotion(say_text)
    
    result = {"success": False, "error": None, "audio_url": None}
    event = asyncio.Event()
    
    def on_complete(file_path):
        nonlocal result
        result["success"] = True
        result["audio_url"] = f"/api/tts/audio/{os.path.basename(file_path)}"
        event.set()
    
    def on_error(error):
        nonlocal result
        result["error"] = error
        event.set()
    
    tts.synthesize_with_emotion(clean_text, emotion=emotion, on_complete=on_complete, on_error=on_error)
    
    try:
        await asyncio.wait_for(event.wait(), timeout=120)
        
        if result["success"]:
            # 广播语音消息给所有客户端
            await manager.broadcast({
                "type": "voice_message",
                "audio_url": result["audio_url"],
                "text": say_text,
                "time": datetime.now().isoformat()
            })
            logger.info(f"语音消息已生成: {say_text[:30]}...")
        else:
            await websocket.send_json({
                "type": "error",
                "message": f"语音合成失败: {result['error']}"
            })
    except asyncio.TimeoutError:
        await websocket.send_json({
            "type": "error",
            "message": "语音合成超时"
        })


async def handle_say_text_save(say_text: str, websocket: WebSocket):
    """
    TTS 禁用时：将文本保存到文件并广播文本消息
    
    参数:
        say_text: 要保存的文本
        websocket: WebSocket 连接
    """
    try:
        # 确保保存目录存在
        save_dir = os.path.join(DATA_DIR, 'say_saved')
        os.makedirs(save_dir, exist_ok=True)
        
        # 生成文件名（使用时间戳）
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"say_{timestamp}.txt"
        filepath = os.path.join(save_dir, filename)
        
        # 写入文件
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(say_text)
        
        logger.info(f"语音指令文本已保存: {filepath}")
        
        # 广播消息给所有客户端（不含 audio_url，表示文本模式）
        await manager.broadcast({
            "type": "voice_message",
            "audio_url": None,
            "text": say_text,
            "time": datetime.now().isoformat(),
            "saved_path": filepath,
            "mode": "text_only"
        })
        
    except Exception as e:
        logger.error(f"保存语音指令文本失败: {e}")
        await websocket.send_json({
            "type": "error",
            "message": f"保存文本失败: {str(e)}"
        })


async def websocket_endpoint(websocket: WebSocket):
    """
    流式聊天 WebSocket 端点
    
    支持的消息类型:
    - user_message: 用户发送消息
    - ai_chunk: AI 响应片段
    - ai_message: AI 完整响应
    - voice_message: 语音消息（由 /say 指令触发）
    - voice_synthesizing: 语音合成中
    - error: 错误消息
    """
    await manager.connect(websocket)
    
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "message")
            message = data.get("content", "")
            
            if not message.strip():
                continue
            
            # 检查是否为 /say 指令（格式：/say 语音内容）
            say_pattern = r'^/say\s+.+'
            if re.match(say_pattern, message.strip(), re.DOTALL):
                # 先广播用户消息，让用户看到自己的输入
                user_msg = {"role": "user", "content": message, "time": datetime.now().isoformat()}
                await manager.broadcast({"type": "user_message", "message": user_msg})
                # 处理 /say 指令
                await handle_say_tts_command(message.strip(), websocket)
                continue
            
            # 记录用户消息到聊天历史
            user_msg = {"role": "user", "content": message, "time": datetime.now().isoformat()}
            
            # 广播用户消息
            await manager.broadcast({"type": "user_message", "message": user_msg})
            
            # 添加到聊天历史
            from zhiyan_web.chat.routers.chat import chat_history
            chat_history.append(user_msg)
            
            try:
                full_response = ""
                cm = get_chat_manager()

                # 检测是否为命令（以 # / ! 开头，# 供 miao-plugin 等插件使用）
                wake_prefixes = ["/", "!", "#"]
                is_command = any(message.strip().startswith(p) for p in wake_prefixes)

                if is_command and hasattr(cm, '_pipeline') and cm._pipeline:
                    # 命令消息：通过管道处理（包括插件轮询）
                    result = await cm.process_via_pipeline(message)
                    full_response = result.get("response", "")

                    # 通过回调模拟流式输出
                    if full_response:
                        await manager.broadcast({
                            "type": "ai_chunk", "chunk": full_response
                        })
                else:
                    # 非命令消息：通过 Coordinator 处理（多 Agent 协作）
                    full_response = await handle_coordinator_message(message, websocket)
                
                # 广播完整消息
                ai_msg = {"role": "assistant", "content": full_response, "time": datetime.now().isoformat()}
                await manager.broadcast({"type": "ai_message", "message": ai_msg})
                
                # 添加到聊天历史
                chat_history.append(ai_msg)

                # 自动保存对话到加密存储 + 更新用户画像
                try:
                    from zhiyan_web.memory.conversation_store import save_conversation
                    from zhiyan_web.memory.user_profile import update_profile_from_messages
                    from zhiyan_web.chat.routers.chat import chat_history, SESSION_ID
                    if chat_history:
                        # 使用会话 ID 保存对话，避免每次覆盖同一个文件
                        save_conversation(SESSION_ID, chat_history)
                        # 更新用户画像
                        update_profile_from_messages(chat_history)
                except Exception as e:
                    logger.debug(f"自动保存对话/更新用户画像失败: {e}")

                # 广播情感/好感度状态更新（实时更新前端 UI）
                try:
                    cm = get_chat_manager()
                    if hasattr(cm, '_pipeline') and cm._pipeline and hasattr(cm._pipeline, 'context'):
                        pipeline_context = cm._pipeline.context
                        if getattr(pipeline_context, 'emotion_engine', None):
                            emotion_data = pipeline_context.emotion_engine.get_emotion()
                            await manager.broadcast({
                                "type": "emotion_update",
                                "emotion": emotion_data,
                            })
                        if getattr(pipeline_context, 'bond_engine', None):
                            bond_data = pipeline_context.bond_engine.get_status()
                            await manager.broadcast({
                                "type": "bond_update",
                                "bond": bond_data,
                            })
                    from zhiyan_web.likability.routers.likability import get_likability_status
                    likability_data = await get_likability_status()
                    if likability_data.get("success"):
                        await manager.broadcast({
                            "type": "likability_update",
                            "likability": likability_data,
                        })
                except Exception as e:
                    logger.debug(f"广播情感状态失败: {e}")

                # 如果启用了 TTS，触发语音合成
                settings = get_settings()
                if settings.get("tts_enabled", False) and full_response:
                    try:
                        from zhiyan_web.chat.routers.chat import trigger_tts
                        await trigger_tts(full_response)
                    except Exception as e:
                        logger.error(f"触发 TTS 失败: {e}")
                
            except Exception as e:
                logger.error(f"WebSocket 聊天错误: {e}")
                await manager.broadcast({"type": "error", "message": str(e)})
                
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"WebSocket 异常: {e}")
        manager.disconnect(websocket)