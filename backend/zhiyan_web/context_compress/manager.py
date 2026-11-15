"""
上下文压缩编排管理器

统一调度「上下文连续压缩」与「历史对话定期压缩」：

1. 上下文连续压缩（compress_context）
   当会话消息数/长度接近或达到预设阈值时，自动对早期非关键对话内容
   进行语义压缩，保留最近 keep_recent 条消息，并将压缩摘要作为 system
   消息前置注入，保证对话连续性。

2. 历史对话定期压缩（compress_history_all / 后台任务）
   定期扫描 data/conversations 下已存储的完整历史对话，对超过阈值的
   长对话进行语义压缩并持久化，保留核心信息的同时大幅减少存储占用。

3. 压缩效果评估
   每次压缩后调用 evaluator 评估保留率与综合评分，留存评估报告，
   评分不合格时自动降级为抽取式摘要保证关键信息不丢失。

4. 持久化
   - 压缩存档：data/context_compress/history/{id}.json
   - 压缩记录：data/context_compress/records.json
"""
from __future__ import annotations

import os
import json
import asyncio
import hashlib
import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from zhiyan_web.config.settings import DATA_DIR, get_settings, update_settings
from zhiyan_web.context_compress.compressor import SemanticCompressor
from zhiyan_web.context_compress.evaluator import CompressionEvaluator

logger = logging.getLogger("ZhiYanWeb.ContextCompress")

# 压缩数据目录
COMPRESS_DIR = os.path.join(DATA_DIR, "context_compress")
HISTORY_ARCHIVE_DIR = os.path.join(COMPRESS_DIR, "history")
RECORDS_FILE = os.path.join(COMPRESS_DIR, "records.json")
STATS_FILE = os.path.join(COMPRESS_DIR, "stats.json")

os.makedirs(HISTORY_ARCHIVE_DIR, exist_ok=True)

# 默认配置（settings.json 的 context_compress 键）
DEFAULT_CONFIG = {
    "enabled": True,                 # 上下文压缩总开关
    "strategy": "auto",              # auto / llm / extractive
    "max_context_messages": 60,      # 会话消息数超过该值触发连续压缩
    "keep_recent": 20,               # 连续压缩保留的最近消息数
    "history_enabled": True,         # 历史对话定期压缩开关
    "history_interval_hours": 24,    # 定期压缩周期（小时）
    "history_min_messages": 60,      # 历史对话超过该消息数才压缩
    "history_mode": "compact",       # compact=压缩原文件+前置摘要 / archive=仅存档不动原文件
    "history_keep_recent": 20,       # 压缩历史文件时保留的最近消息数
    "max_output_chars": 2500,        # 压缩结果最大字符数
    "llm_instruction": "",           # 自定义 LLM 压缩指令（留空用默认）
}


def _get_config() -> Dict[str, Any]:
    """读取 context_compress 配置（与默认值合并，缺键补默认）"""
    settings = get_settings()
    stored = settings.get("context_compress") or {}
    config = dict(DEFAULT_CONFIG)
    if isinstance(stored, dict):
        config.update({k: v for k, v in stored.items() if k in config})
    return config


def _save_config(config: Dict[str, Any]) -> None:
    """保存 context_compress 配置（深合并，不丢其他键）"""
    patch = {"context_compress": {k: config[k] for k in DEFAULT_CONFIG if k in config}}
    try:
        update_settings(patch)
    except Exception as e:
        logger.warning(f"保存 context_compress 配置失败: {e}")


def _normalize_message(m: Any) -> Dict[str, str]:
    """将消息对象（LLMMessage / dict）统一为 dict"""
    if isinstance(m, dict):
        return {
            "role": m.get("role", "user"),
            "content": m.get("content", ""),
        }
    return {
        "role": getattr(m, "role", "user"),
        "content": getattr(m, "content", ""),
    }


def _message_count(messages: List[Any]) -> int:
    return len(messages)


def _archive_file_id(conversation_id: str) -> str:
    """根据对话 ID 生成存档文件名（哈希，安全）"""
    safe = hashlib.sha256(str(conversation_id).encode()).hexdigest()[:16]
    return f"arch_{safe}"


class ContextCompressManager:
    """上下文压缩编排管理器（单例）"""

    def __init__(self):
        self.compressor: Optional[SemanticCompressor] = None
        self.evaluator = CompressionEvaluator()
        self._lock = asyncio.Lock()
        self._started = False
        self._task: Optional[asyncio.Task] = None
        self._last_run_at: Optional[str] = None

    # ---------------- 懒初始化 ----------------

    def _ensure_compressor(self) -> SemanticCompressor:
        if self.compressor is None:
            cfg = _get_config()
            self.compressor = SemanticCompressor(
                llm_instruction=cfg.get("llm_instruction") or None,
                max_output_chars=int(cfg.get("max_output_chars", 2500)),
            )
        return self.compressor

    # ---------------- 配置 ----------------

    def get_config(self) -> Dict[str, Any]:
        """获取当前配置"""
        return dict(_get_config())

    def update_config(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """更新配置并热应用"""
        if not patch:
            return self.get_config()
        config = _get_config()
        for k in DEFAULT_CONFIG:
            if k in patch and patch[k] is not None:
                config[k] = patch[k]
        _save_config(config)
        # 热应用压缩器参数
        self.compressor = SemanticCompressor(
            llm_instruction=config.get("llm_instruction") or None,
            max_output_chars=int(config.get("max_output_chars", 2500)),
        )
        logger.info(f"上下文压缩配置已更新: {json.dumps(config, ensure_ascii=False)}")
        return dict(config)

    # ---------------- 上下文连续压缩 ----------------

    async def compress_context(
        self,
        messages: List[Any],
        conv_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """上下文连续压缩

        当消息数达到阈值时，将早期非关键内容语义压缩为摘要，
        保留最近 keep_recent 条消息。

        Args:
            messages: 当前会话的全部消息（LLMMessage 或 dict 列表）
            conv_id: 会话 ID（用于记录）

        Returns:
            dict: {
                "compressed": bool,       # 是否执行了压缩
                "summary": str,           # 压缩摘要（未压缩时为 ""）
                "kept": list,             # 保留的最近消息（原始对象）
                "removed_count": int,     # 被压缩移除的消息数
                "used_strategy": str,     # llm / extractive
                "evaluation": dict,       # 压缩效果评估
                "reason": str,            # 未压缩时的原因
            }
        """
        cfg = _get_config()
        result = {
            "compressed": False,
            "summary": "",
            "kept": list(messages),
            "removed_count": 0,
            "used_strategy": "",
            "evaluation": {},
            "reason": "",
        }

        if not messages:
            result["reason"] = "空上下文"
            return result
        if not cfg.get("enabled", True):
            result["reason"] = "上下文压缩已关闭"
            return result

        # 统一归一化（支持 LLMMessage 对象 / dict），压缩与评估使用 dict
        normalized = [_normalize_message(m) for m in messages]
        total = len(normalized)
        keep_recent = max(2, int(cfg.get("keep_recent", 20)))
        max_messages = int(cfg.get("max_context_messages", 60))

        if total <= max_messages:
            result["reason"] = f"未达到阈值（{total}/{max_messages}）"
            return result

        # 划分：早期（待压缩）+ 最近（保留，保持原始对象）
        to_compress = normalized[: total - keep_recent]
        kept = messages[total - keep_recent :]

        if not to_compress:
            result["reason"] = "无可压缩的早期消息"
            return result

        try:
            summary, key_info, used_strategy = await self._compress_and_gate(
                to_compress,
                strategy=cfg.get("strategy", "auto"),
            )
        except Exception as e:
            logger.warning(f"连续压缩失败，回退截断: {e}")
            result["reason"] = f"压缩失败: {e}"
            return result

        evaluation = self.evaluator.evaluate(to_compress, summary)

        # 记录压缩统计
        self._record_compression(
            conv_id=conv_id or "live",
            message_count=len(to_compress),
            char_before=len("\n".join(m.get("content", "") for m in to_compress)),
            char_after=len(summary),
            evaluation=evaluation,
            used_strategy=used_strategy,
            mode="continuous",
        )

        result.update({
            "compressed": True,
            "summary": summary,
            "kept": kept,
            "removed_count": len(to_compress),
            "used_strategy": used_strategy,
            "evaluation": evaluation,
        })
        return result

    # ---------------- 历史对话定期压缩 ----------------

    async def compress_conversation_history(self, conversation_id: str) -> Dict[str, Any]:
        """压缩单个历史对话

        从 data/conversations 加载完整对话，压缩早期内容为摘要并持久化。

        Returns:
            dict: {
                "compressed": bool, "conversation_id": str,
                "message_count": int, "summary": str, "used_strategy": str,
                "evaluation": dict, "archive": str, "reason": str,
            }
        """
        from zhiyan_web.memory.conversation_store import (
            load_conversation, save_conversation,
        )

        cfg = _get_config()
        result = {
            "compressed": False,
            "conversation_id": conversation_id,
            "message_count": 0,
            "summary": "",
            "used_strategy": "",
            "evaluation": {},
            "archive": "",
            "reason": "",
        }

        if not cfg.get("history_enabled", True):
            result["reason"] = "历史压缩已关闭"
            return result

        messages = load_conversation(conversation_id) or []
        if not messages:
            result["reason"] = "对话为空"
            return result

        min_messages = int(cfg.get("history_min_messages", 60))
        if len(messages) < min_messages:
            result["reason"] = f"消息数未达阈值（{len(messages)}/{min_messages}）"
            return result

        keep_recent = max(2, int(cfg.get("history_keep_recent", 20)))
        to_compress = messages[: len(messages) - keep_recent]
        kept = messages[len(messages) - keep_recent :]

        if not to_compress:
            result["reason"] = "无可压缩的早期消息"
            return result

        try:
            summary, key_info, used_strategy = await self._compress_and_gate(
                to_compress,
                strategy=cfg.get("strategy", "auto"),
            )
        except Exception as e:
            logger.warning(f"历史对话压缩失败 {conversation_id}: {e}")
            result["reason"] = f"压缩失败: {e}"
            return result

        evaluation = self.evaluator.evaluate(to_compress, summary)

        # 持久化压缩存档
        archive = self._save_archive(
            conversation_id, to_compress, kept, summary,
            key_info, evaluation, used_strategy,
        )

        # compact 模式：重写原对话文件为 [摘要] + 最近消息，减少存储占用
        if cfg.get("history_mode", "compact") == "compact":
            try:
                summary_msg = {
                    "role": "system",
                    "content": f"[历史对话压缩摘要] {summary}",
                }
                new_messages = [summary_msg] + kept
                save_conversation(conversation_id, new_messages)
                logger.info(
                    f"历史对话已压缩重写: {conversation_id[:16]}... "
                    f"({len(messages)} -> {len(new_messages)} 条)"
                )
            except Exception as e:
                logger.warning(f"重写历史对话失败 {conversation_id}: {e}")

        self._record_compression(
            conv_id=conversation_id,
            message_count=len(to_compress),
            char_before=len("\n".join(m.get("content", "") for m in to_compress)),
            char_after=len(summary),
            evaluation=evaluation,
            used_strategy=used_strategy,
            mode="history",
        )

        result.update({
            "compressed": True,
            "message_count": len(messages),
            "summary": summary,
            "used_strategy": used_strategy,
            "evaluation": evaluation,
            "archive": archive,
        })
        return result

    async def compress_history_all(self) -> Dict[str, Any]:
        """扫描并压缩所有达到阈值的已存储历史对话

        Returns:
            dict: {"scanned": int, "compressed": int, "skipped": int, "results": [dict]}
        """
        from zhiyan_web.memory.conversation_store import list_conversations

        if not _get_config().get("history_enabled", True):
            return {"scanned": 0, "compressed": 0, "skipped": 0, "results": [], "reason": "历史压缩已关闭"}

        conversations = list_conversations()
        results = []
        compressed = 0
        skipped = 0
        for conv in conversations:
            conv_id = conv.get("conversation_id")
            if not conv_id:
                continue
            try:
                r = await self.compress_conversation_history(conv_id)
            except Exception as e:
                logger.warning(f"压缩历史对话异常 {conv_id}: {e}")
                skipped += 1
                continue
            results.append(r)
            if r.get("compressed"):
                compressed += 1
            else:
                skipped += 1

        self._last_run_at = datetime.now().isoformat()
        logger.info(f"历史对话定期压缩完成: 扫描 {len(conversations)}, 压缩 {compressed}, 跳过 {skipped}")
        return {
            "scanned": len(conversations),
            "compressed": compressed,
            "skipped": skipped,
            "results": results,
        }

    # ---------------- 后台定时任务 ----------------

    def start_background_task(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """启动后台定期压缩任务（幂等）"""
        if self._started:
            return
        try:
            running_loop = loop or asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("无运行中的事件循环，跳过后台压缩任务启动")
            return
        self._started = True
        self._task = running_loop.create_task(self._periodic_loop())
        logger.info("上下文压缩后台任务已启动")

    def stop_background_task(self) -> None:
        """停止后台任务"""
        self._started = False
        if self._task:
            try:
                self._task.cancel()
            except Exception:
                pass
            self._task = None

    async def _periodic_loop(self) -> None:
        """后台循环：按配置周期执行历史对话压缩"""
        while self._started:
            try:
                await self.compress_history_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"后台历史压缩任务异常: {e}")
            interval = max(0.5, float(_get_config().get("history_interval_hours", 24)) * 3600)
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    # ---------------- 内部：压缩 + 评估把关 ----------------

    async def _compress_and_gate(self, messages: List[dict], strategy: str) -> tuple:
        """压缩消息并做效果评估把关

        若 LLM 摘要保留不足（评估不合格），自动降级为抽取式摘要，
        保证关键信息不丢失（对话连续性保障）。
        """
        compressor = self._ensure_compressor()
        summary, key_info, used_strategy = await compressor.compress(
            messages,
            strategy=strategy,
        )

        evaluation = self.evaluator.evaluate(messages, summary)
        if used_strategy == "llm" and not evaluation.get("acceptable", True):
            # LLM 摘要关键信息保留不足，改用抽取式摘要（保证覆盖）
            fallback = key_info.get("summary_text") or ""
            if fallback:
                summary = fallback
                used_strategy = "extractive"
                evaluation = self.evaluator.evaluate(messages, summary)
                logger.info("LLM 压缩评估不合格，已降级为抽取式摘要")

        return summary, key_info, used_strategy

    # ---------------- 持久化 ----------------

    def _save_archive(
        self,
        conversation_id: str,
        to_compress: List[dict],
        kept: List[dict],
        summary: str,
        key_info: Dict,
        evaluation: Dict,
        used_strategy: str,
    ) -> str:
        """保存压缩存档到 data/context_compress/history/{id}.json"""
        try:
            archive_id = _archive_file_id(conversation_id)
            archive_path = os.path.join(HISTORY_ARCHIVE_DIR, f"{archive_id}.json")
            record = {
                "archive_id": archive_id,
                "conversation_id": conversation_id,
                "compressed_at": datetime.now().isoformat(),
                "used_strategy": used_strategy,
                "message_count": len(to_compress),
                "kept_count": len(kept),
                "summary": summary,
                "key_info": {
                    "topics": key_info.get("topics", []),
                    "key_facts": key_info.get("key_facts", []),
                    "user_requests": key_info.get("user_requests", []),
                    "questions": key_info.get("questions", []),
                    "todos": key_info.get("todos", []),
                    "entities": key_info.get("entities", []),
                    "code_refs": key_info.get("code_refs", []),
                },
                "evaluation": evaluation,
            }
            with open(archive_path, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
            return archive_path
        except Exception as e:
            logger.warning(f"保存压缩存档失败 {conversation_id}: {e}")
            return ""

    def _record_compression(
        self,
        conv_id: str,
        message_count: int,
        char_before: int,
        char_after: int,
        evaluation: Dict,
        used_strategy: str,
        mode: str,
    ) -> None:
        """记录一次压缩（写入 records.json，最多保留 500 条）"""
        try:
            records = []
            if os.path.exists(RECORDS_FILE):
                try:
                    with open(RECORDS_FILE, "r", encoding="utf-8") as f:
                        records = json.load(f)
                except Exception:
                    records = []
            record = {
                "id": hashlib.sha256(
                    f"{conv_id}|{datetime.now().isoformat()}|{mode}".encode()
                ).hexdigest()[:12],
                "conversation_id": conv_id,
                "mode": mode,
                "time": datetime.now().isoformat(),
                "message_count": message_count,
                "char_before": char_before,
                "char_after": char_after,
                "compression_ratio": round(char_before / max(1, char_after), 2),
                "score": evaluation.get("score", 0),
                "grade": evaluation.get("grade", ""),
                "acceptable": evaluation.get("acceptable", False),
                "used_strategy": used_strategy,
            }
            records.insert(0, record)
            records = records[:500]
            with open(RECORDS_FILE, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"记录压缩统计失败: {e}")

    # ---------------- 查询 ----------------

    def get_stats(self) -> Dict[str, Any]:
        """汇总压缩统计"""
        records = self._load_records()
        total = len(records)
        if not total:
            return {
                "total": 0, "continuous": 0, "history": 0,
                "avg_score": 0, "avg_ratio": 0, "acceptable_rate": 0,
                "last_run_at": self._last_run_at, "records": [],
            }
        continuous = sum(1 for r in records if r.get("mode") == "continuous")
        history = sum(1 for r in records if r.get("mode") == "history")
        avg_score = round(sum(r.get("score", 0) for r in records) / total, 1)
        avg_ratio = round(sum(r.get("compression_ratio", 1) for r in records) / total, 2)
        acceptable_rate = round(
            sum(1 for r in records if r.get("acceptable")) / total * 100, 1
        )
        return {
            "total": total,
            "continuous": continuous,
            "history": history,
            "avg_score": avg_score,
            "avg_ratio": avg_ratio,
            "acceptable_rate": acceptable_rate,
            "last_run_at": self._last_run_at,
            "records": records[:50],
        }

    def list_archives(self, limit: int = 50) -> List[Dict[str, Any]]:
        """列出已保存的历史压缩存档"""
        archives = []
        try:
            for fname in os.listdir(HISTORY_ARCHIVE_DIR):
                if not fname.startswith("arch_") or not fname.endswith(".json"):
                    continue
                filepath = os.path.join(HISTORY_ARCHIVE_DIR, fname)
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    archives.append({
                        "archive_id": data.get("archive_id", fname),
                        "conversation_id": data.get("conversation_id", ""),
                        "compressed_at": data.get("compressed_at", ""),
                        "used_strategy": data.get("used_strategy", ""),
                        "message_count": data.get("message_count", 0),
                        "summary": (data.get("summary") or "")[:200],
                        "score": (data.get("evaluation") or {}).get("score", 0),
                        "grade": (data.get("evaluation") or {}).get("grade", ""),
                    })
                except Exception:
                    continue
        except Exception as e:
            logger.warning(f"列出压缩存档失败: {e}")
        archives.sort(key=lambda x: x.get("compressed_at", ""), reverse=True)
        return archives[:limit]

    def get_archive(self, archive_id: str) -> Optional[Dict[str, Any]]:
        """获取单个压缩存档详情"""
        if not archive_id:
            return None
        safe = archive_id.replace("/", "").replace("\\", "").replace("..", "")
        filepath = os.path.join(HISTORY_ARCHIVE_DIR, f"{safe}.json")
        if not os.path.exists(filepath):
            return None
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"读取压缩存档失败: {e}")
            return None

    def _load_records(self) -> List[Dict[str, Any]]:
        if not os.path.exists(RECORDS_FILE):
            return []
        try:
            with open(RECORDS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    # ---------------- 手动触发 ----------------

    async def run_history_compress_once(self) -> Dict[str, Any]:
        """手动触发一次历史压缩（带并发锁）"""
        async with self._lock:
            return await self.compress_history_all()


# ---------------- 单例 ----------------

_instance: Optional[ContextCompressManager] = None
_instance_lock = threading.Lock()


def get_context_compress_manager() -> ContextCompressManager:
    """获取上下文压缩管理器单例"""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = ContextCompressManager()
    return _instance
