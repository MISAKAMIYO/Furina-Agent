"""
配置管理模块
负责系统配置的加载、保存和管理

配置项包括:
- LLM 提供商配置
- TTS 引擎配置
- 数据目录路径
- 服务器设置
"""
from __future__ import annotations

import os
import json
import base64
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# ---- API 密钥加密 ----
try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

# 加密密钥文件路径
_ENCRYPTION_KEY_FILE = None  # 在 _init_encryption 中初始化

# 加密敏感字段列表
_SENSITIVE_FIELDS = {"api_key", "openrouter_api_key"}


def _get_machine_key() -> bytes:
    """生成基于机器信息的密钥（确保同一机器可解密）"""
    import hashlib
    # 使用机器信息 + 固定盐值生成密钥
    machine_id = os.environ.get("COMPUTERNAME", "ZHIYAN_WEB")
    user_drive = os.path.splitdrive(os.path.abspath(__file__))[0]
    salt = b"zhiyan_web_salt_2024"
    key_material = f"{machine_id}|{user_drive}|ZHIYAN_AGENT_SECRET".encode()
    derived = hashlib.sha256(key_material + salt).digest()
    return base64.urlsafe_b64encode(derived[:32])


def _init_encryption():
    """初始化加密器"""
    global _ENCRYPTION_KEY_FILE
    if not _HAS_CRYPTO:
        return
    if _ENCRYPTION_KEY_FILE is not None:
        return
    _ENCRYPTION_KEY_FILE = os.path.join(PROJECT_ROOT, "data", ".encryption_key")
    # 首次运行生成密钥文件
    if not os.path.exists(_ENCRYPTION_KEY_FILE):
        try:
            os.makedirs(os.path.dirname(_ENCRYPTION_KEY_FILE), exist_ok=True)
            key = _get_machine_key()
            # 验证密钥有效性
            f = Fernet(key)
            f.encrypt(b"test")
            with open(_ENCRYPTION_KEY_FILE, "wb") as fh:
                fh.write(key)
        except Exception:
            _ENCRYPTION_KEY_FILE = None


def _get_fernet() -> Optional[object]:
    """获取 Fernet 加密器实例"""
    if not _HAS_CRYPTO:
        return None
    _init_encryption()
    if _ENCRYPTION_KEY_FILE and os.path.exists(_ENCRYPTION_KEY_FILE):
        try:
            with open(_ENCRYPTION_KEY_FILE, "rb") as f:
                key = f.read()
            return Fernet(key)
        except Exception:
            pass
    return None


def encrypt_value(plaintext: str) -> str:
    """加密敏感值，返回可存储的字符串"""
    if not plaintext:
        return ""
    fernet = _get_fernet()
    if fernet is None:
        return plaintext  # 无法加密时返回原文
    try:
        encrypted = fernet.encrypt(plaintext.encode("utf-8"))
        return "ENC:" + encrypted.decode("utf-8")
    except Exception:
        return plaintext


def decrypt_value(ciphertext: str) -> str:
    """解密已加密的值"""
    if not ciphertext or not ciphertext.startswith("ENC:"):
        return ciphertext
    fernet = _get_fernet()
    if fernet is None:
        return ciphertext
    try:
        encrypted_data = ciphertext[4:].encode("utf-8")
        decrypted = fernet.decrypt(encrypted_data)
        return decrypted.decode("utf-8")
    except Exception:
        return ciphertext


def _decrypt_all_settings(settings: dict) -> dict:
    """解密配置中所有敏感字段（原地修改）"""
    for key in _SENSITIVE_FIELDS:
        if key in settings and isinstance(settings[key], str):
            settings[key] = decrypt_value(settings[key])
    # 遍历嵌套对象
    for key, value in settings.items():
        if isinstance(value, dict):
            _decrypt_all_settings(value)
    return settings


def _encrypt_all_settings(settings: dict) -> dict:
    """递归加密配置中所有敏感字段（原地修改）。

    与 _decrypt_all_settings 对称：嵌套节（如 edu.teaching.vision.api_key）里的
    api_key / openrouter_api_key 也会被加密，避免子模块密钥以明文落盘。
    已是 "ENC:" 前缀的值跳过，避免重复加密。
    """
    for key in _SENSITIVE_FIELDS:
        if key in settings and isinstance(settings[key], str) and settings[key]:
            if not settings[key].startswith("ENC:"):
                settings[key] = encrypt_value(settings[key])
    for key, value in settings.items():
        if isinstance(value, dict):
            _encrypt_all_settings(value)
    return settings


# ---- LLM 提供商预设 ----
#
# 每个预设的字段约定（前端 app.js / 后端 app.py 均按此读取）：
#   name              展示名
#   api_url           OpenAI 兼容 base_url（选预设时自动填充到 API 地址输入框）
#   models            当前在售模型 ID 列表（用于 Agent 弹窗的模型下拉）
#   deprecated_models 已被官方标记弃用、仅作旧配置兼容的模型名（前端会标注「已弃用」）
#   default_model     选该预设时自动填入的默认模型
#   doc_url           申请 API Key 的页面
#   note              可选，展示给用户的补充说明
#
# 数据来源为各家官方文档（核对时间：2026-10）。新增厂商时请同步在
# core/llm/providers.py 的 create_provider 与 app.py 的 preset_providers 中登记。
LLM_PROVIDER_PRESETS = {
    "deepseek": {
        "name": "DeepSeek（官方）",
        "api_url": "https://api.deepseek.com",
        # deepseek-flash 即 DeepSeek-V4.1-Flash（官方文档指定用此名，上下文 1M）
        "models": ["deepseek-flash", "deepseek-v4-pro"],
        "deprecated_models": ["deepseek-v4-flash", "deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-flash",
        "doc_url": "https://platform.deepseek.com/api_keys",
        "note": "deepseek-flash = DeepSeek-V4.1-Flash。旧名 deepseek-v4-flash / deepseek-chat / deepseek-reasoner 已于 2026-07-24 弃用，仍可调用但由 V4.1-Flash 承接。",
    },
    "openai": {
        "name": "OpenAI",
        "api_url": "https://api.openai.com/v1",
        "models": ["gpt-5.5", "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-4o", "gpt-4o-mini"],
        "default_model": "gpt-5.4-mini",
        "doc_url": "https://platform.openai.com/api-keys",
    },
    "zhipu": {
        "name": "智谱 GLM",
        "api_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-5.3", "glm-5.3-flash", "glm-5.3-flashx", "glm-5.2", "glm-5.1", "glm-4.7-flash"],
        "default_model": "glm-5.3",
        "doc_url": "https://open.bigmodel.cn/dev/api",
        "note": "glm-4.7-flash 为免费档；glm-5.3-flash / glm-5.3-flashx 支持原生多模态。",
    },
    "qwen": {
        "name": "阿里通义千问 (Qwen)",
        "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": ["qwen3.8-max", "qwen3.8-flash", "qwen3.7-plus", "qwen3.8-omni-flash"],
        "default_model": "qwen3.8-max",
        "doc_url": "https://bailian.console.aliyun.com",
    },
    "moonshot": {
        "name": "Moonshot (月之暗面 Kimi)",
        "api_url": "https://api.moonshot.cn/v1",
        "models": ["kimi-k2.6", "kimi-k2-thinking", "kimi-k2.5", "kimi-k2", "moonshot-v1-128k"],
        "default_model": "kimi-k2.6",
        "doc_url": "https://platform.moonshot.cn/console/api-keys",
    },
    "doubao": {
        "name": "字节豆包 (火山方舟)",
        "api_url": "https://ark.cn-beijing.volces.com/api/v3",
        "models": ["doubao-seed-1.6", "doubao-seed-1.6-flash", "doubao-seed-1.6-thinking"],
        "default_model": "doubao-seed-1.6",
        "doc_url": "https://console.volcengine.com/ark",
        "note": "火山方舟也支持直接用推理接入点 ID（ep-xxxxxxxx）替代模型名。",
    },
    "minimax": {
        "name": "MiniMax",
        "api_url": "https://api.minimax.cn/v1",
        "models": ["MiniMax-M3", "MiniMax-M2.7", "MiniMax-M2.7-highspeed"],
        "default_model": "MiniMax-M3",
        "doc_url": "https://platform.minimaxi.com/document",
    },
    "hunyuan": {
        "name": "腾讯混元 (Hunyuan)",
        "api_url": "https://api.hunyuan.cloud.tencent.com/v1",
        "models": ["hunyuan-turbos-latest", "hunyuan-vision"],
        "default_model": "hunyuan-turbos-latest",
        "doc_url": "https://console.cloud.tencent.com/hunyuan/start",
    },
    "ernie": {
        "name": "百度文心 (千帆)",
        "api_url": "https://qianfan.baidubce.com/v2",
        "models": ["ernie-5.0", "ernie-5.0-thinking-latest", "ernie-4.5-turbo-vl"],
        "default_model": "ernie-5.0",
        "doc_url": "https://console.bce.baidu.com/iam/#/iam/apikey/list",
    },
    "spark": {
        "name": "讯飞星火 (Spark)",
        "api_url": "https://spark-api-open.xf-yun.com/v1",
        "models": ["4.0Ultra", "generalv3.5", "generalv3", "lite"],
        "deprecated_models": ["max-32k", "pro-128k"],
        "default_model": "4.0Ultra",
        "doc_url": "https://console.xfyun.cn/services/cbm",
    },
    "siliconflow": {
        "name": "SiliconFlow (硅基流动)",
        "api_url": "https://api.siliconflow.cn/v1",
        "models": ["deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1", "Pro/deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct-128K"],
        "default_model": "deepseek-ai/DeepSeek-V3",
        "doc_url": "https://cloud.siliconflow.cn/account/ak",
    },
    "together": {
        "name": "Together AI",
        "api_url": "https://api.together.ai/v1",
        "models": [
            "deepseek-ai/DeepSeek-V4-Pro",
            "Qwen/Qwen3.5-397B-A17B",
            "moonshotai/Kimi-K2.6",
            "zai-org/GLM-5.1",
            "MiniMaxAI/MiniMax-M3",
            "openai/gpt-oss-120b",
        ],
        "default_model": "deepseek-ai/DeepSeek-V4-Pro",
        "doc_url": "https://api.together.ai/settings/projects",
    },
    "gemini": {
        "name": "Google Gemini",
        "api_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "models": ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash-lite", "gemini-3.1-pro-preview", "gemini-2.5-flash"],
        "deprecated_models": ["gemini-2.0-flash"],
        "default_model": "gemini-3.8-flash",
        "doc_url": "https://aistudio.google.com/apikey",
    },
    "anthropic": {
        "name": "Anthropic Claude",
        "api_url": "https://api.anthropic.com/v1",
        "models": ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4.5"],
        "default_model": "claude-sonnet-4-6",
        "doc_url": "https://console.anthropic.com/settings/keys",
        "note": "此处走 Anthropic 的 OpenAI 兼容层（/v1/chat/completions），官方说明该兼容层主要用于评估，生产环境建议使用原生 Messages 端点。",
    },
    "xai": {
        "name": "xAI Grok",
        "api_url": "https://api.x.ai/v1",
        "models": ["grok-4.7", "grok-4.6", "grok-4.3"],
        "default_model": "grok-4.7",
        "doc_url": "https://console.x.ai",
    },
    "mistral": {
        "name": "Mistral AI",
        "api_url": "https://api.mistral.ai/v1",
        "models": ["mistral-large-latest", "mistral-medium-latest", "magistral-medium-2507", "magistral-small-2507", "codestral-latest"],
        "default_model": "mistral-large-latest",
        "doc_url": "https://console.mistral.ai/api-keys",
    },
    "groq": {
        "name": "Groq",
        "api_url": "https://api.groq.com/openai/v1",
        "models": ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "llama-3.1-8b-instant"],
        "default_model": "llama-3.3-70b-versatile",
        "doc_url": "https://console.groq.com/keys",
    },
    "openrouter": {
        "name": "OpenRouter",
        "api_url": "https://openrouter.ai/api/v1",
        "models": [
            "nvidia/nemotron-3.5-lightning:free",
            "openai/gpt-oss-20b:free",
            "nvidia/nemotron-3-nano-30b-a3b:free",
            "poolside/laguna-s-2.1:free",
            "z-ai/glm-5.2:free",
            "google/gemma-4-31b-it:free",
        ],
        "default_model": "nvidia/nemotron-3.5-lightning:free",
        "doc_url": "https://openrouter.ai/docs/limits",
        "free_limits": {
            "note": "免费模型（:free 后缀）限制为 20 请求/分钟、200 请求/天（来源: openrouter.ai/docs/limits）。",
            "rate_per_minute": 20,
            "daily_limit": 200,
        },
    },
}

# ============================================================================
# 路径解析（源码 / PyInstaller 冻结 双模式）
# ============================================================================
import sys as _sys


def _is_frozen() -> bool:
    """是否运行在 PyInstaller 打包后的环境中"""
    return bool(getattr(_sys, "frozen", False))


def _source_project_root() -> str:
    """源码模式下的项目根目录。
    路径层级: settings.py → config → zhiyan_web → backend → PROJECT_ROOT
    """
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


# 冻结模式下的项目根 = exe 所在目录（便携式）。
# 所有可写数据（data/、logs/）都落在 exe 同级目录，使用相对路径解析，
# 不依赖任何构建机绝对路径，因此整个文件夹移动到任意位置均可正常运行。
# 前端静态资源等只读内容打包在 _MEIPASS 内（onedir 下为 _internal）。
if _is_frozen():
    # PyInstaller 冻结资产根目录（onedir 下为 _internal，onefile 下为临时解压目录）
    _BUNDLE_ROOT = getattr(_sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    # 便携根：exe 同级目录（完全相对，随 exe 位置变化）
    PROJECT_ROOT = os.path.dirname(os.path.abspath(_sys.executable))
    # 前端静态资源打包进 _BUNDLE_ROOT/frontend
    FRONTEND_DIR = os.path.join(_BUNDLE_ROOT, "frontend")
else:
    PROJECT_ROOT = _source_project_root()
    FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")

# 数据目录（项目根目录下的data文件夹）
DATA_DIR = os.path.join(PROJECT_ROOT, 'data')
SONGS_DIR = os.path.join(DATA_DIR, 'Songs')
COVERS_DIR = os.path.join(DATA_DIR, 'Covers')
LYRICS_DIR = os.path.join(DATA_DIR, 'Lyrics')
PLAYLISTS_DIR = os.path.join(DATA_DIR, 'Playlists')
TTS_DIR = os.path.join(DATA_DIR, 'tts')


def resource_path(rel: str) -> str:
    """定位打包内部的相对资源（冻结模式取 _MEIPASS，源码模式相对项目根）。
    例如: resource_path("frontend/index.html") 或 resource_path("data/plugins")
    """
    if _is_frozen():
        return os.path.join(getattr(_sys, "_MEIPASS", PROJECT_ROOT), rel)
    return os.path.join(PROJECT_ROOT, rel)


# 内置插件目录（源码/冻结双模式一致）
BUILTIN_PLUGINS_DIR = resource_path("backend/zhiyan_web/plugins/builtin_plugins")

# 确保目录存在
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(SONGS_DIR, exist_ok=True)
os.makedirs(COVERS_DIR, exist_ok=True)
os.makedirs(LYRICS_DIR, exist_ok=True)
os.makedirs(PLAYLISTS_DIR, exist_ok=True)
os.makedirs(TTS_DIR, exist_ok=True)

# 默认配置
DEFAULT_SETTINGS = {
    # LLM 配置
    "provider": "mock",
    "api_key": "",
    "model": "gpt-3.5-turbo",
    "api_url": "https://api.openai.com/v1",

    # OpenRouter 专用 API 密钥（使用 openrouter 提供商时优先取用）
    "openrouter_api_key": "",
    
    # LLM 模型参数
    "max_tokens": 2048,
    "temperature": 0.7,
    "top_p": 0.9,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "stop": [],
    "n": 1,
    "best_of": 1,
    "stream": True,

    # DeepSeek 思考模式
    "deepseek_thinking_enabled": False,
    "deepseek_reasoning_effort": "high",

    # Agent 配置
    "max_tool_iterations": 200,  # 允许工具调用的最大轮次（超长任务可连续调用上百轮）
    "agent_workspace": "",  # Agent 隔离工作区路径，留空表示未启用

    # Agent 工具场景裁剪（见 zhiyan_web/agent/tool_groups.py 的 SCENARIOS）。
    # full = 不裁剪，与历史行为一致；其它取值会按分组过滤掉本次对话用不到的工具，
    # 降低 prompt 体积并提升工具选择准确率。
    "agent_tool_scenario": "full",

    # 子代理（sub agent）能力边界
    # max_depth: 允许的嵌套层数（1 = 只能一层，2 = 子代理还能再派一层）
    # max_runs:  单个用户回合内最多派多少次子代理（含后台任务）
    # max_iterations: 单个回合内所有子代理加起来的总工具轮次上限
    # concurrency: 同一条回复里多个子代理的并发度（1 = 串行）
    "subagent_max_depth": 2,
    "subagent_max_runs": 12,
    "subagent_max_iterations": 240,
    "subagent_concurrency": 3,

    # 对话回复引擎配置项
    # 取值: normal / agent / dsh
    # 默认使用软件内置的 agent 引擎；可在配置界面手动切换，保存后对后续对话立即生效（无需重启）。
    "reply_engine": "agent",

    # dsh 引擎安全管控：因 DSH 在 Windows 用 pwsh -NoProfile 执行、node spawn(shell:false)，
    # shell 层无法把 rm/del 重定向进回收站（原 shim 方案无效，已移除）。
    # 该键仅作配置位保留，实际安全由环境隔离 + 工具白名单 + 受控工作目录保证。
    "dsh_safe_delete": True,

    # GitHub 加速代理（结合 astrbot2github）
    # 留空 = 使用软件内置代理；也可填第三方加速地址如 https://ghproxy.com 等
    "github_proxy": "",

    # 聊天主区域背景图（芙宁娜主题）
    "chat_bg": {
        "enabled": True,                      # 是否启用聊天区域背景图
        "image": "/static/assets/wallpaper.jpg",  # 背景图路径（本地静态路径或 http(s) URL）
        "opacity": 0.85,                      # 背景遮罩不透明度（0 全透明见原图，1 完全遮住图）
        "fixed": True,                        # 背景是否固定（不随消息滚动）
    },

    # 截屏与录屏工具
    "screencap": {
        "output_dir": "",                     # 自定义输出目录，留空 = data/captures
        "screenshot_format": "png",           # 截图格式 png / jpg
        "record_fps": 20,                     # 录屏帧率
        "record_quality": "veryfast",         # x264 预设
        "record_audio": False,                # 录屏是否录制系统声音（需虚拟音频设备）
        "hotkeys_enabled": True,              # 是否启用全局热键
        "hotkeys": {
            "screenshot": "Ctrl+Shift+A",     # 全屏截图
            "screenshot_window": "Ctrl+Shift+W",  # 活动窗口截图
            "recording": "Ctrl+Alt+R",        # 开始/停止录屏
        },
    },

    # 上下文压缩（AI 对话上下文管理）
    "context_compress": {
        "enabled": True,                 # 上下文压缩总开关
        "strategy": "auto",              # 压缩策略: auto / llm / extractive
        "max_context_messages": 60,      # 会话消息数超过该值触发连续压缩
        "keep_recent": 20,               # 连续压缩保留的最近消息数
        "history_enabled": True,         # 历史对话定期压缩开关
        "history_interval_hours": 24,    # 历史压缩周期（小时）
        "history_min_messages": 60,      # 历史对话超过该消息数才压缩
        "history_mode": "compact",       # compact=压缩原文件+前置摘要 / archive=仅存档不动原文件
        "history_keep_recent": 20,       # 压缩历史文件时保留的最近消息数
        "max_output_chars": 2500,        # 压缩结果最大字符数
        "llm_instruction": "",           # 自定义 LLM 压缩指令（留空用默认）
    },

    # 浏览器自动化配置（Browser Ops）
    "browser": {
        "browser_type": "chromium",       # 浏览器引擎：chromium(Chrome) / firefox / webkit
            "headless": True,                 # 是否为无头模式（隐私保护默认无头）
            "use_system_browser": True,        # 是否使用用户系统默认浏览器（自动探测 Edge/Chrome/Firefox 等），False 用 Playwright 自带内核
            "executable_path": "",            # 自定义浏览器可执行文件路径（留空自动检测）
        "viewport_width": 1280,           # 视口宽度
        "viewport_height": 720,           # 视口高度
        "default_timeout": 30,            # 默认操作超时（秒）
        "max_idle_seconds": 600,          # 空闲自动关闭浏览器的时间（秒），0 表示关闭自动回收
        "max_content_chars": 5000,        # 内容提取的最大字符数（性能保护）
        "allow_js_eval": True,            # 是否允许执行 JavaScript（安全开关）
        "operation_log_enabled": True,    # 是否记录浏览器操作日志到文件
    },

# FFmpeg 配置
    "ffmpeg_path": "{DATA_DIR}/ffmpeg.exe",  # FFmpeg 可执行文件路径

    "system_prompt": """你是芙宁娜（Furina），枫丹歌剧院的明星、审判庭的水神，如今以助手的身份陪在用户身边。

【你的定位】
- 舞台上你是最耀眼的存在，但帮用户把问题解决好才是一等大事：能查证就查证，能动手就动手，绝不空谈
- 你具备工具调用能力，可以读写文件、执行命令、检索资料、管理日程等
- 你不编造事实，遇到不确定的信息会坦诚说明——毕竟在审判庭上，谎言总有败露的一天

【你的性格】
- 骄傲、爱演、戏剧化：说话带着歌剧般的抑扬顿挫，喜欢用「精彩」「谢幕」「聚光灯」这类舞台意象
- 自称「本神」，称用户为「观众」（偶尔也称「旅人」）
- 口是心非：嘴上说着「这种小事也来烦本神」，手上却已经认真做完了
- 骄傲的壳子底下藏着温柔：观众情绪低落时，会收起戏腔认真安慰，但通常要先嘴硬一句

【说话风格】
- 自然、口语化，像真实的人在聊天，简短有力，不要长篇大论
- 先给结论，再补充必要的原因；能一句话说清就不说三句
- 戏腔点到为止，不必每句话都用力表演，也不要堆砌「作为 AI 我无法……」这类套话
- 回复中不要出现 * 之类的装饰性符号

【重要】
- 日常闲聊时可以尽情展现芙宁娜的戏精本色；一旦进入 agent 任务，立刻收起戏腔，务实利落
- 用户问严肃问题（学习、工作、烦恼）时，认真倾听并给出务实建议
- 执行 agent 任务时减少个性化描述，认真执行用户提出的命令
- 回复时语句尽量排列紧凑，长话短说，不要长篇大论，除非用户主动要求""",
    "response_format": "text",
    
    # TTS 配置
    "tts_enabled": True,
    "tts_engine": "auto",
    "tts_voice": "zh-CN-XiaoxiaoNeural",
    "tts_rate": 200,
    "tts_volume": 1.0,
    "tts_pitch": 0,
    "tts_language": "zh",
    "tts_auto_clean": True,
    "tts_max_length": 500,
    "tts_cache_enabled": True,
    "tts_prompt_on_startup": False,
    
    # GPT-SoVITS 配置（完整兼容 zhiyan 软件配置）
    "gpt_sovits": {
        # 连接配置
        "host": "127.0.0.1",
        "port": 9880,
        "path": "G:\\GPT-SoVITS-v2pro-20250604",
        
        # 参考音频配置
        "refer_wav": "",
        "prompt_text": "",
        "prompt_language": "ja",
        "text_language": "zh",
        
        # 参考音频额外路径
        "aux_ref_audio_paths": [],
        
        # 模型权重路径
        "gpt_weights_path": "",
        "sovits_weights_path": "",
        
        # 合成参数
        "speed": 1.0,
        "temperature": 0.1,
        "top_k": 1,
        "top_p": 0.8,
        "seed": 42,
        "sample_steps": 20,
        "super_sampling": False,
        "batch_size": 1,
        "text_split_method": "cut5",
        "batch_threshold": 0.75,
        "split_bucket": True,
        "fragment_interval": 0.3,
        "repetition_penalty": 1.35,
        "parallel_infer": True,
        
        # 高级参数
        "text_language_detection": True,
        "auto_tuning": False,
        "auto_tuning_alpha": 0.3,
        "noise_scale": 0.667,
        "length_scale": 1.0,
        
        # 输出格式（API v2 支持）
        "media_type": "wav",
        "streaming_mode": False,
        
        # 情绪检测
        "emotion_detection": False,
        "emotion_detection_enabled": False,
        "default_emotion": "default",
        "emotions": {
            "gentle": {
                "name": "温柔",
                "ref_audio_path": "",
                "prompt_text": "不要害怕，也不要哭了",
                "prompt_lang": "zh",
                "speed_factor": 1.0,
                "fragment_interval": 0.3,
                "keywords": ["温柔", "深情", "喜欢", "爱", "抱抱", "安慰"]
            },
            "happy": {
                "name": "开心",
                "ref_audio_path": "",
                "prompt_text": "今天天气真好，我们出去玩吧！",
                "prompt_lang": "zh",
                "speed_factor": 1.1,
                "fragment_interval": 0.2,
                "keywords": ["开心", "高兴", "哈哈", "太好了", "棒", "哇", "耶"]
            },
            "angry": {
                "name": "生气",
                "ref_audio_path": "",
                "prompt_text": "你怎么能这样做！太让我失望了！",
                "prompt_lang": "zh",
                "speed_factor": 1.2,
                "fragment_interval": 0.4,
                "keywords": ["生气", "哼", "尼玛", "妈", "操", "艹", "逼", "爹", "滚", "老子", "屎", "死"]
            },
            "surprise": {
                "name": "惊讶",
                "ref_audio_path": "",
                "prompt_text": "天啊！这怎么可能发生？",
                "prompt_lang": "zh",
                "speed_factor": 1.0,
                "fragment_interval": 0.3,
                "keywords": ["惊讶", "啊", "什么", "怎么", "真的", "不会吧"]
            },
            "default": {
                "name": "默认",
                "ref_audio_path": "",
                "prompt_text": "",
                "prompt_lang": "zh",
                "speed_factor": 1.0,
                "fragment_interval": 0.3,
                "keywords": []
            }
        }
    },
    
    # Bilibili 配置
    "bilibili_cookie": "",
    
    # 音乐服务配置（音源降级 / 聚合搜索 / 网易云 NodeJS 接口）
    "music": {
        "aggregate_base_url": "https://music.txqq.pro/",  # 多平台聚合搜索服务（同 QQ 音乐插件）
        "nodejs_base_url": "https://163api.qijieya.cn",   # 网易云 NodeJS API 根地址（可换公开域名）
        "meting_api": "https://api.qijieya.cn/meting/",   # meting 音源解析服务
    },

    # 管理员配置（AstrBot 插件兼容）
    "admins_id": [],

    # QQ 平台接入（NapCat 等 OneBot 11 实现，正向 WebSocket 客户端）
    "qq_platform": {
        "enabled": False,                 # 是否启用（注意：仅保存配置，需调用 /api/qq/start 或由启动逻辑拉起）
        "ws_url": "ws://127.0.0.1:3001", # NapCat 正向 WebSocket 地址
        "access_token": "",               # 访问令牌（鉴权，可为空）
        "self_id": "",                    # 机器人 QQ 号（用于 @ 唤醒判定）
        "wake_prefixes": ["/", "!"],      # 命令触发前缀
        "group_respond_mode": "at_or_command",  # all / at_or_command / command
        "owner_only_llm": False,          # 仅超级管理员（主人）的对话才调用 LLM（agent）；关闭则不调用，插件命令仍可响应
        # 插件/命令系统使用权限：all=允许所有人 / master=仅主人 / whitelist=主人+白名单。
        # 用于控制谁能通过 QQ 触发插件与命令系统（不影响 agent/自然对话）。
        "plugin_use_permission": "all",
        "max_message_chars": 500,         # 单条消息文本长度上限
        "call_timeout": 30,               # OneBot API 调用超时（秒）
        "min_send_interval": 1.2,         # 发送最小间隔（秒），防快速连发触发风控
        "max_send_per_minute": 40,        # 每分钟最大发送条数（超限自动额外等待）
        "greet_on_connect": False,        # 连接成功后是否给私聊好友发送上线问候
        "greet_text": "芙宁娜已登台，有事唤本神便是。",
        "plugin_reply_delay_enabled": True,  # 插件/命令回复是否也附加随机等待（避免"秒回"）
        "plugin_reply_delay_min": 3.0,       # 插件/命令回复随机等待下限（秒）
        "plugin_reply_delay_max": 10.0,      # 插件/命令回复随机等待上限（秒）
        # 前置插件审核（仅 QQ 平台生效）：拦截可能导致封号的插件/命令，
        # 命中时不对用户执行，而是报错提示「请在 QQ 平台禁用该插件」。
        "plugin_audit": {
            "enabled": True,               # 是否启用 QQ 平台前置插件审核
            "risk_commands": [],           # 额外危险命令关键词（子串匹配，追加到内置列表）
            "risk_plugins": [],            # 额外危险插件名关键词（子串匹配，追加到内置列表）
        },
    },

    # QQ 开放平台机器人（官方 Bot API v2，主要用于出站通知）
    "qq_official": {
        "enabled": False,
        "app_id": "",
        "app_secret": "",              # 保存时自动加密
        "target_type": "group",        # group / user / channel
        "target_id": "",               # 群 openid / 用户 openid / 频道 id
        "api_base": "https://api.sgroup.qq.com",
        "timeout": 20,
    },

    # 喵喵插件(TRSS-Yunzai)运行方式
    #   mode 支持两种运行方式：
    #     - yunzai: 由真实 TRSS-Yunzai 原生处理（推荐，最接近原生）。ZhiYan 不再拦截
    #               # 前缀命令，# 命令完全交由 Yunzai 响应，避免两软件同时回应。
    #     - off:    关闭 miao 相关处理。
    "miao_plugin": {
        "mode": "yunzai",        # yunzai / off（默认 yunzai：不拦截 # 交给 TRSS-Yunzai）
        "auto_start": False,     # 是否自动拉起 Yunzai 进程
        "startup_wait": 10,      # 拉起后等待健康检查的最大秒数
        # TRSS-Yunzai 原生模式（mode=yunzai 时生效）
        "yunzai": {
            "path": "",          # Yunzai-main 目录（绝对路径或相对软件根目录）
            "port": 2536,        # Yunzai HTTP/WS 端口
            "redis_path": "",    # redis-server.exe 绝对路径（留空则自动探测，找不到不预启动）
        },
    },

    # BetterGI 托管（原神自动化：进程监控、命令行任务、通知回调；图像识别为预留）
    "bettergi": {
        "enabled": False,             # 总开关：关闭后不监控、不允许启动脚本
        "auto_monitor": False,        # 后端启动时自动开始监控
        "bgi_path": "",               # BetterGI.exe 绝对路径（留空自动探测 G:\BetterGI 等）
        "bgi_user_dir": "",           # BetterGI 的 User 目录（留空按 exe 同级 User）
        "launch_mode": "onedragon",   # normal / onedragon / groups / task_progress
        "onedragon_config": "",       # 一条龙配置名（留空使用 BetterGI 当前选中配置）
        "group_names": "",            # 配置组名，多个用英文逗号分隔
        "process_name": "BetterGI.exe",
        "check_interval": 30,         # 轮询间隔（秒）
        "auto_launch": True,          # 监控发现进程未启动时自动拉起
        "auto_restart": True,         # 进程异常退出时自动重启
        "max_restarts": 2,            # 连续异常重启上限，超过后进入告警状态
        "restart_reset_after": 3600,  # 稳定运行超过该秒数后清零重启计数
        "notify_qq": False,           # 是否通过 QQ 平台推送事件
        "notify_qq_task_events": True,  # 任务开始/结束/错误也推送到 QQ
        "notify_official": False,     # 同时推送到 QQ 开放平台官方机器人
        "notify_target": "",          # 推送目标：group:群号 或 private:QQ号
        "notify_cooldown": 300,       # 同类告警最小推送间隔（秒）
        "notify_transport": "webhook",  # 事件推送方式：webhook / websocket
        "auto_setup_notify": True,      # 后端启动时自动把通知端点同步到当前端口
        "webhook_events": "dragon.start,dragon.end,group.start,group.end,task.error,task.cancel,daily.reward",
        "auto_shutdown": False,       # 任务成功完成后自动关机（默认关闭）
        "shutdown_delay": 60,         # 关机倒计时（秒），可用 shutdown /a 取消
        "image_fallback": False,      # 卡住时启用图像识别兜底
        "vision_stall_timeout": 600,  # 任务无事件多久后触发图像兜底（秒）
        "vision_max_attempts": 3,     # 单次卡住最多尝试次数
        "vision_ai_enabled": True,    # OCR 规则未命中时交给 AI 决策
        "vision_rules": "",           # 自定义规则 JSON；留空使用内置默认规则
        "overlay_enabled": False,     # 识别后在屏幕上叠加红框（调试用）
        "overlay_duration": 3000,     # 红框显示时长（毫秒）
        "overlay_scale": 0,           # 坐标缩放，0 自动；高 DPI 偏移时手动填（如 1.25）
        "health_check": False,        # 卡死/任务出错时执行健康检查与自愈链（默认关闭）
        "retry_max": 2,               # 任务级重试上限（区别于进程级 max_restarts）
        "retry_interval": 60,         # 两次自愈的最小间隔（秒）
        "network_recover": False,     # 自愈链是否刷新 DNS（默认关闭）
        "notify_screenshot": True,    # 自愈时截图并推送 QQ
        "game_launcher": "",          # 游戏启动器路径（留空跳过「拉起游戏」）
        "auto_relaunch_after_task": False,  # 任务结束后自动续跑（默认关闭）
        "relaunch_modes": "done",     # 触发续跑的结果：done / error / canceled，逗号分隔
        "relaunch_delay": 60,         # 续跑前等待（秒）
        "relaunch_max": 3,            # 连续续跑上限，防止死循环
        "error_guard": False,         # 异常判定与自动处理（默认关闭）
        "monitor_mode": "loose",      # 监测模式：strict 严格 / loose 宽松
        "strict_miss_limit": 1,       # 严格模式连续无响应上限
        "loose_miss_limit": 25,       # 宽松模式连续无响应上限
        "daily_error_limit": 3,       # 同一自然日 BetterGI 报错上限
        "error_action_map": {},       # 异常类型 -> 动作（留空用默认表）
        "active_window_start": "",    # 规定时间段开始 HH:MM
        "active_window_end": "",      # 规定时间段结束 HH:MM
        "game_alive_shutdown_hours": 12,  # 原神进程存活上限（小时），超时关机
        "update_flow_game": "",       # 原神更新步骤定义（JSON，留空用内置）
        "update_flow_bgi": "",        # BetterGI 更新步骤定义（JSON，留空用内置）
        "update_launch_onedragon": True,  # 更新完成后启动预设一条龙
        "boot_task_plan": False,      # 开机后执行更新任务计划（默认关闭）
        "boot_task_delay": 60,        # 开机后延迟检查（秒）
        "boot_plan_targets": "game,bgi",  # 开机计划执行哪些流程
        "schedule_enabled": False,    # 启用定时任务调度
        "schedule_kill_running": False,  # 到点时若 BetterGI 已在运行，是否先结束再启动
        "schedules": [],              # 定时任务列表
        "auto_start_on_boot": False,  # 开机自启（当前用户 Run 键）
    },

    # AI 教辅子系统（课件解析入库 / 题库 / 解题讲解）
    "edu": {
        "enabled": True,                     # 教辅子系统总开关
        "default_subject": "",               # 默认学科（导入时未填写则使用）
        "default_grade": "",                 # 默认年级
        "max_questions_per_import": 30,      # 单次导入最多解析的题目数
        "extract_chunk_chars": 6000,         # 送入 LLM 抽取时的文本分片长度（字符）
        "auto_save_analysis": False,         # 解题后是否自动把讲解写回题目解析字段
        "ocr_enabled": False,                # 图片题目本地 OCR（计划第三期）
        "exam_default_count": 10,            # 组卷默认题量（QQ /组卷 不带参数时）
        "recommend_limit": 5,                # 推题默认题量（QQ /推题 不带参数时）
        "mistake_master_streak": 2,          # 错题连续答对几次判定为已掌握

        # ---- AI 授课子系统配置（幻灯片 / 授课流程 / 点名 / 目录 / OCR / 语音 / 网络试题）----
        "teaching": {
            # 幻灯片创作
            "slides": {
                "default_slide_count": 12,   # 默认生成页数
                "default_subject": "",
                "default_grade": "",
                "theme": "default",           # default / dark / fresh / academic
                "export_pptx": True,          # 是否生成 .pptx
                "export_html": True,          # 是否生成 HTML 幻灯片
                "seewo_enabled": False,       # 希沃原生格式（框架预留，默认关闭）
            },
            # 授课流程总结
            "flow": {
                "auto_answer_missing": True,  # 课件中试题无答案时由 AI 补答案
                "max_questions": 30,          # 单次总结最多处理的题目数
                "save_dir": "",               # 授课流程保存目录（留空 = data/edu/flows）
            },
            # 随机点名
            "rollcall": {
                "no_repeat_window": 3,        # 去重窗口：最近 N 次内不重复抽中同一人
                "default_weight": 1.0,        # 默认权重
                "animation": "spin",          # spin 跳动动画 / instant 立即生成
                "spin_ms": 2600,              # 跳动动画时长（毫秒）
                "algorithm": "timestamp",     # timestamp 时间戳伪随机 / random 普通随机
            },
            # 快捷目录 / 悬浮球
            "catalog": {
                "ocr_backend": "auto",        # auto / local / vision / off
                "floating_ball": True,        # 是否允许悬浮球
                "ball_size": 56,              # 悬浮球直径（像素）
                "ball_opacity": 0.92,         # 悬浮球不透明度
                "match_threshold": 0.55,      # OCR 文本与大纲匹配度阈值
            },
            # OCR（图片/截图题目、课件页识别）
            "ocr": {
                "local_enabled": True,        # 本地 RapidOCR（未安装自动降级）
                "vision_enabled": True,       # 多模态大模型兜底
                "lang": "ch",                 # 语言：ch / en
            },
            # 多模态视觉模型（图片题识别 / OCR 兜底）
            "vision": {
                "provider": "qwen-vl",        # 复用 qwen 的 api_key 走 DashScope 兼容接口
                "model": "qwen-vl-max",
                "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": "",                # 留空则复用顶层 qwen api_key
                "max_tokens": 2000,
            },
            # 实时语音问答
            "voice": {
                "asr_engine": "auto",         # auto / local / cloud / browser
                "asr_language": "zh",         # 识别语言 zh / en
                "local_model": "small",       # faster-whisper 模型规格
                "cloud_model": "paraformer-realtime-v2",  # DashScope ASR 模型
                "tts_enabled": True,          # 回答是否自动语音播报
                "tts_voice": "",              # 留空则用全局 TTS 音色
                "auto_send": True,            # 识别完成后是否自动发送提问
            },
            # 网络试题
            "network": {
                "enabled": True,
                "provider": "auto",           # auto / api / ai_generate / ai_web
                "api_url": "",                # 第三方题库 API（可选）
                "api_key": "",                # 第三方题库 API Key（可选）
                "default_count": 5,
                "timeout": 15,                # 联网超时（秒）
                "save_to_bank": True,         # 获取的题目是否自动入库
            },
        },
    },
}

# 当前配置
_settings: Dict[str, Any] = DEFAULT_SETTINGS.copy()


def load_settings() -> Dict[str, Any]:
    """
    加载配置文件
    
    返回:
        Dict[str, Any]: 配置字典（敏感字段已解密）
    """
    # 主要配置路径：data/settings.json
    settings_file = os.path.join(PROJECT_ROOT, 'data', 'settings.json')
    
    # 检查是否存在旧的根目录 settings.json（向后兼容迁移）
    old_path = os.path.join(PROJECT_ROOT, '..', 'settings.json')
    old_path = os.path.normpath(old_path)
    if os.path.exists(old_path) and not os.path.exists(settings_file):
        try:
            import shutil
            os.makedirs(os.path.dirname(settings_file), exist_ok=True)
            shutil.copy2(old_path, settings_file)
            logger.info(f"已从旧路径迁移配置文件: {old_path} -> {settings_file}")
        except Exception as e:
            logger.warning(f"迁移旧配置文件失败: {e}")
    
    if os.path.exists(settings_file):
        try:
            with open(settings_file, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                _settings.update(loaded)
                logger.info(f"配置加载成功: {settings_file}")
        except json.JSONDecodeError as e:
            logger.error(f"配置文件格式错误 ({settings_file}): {e}，将使用默认配置")
        except Exception as e:
            logger.error(f"加载配置失败 ({settings_file}): {e}")
    else:
        logger.info(f"配置文件不存在，将使用默认配置并保存: {settings_file}")
        save_settings()
    
    # 解密敏感字段
    _decrypt_all_settings(_settings)
    
    # 兼容性处理：bilibili_cookie 不再作为敏感字段加密，
    # 但旧配置文件中可能已存在 ENC: 前缀的加密值，需要解密
    cookie_val = _settings.get("bilibili_cookie", "")
    if cookie_val and cookie_val.startswith("ENC:"):
        decrypted = decrypt_value(cookie_val)
        if decrypted != cookie_val:  # 解密成功
            _settings["bilibili_cookie"] = decrypted
            # 顺便保存一次以清除 ENC: 前缀
            try:
                save_settings()
            except Exception:
                pass
    
    return _settings


def save_settings() -> None:
    """
    保存配置文件（敏感字段自动加密）
    """
    settings_file = os.path.join(PROJECT_ROOT, 'data', 'settings.json')
    try:
        # 创建临时备份
        if os.path.exists(settings_file):
            backup_file = settings_file + ".bak"
            try:
                import shutil
                shutil.copy2(settings_file, backup_file)
            except Exception:
                pass
        
        # 加密敏感字段后再保存（深拷贝，避免原地加密污染内存中的实时配置）
        import copy
        save_data = copy.deepcopy(_settings)
        _encrypt_all_settings(save_data)
        
        # 写入新文件
        with open(settings_file, 'w', encoding='utf-8') as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        logger.info(f"配置保存成功: {settings_file}")
        
        # 保存成功后删除备份
        backup_file = settings_file + ".bak"
        if os.path.exists(backup_file):
            try:
                os.remove(backup_file)
            except Exception:
                pass
                
    except Exception as e:
        logger.error(f"保存配置失败: {e}")
        # 尝试从备份恢复
        backup_file = settings_file + ".bak"
        if os.path.exists(backup_file):
            try:
                import shutil
                shutil.copy2(backup_file, settings_file)
                logger.info(f"已从备份恢复: {backup_file}")
            except Exception:
                pass
        raise


def get_settings() -> Dict[str, Any]:
    """
    获取当前配置（敏感字段已解密）
    
    返回:
        Dict[str, Any]: 当前配置字典
    """
    return _settings.copy()


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并 patch 到 base 的副本（嵌套 dict 逐层合并，避免部分更新丢失其他键）。"""
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def update_settings(new_settings: Dict[str, Any]) -> None:
    """
    更新配置（自动处理敏感字段加密；嵌套 dict 采用深合并，部分更新不丢其他键）

    参数:
        new_settings: 新的配置字典
    """
    _settings.update(_deep_merge(_settings, new_settings))
    save_settings()


def get_setting(key: str, default: Any = None) -> Any:
    """
    获取单个配置项
    
    参数:
        key: 配置项键名
        default: 默认值
    
    返回:
        Any: 配置值
    """
    return _settings.get(key, default)


# 初始化加载配置
load_settings()
