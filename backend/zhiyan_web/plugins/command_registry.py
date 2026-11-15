"""
插件命令注册中心

跟踪所有插件注册的命令，检测命令冲突，提供冲突查询和解决机制。
当多个插件注册了相同的命令名时，用户可通过 API 指定哪个插件优先处理。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("ZhiYanWeb.CommandRegistry")


@dataclass
class CommandRegistration:
    """一条命令注册记录"""
    command_name: str
    plugin_name: str
    plugin_display_name: str = ""
    description: str = ""
    priority: int = 0
    timestamp: float = 0.0
    handler_path: str = ""


class CommandRegistry:
    """
    命令注册中心（单例）

    职责:
    - 记录所有插件注册的命令及其元信息
    - 检测命令冲突（同一命令被多个插件注册）
    - 提供冲突解决机制（指定某个插件优先处理）
    - 提供查询接口供前端和管理员使用
    """

    _instance: Optional['CommandRegistry'] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """初始化内部状态"""
        # command_name -> [CommandRegistration, ...]  按注册时间排序
        self._commands: Dict[str, List[CommandRegistration]] = {}
        # command_name -> 获胜的 plugin_name (由用户手动解决)
        self._resolutions: Dict[str, str] = {}
        # command_name -> plugin_name (自动解决: 优先级高的先注册的胜出)
        self._auto_resolved: Dict[str, str] = {}

    # ──────────────── 注册 ────────────────

    def register(
        self,
        command_name: str,
        plugin_name: str,
        plugin_display_name: str = "",
        description: str = "",
        priority: int = 0,
        handler_path: str = "",
    ) -> bool:
        """注册一条命令

        如果检测到冲突（同一命令已被其他插件注册），会记录日志。

        Args:
            command_name: 命令名（如 "help"）
            plugin_name: 插件标识名
            plugin_display_name: 插件显示名
            description: 命令描述
            priority: 优先级（数字越大越优先）
            handler_path: 处理器路径

        Returns:
            bool: 是否注册成功（总是 True，冲突也被记录）
        """
        if command_name not in self._commands:
            self._commands[command_name] = []

        reg = CommandRegistration(
            command_name=command_name,
            plugin_name=plugin_name,
            plugin_display_name=plugin_display_name or plugin_name,
            description=description,
            priority=priority,
            timestamp=time.time(),
            handler_path=handler_path,
        )
        self._commands[command_name].append(reg)
        # 按注册时间排序
        self._commands[command_name].sort(key=lambda r: r.timestamp)

        # 检测冲突
        if len(self._commands[command_name]) > 1:
            existing_plugins = [r.plugin_name for r in self._commands[command_name] if r.plugin_name != plugin_name]
            logger.warning(
                f"命令冲突: 命令 '{command_name}' 已被插件 {existing_plugins} 注册，"
                f"插件 '{plugin_name}' 也注册了该命令。"
            )

        # 自动选择: 优先级高的胜出；优先级相同则先注册的胜出
        self._auto_resolve(command_name)

        return True

    def unregister_plugin(self, plugin_name: str) -> None:
        """卸载某个插件的所有命令注册"""
        for cmd_name in list(self._commands.keys()):
            self._commands[cmd_name] = [
                r for r in self._commands[cmd_name]
                if r.plugin_name != plugin_name
            ]
            if not self._commands[cmd_name]:
                del self._commands[cmd_name]
                self._resolutions.pop(cmd_name, None)
                self._auto_resolved.pop(cmd_name, None)
            else:
                self._auto_resolve(cmd_name)

    # ──────────────── 冲突检测 ────────────────

    def _auto_resolve(self, command_name: str) -> None:
        """自动解决冲突: 优先级高的胜出，优先级相同则先注册的胜出"""
        registrations = self._commands.get(command_name, [])
        if not registrations:
            self._auto_resolved.pop(command_name, None)
            return

        # 按优先级降序、注册时间升序排序
        sorted_regs = sorted(registrations, key=lambda r: (-r.priority, r.timestamp))
        self._auto_resolved[command_name] = sorted_regs[0].plugin_name

    def get_owner(self, command_name: str) -> Optional[str]:
        """获取命令的当前所有者

        优先使用用户手动解决的方案，否则使用自动解决的方案。
        """
        if command_name in self._resolutions:
            return self._resolutions[command_name]
        return self._auto_resolved.get(command_name)

    def get_conflicts(self) -> List[dict]:
        """获取所有命令冲突列表

        Returns:
            list[dict]: 每个冲突包含:
                - command: 命令名
                - plugins: 冲突的插件列表 [name, ...]
                - resolved: 是否已解决
                - winner: 当前胜出者（如果有）
                - description: 各插件的命令描述
        """
        conflicts = []
        for cmd_name, registrations in self._commands.items():
            if len(registrations) < 2:
                continue
            conflict = {
                "command": cmd_name,
                "plugins": [r.plugin_name for r in registrations],
                "plugin_details": [
                    {
                        "plugin_name": r.plugin_name,
                        "plugin_display_name": r.plugin_display_name,
                        "description": r.description,
                        "priority": r.priority,
                    }
                    for r in registrations
                ],
                "resolved": cmd_name in self._resolutions,
                "auto_resolved": cmd_name in self._auto_resolved,
                "winner": self.get_owner(cmd_name),
            }
            conflicts.append(conflict)
        return conflicts

    def resolve_conflict(self, command_name: str, winning_plugin: str) -> bool:
        """手动解决命令冲突

        Args:
            command_name: 命令名
            winning_plugin: 获胜的插件名

        Returns:
            bool: 是否成功解决
        """
        if command_name not in self._commands:
            logger.warning(f"解决冲突失败: 命令 '{command_name}' 不存在")
            return False

        plugins = [r.plugin_name for r in self._commands[command_name]]
        if winning_plugin not in plugins:
            logger.warning(
                f"解决冲突失败: 插件 '{winning_plugin}' 未注册命令 '{command_name}'"
            )
            return False

        self._resolutions[command_name] = winning_plugin
        logger.info(
            f"命令冲突已解决: 命令 '{command_name}' 由插件 '{winning_plugin}' 处理"
        )
        return True

    def reset_resolution(self, command_name: str) -> bool:
        """重置某命令的冲突解决，恢复自动选择"""
        if command_name in self._resolutions:
            del self._resolutions[command_name]
            self._auto_resolve(command_name)
            return True
        return False

    # ──────────────── 查询 ────────────────

    def get_all_commands(self) -> Dict[str, List[CommandRegistration]]:
        """获取所有已注册的命令"""
        return dict(self._commands)

    def get_commands_by_plugin(self, plugin_name: str) -> List[CommandRegistration]:
        """获取某个插件注册的所有命令"""
        result = []
        for registrations in self._commands.values():
            for r in registrations:
                if r.plugin_name == plugin_name:
                    result.append(r)
        return result

    def get_plugin_names_by_command(self, command_name: str) -> List[str]:
        """获取注册了某命令的所有插件名"""
        registrations = self._commands.get(command_name, [])
        return [r.plugin_name for r in registrations]

    def get_command_summary(self) -> List[dict]:
        """获取所有命令的摘要信息（用于前端展示）"""
        summary = []
        for cmd_name, registrations in self._commands.items():
            owner = self.get_owner(cmd_name)
            is_conflict = len(registrations) > 1
            owner_marked = False
            details = []
            for r in registrations:
                this_is_owner = not owner_marked and r.plugin_name == owner
                if this_is_owner:
                    owner_marked = True
                details.append({
                    "plugin_name": r.plugin_name,
                    "plugin_display_name": r.plugin_display_name,
                    "description": r.description,
                    "priority": r.priority,
                    "is_owner": this_is_owner,
                })
            summary.append({
                "command": cmd_name,
                "plugin_count": len(registrations),
                "plugins": [r.plugin_name for r in registrations],
                "plugin_details": details,
                "is_conflict": is_conflict,
                "resolved": cmd_name in self._resolutions,
                "owner": owner,
            })
        # 按命令名排序
        summary.sort(key=lambda x: x["command"])
        return summary


# 单例实例
_command_registry = CommandRegistry()


def get_command_registry() -> CommandRegistry:
    """获取命令注册中心单例"""
    return _command_registry