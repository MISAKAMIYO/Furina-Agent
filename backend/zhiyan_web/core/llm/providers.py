"""
LLM 提供商模块
定义 LLMProvider 抽象接口和具体提供商实现

支持的提供商:
- MockProvider: 模拟提供商，用于测试
- OpenAIProvider: OpenAI 兼容接口提供商（使用 aiohttp 异步请求）
- Deepseek: Deepseek 提供商
- Qwen: 通义千问提供商

优化:
- 使用 aiohttp 替代 requests，实现真正的异步非阻塞 HTTP
- 添加自动重试机制（指数退避）
- 添加超时控制
- 添加响应流式解析优化
"""
from __future__ import annotations

import asyncio
import json
import logging
import datetime
import re
import uuid
from dataclasses import dataclass
from typing import List, Optional, Dict, Any, AsyncIterator

logger = logging.getLogger("ZhiYanWeb")


@dataclass
class LLMMessage:
    """
    LLM 消息数据类
    
    属性:
        role: 消息角色 (user, assistant, system, tool)
        content: 消息内容
        tool_call_id: 工具调用 ID（仅在 role="tool" 时需要）
        tool_calls: 工具调用列表（仅在 role="assistant" 且调用工具时需要）
    """
    role: str
    content: str
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    reasoning_content: Optional[str] = None
    # 以下两个字段只服务于界面展示与历史恢复，不参与请求体构造
    # （_build_messages 只读 role/content/tool_calls/tool_call_id/reasoning_content）
    time: str = ""
    think_ms: int = 0


@dataclass
class LLMResponse:
    """
    LLM 响应数据类
    
    属性:
        content: 响应内容
        model: 使用的模型名称
        usage: Token 使用统计
        finish_reason: 结束原因
        tool_calls: 工具调用列表（每个元素为 {"id": str, "type": "function", "function": {"name": str, "arguments": str}}）
    """
    content: str = ""
    model: str = ""
    usage: Dict[str, Any] = None
    finish_reason: str = ""
    tool_calls: Optional[List[Dict[str, Any]]] = None
    reasoning_content: Optional[str] = None


def _strip_dsml(text: str) -> str:
    """移除 DeepSeek 内部工具调用标记（<｜｜DSML｜｜...>）与整段 calls 块。

    这些标记属于服务端特殊 token，不应该出现在正文或回传给 API 的历史里；
    一旦混进 assistant 内容，模型会跟着模仿，形成「输出一堆标记」的坏循环。
    """
    if not text or "DSML" not in text:
        return text or ""
    cleaned = re.sub(r"<｜｜DSML｜｜\s*calls>.*?(?:</｜｜DSML｜｜\s*calls>|$)", "", text, flags=re.S)
    cleaned = re.sub(r"</?｜｜DSML｜｜\s*(?:calls|invoke|parameter)[^>]*>", "", cleaned)
    cleaned = re.sub(r"<｜｜DSML｜｜[^>]*>", "", cleaned)
    return cleaned.strip()


def _extract_dsml_tool_calls(text: str):
    """把泄漏到正文里的 DSML 调用块解析成标准 tool_calls。

    返回 (清理后的正文, tool_calls)。解析失败时只做清理，不抛异常。
    """
    if not text or "DSML" not in text:
        return text or "", []
    calls: List[Dict[str, Any]] = []
    pattern = r'<｜｜DSML｜｜\s*invoke\s+name="([^"]+)"[^>]*>(.*?)</｜｜DSML｜｜\s*invoke>'
    for match in re.finditer(pattern, text, flags=re.S):
        name, body = match.group(1), match.group(2)
        args: Dict[str, Any] = {}
        param_pattern = (r'<｜｜DSML｜｜\s*parameter\s+name="([^"]+)"'
                         r'(?:\s+string="(true|false)")?[^>]*>(.*?)'
                         r'</｜｜DSML｜｜\s*parameter>')
        for param in re.finditer(param_pattern, body, flags=re.S):
            key = param.group(1)
            is_str = (param.group(2) or "").lower()
            raw = (param.group(3) or "").strip()
            if is_str == "true" or (not raw.startswith(("{", "[", "-")) and not raw.isdigit()):
                args[key] = raw
            else:
                try:
                    args[key] = json.loads(raw)
                except Exception:
                    args[key] = raw
        calls.append({
            "id": "dsml_%s" % uuid.uuid4().hex[:12],
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
        })
    return _strip_dsml(text), calls


# 视觉模型识别：只有命中这些特征才把图片转成 image_url 结构化内容，
# 避免非视觉模型收到 image_url 后整轮请求 400。
_VISION_MODEL_HINTS = (
    "vl", "vision", "gpt-4o", "gpt-4.1", "gpt-5", "claude", "gemini",
    "pixtral", "llava", "glm-4v", "internvl",
)
_IMG_SENTINEL = re.compile(r"\[img\]([^|\]]*)\|([^\]]+)\[/img\]")


def _model_supports_vision(model: str) -> bool:
    m = (model or "").lower()
    return bool(m) and any(hint in m for hint in _VISION_MODEL_HINTS)


def _image_url_to_data_url(url: str) -> Optional[str]:
    """把聊天图片 URL 转成可直接发给模型的内联 data URL。

    - data: 开头直接复用
    - /api/chat/images/<name>、/api/chat/files/<name> 读取本地文件转 base64
    - http(s) 远程地址原样返回
    """
    import base64
    import mimetypes
    import os as _os
    u = (url or "").strip()
    if not u:
        return None
    if u.startswith("data:"):
        return u
    if u.startswith("http://") or u.startswith("https://"):
        return u
    try:
        from zhiyan_web.config import DATA_DIR
    except Exception:
        return None
    candidates = (
        ("/api/chat/images/", _os.path.join(DATA_DIR, "chat_images")),
        ("/api/chat/files/", _os.path.join(DATA_DIR, "chat_files")),
    )
    for prefix, base in candidates:
        if u.startswith(prefix):
            name = u[len(prefix):].split("?")[0]
            name = _os.path.basename(name)
            path = _os.path.join(base, name)
            if not _os.path.isfile(path):
                return None
            try:
                with open(path, "rb") as f:
                    raw = f.read()
                if not raw:
                    return None
                mime = mimetypes.guess_type(path)[0] or "image/png"
                return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
            except Exception:
                return None
    return None


def _user_content_with_images(content: str) -> Any:
    """把带 [img]名称|url[/img] 哨兵的用户消息转成 OpenAI 多模态 content 数组。

    任一图片无法解析时返回 None，调用方保留纯文本内容（降级不报错）。
    """
    if not content or "[img]" not in content:
        return None
    matches = _IMG_SENTINEL.findall(content)
    if not matches:
        return None
    parts: List[Dict[str, Any]] = [{"type": "text", "text": content}]
    for _name, url in matches:
        data_url = _image_url_to_data_url(url)
        if not data_url:
            return None
        parts.append({"type": "image_url", "image_url": {"url": data_url}})
    return parts


def _record_usage(model: str, usage: Any, source: str = "chat") -> None:
    """把服务端返回的 usage 写进本地用量台账（失败静默）。"""
    try:
        from zhiyan_web.core.llm import usage_meter
        if not isinstance(usage, dict):
            return
        p = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        c = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        if p or c:
            usage_meter.record_usage(model, p, c, source=source)
    except Exception:
        pass


def _record_estimated_usage(model: str, messages: Any, output_text: str) -> None:
    """流式响应没有 usage 时，用字符数估算并标记 estimated。"""
    try:
        from zhiyan_web.core.llm import usage_meter
        p = usage_meter.estimate_message_tokens(messages)
        c = usage_meter._estimate_tokens(output_text)
        if p or c:
            usage_meter.record_usage(model, p, c, source="chat", estimated=True)
    except Exception:
        pass


def _sanitize_tool_messages(msgs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """修正非法的 tool 消息序列。

    服务端要求 role=tool 必须紧跟在对应用 tool_call_id 的 assistant.tool_calls
    之后（DeepSeek 报错：Messages with role 'tool' must be a response to a
    preceding message with 'tool_calls'）。历史裁剪、上下文压缩或旧数据恢复
    都可能留下孤儿 tool 消息，这里统一兜底：

    - 合法的 assistant.tool_calls + tool 组合原样保留；
    - 没有被全部回应的 tool_calls 降级为普通 assistant 文本；
    - 孤儿 / 多余的 tool 结果转成 user 文本，尽量不丢信息；
    - 空内容的 tool 结果直接丢弃。
    """
    out: List[Dict[str, Any]] = []
    i = 0
    total = len(msgs)
    while i < total:
        msg = msgs[i]
        role = msg.get("role")

        if role == "assistant" and msg.get("tool_calls"):
            calls = msg.get("tool_calls") or []
            ids = [str(c.get("id") or "") for c in calls if c.get("id")]
            j = i + 1
            answers: List[Dict[str, Any]] = []
            while j < total and msgs[j].get("role") == "tool":
                answers.append(msgs[j])
                j += 1

            answered = set(str(a.get("tool_call_id") or "") for a in answers)
            id_set = set(ids)
            if ids and id_set.issubset(answered):
                out.append(msg)
                for answer in answers:
                    if str(answer.get("tool_call_id") or "") in id_set:
                        out.append(answer)
                    else:
                        text = str(answer.get("content") or "").strip()
                        if text:
                            out.append({"role": "user", "content": "[工具结果] " + text[:4000]})
            else:
                # tool_calls 没有匹配齐，整组降级，避免服务端继续报错
                fallback = {"role": "assistant", "content": msg.get("content") or ""}
                if msg.get("reasoning_content") is not None:
                    fallback["reasoning_content"] = msg.get("reasoning_content")
                out.append(fallback)
                for answer in answers:
                    text = str(answer.get("content") or "").strip()
                    if text:
                        out.append({"role": "user", "content": "[工具结果] " + text[:4000]})
            i = j
            continue

        if role == "tool":
            # 前面没有 assistant.tool_calls 的孤儿结果
            text = str(msg.get("content") or "").strip()
            if text:
                out.append({"role": "user", "content": "[工具结果] " + text[:4000]})
            i += 1
            continue

        out.append(msg)
        i += 1
    return out


class LLMProvider:
    """
    LLM 提供商抽象基类
    
    所有具体提供商必须实现以下方法:
    - name: 获取提供商名称
    - default_model: 获取默认模型
    - is_available: 检查提供商是否可用
    - chat: 同步聊天接口
    - chat_stream: 流式聊天接口
    """
    
    @property
    def name(self) -> str:
        """获取提供商名称"""
        raise NotImplementedError
    
    @property
    def default_model(self) -> str:
        """获取默认模型名称"""
        raise NotImplementedError
    
    def is_available(self) -> bool:
        """检查提供商是否可用"""
        raise NotImplementedError
    
    async def chat(self, messages: List[LLMMessage], **kwargs) -> LLMResponse:
        """
        同步聊天接口
        
        参数:
            messages: 消息列表
            **kwargs: 额外参数（temperature, max_tokens等）
        
        返回:
            LLMResponse: 响应对象
        """
        raise NotImplementedError
    
    async def chat_stream(self, messages: List[LLMMessage], **kwargs) -> AsyncIterator[str]:
        """
        流式聊天接口
        
        参数:
            messages: 消息列表
            **kwargs: 额外参数
        
        返回:
            AsyncIterator[str]: 流式响应迭代器
        """
        raise NotImplementedError

    async def chat_stream_events(self, messages: List[LLMMessage],
                                 **kwargs) -> AsyncIterator[Dict[str, Any]]:
        """结构化流式调用（默认降级为一次性 chat 结果，供不支持 SSE 的实现使用）。"""
        resp = await self.chat(messages, **kwargs)
        if resp.reasoning_content:
            yield {"type": "reasoning", "text": resp.reasoning_content}
        if resp.content:
            yield {"type": "text", "text": resp.content}
        yield {
            "type": "done",
            "content": resp.content or "",
            "reasoning": resp.reasoning_content or "",
            "tool_calls": resp.tool_calls or [],
            "usage": resp.usage or {},
        }


class MockProvider(LLMProvider):
    """
    模拟 LLM 提供商
    
    用于测试和开发，不调用真实 API，根据关键词返回预设响应
    """
    
    KEYWORD_RESPONSES = {
        "你好": "你好！有什么我可以帮你的吗？",
        "你是谁": "我是一个通用型 AI 助手，可以帮你处理各种任务，包括代码编写、文件操作、信息查询等。",
        "名字": "你可以直接叫我助手。",
        "能力": "我支持多种功能：代码编写与执行、文件读写、网页搜索、信息整理等。有什么需要帮忙的吗？",
        "谢谢": "不客气，有需要随时说。",
        "测试": "收到！这是模拟回复，用于测试 UI 和流程，不消耗任何 token。",
        "帮助": "可以帮你处理代码编写、文件操作、信息查询、数据分析等任务。请描述你的具体需求。",
    }
    
    DEFAULT_RESPONSE = "抱歉，我没能理解你的意思，可以换个方式描述一下吗？"
    
    def __init__(self, api_key: str = "mock", model: str = "mock-model", timeout: int = 60):
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
    
    @property
    def name(self) -> str:
        return "mock"
    
    @property
    def default_model(self) -> str:
        return self._model
    
    def is_available(self) -> bool:
        return True
    
    def _match_response(self, text: str) -> str:
        text_lower = text.lower()
        for keyword, response in self.KEYWORD_RESPONSES.items():
            if keyword in text_lower:
                return response
        return self.DEFAULT_RESPONSE
    
    async def chat(self, messages: List[LLMMessage], **kwargs) -> LLMResponse:
        if not messages:
            return LLMResponse(content=self.DEFAULT_RESPONSE, model=self._model)
        
        last_message = messages[-1]
        response_text = self._match_response(last_message.content)
        
        return LLMResponse(
            content=response_text,
            model=self._model,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            finish_reason="stop"
        )
    
    async def chat_stream(self, messages: List[LLMMessage], **kwargs) -> AsyncIterator[str]:
        if not messages:
            yield self.DEFAULT_RESPONSE
            return
        
        last_message = messages[-1]
        response_text = self._match_response(last_message.content)
        
        chunk_size = 3
        for i in range(0, len(response_text), chunk_size):
            chunk = response_text[i:i + chunk_size]
            yield chunk
            await asyncio.sleep(0.02)


class OpenAIProvider(LLMProvider):
    """
    OpenAI 兼容接口提供商（aiohttp 异步版）
    
    支持 OpenAI API 以及兼容 OpenAI 格式的其他提供商（Deepseek, Qwen等）
    
    优化:
    - 使用 aiohttp 替代 requests，实现真正的异步非阻塞
    - 自动重试机制（3次，指数退避）
    - 超时控制
    - 流式解析优化
    """
    
    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1", 
                 model: str = "gpt-3.5-turbo", timeout: int = 60,
                 provider_name: str = "openai_compatible",
                 thinking_enabled: bool = False,
                 reasoning_effort: str = "high",
                 response_format: Optional[str] = None):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._provider_name = provider_name
        # 最大重试次数
        self._max_retries = 3
        # 基础重试延迟（秒）
        self._base_retry_delay = 1.0
        # DeepSeek 思考模式参数
        self._thinking_enabled = thinking_enabled
        self._reasoning_effort = reasoning_effort
        # JSON 输出格式
        self._response_format = response_format
    
    @property
    def name(self) -> str:
        return self._provider_name
    
    @property
    def default_model(self) -> str:
        return self._model
    
    def is_available(self) -> bool:
        return bool(self._api_key)
    
    def _build_messages(self, messages: List[LLMMessage], system_prompt: str = "",
                        thinking_enabled: Optional[bool] = None) -> List[Dict]:
        """构建消息列表，正确处理 tool_calls、tool_call_id 和 reasoning_content"""
        if thinking_enabled is None:
            thinking_enabled = self._thinking_enabled
        result = []
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})

        for m in messages:
            content = m.content
            # 历史里可能混入 DSML 特殊标记，回传给模型会引发模仿；这里先清掉
            if m.role == "assistant" and content and "DSML" in content:
                content = _strip_dsml(content)
            msg = {"role": m.role, "content": content}
            # 视觉模型：把 [img]…[/img] 哨兵转成 OpenAI 多模态 content 数组。
            # 非视觉模型/图片不可解析时保持纯文本，避免整轮请求被服务端拒绝。
            if m.role == "user" and content and "[img]" in content:
                model_name = getattr(self, "_model", "") or ""
                if _model_supports_vision(model_name):
                    parts = _user_content_with_images(content)
                    if parts:
                        msg["content"] = parts
            # tool 角色消息需要 tool_call_id
            if m.role == "tool" and m.tool_call_id:
                msg["tool_call_id"] = m.tool_call_id
            # assistant 角色消息可能包含 tool_calls
            if m.role == "assistant":
                if m.tool_calls:
                    msg["tool_calls"] = m.tool_calls
                    # 当有 tool_calls 时，content 应为 null（OpenAI 规范）
                    if not m.content:
                        msg["content"] = None
                # DeepSeek thinking 模式要求将上轮的 reasoning_content 回传给 API，
                # 否则返回 400（invalid_request_error）。历史消息经过压缩/落盘后常常
                # 丢失该字段，因此思考模式下对 assistant 消息统一补空串（实测空串可接受），
                # 避免整轮请求被拒。仅在 thinking 模式下回传，避免其他 API 收到未知字段。
                if thinking_enabled and m.role == "assistant":
                    msg["reasoning_content"] = getattr(m, "reasoning_content", None) or ""
            # 清理后变成空 assistant 消息就丢掉，避免无意义占位
            if (msg.get("role") == "assistant" and not msg.get("tool_calls")
                    and not (msg.get("content") or "").strip()
                    and not msg.get("reasoning_content")):
                continue
            result.append(msg)

        return _sanitize_tool_messages(result)
    
    def _build_request_data(self, msgs: List[Dict], stream: bool = False, **kwargs) -> Dict:
        """构建请求数据，支持思考模式、JSON Output、模型切换等"""
        # 模型选择：kwargs > 实例属性 > 默认值
        model = kwargs.get("model") or self._model

        data = {
            "model": model,
            "messages": msgs,
            "stream": stream,
        }

        # 思考模式：思考模式不支持 temperature/top_p/penalty 参数。
        # DeepSeek 官方文档（thinking_mode）明确：思考开关默认即为 enabled，
        # 因此关闭时必须显式下发 disabled，否则等于没关。该字段仅 DeepSeek
        # 官方端点认，其它 OpenAI 兼容厂商收到未知字段可能直接 400，故做白名单限制。
        thinking_enabled = kwargs.get("thinking_enabled", self._thinking_enabled)
        reasoning_effort = kwargs.get("reasoning_effort", self._reasoning_effort)

        if thinking_enabled:
            self._apply_thinking_params(data, enabled=True, effort=reasoning_effort)
        else:
            self._apply_thinking_params(data, enabled=False, effort=reasoning_effort)
            # 非思考模式才设置这些参数
            data["temperature"] = kwargs.get("temperature", 0.7)
            data["top_p"] = kwargs.get("top_p", 1.0)
            if "presence_penalty" in kwargs:
                data["presence_penalty"] = kwargs["presence_penalty"]
            if "frequency_penalty" in kwargs:
                data["frequency_penalty"] = kwargs["frequency_penalty"]

        # max_tokens <= 0 会被服务端判为非法参数（DeepSeek 返回 400：
        # Invalid max_tokens value），因此非正数时直接不下发，沿用服务端默认值。
        try:
            max_tokens = int(kwargs.get("max_tokens", 8192))
        except (TypeError, ValueError):
            max_tokens = 8192
        if max_tokens > 0:
            data["max_tokens"] = max_tokens

        # JSON Output 模式
        response_format = kwargs.get("response_format", self._response_format)
        if response_format == "json_object":
            data["response_format"] = {"type": "json_object"}

        # 工具调用
        tools = kwargs.get("tools")
        if tools:
            data["tools"] = tools
            # tool_choice
            tool_choice = kwargs.get("tool_choice")
            if tool_choice:
                data["tool_choice"] = tool_choice

        return data

    # DeepSeek 官方文档只认 high / max 两档（low、medium 会被映射为 high，
    # xhigh 会被映射为 max），这里做一次归一化，避免下发无意义的档位。
    _THINKING_CAPABLE = ("deepseek",)
    _EFFORT_ALIAS = {"low": "high", "medium": "high", "xhigh": "max"}

    def _apply_thinking_params(self, data: Dict, enabled: bool, effort: str) -> None:
        """按 DeepSeek v4.1 规范写入思考开关/强度（仅对白名单内的提供方生效）。

        注意：官方 SDK 里 thinking 是走 extra_body 合并进请求体的，而本模块用
        aiohttp/requests 直接投递 JSON，所以必须写成 body 顶层的 thinking 字段；
        若沿用 extra_body 会被原样当作未知参数下发，等于没生效。
        """
        if self._provider_name not in self._THINKING_CAPABLE:
            return
        data["thinking"] = {"type": "enabled" if enabled else "disabled"}
        if enabled:
            level = str(effort or "high").strip().lower()
            level = self._EFFORT_ALIAS.get(level, level)
            data["reasoning_effort"] = level if level in ("high", "max") else "high"

    def _build_headers(self) -> Dict:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        # OpenRouter 官方建议携带来源标识（HTTP-Referer / X-Title），
        # 便于模型路由与用量归属，部分模型缺省可能被拒绝。
        if self._provider_name == "openrouter":
            headers["HTTP-Referer"] = "http://localhost:8001"
            headers["X-Title"] = "Furina Agent"
        return headers
    
    async def _perform_request(self, session, url: str, headers: Dict, data: Dict, 
                                stream: bool = False, timeout: aiohttp.ClientTimeout = None):
        """执行 HTTP 请求"""
        try:
            async with session.post(url, headers=headers, json=data, 
                                     timeout=timeout) as response:
                if response.status >= 400:
                    body = await response.text()
                    logger.error(f"LLM 请求失败 [{response.status}] {url}: {body[:2000]}")
                response.raise_for_status()
                
                if stream:
                    # 流式响应
                    async for line in response.content:
                        if line:
                            line_str = line.decode('utf-8').strip()
                            if line_str.startswith('data: '):
                                payload = line_str[6:]
                                if payload == '[DONE]':
                                    break
                                try:
                                    chunk = json.loads(payload)
                                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                                    content = delta.get("content", "")
                                    if content:
                                        yield content
                                except json.JSONDecodeError:
                                    continue
                else:
                    # 非流式响应
                    result = await response.json()
                    content = result["choices"][0]["message"]["content"]
                    usage = result.get("usage", {}) or {}
                    _record_usage(self._model, usage, messages)
                    yield LLMResponse(
                        content=content,
                        model=self._model,
                        usage=usage,
                        finish_reason=result["choices"][0].get("finish_reason", "")
                    )
        except asyncio.TimeoutError:
            raise TimeoutError(f"LLM 请求超时（{self._timeout}秒）")
        except Exception as e:
            raise
    
    async def _perform_with_retry(self, session, url: str, headers: Dict, data: Dict,
                                    stream: bool = False):
        """带重试的请求执行"""
        timeout = aiohttp.ClientTimeout(total=self._timeout)
        last_error = None
        
        for attempt in range(self._max_retries):
            try:
                async for result in self._perform_request(
                    session, url, headers, data, stream=stream, timeout=timeout
                ):
                    yield result
                return  # 成功后返回
            except Exception as e:
                last_error = e
                if attempt < self._max_retries - 1:
                    delay = self._base_retry_delay * (2 ** attempt)
                    logger.warning(f"LLM 请求失败（第{attempt + 1}次），{delay}秒后重试: {e}")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"LLM 请求失败（已重试{self._max_retries}次）: {e}")
        
        # 所有重试都失败
        if stream:
            yield f"错误：{str(last_error)}"
        else:
            raise last_error
    
    async def chat(self, messages: List[LLMMessage], **kwargs) -> LLMResponse:
        try:
            import aiohttp

            system_prompt = kwargs.get("system_prompt", "")
            thinking_flag = kwargs.get("thinking_enabled", self._thinking_enabled)
            msgs = self._build_messages(messages, system_prompt, thinking_flag)
            headers = self._build_headers()
            data = self._build_request_data(msgs, stream=False, **kwargs)

            url = f"{self._base_url}/chat/completions"

            timeout = aiohttp.ClientTimeout(total=self._timeout)
            # 重试逻辑：429（上游限流）与 5xx 服务端错误会自动重试
            last_exc = None
            for attempt in range(self._max_retries):
                try:
                    async with aiohttp.ClientSession(headers=headers) as session:
                        async with session.post(url, json=data, timeout=timeout) as response:
                            if response.status == 429 or response.status >= 500:
                                body = await response.text()
                                logger.warning(
                                    f"LLM 请求返回 {response.status}（第{attempt + 1}次）: {body[:500]}"
                                )
                                if attempt < self._max_retries - 1:
                                    await asyncio.sleep(self._base_retry_delay * (2 ** attempt))
                                    continue
                                response.raise_for_status()
                            if response.status >= 400:
                                body = await response.text()
                                logger.error(f"LLM 请求失败 [{response.status}] {url}: {body[:2000]}")
                                raise RuntimeError(
                                    "HTTP %s: %s" % (response.status, (body or "")[:800])
                                )
                            response.raise_for_status()
                            result = await response.json()

                            choice = result["choices"][0]
                            message = choice.get("message", {})
                            content = message.get("content", "")
                            tool_calls = message.get("tool_calls")
                            reasoning_content = message.get("reasoning_content", "")

                            # DSML 标记泄漏到正文时：清理正文，并尽量还原成标准工具调用
                            if content and "DSML" in content:
                                content, leaked_calls = _extract_dsml_tool_calls(content)
                                if leaked_calls:
                                    logger.warning("检测到 DSML 工具标记泄漏，已还原 %d 个工具调用", len(leaked_calls))
                                    tool_calls = tool_calls or leaked_calls
                                else:
                                    logger.warning("检测到 DSML 标记，已从正文清理")

                            return LLMResponse(
                                content=content,
                                model=self._model,
                                usage=result.get("usage", {}),
                                finish_reason=choice.get("finish_reason", ""),
                                tool_calls=tool_calls,
                                reasoning_content=reasoning_content,
                            )
                except (aiohttp.ClientConnectionError, aiohttp.ServerDisconnectedError) as e:
                    last_exc = e
                    if attempt < self._max_retries - 1:
                        logger.warning(f"LLM 连接失败（第{attempt + 1}次），重试中: {e}")
                        await asyncio.sleep(self._base_retry_delay * (2 ** attempt))
                    else:
                        raise
            raise last_exc if last_exc else RuntimeError("LLM 请求重试后仍失败")
        except ImportError:
            logger.error("aiohttp 未安装，请执行: pip install aiohttp")
            # 回退到 requests
            return await self._chat_fallback_requests(messages, **kwargs)
        except Exception as e:
            logger.error(f"LLM 聊天错误: {e}")
            return LLMResponse(content=f"错误：{str(e)}")
    
    async def _chat_fallback_requests(self, messages: List[LLMMessage], **kwargs) -> LLMResponse:
        """回退到 requests 库（同步但保持异步接口）"""
        try:
            import requests

            system_prompt = kwargs.get("system_prompt", "")
            thinking_flag = kwargs.get("thinking_enabled", self._thinking_enabled)
            msgs = self._build_messages(messages, system_prompt, thinking_flag)
            headers = self._build_headers()
            data = self._build_request_data(msgs, stream=False, **kwargs)

            url = f"{self._base_url}/chat/completions"
            response = requests.post(url, headers=headers, json=data, timeout=self._timeout)
            response.raise_for_status()

            result = response.json()
            choice = result["choices"][0]
            message = choice.get("message", {})
            content = message.get("content", "")
            tool_calls = message.get("tool_calls")
            reasoning_content = message.get("reasoning_content", "")

            return LLMResponse(
                content=content,
                model=self._model,
                usage=result.get("usage", {}),
                finish_reason=choice.get("finish_reason", ""),
                tool_calls=tool_calls,
                reasoning_content=reasoning_content,
            )
        except Exception as e:
            logger.error(f"LLM 聊天错误（回退模式）: {e}")
            return LLMResponse(content=f"错误：{str(e)}")
    
    async def chat_stream(self, messages: List[LLMMessage], **kwargs) -> AsyncIterator[str]:
        """兼容旧调用方的纯文本流（chunk 字符串，推理用 __REASONING__: 前缀）。"""
        async for ev in self.chat_stream_events(messages, **kwargs):
            etype = ev.get("type")
            if etype == "text":
                yield ev.get("text", "")
            elif etype == "reasoning":
                yield "__REASONING__:" + ev.get("text", "")
            elif etype == "error":
                yield f"错误：{ev.get('message') or 'LLM 流式调用失败'}"

    async def chat_stream_events(self, messages: List[LLMMessage],
                                 **kwargs) -> AsyncIterator[Dict[str, Any]]:
        """结构化流式调用：边收边吐 text / reasoning，结束时给出 tool_calls。

        事件形态：
        - {"type": "reasoning", "text": ...}
        - {"type": "text", "text": ...}
        - {"type": "done", "content": 全文, "reasoning": 全部推理,
           "tool_calls": [...] | [], "usage": {...}}
        - {"type": "error", "message": ...}
        """
        full_reasoning: List[str] = []
        try:
            import aiohttp

            system_prompt = kwargs.get("system_prompt", "")
            thinking_flag = kwargs.get("thinking_enabled", self._thinking_enabled)
            msgs = self._build_messages(messages, system_prompt, thinking_flag)
            headers = self._build_headers()
            data = self._build_request_data(msgs, stream=True, **kwargs)

            url = f"{self._base_url}/chat/completions"
            timeout = aiohttp.ClientTimeout(total=self._timeout)

            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.post(url, json=data, timeout=timeout) as response:
                    if response.status >= 400:
                        body = await response.text()
                        logger.error(f"LLM 流式请求失败 [{response.status}] {url}: {body[:2000]}")
                        yield {"type": "error", "message": f"HTTP {response.status}: {(body or '')[:800]}"}
                        return

                    dsml_mode = False
                    dsml_buffer = ""
                    collected: List[str] = []
                    usage_done = False
                    tool_acc: Dict[int, Dict[str, Any]] = {}

                    async for line in response.content:
                        if not line:
                            continue
                        line_str = line.decode('utf-8').strip()
                        if not line_str.startswith('data: '):
                            continue
                        payload = line_str[6:]
                        if payload == '[DONE]':
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue

                        chunk_usage = chunk.get("usage")
                        if isinstance(chunk_usage, dict) and not usage_done:
                            _record_usage(self._model, chunk_usage, source="chat-stream")
                            usage_done = True

                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        reasoning = delta.get("reasoning_content", "")
                        if reasoning:
                            full_reasoning.append(reasoning)
                            yield {"type": "reasoning", "text": reasoning}

                        # 工具调用增量：按 index 拼接 id / name / arguments 片段
                        for tc in (delta.get("tool_calls") or []):
                            idx = int(tc.get("index") or 0)
                            slot = tool_acc.setdefault(idx, {
                                "id": "", "type": "function",
                                "function": {"name": "", "arguments": ""},
                            })
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["function"]["name"] += fn["name"]
                            if fn.get("arguments"):
                                slot["function"]["arguments"] += fn["arguments"]

                        content = delta.get("content", "")
                        if not content:
                            continue
                        collected.append(content)
                        if dsml_mode:
                            dsml_buffer += content
                        elif "DSML" in content:
                            dsml_mode = True
                            dsml_buffer += content
                        else:
                            yield {"type": "text", "text": content}

                    if dsml_mode:
                        cleaned, leaked_calls = _extract_dsml_tool_calls(dsml_buffer)
                        logger.warning(
                            "流式响应检测到 DSML 标记，已清理正文（还原工具调用 %d 个）",
                            len(leaked_calls),
                        )
                        if cleaned:
                            yield {"type": "text", "text": cleaned}
                        if leaked_calls and not tool_acc:
                            for i, call in enumerate(leaked_calls):
                                tool_acc[i] = call

                    calls = []
                    for idx in sorted(tool_acc.keys()):
                        call = tool_acc[idx]
                        if not call.get("id"):
                            call["id"] = f"call_{idx}_{uuid.uuid4().hex[:8]}"
                        calls.append(call)

                    if not usage_done:
                        _record_estimated_usage(self._model, messages, "".join(collected))

                    yield {
                        "type": "done",
                        "content": "".join(collected),
                        "reasoning": "".join(full_reasoning),
                        "tool_calls": calls,
                        "usage": {},
                    }
        except ImportError:
            logger.warning("aiohttp 未安装，回退到 requests 流式模式")
            async for chunk in self._chat_stream_fallback_requests(messages, **kwargs):
                if chunk.startswith("__REASONING__:"):
                    yield {"type": "reasoning", "text": chunk[len("__REASONING__:"):]}
                else:
                    yield {"type": "text", "text": chunk}
            yield {"type": "done", "content": "", "reasoning": "", "tool_calls": [], "usage": {}}
        except Exception as e:
            logger.error(f"LLM 流式聊天错误: {e}")
            yield {"type": "error", "message": str(e)}
    
    async def _chat_stream_fallback_requests(self, messages: List[LLMMessage], 
                                              **kwargs) -> AsyncIterator[str]:
        """回退到 requests 流式模式"""
        try:
            import requests
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
            
            system_prompt = kwargs.get("system_prompt", "")
            thinking_flag = kwargs.get("thinking_enabled", self._thinking_enabled)
            msgs = self._build_messages(messages, system_prompt, thinking_flag)
            headers = self._build_headers()
            data = self._build_request_data(msgs, stream=True, **kwargs)
            
            url = f"{self._base_url}/chat/completions"
            
            # 配置重试策略
            session = requests.Session()
            retries = Retry(total=self._max_retries, backoff_factor=self._base_retry_delay,
                           status_forcelist=[429, 500, 502, 503, 504])
            session.mount('https://', HTTPAdapter(max_retries=retries))
            session.mount('http://', HTTPAdapter(max_retries=retries))
            
            with session.post(url, headers=headers, json=data, stream=True, 
                               timeout=self._timeout) as response:
                response.raise_for_status()
                
                for line in response.iter_lines():
                    if line:
                        line_str = line.decode('utf-8').strip()
                        if line_str.startswith('data: '):
                            payload = line_str[6:]
                            if payload == '[DONE]':
                                break
                            try:
                                chunk = json.loads(payload)
                                delta = chunk.get("choices", [{}])[0].get("delta", {})
                                # 思考模式
                                reasoning_content = delta.get("reasoning_content", "")
                                if reasoning_content:
                                    yield f"__REASONING__:{reasoning_content}"
                                # 普通内容
                                content = delta.get("content", "")
                                if content:
                                    yield content
                            except json.JSONDecodeError:
                                continue
        except Exception as e:
            logger.error(f"LLM 流式聊天错误（回退模式）: {e}")
            yield f"错误：{str(e)}"


# ---- OpenRouter 请求限制 ----
# 依据官方文档（https://openrouter.ai/docs/limits）：
# 免费模型变体（模型 ID 以 :free 结尾）限制为 20 请求/分钟 和 200 请求/天。
_OPENROUTER_RATE_LIMIT = 20           # 每分钟最大请求数（RPM）
_OPENROUTER_DAILY_LIMIT = 200         # 每日最大请求数（免费模型上限）
_openrouter_timestamps = []           # 最近请求时间戳列表
_openrouter_daily_count = 0           # 当日请求计数
_openrouter_daily_date = None         # 当日日期


def _check_openrouter_free_limits() -> bool:
    """检查是否超出 OpenRouter 免费模型请求限制，超出则返回 False（应拒绝请求）

    限制策略（来源: https://openrouter.ai/docs/limits）：
    - 免费模型（:free 后缀）：20 请求/分钟，200 请求/天
    - 持续超出限制可能触发 Cloudflare DDoS 保护封禁，故在客户端主动拦截。
    """
    global _openrouter_timestamps, _openrouter_daily_count, _openrouter_daily_date
    now = datetime.datetime.now()

    # 每日计数重置
    today = now.date()
    if _openrouter_daily_date != today:
        _openrouter_daily_date = today
        _openrouter_daily_count = 0

    # 速率限制：清理超过 60 秒的时间戳
    _openrouter_timestamps = [t for t in _openrouter_timestamps if now - t < datetime.timedelta(seconds=60)]
    if len(_openrouter_timestamps) >= _OPENROUTER_RATE_LIMIT:
        return False

    # 每日上限
    if _openrouter_daily_count >= _OPENROUTER_DAILY_LIMIT:
        return False

    _openrouter_timestamps.append(now)
    _openrouter_daily_count += 1
    return True


class OpenRouterProvider(OpenAIProvider):
    """OpenRouter 提供商
    兼容 OpenAI 接口，并叠加免费模型请求限制策略。
    """

    def __init__(self, api_key: str, base_url: str = "https://openrouter.ai/api/v1",
                 model: str = "google/gemma-4-26b-a4b-it:free", timeout: int = 60,
                 provider_name: str = "openrouter", **kwargs):
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            model=model,
            timeout=timeout,
            provider_name=provider_name,
            **kwargs,
        )

    def _enforce_rate_limit(self) -> None:
        """在发起请求前检查免费额度，超限时抛出异常阻止请求"""
        if not _check_openrouter_free_limits():
            raise RuntimeError(
                "已达到 OpenRouter 免费模型请求限制（20 请求/分钟 或 200 请求/天，"
                "见 https://openrouter.ai/docs/limits）。请稍后再试。"
            )

    async def chat(self, messages, **kwargs):
        self._enforce_rate_limit()
        return await super().chat(messages, **kwargs)

    async def chat_stream(self, messages, **kwargs):
        self._enforce_rate_limit()
        async for chunk in super().chat_stream(messages, **kwargs):
            yield chunk


def create_provider(provider_type: str, **kwargs) -> Optional[LLMProvider]:
    """
    根据提供商类型创建提供商实例
    
    参数:
        provider_type: 提供商类型（mock, openai, deepseek, zhipu, qwen, moonshot,
                       doubao, minimax, hunyuan, ernie, spark, siliconflow, together,
                       gemini, anthropic, xai, mistral, groq, openrouter）
        **kwargs: 提供商初始化参数
    
    返回:
        Optional[LLMProvider]: 提供商实例，创建失败返回 None
    """
    providers = {
        "mock": MockProvider,
        "openai": OpenAIProvider,
        "deepseek": lambda **kw: OpenAIProvider(
            base_url="https://api.deepseek.com",
            provider_name="deepseek",
            **kw
        ),
        "zhipu": lambda **kw: OpenAIProvider(
            base_url="https://open.bigmodel.cn/api/paas/v4",
            provider_name="zhipu",
            **kw
        ),
        "qwen": lambda **kw: OpenAIProvider(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            provider_name="qwen",
            **kw
        ),
        "moonshot": lambda **kw: OpenAIProvider(
            base_url="https://api.moonshot.cn/v1",
            provider_name="moonshot",
            **kw
        ),
        "doubao": lambda **kw: OpenAIProvider(
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            provider_name="doubao",
            **kw
        ),
        "minimax": lambda **kw: OpenAIProvider(
            base_url="https://api.minimax.cn/v1",
            provider_name="minimax",
            **kw
        ),
        "hunyuan": lambda **kw: OpenAIProvider(
            base_url="https://api.hunyuan.cloud.tencent.com/v1",
            provider_name="hunyuan",
            **kw
        ),
        "ernie": lambda **kw: OpenAIProvider(
            base_url="https://qianfan.baidubce.com/v2",
            provider_name="ernie",
            **kw
        ),
        "spark": lambda **kw: OpenAIProvider(
            base_url="https://spark-api-open.xf-yun.com/v1",
            provider_name="spark",
            **kw
        ),
        "gemini": lambda **kw: OpenAIProvider(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            provider_name="gemini",
            **kw
        ),
        "siliconflow": lambda **kw: OpenAIProvider(
            base_url="https://api.siliconflow.cn/v1",
            provider_name="siliconflow",
            **kw
        ),
        "together": lambda **kw: OpenAIProvider(
            base_url="https://api.together.ai/v1",
            provider_name="together",
            **kw
        ),
        "anthropic": lambda **kw: OpenAIProvider(
            base_url="https://api.anthropic.com/v1",
            provider_name="anthropic",
            **kw
        ),
        "xai": lambda **kw: OpenAIProvider(
            base_url="https://api.x.ai/v1",
            provider_name="xai",
            **kw
        ),
        "mistral": lambda **kw: OpenAIProvider(
            base_url="https://api.mistral.ai/v1",
            provider_name="mistral",
            **kw
        ),
        "groq": lambda **kw: OpenAIProvider(
            base_url="https://api.groq.com/openai/v1",
            provider_name="groq",
            **kw
        ),
        "openrouter": OpenRouterProvider,
    }
    
    provider_class = providers.get(provider_type.lower())
    if provider_class:
        return provider_class(**kwargs)
    return None