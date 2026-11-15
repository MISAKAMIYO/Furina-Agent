"""
自动记忆写入模块 - AI 主动将对话中的重要信息写入记忆系统

功能:
- 提供 write_memory 工具供 AI 调用
- 频率限制（避免频繁写入）
- 关键词捕捉
- 自动重要性评分
- 用户画像同步更新
- LLM 定期总结记忆（自动摘要与归类）
- 手动触发记忆回顾与总结
"""
from __future__ import annotations

import time
import json
import logging
import re
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

from zhiyan_web.memory.vector_memory import get_vector_memory
from zhiyan_web.memory.user_profile import update_profile_from_messages, get_profile
from zhiyan_web.config.settings import DATA_DIR

logger = logging.getLogger("ZhiYanWeb.Memory.AutoWriter")

# 频率限制配置（默认值，可通过 set_config 动态调整）
MIN_WRITE_INTERVAL_SECONDS = 300  # 5 分钟
MAX_DAILY_WRITES = 50

# LLM 总结频率（每 N 条新记忆触发一次自动总结）
SUMMARY_TRIGGER_COUNT = 20
# 总结间隔最小时间（小时）
SUMMARY_MIN_INTERVAL_HOURS = 6

# 重要关键词模式
IMPORTANCE_KEYWORDS = {
    "high": [
        r"(喜欢|爱|最爱|超爱|讨厌|恨|害怕|恐惧)",
        r"(名字|叫|称呼|我是|我叫)",
        r"(生日|年龄|出生|岁)",
        r"(工作|职业|公司|学校|专业)",
        r"(家人|父母|爸爸|妈妈|兄弟|姐妹|妹妹|姐姐|哥哥)",
        r"(重要|关键|必须|一定|绝对)",
        r"(梦想|理想|目标|愿望|希望)",
        r"(芙宁娜|furina|水神|枫丹|本神|观众)",
    ],
    "medium": [
        r"(爱好|兴趣|喜欢.*[做玩看听吃])",
        r"(经常|每天|总是|习惯)",
        r"(去过|去过|住在|来自)",
        r"(觉得|认为|感觉|认为)",
        r"(想|想要|希望|打算|计划)",
        r"(游戏|动漫|番剧|电影|音乐|书)",
        r"(好吃|美味|好喝|美食)",
    ],
}


class AutoMemoryWriter:
    """自动记忆写入器"""

    def __init__(self):
        self._last_write_time: float = 0
        self._daily_write_count: int = 0
        self._last_reset_date: str = ""
        self._write_history: List[Dict] = []
        self._total_writes_since_summary: int = 0
        self._last_summary_time: float = 0
        self._summaries: List[Dict] = []
        # 可配置项（默认值，可用 set_config 覆盖）
        self._enabled: bool = True
        self._min_interval: int = MIN_WRITE_INTERVAL_SECONDS
        self._max_daily: int = MAX_DAILY_WRITES
        self._summary_trigger: int = SUMMARY_TRIGGER_COUNT
        self._summary_min_interval_hours: float = SUMMARY_MIN_INTERVAL_HOURS
        self._load_state()

    def _load_state(self):
        """加载持久化状态"""
        state_file = os.path.join(DATA_DIR, "auto_memory_state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._last_write_time = data.get("last_write_time", 0)
                self._daily_write_count = data.get("daily_write_count", 0)
                self._last_reset_date = data.get("last_reset_date", "")
                self._write_history = data.get("write_history", [])
                self._total_writes_since_summary = data.get("total_writes_since_summary", 0)
                self._last_summary_time = data.get("last_summary_time", 0)
                self._summaries = data.get("summaries", [])
                # 持久化配置
                self._enabled = data.get("enabled", True)
                self._min_interval = data.get("min_interval", MIN_WRITE_INTERVAL_SECONDS)
                self._max_daily = data.get("max_daily", MAX_DAILY_WRITES)
                self._summary_trigger = data.get("summary_trigger", SUMMARY_TRIGGER_COUNT)
                self._summary_min_interval_hours = data.get("summary_min_interval_hours", SUMMARY_MIN_INTERVAL_HOURS)
            except Exception as e:
                logger.error(f"加载自动记忆状态失败: {e}")

    def _save_state(self):
        """保存持久化状态"""
        state_file = os.path.join(DATA_DIR, "auto_memory_state.json")
        try:
            os.makedirs(os.path.dirname(state_file), exist_ok=True)
            data = {
                "last_write_time": self._last_write_time,
                "daily_write_count": self._daily_write_count,
                "last_reset_date": self._last_reset_date,
                "write_history": self._write_history[-200:],
                "total_writes_since_summary": self._total_writes_since_summary,
                "last_summary_time": self._last_summary_time,
                "summaries": self._summaries[-50:],
                # 配置持久化
                "enabled": self._enabled,
                "min_interval": self._min_interval,
                "max_daily": self._max_daily,
                "summary_trigger": self._summary_trigger,
                "summary_min_interval_hours": self._summary_min_interval_hours,
            }
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存自动记忆状态失败: {e}")

    def set_config(self, enabled=None, min_interval_seconds=None, max_daily_writes=None,
                   summary_trigger_count=None, summary_min_interval_hours=None) -> Dict:
        """动态更新自动记忆配置"""
        if enabled is not None:
            self._enabled = bool(enabled)
        if min_interval_seconds is not None and int(min_interval_seconds) >= 10:
            self._min_interval = int(min_interval_seconds)
        if max_daily_writes is not None and int(max_daily_writes) >= 1:
            self._max_daily = int(max_daily_writes)
        if summary_trigger_count is not None and int(summary_trigger_count) >= 1:
            self._summary_trigger = int(summary_trigger_count)
        if summary_min_interval_hours is not None and float(summary_min_interval_hours) >= 0:
            self._summary_min_interval_hours = float(summary_min_interval_hours)
        self._save_state()
        return {"success": True, "config": self.get_config()}

    def get_config(self) -> Dict:
        """获取当前配置"""
        return {
            "enabled": self._enabled,
            "min_interval_seconds": self._min_interval,
            "max_daily_writes": self._max_daily,
            "summary_trigger_count": self._summary_trigger,
            "summary_min_interval_hours": self._summary_min_interval_hours,
        }

    def _check_rate_limit(self) -> Dict:
        """检查频率限制"""
        # 自动记忆已关闭
        if not self._enabled:
            return {"allowed": False, "reason": "自动记忆功能已关闭", "remaining_seconds": 0}

        now = time.time()
        today = datetime.now().strftime("%Y-%m-%d")

        # 日期切换时重置
        if self._last_reset_date != today:
            self._daily_write_count = 0
            self._last_reset_date = today

        # 检查间隔
        if now - self._last_write_time < self._min_interval:
            remaining = int(self._min_interval - (now - self._last_write_time))
            return {"allowed": False, "reason": f"频率限制，请 {remaining} 秒后再次尝试", "remaining_seconds": remaining}

        # 检查日限额
        if self._daily_write_count >= self._max_daily:
            return {"allowed": False, "reason": "今日记忆写入次数已达上限", "remaining_seconds": 0}

        return {"allowed": True, "reason": ""}

    def _analyze_importance(self, content: str) -> float:
        """分析内容重要性 (0.0 - 1.0)"""
        # 默认中等重要性
        importance = 0.5

        for pattern in IMPORTANCE_KEYWORDS["high"]:
            if re.search(pattern, content):
                importance = max(importance, 0.85)

        for pattern in IMPORTANCE_KEYWORDS["medium"]:
            if re.search(pattern, content):
                importance = max(importance, 0.65)

        # 长内容提升重要性
        if len(content) > 50:
            importance = min(1.0, importance + 0.1)
        if len(content) > 100:
            importance = min(1.0, importance + 0.05)

        # 包含数字和具体信息提升重要性
        if re.search(r'\d+', content):
            importance = min(1.0, importance + 0.05)

        return round(importance, 2)

    def _detect_memory_type(self, content: str) -> str:
        """检测记忆类型"""
        preference_patterns = [
            r"(喜欢|爱|讨厌|爱好|兴趣|偏好|想|想要)",
            r"(不爱|不喜欢|讨厌|反感)",
            r"(最爱|超爱|最喜欢)",
        ]
        event_patterns = [
            r"(今天|昨天|明天|上周|下周|刚才|刚刚|之前|之后)",
            r"(发生|遇到|看到|听说|发现)",
            r"(去了|来过|到过|参加)",
        ]
        concept_patterns = [
            r"(知道|了解|明白|理解|认为|觉得)",
            r"(是什么|什么是|怎样|如何)",
            r"(意思是|指的是|表示)",
        ]

        for pattern in preference_patterns:
            if re.search(pattern, content):
                return "preference"
        for pattern in event_patterns:
            if re.search(pattern, content):
                return "event"
        for pattern in concept_patterns:
            if re.search(pattern, content):
                return "concept"
        return "fact"

    def write_memory(self, content: str, memory_type: str = None, importance: float = None, tags: List[str] = None) -> Dict:
        """
        写入记忆（供 AI 工具调用）

        Args:
            content: 记忆内容
            memory_type: 记忆类型 (auto-detect if None)
            importance: 重要性 (auto-calculate if None)
            tags: 标签

        Returns:
            Dict: 写入结果
        """
        # 检查频率限制
        rate_check = self._check_rate_limit()
        if not rate_check["allowed"]:
            return {"success": False, "error": rate_check["reason"]}

        # 自动检测类型和重要性
        if not memory_type:
            memory_type = self._detect_memory_type(content)
        if importance is None:
            importance = self._analyze_importance(content)

        # 写入向量记忆
        vm = get_vector_memory()
        result = vm.add(
            content=content,
            memory_type=memory_type,
            importance=importance,
            source="ai_auto",
            tags=tags or [],
        )

        # 更新状态
        self._last_write_time = time.time()
        self._daily_write_count += 1
        self._total_writes_since_summary += 1
        self._write_history.append({
            "time": datetime.now().isoformat(),
            "content_preview": content[:100],
            "memory_type": memory_type,
            "importance": importance,
        })

        # 限制历史记录
        if len(self._write_history) > 200:
            self._write_history = self._write_history[-200:]

        self._save_state()

        # 检查是否需要自动总结
        self._check_auto_summary_needed()

        return {
            "success": True,
            "data": {
                "memory_id": result.get("id", ""),
                "content": content,
                "memory_type": memory_type,
                "importance": importance,
                "message": f"已记录: {content[:80]}...",
            }
        }

    def _check_auto_summary_needed(self) -> bool:
        """检查是否需要触发自动总结"""
        if self._total_writes_since_summary >= self._summary_trigger:
            hours_since = (time.time() - self._last_summary_time) / 3600
            if hours_since >= self._summary_min_interval_hours:
                return True
        return False

    def summarize_memories(self) -> Dict:
        """
        使用 LLM 总结近期记忆，并写入汇总记忆

        这个方法会:
        1. 获取最近写入的记忆
        2. 调用 LLM 进行总结（如果可用）
        3. 将总结写入向量记忆
        4. 重置计数器
        """
        if not self._write_history:
            return {"success": False, "message": "没有可总结的记忆"}

        # 获取最近写入的记忆内容
        recent_entries = self._write_history[-SUMMARY_TRIGGER_COUNT:]

        # 尝试用 LLM 进行智能总结
        summary_content = self._generate_summary_with_llm(recent_entries)

        # 写入总结记忆
        vm = get_vector_memory()
        vm.add(
            content=summary_content,
            memory_type="concept",
            importance=0.9,
            source="ai_summary",
            tags=["summary", "auto_summary"],
        )

        # 记录总结
        self._summaries.append({
            "time": datetime.now().isoformat(),
            "entry_count": len(recent_entries),
            "summary": summary_content[:200],
        })
        self._total_writes_since_summary = 0
        self._last_summary_time = time.time()
        self._save_state()

        logger.info(f"记忆自动总结完成，总结了 {len(recent_entries)} 条记忆")
        return {
            "success": True,
            "message": f"记忆总结完成，涵盖了 {len(recent_entries)} 条记忆",
            "summary": summary_content,
        }

    def _generate_summary_with_llm(self, entries: List[Dict]) -> str:
        """使用 LLM 生成记忆总结（降级方案：基于规则的总结）"""
        # 按类型分组
        by_type: Dict[str, List[str]] = {}
        for entry in entries:
            mt = entry.get("memory_type", "fact")
            content = entry.get("content_preview", "")
            if mt not in by_type:
                by_type[mt] = []
            by_type[mt].append(content)

        # 生成总结文本
        parts = [f"========== 记忆总结（{datetime.now().strftime('%Y-%m-%d %H:%M')}） =========="]

        type_labels = {
            "preference": "偏好与喜好",
            "event": "事件记录",
            "fact": "事实信息",
            "concept": "概念认知",
        }

        for mt, label in type_labels.items():
            items = by_type.get(mt, [])
            if items:
                parts.append(f"\n【{label}】（共 {len(items)} 条）:")
                # 去重后列出
                unique = list(dict.fromkeys(items))
                for item in unique[:5]:  # 每种类型最多 5 条
                    parts.append(f"  - {item}")

        # 如果有偏好，尝试提取关键偏好
        preferences = by_type.get("preference", [])
        if preferences:
            parts.append(f"\n【关键偏好提炼】")
            # 统计关键词
            keywords = []
            for p in preferences:
                for pattern in IMPORTANCE_KEYWORDS["high"]:
                    match = re.search(pattern, p)
                    if match:
                        keywords.append(match.group(0))
            # 提取点赞/不喜欢
            likes = [p for p in preferences if re.search(r"(喜欢|爱|最爱|超爱)", p)]
            dislikes = [p for p in preferences if re.search(r"(讨厌|不喜欢|不爱|反感)", p)]
            if likes:
                parts.append(f"  - 用户喜欢: {'; '.join(likes[:3])}")
            if dislikes:
                parts.append(f"  - 用户不喜欢: {'; '.join(dislikes[:3])}")

        return "\n".join(parts)

    def review_unsummarized(self) -> Dict:
        """
        查看待总结的记忆概况

        Returns:
            Dict: 待总结记忆的统计和内容预览
        """
        recent = self._write_history[-max(1, self._total_writes_since_summary):]
        return {
            "success": True,
            "pending_count": self._total_writes_since_summary,
            "last_summary_time": datetime.fromtimestamp(self._last_summary_time).isoformat() if self._last_summary_time > 0 else None,
            "trigger_count": SUMMARY_TRIGGER_COUNT,
            "recent_entries": [
                {
                    "time": e.get("time", ""),
                    "content": e.get("content_preview", ""),
                    "type": e.get("memory_type", ""),
                    "importance": e.get("importance", 0),
                }
                for e in recent[-10:]
            ],
        }

    def get_summaries(self) -> List[Dict]:
        """获取历史总结列表"""
        return self._summaries[-20:]

    def get_stats(self) -> Dict:
        """获取写入统计"""
        stats = self.get_config()
        stats.update({
            "last_write_time": datetime.fromtimestamp(self._last_write_time).isoformat() if self._last_write_time > 0 else None,
            "daily_write_count": self._daily_write_count,
            "total_writes": len(self._write_history),
            "pending_summary_count": self._total_writes_since_summary,
            "last_summary_time": datetime.fromtimestamp(self._last_summary_time).isoformat() if self._last_summary_time > 0 else None,
            "total_summaries": len(self._summaries),
        })
        return stats


# 全局单例
_auto_writer: Optional[AutoMemoryWriter] = None


def get_auto_memory_writer() -> AutoMemoryWriter:
    global _auto_writer
    if _auto_writer is None:
        _auto_writer = AutoMemoryWriter()
    return _auto_writer