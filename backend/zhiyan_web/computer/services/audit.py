"""
审计日志模块 - 记录所有 Agent 操作

功能:
- 记录每次工具调用的时间、操作类型、参数、结果
- 支持按时间范围、操作类型、用户查询
- 支持安全审计和异常检测

数据存储: data/audit/ 目录
"""
from __future__ import annotations

import os
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from zhiyan_web.config.settings import DATA_DIR

logger = logging.getLogger("ZhiYanWeb.Computer.Audit")

AUDIT_DIR = os.path.join(DATA_DIR, "audit")
os.makedirs(AUDIT_DIR, exist_ok=True)

MAX_LOG_ENTRIES = 10000  # 最大审计日志条目数
DANGEROUS_OPERATIONS = {
    "delete_file", "delete_directory", "format_disk",
    "shutdown", "restart", "kill_process_by_name",
    "execute_command", "write_system_file",
}


class AuditLogger:
    """审计日志记录器"""

    def __init__(self):
        self._entries: List[Dict] = []
        self._load()

    def _load(self):
        """加载最近的审计日志"""
        log_file = os.path.join(AUDIT_DIR, "audit_log.jsonl")
        if os.path.exists(log_file):
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            self._entries.append(json.loads(line))
                # 只保留最近 N 条
                if len(self._entries) > MAX_LOG_ENTRIES:
                    self._entries = self._entries[-MAX_LOG_ENTRIES:]
            except Exception as e:
                logger.error(f"加载审计日志失败: {e}")

    def _save(self, entry: Dict):
        """追加写入审计日志"""
        log_file = os.path.join(AUDIT_DIR, "audit_log.jsonl")
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"写入审计日志失败: {e}")

    def log(
        self,
        operation: str,
        params: Dict,
        result: Dict,
        user_id: str = "agent",
        duration_ms: float = 0,
        success: bool = True,
    ):
        """记录一条审计日志

        Args:
            operation: 操作类型
            params: 操作参数
            result: 操作结果
            user_id: 用户/调用者 ID
            duration_ms: 执行耗时(毫秒)
            success: 是否成功
        """
        entry = {
            "timestamp": datetime.now().isoformat(),
            "operation": operation,
            "params": self._sanitize_params(params),
            "result": self._sanitize_result(result),
            "user_id": user_id,
            "duration_ms": round(duration_ms, 2),
            "success": success,
            "is_dangerous": operation in DANGEROUS_OPERATIONS,
        }
        self._entries.append(entry)
        self._save(entry)

        # 危险操作额外记录到警告日志
        if entry["is_dangerous"]:
            logger.warning(f"[AUDIT] 危险操作: {operation} 参数: {params}")

    def _sanitize_params(self, params: Dict) -> Dict:
        """脱敏敏感参数"""
        sanitized = dict(params)
        # 脱敏文件内容
        if "content" in sanitized and isinstance(sanitized["content"], str):
            if len(sanitized["content"]) > 200:
                sanitized["content"] = sanitized["content"][:200] + "... [截断]"
        # 脱敏密码
        for key in list(sanitized.keys()):
            if any(kw in key.lower() for kw in ["password", "secret", "token", "key", "credential"]):
                sanitized[key] = "***"
        return sanitized

    def _sanitize_result(self, result: Dict) -> Dict:
        """脱敏结果"""
        sanitized = dict(result)
        if "output" in sanitized and isinstance(sanitized["output"], str):
            if len(sanitized["output"]) > 500:
                sanitized["output"] = sanitized["output"][:500] + "... [截断]"
        return sanitized

    def query(
        self,
        operation: Optional[str] = None,
        user_id: Optional[str] = None,
        hours: int = 24,
        limit: int = 100,
        only_dangerous: bool = False,
    ) -> List[Dict]:
        """查询审计日志

        Args:
            operation: 按操作类型筛选
            user_id: 按用户筛选
            hours: 查询最近多少小时
            limit: 返回条数限制
            only_dangerous: 只返回危险操作

        Returns:
            List[Dict]: 日志条目列表
        """
        cutoff = datetime.now() - timedelta(hours=hours)
        results = []

        for entry in reversed(self._entries):
            if len(results) >= limit:
                break

            timestamp = datetime.fromisoformat(entry["timestamp"])
            if timestamp < cutoff:
                continue

            if operation and entry["operation"] != operation:
                continue

            if user_id and entry["user_id"] != user_id:
                continue

            if only_dangerous and not entry["is_dangerous"]:
                continue

            results.append(entry)

        return results

    def get_stats(self, hours: int = 24) -> Dict:
        """获取审计统计

        Args:
            hours: 统计最近多少小时

        Returns:
            Dict: 统计信息
        """
        cutoff = datetime.now() - timedelta(hours=hours)
        recent = [e for e in self._entries if datetime.fromisoformat(e["timestamp"]) >= cutoff]

        operations = {}
        for entry in recent:
            op = entry["operation"]
            if op not in operations:
                operations[op] = {"count": 0, "success": 0, "fail": 0}
            operations[op]["count"] += 1
            if entry["success"]:
                operations[op]["success"] += 1
            else:
                operations[op]["fail"] += 1

        return {
            "total_operations": len(recent),
            "total_dangerous": sum(1 for e in recent if e["is_dangerous"]),
            "success_rate": round(
                sum(1 for e in recent if e["success"]) / max(len(recent), 1) * 100, 2
            ),
            "operations": operations,
        }


# 全局单例
_audit_logger: Optional[AuditLogger] = None


def get_audit_logger() -> AuditLogger:
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger()
    return _audit_logger