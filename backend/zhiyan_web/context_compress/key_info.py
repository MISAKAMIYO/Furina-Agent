"""
关键信息识别算法模块

从对话历史中自动提取「对后续对话连续性至关重要的信息」，包括：
- 关键事实（用户偏好、个人信息、重要事件）
- 对话主题（高频/核心话题）
- 用户请求与待办事项（未完成的诉求）
- 用户提问（需要持续跟踪的疑问）
- 重要实体（数字、URL、文件路径、邮箱、人名等）
- 代码与文件引用（Agent 任务相关）
- 抽取式摘要（LLM 不可用时的降级摘要）

该模块与上下文语义压缩、压缩效果评估配合使用，保证压缩后不丢失关键信息与对话主题。
"""
from __future__ import annotations

import re
import math
from collections import Counter
from typing import Dict, List, Optional

# 重要信息关键词模式（与自动记忆系统保持一致，保证识别口径统一）
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
        r"(去过|住在|来自)",
        r"(觉得|认为|感觉)",
        r"(想|想要|希望|打算|计划)",
        r"(游戏|动漫|番剧|电影|音乐|书)",
        r"(好吃|美味|好喝|美食)",
    ],
}

# 用户主动请求的动词开头
REQUEST_PATTERNS = [
    r"^(请|帮我|麻烦你|能不能|可以.*吗|帮我写|帮我找|帮我查|帮我做|帮我整理|帮我压缩|帮我翻译)",
    r"^(写|创建|生成|新建|保存|修改|编辑|删除|整理|总结|翻译|搜索|查找|查询|下载|安装|启动|停止)",
    r"^(列出|展示|告诉我|讲一下|介绍一下|解释一下|说明一下)",
]

# 疑问句（用户提问）
QUESTION_PATTERNS = [
    r"[?？]$",
    r"^(如何|怎么|怎样|为什么|是什么|什么是|哪里|多少|能否|能不能|可不可以|是不是|有没有|会.*吗|为什么.*[?？]?$)",
]

# 待办/承诺关键词（出现即可能是未完成事项）
TODO_KEYWORDS = [
    "待会", "稍后", "之后", "一会儿", "下次", "明天", "明天再说",
    "还没", "尚未", "未完成", "以后", "回头", "晚点", "记得", "别忘了",
]

# 实体识别正则
ENTITY_PATTERNS = {
    "url": r"https?://[^\s，。；！？、\"']+",
    "email": r"[\w.+-]+@[\w-]+\.[\w.-]+",
    "path": r"(?:[A-Za-z]:\\[^\s，。；！？、\"']*|\/[^\s，。；！？、\"']*\.[a-zA-Z0-9]{1,6})",
    "number": r"\d+(?:\.\d+)?(?:%|％|元|块|万|亿|个|天|小时|分钟|岁|级|帧|MB|GB|KB)?",
    "time": r"\d{1,2}[:：]\d{2}(?:\s*(?:AM|PM|am|pm))?|(?:上午|下午|晚上|中午|凌晨)\s*\d{1,2}点",
}

# 中文停用词（用于主题提取）
STOP_WORDS = set(
    "的 了 在 是 我 有 和 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 没有 看 好 "
    "自己 这 他 她 它 们 那 这个 那个 什么 怎么 为什么 可以 我们 你们 他们 还是 但是 因为 "
    "所以 然后 现在 已经 知道 觉得 认为 真的 特别 非常 有点 一些 什么 请问 那个 一下 如果 "
    "the a an is are was were be been being have has had do does did will would could should "
    "may might shall can to of in for on with at by from as into through during before after "
    "and or but not so if because than i you he she it we they me him her us them my your his "
    "its our their this that these those".split()
)


def split_sentences(text: str) -> List[str]:
    """按中文/英文标点切分句子"""
    parts = re.split(r"(?<=[。！？；!?;])|\n+", text)
    sentences = [p.strip() for p in parts if p and p.strip()]
    return sentences


class KeyInfoExtractor:
    """关键信息识别器

    从消息列表（[{"role": ..., "content": ...}, ...]）中识别并抽取
    压缩时必须保留的关键信息，输出结构化结果。
    """

    def __init__(self, max_key_facts: int = 20, max_topics: int = 8):
        self.max_key_facts = max_key_facts
        self.max_topics = max_topics

    # ---------------- 对外主入口 ----------------

    def extract(self, messages: List[dict]) -> Dict:
        """从消息列表提取关键信息

        Args:
            messages: 消息列表，每项含 role 与 content

        Returns:
            dict: {
                "topics": [str],                  # 对话主题
                "key_facts": [str],               # 关键事实/偏好/事件
                "user_requests": [str],           # 用户请求
                "questions": [str],               # 用户提问
                "todos": [str],                   # 待办/未完成事项
                "entities": [str],                # 重要实体
                "code_refs": [str],               # 代码/文件引用
                "summary_text": str,              # 抽取式摘要文本
            }
        """
        if not messages:
            return {
                "topics": [], "key_facts": [], "user_requests": [],
                "questions": [], "todos": [], "entities": [],
                "code_refs": [], "summary_text": "",
            }

        # 分角色收集文本
        user_texts, assistant_texts = [], []
        for m in messages:
            role = m.get("role", "")
            content = m.get("content", "") or ""
            if not content:
                continue
            if role == "user":
                user_texts.append(content)
            elif role in ("assistant", "tool", "system"):
                assistant_texts.append(content)

        all_text = "\n".join(user_texts + assistant_texts)
        user_all = "\n".join(user_texts)

        topics = self._extract_topics(all_text)
        key_facts = self._extract_key_facts(user_texts + assistant_texts)
        user_requests = self._extract_user_requests(user_texts)
        questions = self._extract_questions(user_texts)
        todos = self._extract_todos(user_texts + assistant_texts)
        entities = self._extract_entities(all_text)
        code_refs = self._extract_code_refs(all_text)
        summary_text = self._build_extractive_summary(
            messages, key_facts, topics, questions, max_chars=1200
        )

        return {
            "topics": topics,
            "key_facts": key_facts,
            "user_requests": user_requests,
            "questions": questions,
            "todos": todos,
            "entities": entities,
            "code_refs": code_refs,
            "summary_text": summary_text,
        }

    # ---------------- 主题提取 ----------------

    def _tokenize(self, text: str) -> List[str]:
        """简单分词（英文单词 + 中文二元组）"""
        text = text.lower()
        en_tokens = re.findall(r"\b[a-z]{2,}\b", text)
        cn_chars = re.findall(r"[\u4e00-\u9fff]", text)
        cn_tokens = [cn_chars[i] + cn_chars[i + 1] for i in range(len(cn_chars) - 1)]
        tokens = [t for t in en_tokens + cn_tokens
                  if t not in STOP_WORDS and len(t) > 1]
        return tokens

    def _extract_topics(self, text: str) -> List[str]:
        """基于词频提取对话主题"""
        tokens = self._tokenize(text)
        if not tokens:
            return []
        counter = Counter(tokens)
        # 过滤掉过泛的词（出现频率异常高多为通用词）
        top = counter.most_common(self.max_topics * 2)
        topics = []
        for word, _ in top:
            if word in topics:
                continue
            if len(topics) >= self.max_topics:
                break
            # 过于泛化的词跳过
            if word in ("嗯", "啊", "哦", "喂", "嘛", "哈", "啦"):
                continue
            topics.append(word)
        return topics

    # ---------------- 关键事实 ----------------

    def _extract_key_facts(self, texts: List[str]) -> List[str]:
        """提取关键事实句子"""
        facts = []
        seen = set()
        for text in texts:
            for sentence in split_sentences(text):
                if len(sentence) < 4:
                    continue
                if self._is_important_sentence(sentence):
                    norm = sentence[:60]
                    if norm not in seen:
                        seen.add(norm)
                        facts.append(sentence[:120])
            if len(facts) >= self.max_key_facts:
                break
        return facts[:self.max_key_facts]

    def _is_important_sentence(self, sentence: str) -> bool:
        """判断句子是否包含关键信息"""
        if re.search(r"[\d]+", sentence):
            return True
        if re.search(r"https?://", sentence):
            return True
        for pattern in IMPORTANCE_KEYWORDS["high"]:
            if re.search(pattern, sentence):
                return True
        for pattern in IMPORTANCE_KEYWORDS["medium"]:
            if re.search(pattern, sentence):
                return True
        # 包含文件路径
        if re.search(r"(?:[A-Za-z]:\\|\w+\.(?:py|js|ts|txt|json|md|html|css|java|cpp|c))", sentence):
            return True
        return False

    # ---------------- 用户请求 / 提问 / 待办 ----------------

    def _extract_user_requests(self, user_texts: List[str]) -> List[str]:
        """提取用户明确请求"""
        requests = []
        seen = set()
        for text in user_texts:
            for sentence in split_sentences(text):
                if len(sentence) < 4:
                    continue
                for pattern in REQUEST_PATTERNS:
                    if re.search(pattern, sentence):
                        norm = sentence[:40]
                        if norm not in seen:
                            seen.add(norm)
                            requests.append(sentence[:120])
                        break
        return requests[:self.max_key_facts]

    def _extract_questions(self, user_texts: List[str]) -> List[str]:
        """提取用户提问（需持续跟踪的疑问）"""
        questions = []
        seen = set()
        for text in user_texts:
            for sentence in split_sentences(text):
                if len(sentence) < 3:
                    continue
                for pattern in QUESTION_PATTERNS:
                    if re.search(pattern, sentence):
                        norm = sentence[:40]
                        if norm not in seen:
                            seen.add(norm)
                            questions.append(sentence[:120])
                        break
        return questions[:self.max_key_facts]

    def _extract_todos(self, texts: List[str]) -> List[str]:
        """提取待办/未完成事项"""
        todos = []
        seen = set()
        for text in texts:
            for sentence in split_sentences(text):
                if len(sentence) < 4:
                    continue
                for kw in TODO_KEYWORDS:
                    if kw in sentence and sentence not in seen:
                        seen.add(sentence)
                        todos.append(sentence[:120])
                        break
        return todos[:self.max_key_facts]

    # ---------------- 实体 / 代码引用 ----------------

    def _extract_entities(self, text: str) -> List[str]:
        """提取重要实体（URL/邮箱/路径/数字/时间）"""
        entities = []
        seen = set()
        for kind, pattern in ENTITY_PATTERNS.items():
            for match in re.findall(pattern, text):
                val = match.strip()
                if not val or val in seen:
                    continue
                seen.add(val)
                entities.append(f"{kind}: {val}")
        return entities[:30]

    def _extract_code_refs(self, text: str) -> List[str]:
        """提取代码块与文件引用"""
        refs = []
        code_blocks = re.findall(r"```[^\n]*\n.*?```", text, re.DOTALL)
        for block in code_blocks[:5]:
            refs.append(block[:300])
        # 文件引用（带扩展名）
        file_refs = re.findall(
            r"[\w./\\-]+\.[a-zA-Z0-9]{1,6}(?![a-zA-Z0-9])",
            text,
        )
        seen = set()
        for ref in file_refs:
            if len(ref) < 3 or ref in seen:
                continue
            seen.add(ref)
            refs.append(ref)
        return refs[:15]

    # ---------------- 抽取式摘要 ----------------

    def _build_extractive_summary(
        self,
        messages: List[dict],
        key_facts: List[str],
        topics: List[str],
        questions: List[str],
        max_chars: int = 1200,
    ) -> str:
        """构建抽取式摘要（LLM 不可用时的降级方案）

        结构：
        - 对话主题
        - 关键事实
        - 用户提问/待办
        - 关键角色对话（按信息量挑选）
        """
        parts = []
        if topics:
            parts.append("对话主题: " + "、".join(topics[:5]))
        if key_facts:
            parts.append("关键信息:")
            for f in key_facts[:8]:
                parts.append(f"  - {f}")
        if questions:
            parts.append("用户提问:")
            for q in questions[:5]:
                parts.append(f"  - {q}")
        if not key_facts and not questions:
            # 无显著关键信息时，抽取信息密度较高的对话行
            lines = []
            for m in messages:
                role = m.get("role", "")
                content = (m.get("content", "") or "").strip()
                if not content or role in ("tool", "system"):
                    continue
                label = "用户" if role == "user" else "助手"
                lines.append(f"{label}: {content[:100]}")
            if lines:
                parts.append("对话要点:")
                for line in lines[-8:]:
                    parts.append(f"  - {line}")

        summary = "\n".join(parts)
        if len(summary) > max_chars:
            summary = summary[:max_chars] + "……"
        return summary
