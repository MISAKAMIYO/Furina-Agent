"""
上下文压缩系统包

提供 AI 对话系统的上下文管理功能：
- 关键信息识别算法（key_info）
- 语义压缩模型（compressor，LLM + 抽取式降级）
- 压缩效果评估机制（evaluator）
- 编排管理（manager：连续压缩 + 历史定期压缩 + 持久化）
"""
from zhiyan_web.context_compress.manager import get_context_compress_manager

__all__ = ["get_context_compress_manager"]
