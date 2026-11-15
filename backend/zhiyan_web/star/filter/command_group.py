"""
指令组过滤器

支持父子指令组嵌套，与 AstrBot 的 CommandGroupFilter 完全兼容。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import HandlerFilter
from .command import CommandFilter

if TYPE_CHECKING:
    from ..platform.astr_message_event import AstrMessageEvent


class CommandGroupFilter(HandlerFilter):
    """指令组过滤器

    用于处理指令组（如 `cmdmgr on`、`cmdmgr off`、`cmdmgr status`）。
    支持嵌套指令组和自定义过滤器。
    """

    def __init__(
        self,
        group_name: str,
        alias: set | None = None,
        parent_group: CommandGroupFilter | None = None,
    ) -> None:
        self.group_name = group_name
        # 字符串 alias 视为单个别名，避免拆成字符
        if isinstance(alias, str):
            alias = {alias}
        self.alias = alias if alias else set()
        self._original_group_name = group_name
        self.sub_command_filters: list[CommandFilter | CommandGroupFilter] = []
        self.custom_filter_list: list[HandlerFilter] = []
        self.parent_group = parent_group
        self._cmpl_cmd_names: list | None = None

    def add_sub_command_filter(
        self,
        sub_command_filter: CommandFilter | CommandGroupFilter,
    ) -> None:
        """添加子指令过滤器"""
        self.sub_command_filters.append(sub_command_filter)

    def add_custom_filter(self, custom_filter: HandlerFilter) -> None:
        """添加自定义过滤器"""
        self.custom_filter_list.append(custom_filter)

    def get_complete_command_names(self) -> list[str]:
        """遍历父节点获取完整的指令名列表（含别名）"""
        if self._cmpl_cmd_names is not None:
            return self._cmpl_cmd_names

        parent_cmd_names = (
            self.parent_group.get_complete_command_names() if self.parent_group else []
        )

        if not parent_cmd_names:
            # 根节点
            result = [self.group_name] + list(self.alias)
            self._cmpl_cmd_names = result
            return result

        result = []
        candidates = [self.group_name] + list(self.alias)
        for parent_cmd_name in parent_cmd_names:
            for candidate in candidates:
                result.append(parent_cmd_name + " " + candidate)

        self._cmpl_cmd_names = result
        return result

    def print_cmd_tree(
        self,
        sub_command_filters: list[CommandFilter | CommandGroupFilter] | None = None,
        prefix: str = "",
        event: AstrMessageEvent | None = None,
        cfg: dict | None = None,
    ) -> str:
        """以树形结构打印指令组"""
        if sub_command_filters is None:
            sub_command_filters = self.sub_command_filters

        parts = []
        for sub_filter in sub_command_filters:
            if isinstance(sub_filter, CommandFilter):
                custom_filter_pass = True
                if event and cfg:
                    try:
                        custom_filter_pass = sub_filter.custom_filter_ok(event, cfg)
                    except Exception:
                        pass
                if custom_filter_pass:
                    cmd_th = sub_filter.print_types()
                    line = f"{prefix}├── {sub_filter.command_name}"
                    if cmd_th:
                        line += f" ({cmd_th})"
                    else:
                        line += " (无参数指令)"
                    if sub_filter.handler_md and sub_filter.handler_md.desc:
                        line += f": {sub_filter.handler_md.desc}"
                    parts.append(line + "\n")
            elif isinstance(sub_filter, CommandGroupFilter):
                custom_filter_pass = True
                if event and cfg:
                    try:
                        custom_filter_pass = sub_filter.custom_filter_ok(event, cfg)
                    except Exception:
                        pass
                if custom_filter_pass:
                    parts.append(f"{prefix}├── {sub_filter.group_name}\n")
                    parts.append(
                        sub_filter.print_cmd_tree(
                            sub_filter.sub_command_filters,
                            prefix + "│   ",
                            event=event,
                            cfg=cfg,
                        )
                    )
        return "".join(parts)

    def custom_filter_ok(self, event: AstrMessageEvent, cfg: dict) -> bool:
        """检查所有自定义过滤器是否通过"""
        for custom_filter in self.custom_filter_list:
            try:
                if not custom_filter.filter(event, cfg):
                    return False
            except Exception:
                return False
        return True

    def startswith(self, message_str: str) -> bool:
        """检查消息是否以本指令组的任何指令名开头"""
        return message_str.startswith(tuple(self.get_complete_command_names()))

    def equals(self, message_str: str) -> bool:
        """检查消息是否等于本指令组的任何指令名"""
        return message_str in self.get_complete_command_names()

    def filter(self, event: AstrMessageEvent, cfg: dict) -> bool:
        """过滤器主逻辑

        1. 检查是否唤醒消息
        2. 检查自定义过滤器
        3. 去除唤醒前缀
        4. 如果消息等于指令组名（无子命令），抛出参数不足错误
        5. 如果消息以指令组名开头，为子命令匹配做准备
        """
        if not event.is_at_or_wake_command:
            return False

        # 判断当前指令组的自定义过滤器
        if not self.custom_filter_ok(event, cfg):
            return False

        # 去除唤醒前缀（如 / 或 !）
        message_str = event.message_str.strip()
        wake_prefixes = getattr(event, 'wake_prefixes', ['/', '!'])
        for prefix in wake_prefixes:
            if message_str.startswith(prefix):
                message_str = message_str[len(prefix):].strip()
                break

        if self.equals(message_str):
            tree = (
                self.group_name
                + "\n"
                + self.print_cmd_tree(self.sub_command_filters, event=event, cfg=cfg)
            )
            raise ValueError(
                f"参数不足。{self.group_name} 指令组下有如下指令，请参考：\n" + tree,
            )

        return self.startswith(message_str)