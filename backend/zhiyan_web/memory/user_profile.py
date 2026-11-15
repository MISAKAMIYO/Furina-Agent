"""
用户画像模块
通过持续学习用户对话风格、偏好和需求，构建用户个性化模型

数据存储路径: data/user_profile.json
"""
from __future__ import annotations

import os
import json
import logging
import hashlib
from datetime import datetime, date
from collections import Counter, defaultdict

from zhiyan_web.config.settings import DATA_DIR
from zhiyan_web.memory.conversation_store import load_conversation, list_conversations

logger = logging.getLogger("ZhiYanWeb.Memory")

# ============= 配置 =============

PROFILE_FILE = os.path.join(DATA_DIR, 'user_profile.json')
MAX_PROFILE_HISTORY = 100   # 分析时使用的最大消息数
TOPIC_WINDOW_SIZE = 5       # 话题提取窗口大小


def _get_default_profile() -> dict:
    """获取默认用户画像"""
    return {
        "version": 1,
        "updated_at": datetime.now().isoformat(),
        "total_conversations": 0,
        "total_messages": 0,
        "first_interaction": None,
        "last_interaction": None,
        "preferences": {
            "topics": [],           # 高频话题
            "style": "neutral",     # 对话风格: formal/casual/neutral
            "avg_message_length": 0,  # 平均消息长度
            "emoji_usage": "low",    # 表情使用频率: low/medium/high
        },
        "personality": {
            "curiosity": 0.5,       # 好奇心（问问题频率）
            "expressiveness": 0.5,  # 表达欲（消息长度）
            "emotionality": 0.5,    # 情感倾向
        },
        "recent_topics": [],        # 近期话题列表
        "keywords": [],             # 高频关键词
        "interaction_patterns": {
            "peak_hours": [],       # 活跃时段
            "avg_response_time": 0, # 平均响应时间（秒）
            "preferred_time": "",   # 偏好时段
        },
    }


def _read_profile() -> dict:
    """读取用户画像"""
    try:
        if not os.path.exists(PROFILE_FILE):
            return _get_default_profile()
        with open(PROFILE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _get_default_profile()
        return data
    except Exception as e:
        logger.error(f"读取用户画像失败: {e}")
        return _get_default_profile()


def _write_profile(profile: dict) -> bool:
    """写入用户画像"""
    try:
        profile["updated_at"] = datetime.now().isoformat()
        temp_path = PROFILE_FILE + '.tmp'
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, PROFILE_FILE)
        return True
    except Exception as e:
        logger.error(f"写入用户画像失败: {e}")
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        return False


def _extract_keywords(text: str) -> list:
    """从文本中提取关键词（简单分词）"""
    import re
    # 中文停用词
    stop_words = {"的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都",
                  "一", "一个", "上", "也", "很", "到", "说", "要", "去", "你",
                  "会", "着", "没有", "看", "好", "自己", "这", "他", "她", "它",
                  "们", "那", "什么", "怎么", "为什么", "可以", "吗", "啊", "呢",
                  "吧", "嗯", "哦", "哦", "嗨", "hello", "hi", "hey", "ok"}

    # 提取中文词汇（2-6 字）和英文单词
    words = []
    # 中文
    chinese_chars = re.findall(r'[\u4e00-\u9fff]{2,6}', text)
    words.extend(chinese_chars)
    # 英文
    english_words = re.findall(r'\b[a-zA-Z]{3,}\b', text)
    words.extend([w.lower() for w in english_words])

    return [w for w in words if w.lower() not in stop_words]


def _extract_topics(messages: list) -> list:
    """从消息中提取话题"""
    all_text = " ".join([
        msg.get("content", "")
        for msg in messages
        if msg.get("role") == "user"
    ])

    keywords = _extract_keywords(all_text)
    if not keywords:
        return []

    # 统计词频
    counter = Counter(keywords)
    # 返回前 10 个高频词
    return [{"word": word, "count": count}
            for word, count in counter.most_common(10)]


def _analyze_style(messages: list) -> str:
    """分析对话风格"""
    user_msgs = [m.get("content", "") for m in messages if m.get("role") == "user"]
    if not user_msgs:
        return "neutral"

    formal_indicators = ["您好", "谢谢", "请问", "感谢", "抱歉", "麻烦"]
    casual_indicators = ["嗨", "嘿", "哈", "呢", "呗", "啦", "哦", "嗯"]

    formal_count = sum(1 for msg in user_msgs if any(i in msg for i in formal_indicators))
    casual_count = sum(1 for msg in user_msgs if any(i in msg for i in casual_indicators))

    total = len(user_msgs)
    if total == 0:
        return "neutral"

    formal_ratio = formal_count / total
    casual_ratio = casual_count / total

    if formal_ratio > 0.3:
        return "formal"
    elif casual_ratio > 0.3:
        return "casual"
    return "neutral"


def _analyze_emoji_usage(messages: list) -> str:
    """分析表情使用频率"""
    import re
    user_msgs = [m.get("content", "") for m in messages if m.get("role") == "user"]
    if not user_msgs:
        return "low"

    # 匹配 emoji
    emoji_pattern = re.compile(
        "[\U0001F600-\U0001F64F"  # 表情符号
        "\U0001F300-\U0001F5FF"  # 符号和象形文字
        "\U0001F680-\U0001F6FF"  # 交通和地图符号
        "\U0001F1E0-\U0001F1FF"  # 国旗
        "\U00002702-\U000027B0"  # 其他符号
        "\U000024C2-\U0001F251"  # 补充符号
        "]+"
    )

    total = len(user_msgs)
    emoji_count = sum(1 for msg in user_msgs if emoji_pattern.search(msg))

    ratio = emoji_count / total if total > 0 else 0
    if ratio > 0.5:
        return "high"
    elif ratio > 0.2:
        return "medium"
    return "low"


def update_profile_from_messages(messages: list):
    """
    从消息列表更新用户画像

    Args:
        messages: 消息列表 [{"role": str, "content": str, "time": str}, ...]
    """
    if not messages:
        return

    profile = _read_profile()
    user_msgs = [m for m in messages if m.get("role") == "user"]

    if not user_msgs:
        return

    # 更新基本信息
    profile["total_messages"] += len(messages)
    profile["last_interaction"] = datetime.now().isoformat()
    if not profile["first_interaction"]:
        profile["first_interaction"] = messages[0].get("time", datetime.now().isoformat())

    # 分析偏好
    profile["preferences"]["topics"] = _extract_topics(messages)
    profile["preferences"]["style"] = _analyze_style(messages)
    profile["preferences"]["emoji_usage"] = _analyze_emoji_usage(messages)

    # 平均消息长度
    avg_len = sum(len(m.get("content", "")) for m in user_msgs) / len(user_msgs)
    profile["preferences"]["avg_message_length"] = round(avg_len, 1)

    # 分析个性特征
    # 好奇心 - 基于问句比例
    question_count = sum(1 for m in user_msgs if "?" in m.get("content", "") or "？" in m.get("content", ""))
    profile["personality"]["curiosity"] = round(min(1.0, question_count / len(user_msgs) * 2), 2)

    # 表达欲 - 基于消息平均长度
    expressiveness = min(1.0, avg_len / 200)
    profile["personality"]["expressiveness"] = round(expressiveness, 2)

    # 情感倾向 - 基于情感词比例
    positive_words = ["喜欢", "开心", "好", "棒", "漂亮", "厉害", "感谢", "谢谢", "爱", "美"]
    negative_words = ["讨厌", "不开心", "烦", "差", "糟糕", "生气", "伤心", "难过"]
    pos_count = sum(1 for m in user_msgs if any(w in m.get("content", "") for w in positive_words))
    neg_count = sum(1 for m in user_msgs if any(w in m.get("content", "") for w in negative_words))
    total_emotional = pos_count + neg_count
    if total_emotional > 0:
        profile["personality"]["emotionality"] = round(pos_count / total_emotional, 2)
    else:
        profile["personality"]["emotionality"] = 0.5

    # 提取关键词
    all_user_text = " ".join(m.get("content", "") for m in user_msgs)
    keywords = _extract_keywords(all_user_text)
    keyword_counts = Counter(keywords)
    profile["keywords"] = [{"word": w, "count": c}
                           for w, c in keyword_counts.most_common(20)]

    # 更新对话次数
    profile["total_conversations"] = len(list_conversations())

    _write_profile(profile)
    logger.debug(f"用户画像已更新 (共 {profile['total_messages']} 条消息)")


def get_profile_context() -> str:
    """
    获取用户画像的格式化字符串（用于 LLM 系统提示词）

    Returns:
        str: 格式化后的用户画像文本
    """
    profile = _read_profile()
    prefs = profile.get("preferences", {})
    personality = profile.get("personality", {})
    keywords = profile.get("keywords", [])[:5]

    parts = []

    # 对话风格
    style_map = {"formal": "正式", "casual": "随意", "neutral": "中性"}
    parts.append(f"【用户对话风格】{style_map.get(prefs.get('style', 'neutral'), '中性')}")

    # 个性特征
    curiosity_desc = "高" if personality.get("curiosity", 0.5) > 0.6 else "中" if personality.get("curiosity", 0.5) > 0.3 else "低"
    expressiveness_desc = "高" if personality.get("expressiveness", 0.5) > 0.6 else "中" if personality.get("expressiveness", 0.5) > 0.3 else "低"
    parts.append(f"【用户个性】好奇心({curiosity_desc})、表达欲({expressiveness_desc})")

    # 高频关键词
    if keywords:
        kw_str = "、".join(k.get("word", "") for k in keywords)
        parts.append(f"【用户关注话题】{kw_str}")

    # 互动次数
    total_msgs = profile.get("total_messages", 0)
    if total_msgs > 0:
        parts.append(f"【互动历史】已进行 {total_msgs} 次对话交流")

    return "\n".join(parts)


def get_keywords() -> list:
    """获取用户高频关键词"""
    profile = _read_profile()
    return profile.get("keywords", [])


def get_profile() -> dict:
    """获取完整用户画像"""
    return _read_profile()


def get_relationship_context() -> str:
    """
    获取用户关系的格式化上下文（用于 LLM 系统提示词）
    包含上次互动时间、总对话次数、情感倾向等
    """
    profile = _read_profile()
    parts = []

    total_msgs = profile.get("total_messages", 0)
    if total_msgs > 0:
        parts.append(f"已和观众进行了 {total_msgs} 次对话交流")

    last_interaction = profile.get("last_interaction", "")
    if last_interaction:
        try:
            from datetime import datetime
            last_time = datetime.fromisoformat(last_interaction)
            hours_ago = (datetime.now() - last_time).total_seconds() / 3600
            if hours_ago < 1:
                parts.append("观众刚才还在和我说话")
            elif hours_ago < 24:
                parts.append(f"观众大约 {int(hours_ago)} 小时前和我聊过天")
            else:
                days_ago = int(hours_ago / 24)
                parts.append(f"观众 {days_ago} 天前和我聊过天")
        except:
            pass

    # 情感倾向
    personality = profile.get("personality", {})
    emotionality = personality.get("emotionality", 0.5)
    if emotionality > 0.7:
        parts.append("观众今天心情似乎很好")
    elif emotionality < 0.3:
        parts.append("观众今天心情不太好，要多关心一下")

    return "\n".join(parts)


def reset_profile():
    """重置用户画像"""
    _write_profile(_get_default_profile())
    logger.info("用户画像已重置")


# 模块加载时自动初始化
if not os.path.exists(PROFILE_FILE):
    _write_profile(_get_default_profile())
    logger.info("用户画像文件已初始化")