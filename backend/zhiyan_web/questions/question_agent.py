"""
交互式提问 Agent
用于在软件开发等任务执行前主动向用户发起提问，实现：
1. 确认后续操作流程的执行权限与具体参数和细节
2. 系统性收集用户的完整需求信息，确保需求理解的准确性与全面性
3. 在需要用户授权或提供关键信息时，以友好且明确的方式主动发起请求

核心机制：
- 通过 ask_questions 虚拟工具让 LLM 在任务模糊、缺参数、需授权时主动提问
- 提问以结构化 JSON（id/问题/类型/选项/是否必答）发送到前端渲染为交互式卡片
- 用户回答通过 /api/chat/agent-answer 回传，后端注入回答后恢复 ReAct 循环
- 支持多轮对话，直至获取到所需全部信息或确认完成
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger("ZhiYanWeb.questions")


class QuestionAgent:
    """交互式提问 Agent"""

    def __init__(self):
        # 每会话待回答问题状态: {conv_id: {questions, tool_call_id, resume_ctx}}
        self._pending: Dict[str, Dict[str, Any]] = {}

    # ---- 系统提示词 ----

    def get_ask_guidelines(self) -> str:
        """返回注入系统提示词的主动提问行为指南"""
        return (
            "\n\n**主动提问（重要）:**\n"
            "1. 在动手执行任务之前，先判断任务是否清晰完整、是否缺少必要参数/细节、"
            "是否涉及敏感或高风险操作。\n"
            "2. 出现以下情况时，必须先调用 ask_questions 工具向用户提问，"
            "而不是直接猜测或擅自执行：\n"
            "   - 任务描述模糊、有歧义，或缺少完成任务所必需的关键参数/细节\n"
            "   - 涉及删除、覆盖、格式化、修改系统配置、发送外部消息等敏感或高风险操作"
            "（需要用户明确授权）\n"
            "   - 需要用户提供关键信息（如目标路径、具体格式、偏好、账号信息等）\n"
            "3. 提问要求：\n"
            "   - 一次最多提出 5 个问题，问题要具体、简洁、聚焦，避免冗余\n"
            "   - 能给出选项的问题尽量使用 choice 类型，方便用户快速选择\n"
            "   - 授权类问题使用 confirm 类型，明确说明操作内容和风险\n"
            "4. 不要编造用户未提供的信息；提问后必须等待用户回答，再基于回答继续执行。\n"
            "5. 如果任务描述清晰完整、参数齐全，则直接执行，不要多余提问。\n"
            "6. 执行过程中若某工具连续多次失败（如权限拒绝、路径不存在、参数错误），"
            "不要反复尝试相同或相似的操作，应停止并主动调用 ask_questions 向用户说明"
            "失败原因并询问替代方案，或直接总结失败情况与解决建议。\n"
        )

    def get_tool_schema(self) -> dict:
        """返回 ask_questions 虚拟工具定义"""
        return {
            "type": "function",
            "function": {
                "name": "ask_questions",
                "description": (
                    "向用户提出澄清问题，用于收集需求、确认执行权限与参数细节。"
                    "当任务模糊、缺少必要参数、涉及敏感/高风险操作需要授权、"
                    "或需用户提供关键信息时调用。调用后请等待用户回答，不要再做其他操作。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "questions": {
                            "type": "array",
                            "description": "要提出的问题列表（1-5个）",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {
                                        "type": "string",
                                        "description": "问题唯一标识，如 q1",
                                    },
                                    "question": {
                                        "type": "string",
                                        "description": "问题内容",
                                    },
                                    "type": {
                                        "type": "string",
                                        "enum": ["text", "choice", "confirm"],
                                        "description": "text=文本输入，choice=选项选择，confirm=确认授权",
                                    },
                                    "options": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                        "description": "type 为 choice 时的选项列表",
                                    },
                                    "required": {
                                        "type": "boolean",
                                        "description": "是否必答",
                                    },
                                },
                                "required": ["id", "question", "type"],
                            },
                        }
                    },
                    "required": ["questions"],
                },
            },
        }

    # ---- 状态管理 ----

    def register_pending(
        self,
        conv_id: str,
        questions: List[dict],
        tool_call_id: str,
        resume_ctx: Optional[dict] = None,
    ) -> None:
        """记录待回答的问题状态"""
        self._pending[conv_id] = {
            "questions": questions,
            "tool_call_id": tool_call_id,
            "resume_ctx": resume_ctx or {},
        }

    def get_pending(self, conv_id: str) -> Optional[dict]:
        """获取待回答状态"""
        return self._pending.get(conv_id)

    def pop_pending(self, conv_id: str) -> Optional[dict]:
        """弹出并清除待回答状态"""
        return self._pending.pop(conv_id, None)

    # ---- 回答处理 ----

    def format_answers(self, answers: Dict[str, Any]) -> str:
        """将用户回答格式化为工具结果字符串"""
        try:
            return json.dumps({"answers": answers}, ensure_ascii=False, indent=2)
        except Exception:
            return str(answers)


# 全局单例
question_agent = QuestionAgent()
