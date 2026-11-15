"""
Help 插件 - 提供 /help、/commands、/about 命令
"""

from __future__ import annotations

from zhiyan_web.plugins import PluginBase, event_handler_registry, EventType


class HelpPlugin(PluginBase):
    """Help 插件"""

    author = "ZhiYan"
    name = "help"

    async def initialize(self):
        """插件初始化"""
        print("HelpPlugin initialized")
        self.register_command("help", desc="显示帮助信息")
        self.register_command("commands", desc="显示所有命令列表")
        self.register_command("about", desc="关于系统")

    async def terminate(self):
        """插件卸载"""
        print("HelpPlugin terminated")

    async def on_message(self, message: dict) -> str | None:
        """处理消息"""
        content = message.get("content", "")

        if content.startswith("/help"):
            return self._build_help()
        elif content.startswith("/commands"):
            return self._build_commands()
        elif content.startswith("/about"):
            return self._build_about()
        return None

    def _build_help(self) -> str:
        result = ["=== 可用命令 ===", ""]

        handlers = event_handler_registry.get_handlers_by_event_type(EventType.MessageEvent)
        commands = {}
        for handler in handlers:
            cmd = handler.extras_configs.get("command_name")
            if cmd:
                commands[cmd] = handler.handler_name

        if commands:
            for cmd in sorted(commands.keys()):
                result.append(f"/{cmd} - {commands[cmd]}")
        else:
            result.append("暂无可用命令")

        result.append("")
        result.append("使用 /help <命令> 查看详细帮助")
        return "\n".join(result)

    def _build_commands(self) -> str:
        result = ["=== 所有命令列表 ===", ""]

        handlers = event_handler_registry.get_handlers_by_event_type(EventType.MessageEvent)
        cmd_info = {}
        for handler in handlers:
            cmd = handler.extras_configs.get("command_name")
            if cmd:
                cmd_info[cmd] = {
                    "name": handler.handler_name,
                    "plugin": handler.handler_module_path,
                }

        if cmd_info:
            for cmd in sorted(cmd_info.keys()):
                info = cmd_info[cmd]
                result.append(f"/{cmd} - {info['name']} ({info['plugin']})")
        else:
            result.append("暂无可用命令")

        return "\n".join(result)

    def _build_about(self) -> str:
        result = [
            "=== Furina Agent Web ===",
            "版本: 1.0.0",
            "基于 FURINA AGENT WEB 插件系统",
            "",
            "功能:",
            "  • AI 对话",
            "  • 音乐播放",
            "  • 文件转换",
            "  • 插件系统",
            "",
            "使用 /help 查看可用命令",
        ]
        return "\n".join(result)
