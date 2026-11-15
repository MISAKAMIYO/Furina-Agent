from __future__ import annotations
import inspect
import logging
import re
import types
import typing
from typing import Any

from ..star_handler import StarHandlerMetadata
from . import HandlerFilter

logger = logging.getLogger("ZhiYanWeb")


class GreedyStr(str):
    """标记指令完成其他参数接收后的所有剩余文本。"""


def unwrap_optional(annotation) -> tuple:
    """去掉 Optional[T] / Union[T, None] / T|None，返回 T"""
    args = typing.get_args(annotation)
    non_none_args = [a for a in args if a is not type(None)]
    if len(non_none_args) == 1:
        return (non_none_args[0],)
    if len(non_none_args) > 1:
        return tuple(non_none_args)
    return ()


def _is_union_type(obj) -> bool:
    """判断类型是否为联合类型（Union/Optional 或 PEP 604 `X | Y`）。

    兼容 Python 3.8：`types.UnionType` 需 3.10+ 才有，通过短路保护避免引用。
    """
    if typing.get_origin(obj) is typing.Union:
        return True
    _union_type = getattr(types, "UnionType", None)
    return _union_type is not None and isinstance(obj, _union_type)


class CommandFilter(HandlerFilter):
    def __init__(
        self,
        command_name: str,
        alias: set | None = None,
        handler_md: StarHandlerMetadata | None = None,
        parent_command_names: list[str] | None = None,
    ) -> None:
        self.command_name = command_name
        # 字符串 alias 视为单个别名（兼容插件将描述文字误传为 alias 的写法），避免拆成字符
        if isinstance(alias, str):
            alias = {alias}
        self.alias = alias if alias else set()
        self._original_command_name = command_name
        self.parent_command_names = parent_command_names if parent_command_names is not None else [""]
        self.custom_filter_list: list[HandlerFilter] = []
        self._cmpl_cmd_names: list | None = None
        if handler_md:
            self.init_handler_md(handler_md)

    def init_handler_md(self, handle_md: StarHandlerMetadata) -> None:
        self.handler_md = handle_md
        signature = inspect.signature(self.handler_md.handler)
        self.handler_params = {}
        idx = 0
        for k, v in signature.parameters.items():
            if idx < 2:
                idx += 1
                continue
            if v.default == inspect.Parameter.empty:
                # 无默认值的参数：记录类型注解
                self.handler_params[k] = v.annotation if v.annotation is not inspect.Parameter.empty else str
            else:
                self.handler_params[k] = v.default

    def get_complete_command_names(self):
        if self._cmpl_cmd_names is not None:
            return self._cmpl_cmd_names
        self._cmpl_cmd_names = [
            f"{parent} {cmd}" if parent else cmd
            for cmd in [self.command_name] + list(self.alias)
            for parent in self.parent_command_names or [""]
        ]
        return self._cmpl_cmd_names

    def get_handler_md(self):
        """获取关联的 Handler 元数据"""
        return getattr(self, 'handler_md', None)

    def add_custom_filter(self, custom_filter: HandlerFilter) -> None:
        """添加自定义过滤器"""
        self.custom_filter_list.append(custom_filter)

    def custom_filter_ok(self, event, cfg: dict) -> bool:
        """检查所有自定义过滤器是否通过"""
        for custom_filter in self.custom_filter_list:
            if not custom_filter.filter(event, cfg):
                return False
        return True

    def print_types(self) -> str:
        """打印命令参数类型描述"""
        if not hasattr(self, 'handler_params') or not self.handler_params:
            return ""
        parts = []
        for k, v in self.handler_params.items():
            if isinstance(v, type):
                parts.append(f"{k}({v.__name__}),")
            elif _is_union_type(v):
                parts.append(f"{k}({v}),")
            else:
                parts.append(f"{k}({type(v).__name__})={v},")
        result = "".join(parts).rstrip(",")
        return result

    def equals(self, message_str: str) -> bool:
        """检查消息是否等于任何完整命令名"""
        for full_cmd in self.get_complete_command_names():
            if message_str == full_cmd:
                return True
        return False

    def validate_and_convert_params(
        self,
        params: list[Any],
        param_type: dict[str, type],
    ) -> dict[str, Any]:
        """将参数列表 params 根据 param_type 转换为参数字典。"""
        result = {}
        if not param_type:
            return result
        param_items = list(param_type.items())
        for i, (param_name, param_type_or_default_val) in enumerate(param_items):
            is_greedy = param_type_or_default_val is GreedyStr

            if is_greedy:
                # GreedyStr 必须是最后一个参数
                if i != len(param_items) - 1:
                    raise ValueError(
                        f"参数 '{param_name}' (GreedyStr) 必须是最后一个参数。",
                    )
                # 将剩余的所有部分合并成一个字符串
                remaining_params = params[i:]
                result[param_name] = " ".join(remaining_params)
                break

            if i >= len(params):
                if _is_union_type(param_type_or_default_val):
                    raise ValueError(
                        f"必要参数缺失。该指令完整参数: {self.print_types()}",
                    )
                # 有默认值
                result[param_name] = param_type_or_default_val
            else:
                try:
                    if param_type_or_default_val is None:
                        result[param_name] = int(params[i]) if params[i].isdigit() else params[i]
                    elif isinstance(param_type_or_default_val, str):
                        result[param_name] = params[i]
                    elif isinstance(param_type_or_default_val, bool):
                        lower_param = str(params[i]).lower()
                        if lower_param in ["true", "yes", "1"]:
                            result[param_name] = True
                        elif lower_param in ["false", "no", "0"]:
                            result[param_name] = False
                        else:
                            raise ValueError(
                                f"参数 {param_name} 必须是布尔值（true/false, yes/no, 1/0）。",
                            )
                    elif isinstance(param_type_or_default_val, int):
                        result[param_name] = int(params[i])
                    elif isinstance(param_type_or_default_val, float):
                        result[param_name] = float(params[i])
                    else:
                        origin = typing.get_origin(param_type_or_default_val)
                        if _is_union_type(param_type_or_default_val):
                            nn_types = unwrap_optional(param_type_or_default_val)
                            if len(nn_types) == 1:
                                result[param_name] = nn_types[0](params[i])
                            else:
                                result[param_name] = params[i]
                        else:
                            result[param_name] = param_type_or_default_val(params[i])
                except (ValueError, TypeError, IndexError) as e:
                    raise ValueError(
                        f"参数 {param_name} 类型错误: {e}。完整参数: {self.print_types()}",
                    )
        return result

    def filter(self, event, cfg: dict) -> bool:
        if not event.is_at_or_wake_command:
            logger.debug(f"CommandFilter[{self.command_name}]: 跳过 - 非唤醒命令")
            return False

        if not self.custom_filter_ok(event, cfg):
            logger.debug(f"CommandFilter[{self.command_name}]: 跳过 - 自定义过滤器未通过")
            return False

        message_str = re.sub(r"\s+", " ", event.get_message_str().strip())
        # 去除唤醒前缀（如 / 或 !）
        wake_prefixes = getattr(event, 'wake_prefixes', ['/', '!'])
        for prefix in wake_prefixes:
            if message_str.startswith(prefix):
                message_str = message_str[len(prefix):].strip()
                break
        ok = False
        matched_cmd = None
        for full_cmd in self.get_complete_command_names():
            if message_str.startswith(f"{full_cmd} ") or message_str == full_cmd:
                ok = True
                matched_cmd = full_cmd
                message_str = message_str[len(full_cmd):].strip()
                break
        if not ok:
            logger.debug(f"CommandFilter[{self.command_name}]: 跳过 - 命令名不匹配 "
                         f"(处理后消息={message_str!r}, 候选命令={self.get_complete_command_names()})")
            return False
        ls = [param for param in message_str.split(" ") if param]
        handler_params = getattr(self, 'handler_params', {})
        try:
            params = self.validate_and_convert_params(ls, handler_params)
        except ValueError as e:
            logger.warning(f"CommandFilter[{self.command_name}]: 参数解析失败: {e}")
            return False
        event.set_extra("parsed_params", params)
        logger.debug(f"CommandFilter[{self.command_name}]: 匹配成功 (命令={matched_cmd!r}, 参数={ls}, handler_params={handler_params})")
        return True