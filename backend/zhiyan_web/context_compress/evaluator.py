"""
压缩效果评估机制模块

对「原始对话上下文」与「压缩后摘要」进行量化对比评估，确保压缩后
仍然保留关键信息、对话主题与上下文理解，支持 AI 后续的意图理解与响应生成。

评估维度：
- 压缩比（信息密度提升程度）
- 关键事实保留率（用户偏好、个人信息、重要事件）
- 对话主题保留率
- 用户提问保留率
- 重要实体保留率
- 综合评分（0-100）与等级评定

评估结果可用于：
- 压缩前质量把关（分数过低则降级/重试）
- 压缩后监控（历史记录中留存评估报告）
- 策略调优（对比不同压缩策略的效果）
"""
from __future__ import annotations

import re
import logging
from typing import Dict, List, Optional

from zhiyan_web.context_compress.key_info import KeyInfoExtractor

logger = logging.getLogger("ZhiYanWeb.ContextCompress")

# 各维度权重（总和为 1.0）
WEIGHTS = {
    "fact_retention": 0.40,      # 关键事实保留最重要
    "topic_retention": 0.20,     # 主题连续性
    "question_retention": 0.15,  # 待跟踪提问
    "entity_retention": 0.15,    # 数字/URL/路径等硬信息
    "compression_ratio_score": 0.10,  # 压缩幅度
}

# 等级阈值
GRADES = [
    (90, "优秀"),
    (75, "良好"),
    (60, "合格"),
    (0, "较差"),
]


class CompressionEvaluator:
    """压缩效果评估器

    使用关键信息识别算法分别抽取原始与压缩文本的关键信息，
    通过词元重叠度计算各维度保留率，输出结构化评估报告。
    """

    def __init__(self, min_acceptable_score: int = 60):
        self.extractor = KeyInfoExtractor()
        self.min_acceptable_score = min_acceptable_score

    # ---------------- 主入口 ----------------

    def evaluate(
        self,
        messages: List[dict],
        compressed_text: str,
        original_text: Optional[str] = None,
    ) -> Dict:
        """评估压缩效果

        Args:
            messages: 原始消息列表（[{"role": ..., "content": ...}, ...]）
            compressed_text: 压缩后的摘要文本
            original_text: 原始全部文本（可选，默认由 messages 拼接）

        Returns:
            dict: {
                "score": float,            # 综合评分 0-100
                "grade": str,              # 等级（优秀/良好/合格/较差）
                "acceptable": bool,        # 是否达到可接受阈值
                "compression_ratio": float,  # 压缩比（原字符数/压缩后字符数）
                "char_before": int,        # 压缩前字符数
                "char_after": int,         # 压缩后字符数
                "fact_retention": float,   # 关键事实保留率 0-1
                "topic_retention": float,  # 主题保留率 0-1
                "question_retention": float,  # 提问保留率 0-1
                "entity_retention": float,    # 实体保留率 0-1
                "facts_kept": [str],       # 保留的关键事实
                "facts_lost": [str],       # 丢失的关键事实
                "topics_kept": [str],      # 保留的主题
                "topics_lost": [str],      # 丢失的主题
                "questions_kept": [str],   # 保留的提问
                "questions_lost": [str],   # 丢失的提问
                "key_facts_count": int,    # 原始关键事实总数
            }
        """
        compressed_text = (compressed_text or "").strip()
        if not compressed_text:
            return self._empty_result(0.0, reason="空摘要")

        original_text = original_text or self._join_messages(messages)

        # 抽取两侧关键信息
        orig_info = self.extractor.extract(messages)
        comp_info = self.extractor.extract(
            [{"role": "assistant", "content": compressed_text}]
        )

        # 逐维度计算保留率
        facts_kept, facts_lost = self._match_items(
            orig_info.get("key_facts", []), compressed_text
        )
        topics_kept, topics_lost = self._match_items(
            orig_info.get("topics", []), compressed_text
        )
        questions_kept, questions_lost = self._match_items(
            orig_info.get("questions", []), compressed_text
        )
        entities_kept, entities_lost = self._match_items(
            orig_info.get("entities", []), compressed_text, min_overlap=0.9
        )

        fact_retention = self._ratio(len(orig_info.get("key_facts", [])), len(facts_kept))
        topic_retention = self._ratio(len(orig_info.get("topics", [])), len(topics_kept))
        question_retention = self._ratio(len(orig_info.get("questions", [])), len(questions_kept))
        entity_retention = self._ratio(len(orig_info.get("entities", [])), len(entities_kept))

        char_before = max(1, len(original_text or ""))
        char_after = len(compressed_text)
        compression_ratio = round(char_before / max(1, char_after), 2)
        # 压缩幅度分：压缩越多得分越高，但过短（<20字）不奖励
        ratio_score = min(1.0, compression_ratio / 10.0)
        if char_after < 20:
            ratio_score = 0.0

        score = self._weighted_score(
            fact_retention, topic_retention, question_retention,
            entity_retention, ratio_score,
        )
        grade = self._grade(score)

        return {
            "score": round(score, 1),
            "grade": grade,
            "acceptable": score >= self.min_acceptable_score,
            "compression_ratio": compression_ratio,
            "char_before": char_before,
            "char_after": char_after,
            "fact_retention": round(fact_retention, 3),
            "topic_retention": round(topic_retention, 3),
            "question_retention": round(question_retention, 3),
            "entity_retention": round(entity_retention, 3),
            "facts_kept": facts_kept,
            "facts_lost": facts_lost,
            "topics_kept": topics_kept,
            "topics_lost": topics_lost,
            "questions_kept": questions_kept,
            "questions_lost": questions_lost,
            "key_facts_count": len(orig_info.get("key_facts", [])),
        }

    # ---------------- 匹配与计算 ----------------

    def _match_items(
        self,
        items: List[str],
        compressed_text: str,
        min_overlap: float = 0.35,
    ) -> tuple:
        """将原始关键信息项与压缩文本进行模糊匹配

        基于去停用词后的词元集合重叠度判断该项是否被保留。
        短项（<=4 字）使用子串包含判断，避免误判。

        Returns:
            (kept, lost) 两个列表
        """
        kept, lost = [], []
        comp_tokens = self._significant_tokens(compressed_text)
        for item in items:
            item = (item or "").strip()
            if not item:
                continue
            if self._is_preserved(item, compressed_text, comp_tokens, min_overlap):
                kept.append(item)
            else:
                lost.append(item)
        return kept, lost

    def _is_preserved(
        self, item: str, compressed_text: str, comp_tokens: set, min_overlap: float
    ) -> bool:
        """判断单个信息项是否在压缩文本中被保留"""
        # 短项：直接子串匹配
        if len(item) <= 4:
            return item in compressed_text

        item_tokens = self._significant_tokens(item)
        if not item_tokens:
            return item[:8] in compressed_text

        hit = len(item_tokens & comp_tokens)
        overlap = hit / len(item_tokens)
        return overlap >= min_overlap

    def _significant_tokens(self, text: str) -> set:
        """提取文本的显著性词元（去停用词后的中文二元组 + 英文单词 + 数字/URL）"""
        tokens = set()
        text_lower = text.lower()
        for m in re.findall(r"\b[a-z]{2,}\b", text_lower):
            tokens.add(m)
        for m in re.findall(r"\d+", text):
            tokens.add(m)
        for m in re.findall(r"https?://\S+", text):
            tokens.add("url:" + m[:60])
        cn_chars = re.findall(r"[\u4e00-\u9fff]", text)
        for i in range(len(cn_chars) - 1):
            bigram = cn_chars[i] + cn_chars[i + 1]
            # 简单过滤：跳过常见虚词二元组（数量有限，用常见字排除）
            if bigram[0] in "的了是在我有和就不人都一上也很到说要你去会着没有看好自己这他她它们那什么怎么可以我们你们他们还是但是因为所以然后现在已经知道觉得认为真的特别非常有点一些如果":
                continue
            tokens.add(bigram)
        return tokens

    def _ratio(self, total: int, kept: int) -> float:
        """保留率计算（total=0 时视为 1.0，无关键信息则该项不扣分）"""
        if total <= 0:
            return 1.0
        return kept / total

    def _weighted_score(
        self,
        fact_retention: float,
        topic_retention: float,
        question_retention: float,
        entity_retention: float,
        ratio_score: float,
    ) -> float:
        """加权综合评分（0-100）"""
        score = (
            fact_retention * WEIGHTS["fact_retention"]
            + topic_retention * WEIGHTS["topic_retention"]
            + question_retention * WEIGHTS["question_retention"]
            + entity_retention * WEIGHTS["entity_retention"]
            + ratio_score * WEIGHTS["compression_ratio_score"]
        )
        return round(score * 100, 1)

    def _grade(self, score: float) -> str:
        """等级评定"""
        for threshold, name in GRADES:
            if score >= threshold:
                return name
        return "较差"

    # ---------------- 工具方法 ----------------

    def _join_messages(self, messages: List[dict]) -> str:
        """拼接消息为原始文本"""
        parts = []
        for m in messages:
            content = (m.get("content", "") or "").strip()
            if content:
                parts.append(content)
        return "\n".join(parts)

    def _empty_result(self, score: float, reason: str = "") -> Dict:
        """空结果"""
        return {
            "score": round(score, 1),
            "grade": "较差",
            "acceptable": False,
            "compression_ratio": 1.0,
            "char_before": 0,
            "char_after": 0,
            "fact_retention": 0.0,
            "topic_retention": 0.0,
            "question_retention": 0.0,
            "entity_retention": 0.0,
            "facts_kept": [],
            "facts_lost": [],
            "topics_kept": [],
            "topics_lost": [],
            "questions_kept": [],
            "questions_lost": [],
            "key_facts_count": 0,
            "reason": reason,
        }
