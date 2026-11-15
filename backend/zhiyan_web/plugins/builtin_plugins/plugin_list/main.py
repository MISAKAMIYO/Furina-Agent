"""
plugin_list - 插件列表图片生成插件

功能:
1. 自动扫描系统中所有已安装的插件及其命令
2. 将插件名称、命令列表、功能描述进行结构化整理
3. 生成直观的插件命令列表图片
4. 支持图片格式自定义（尺寸、字体、颜色主题等）
5. 支持图片导出（保存到本地或返回路径）

兼容性:
- 完全兼容 AstrBot 插件接口规范
- 支持所有通过 Star 系统注册的插件
"""

from __future__ import annotations

import os
import json
import logging

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.core.star.filter.command import CommandFilter
# 直接导入真实实现，避免 astrbot 兼容层的 CommandGroupFilter 存根缺少 group_name 属性
from zhiyan_web.star.filter.command_group import CommandGroupFilter
from astrbot.core.star.star_handler import star_handlers_registry, StarHandlerMetadata

from zhiyan_web.config import DATA_DIR
from .draw import generate_plugin_list_image, render_preset_image

logger = logging.getLogger("ZhiYanWeb.plugin_list")

# 专用配置文件路径：data/plugin_configs/plugin_list_config.json
_CONFIG_FILE = os.path.join(DATA_DIR, "plugin_configs", "plugin_list_config.json")


def _load_schema() -> dict:
    """从同目录 _conf_schema.json 读取插件配置 schema（类型/默认值/说明）。"""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_conf_schema.json")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("加载 plugin_list 配置 schema 失败: %s", e)
        return {}


def _schema_defaults(schema: dict) -> dict:
    """依据 schema 构造缺省配置。"""
    out = {}
    for key, field in schema.items():
        if isinstance(field, dict):
            out[key] = field.get("default")
    return out


def _coerce_config(raw: dict, schema: dict) -> dict:
    """按 schema 类型把配置值规范化（缺失补默认、类型转换失败回退默认）。"""
    if not isinstance(raw, dict):
        raw = {}
    result = {}
    for key, field in schema.items():
        if not isinstance(field, dict):
            continue
        default = field.get("default")
        ftype = field.get("type", "str")
        value = raw.get(key, default)
        try:
            if ftype == "int":
                result[key] = int(value) if value is not None else default
            elif ftype == "bool":
                if isinstance(value, bool):
                    result[key] = value
                else:
                    result[key] = str(value).lower() in ("true", "1", "yes", "是", "y", "on") if value is not None else default
            elif ftype == "float":
                result[key] = float(value) if value is not None else default
            else:
                result[key] = str(value) if value is not None else (default or "")
        except (ValueError, TypeError):
            result[key] = default
    return result


@register("plugin_list", "ZhiYanWeb", "生成系统中所有插件的命令列表图片，支持自定义尺寸、颜色主题和导出", "v1.0.0")
class PluginListPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._schema = _load_schema()
        self._config = self._load_config()

    async def initialize(self):
        """插件初始化时加载配置"""
        self._schema = _load_schema()
        self._config = self._load_config()
        logger.info("plugin_list 插件已初始化，配置项 %d 个", len(self._schema))

    def _load_config(self) -> dict:
        """加载插件配置：合并内置 schema 默认值、专用配置文件与全局配置。

        优先级：专用配置文件 data/plugin_configs/plugin_list_config.json
        > 全局配置中的 plugin_list_config 区块 > schema 默认值。
        """
        merged = _schema_defaults(self._schema)

        # 1) 读取专用配置文件
        try:
            if os.path.exists(_CONFIG_FILE):
                with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    merged.update(_coerce_config(data, self._schema))
        except Exception as e:
            logger.warning("读取 plugin_list 配置文件失败: %s", e)

        # 2) 尽力兼容旧版：从全局 context 配置读取 plugin_list_config 区块
        try:
            global_cfg = self.context.get_config() or {}
            legacy = global_cfg.get("plugin_list_config", {})
            if isinstance(legacy, dict):
                for k, v in legacy.items():
                    if k in merged:
                        merged[k] = v
        except Exception:
            pass

        return _coerce_config(merged, self._schema)

    def _get_config_value(self, key: str, default=None):
        """获取配置值"""
        return self._config.get(key, default)

    def _save_config(self):
        """持久化配置：写入专用配置文件，并尽量同步到全局/星管理器。"""
        try:
            os.makedirs(os.path.dirname(_CONFIG_FILE), exist_ok=True)
            with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("保存 plugin_list 配置失败: %s", e)
        # 尽力同步到 context 全局配置
        try:
            g = self.context.get_config() or {}
            g["plugin_list_config"] = dict(self._config)
            self.context.set_config(g)
        except Exception:
            pass

    @staticmethod
    def _platform(event) -> str:
        """识别当前消息平台（qq → 用媒体标记；其余 → markdown）。"""
        try:
            name = (event.get_platform_name() or "").lower()
        except Exception:
            name = "webchat"
        return "qq" if name in ("qq", "onebot", "napcat") else "webchat"

    @staticmethod
    def _media_text(platform: str, path: str, url: str, caption: str) -> str:
        """按平台生成可识别的图片消息文本。"""
        if platform == "qq" and path:
            text = "[图片:{}]".format(path)
            return (text + "\n" + caption) if caption else text
        md = "![插件命令列表]({})".format(url) if url else (path or "")
        if caption:
            md += "\n\n{}".format(caption)
        return md

    @filter.command("plugin_list", alias={"插件列表", "命令列表", "所有插件", "plugins"})
    async def plugin_list(self, event: AstrMessageEvent) -> MessageEventResult:
        """生成插件命令列表图片"""
        platform = self._platform(event)
        try:
            # 收集插件数据
            plugin_data = self._collect_plugin_data()
            if not plugin_data:
                # 以图代文：生成"无插件"预置卡片
                preset = render_preset_image("未找到已安装的插件", "可前往插件市场安装更多插件", config=self._config)
                if preset:
                    url = "/api/plugin_list/image/{}".format(os.path.basename(preset))
                    yield event.plain_result(self._media_text(
                        platform, preset, url, "当前没有已安装的插件，可前往插件市场安装。"))
                    return
                yield event.plain_result("没有找到已安装的插件")
                return

            # 支持 max_plugins 限制展示数量
            max_plugins = self._get_config_value("max_plugins", 0) or 0
            shown = plugin_data
            if max_plugins > 0 and len(shown) > max_plugins:
                shown = plugin_data[:max_plugins]

            # 构建绘图配置（由 schema 驱动的配置一致应用到渲染）
            draw_config = dict(self._config)
            draw_config.setdefault("image_width", 800)
            draw_config.setdefault("image_height_per_plugin", 120)
            draw_config.setdefault("font_size_title", 28)
            draw_config.setdefault("font_size_plugin_name", 18)
            draw_config.setdefault("font_size_command", 14)
            draw_config.setdefault("font_size_desc", 12)
            draw_config.setdefault("bg_color", "#F5F5F5")
            draw_config.setdefault("card_color", "#FFFFFF")
            draw_config.setdefault("text_color", "#333333")
            draw_config.setdefault("accent_color", "#4A90D9")
            draw_config.setdefault("border_color", "#E0E0E0")
            draw_config.setdefault("show_plugin_version", True)
            draw_config.setdefault("show_plugin_author", False)
            draw_config.setdefault("show_command_count", False)
            draw_config.setdefault("default_font", "")
            draw_config.setdefault("output_format", "png")
            draw_config.setdefault("output_quality", 95)
            draw_config.setdefault("footer_note", "")

            # 生成图片
            image_path = generate_plugin_list_image(shown, draw_config)
            if not image_path:
                preset = render_preset_image("生成插件列表图片失败", "请查看后端日志", config=self._config)
                if preset:
                    url = "/api/plugin_list/image/{}".format(os.path.basename(preset))
                    yield event.plain_result(self._media_text(
                        platform, preset, url, "生成插件列表图片失败，请检查后端日志。"))
                    return
                yield event.plain_result("生成插件列表图片失败，请检查日志")
                return

            # 构造 Web 可访问的图片 URL
            filename = os.path.basename(image_path)
            image_url = f"/api/plugin_list/image/{filename}"

            caption = "📋 共 {} 个插件{}".format(
                len(shown),
                "" if len(shown) >= len(plugin_data) else "（{} 个未展示，max_plugins={}）".format(
                    len(plugin_data) - len(shown), max_plugins),
            )
            caption += "\n💡 使用 /plugin_list_config 自定义图片样式"
            yield event.plain_result(self._media_text(platform, image_path, image_url, caption))

        except Exception as e:
            logger.error(f"生成插件列表失败: {e}", exc_info=True)
            try:
                preset = render_preset_image("生成插件列表失败", str(e)[:40], config=self._config)
                if preset:
                    url = "/api/plugin_list/image/{}".format(os.path.basename(preset))
                    yield event.plain_result(self._media_text(
                        platform, preset, url, "生成插件列表失败: {}".format(e)))
                    return
            except Exception:
                pass
            yield event.plain_result(f"生成插件列表失败: {str(e)}")

    @filter.command("plugin_list_config", alias={"插件列表配置", "列表配置"})
    async def plugin_list_config(self, event: AstrMessageEvent) -> MessageEventResult:
        """查看/修改插件列表图片生成配置"""
        message = event.get_message_str().strip()
        parts = message.split(maxsplit=2)

        # 仅输入命令，显示当前配置
        if len(parts) <= 1:
            config_text = self._format_config_display()
            yield event.plain_result(config_text)
            return

        # 尝试设置配置项
        if len(parts) >= 2 and parts[1].lower() in ("set", "设置", "修改"):
            if len(parts) < 3:
                yield event.plain_result("用法: /plugin_list_config set <键> <值>\n"
                                        "示例: /plugin_list_config set image_width 1000\n"
                                        "示例: /plugin_list_config set bg_color #F0F0F0")
                return

            set_parts = parts[2].split(maxsplit=1)
            if len(set_parts) < 2:
                yield event.plain_result("请指定键和值，例如: /plugin_list_config set image_width 1000")
                return

            key = set_parts[0].strip()
            value = set_parts[1].strip()
            result = self._set_config_value(key, value)
            yield event.plain_result(result)
            return

        # 显示配置（即 reset/默认行为）
        if parts[1].lower() in ("reset", "重置", "默认"):
            self._config = {}
            self._save_config()
            yield event.plain_result("✅ 配置已重置为默认值")
            return

        yield event.plain_result(self._format_config_display())

    def _collect_plugin_data(self) -> list[dict]:
        """收集所有已安装插件的名称、命令列表和描述信息

        返回:
            list[dict]: 每个元素包含:
                - name: 插件名称
                - desc: 插件描述
                - version: 版本号
                - author: 作者
                - commands: 命令列表
        """
        plugin_data = []
        try:
            all_stars = self.context.get_all_stars()
            activated_stars = [s for s in all_stars if s.activated]
        except Exception as e:
            logger.error(f"获取插件列表失败: {e}")
            return []

        if not activated_stars:
            logger.warning("没有找到已激活的插件")
            return []

        for star in activated_stars:
            plugin_name = getattr(star, "name", "未知插件")
            plugin_instance = getattr(star, "star_cls", None)
            module_path = getattr(star, "module_path", "")

            # 跳过自身
            if plugin_instance is self:
                continue

            # 跳过核心插件
            if plugin_name in ("astrbot", "plugin_list", "astrbot-reminder"):
                continue

            # 验证插件元数据
            if not plugin_name or not module_path:
                logger.warning(f"插件 '{plugin_name}' 元数据不完整，已跳过")
                continue

            # 收集该插件的所有命令
            commands = []
            for handler in star_handlers_registry:
                if not isinstance(handler, StarHandlerMetadata):
                    continue
                if handler.handler_module_path != module_path:
                    continue

                command_name = None
                handler_desc = handler.desc

                for hfilter in handler.event_filters:
                    if isinstance(hfilter, CommandFilter):
                        command_name = hfilter.command_name
                        break
                    elif isinstance(hfilter, CommandGroupFilter):
                        command_name = hfilter.group_name
                        break

                if command_name:
                    if handler_desc:
                        formatted = f"{command_name}#{handler_desc}"
                    else:
                        formatted = command_name
                    if formatted not in commands:
                        commands.append(formatted)

            # 构建插件数据
            plugin_info = {
                "name": plugin_name,
                "desc": getattr(star, "desc", "") or "",
                "version": getattr(star, "version", "") or "",
                "author": getattr(star, "author", "") or "",
                "commands": commands,
            }
            plugin_data.append(plugin_info)

        # 按插件名称排序
        plugin_data.sort(key=lambda x: x["name"].lower())
        return plugin_data

    def _format_config_display(self) -> str:
        """格式化显示当前配置"""
        lines = [
            "📋 插件列表图片配置",
            "=" * 30,
            f"📐 图片宽度: {self._get_config_value('image_width', 800)}px",
            f"📏 插件区块高度: {self._get_config_value('image_height_per_plugin', 120)}px",
            "",
            "🔤 字体设置:",
            f"  标题: {self._get_config_value('font_size_title', 28)}px",
            f"  插件名: {self._get_config_value('font_size_plugin_name', 18)}px",
            f"  命令: {self._get_config_value('font_size_command', 14)}px",
            f"  描述: {self._get_config_value('font_size_desc', 12)}px",
            f"  自定义字体: {self._get_config_value('default_font', '（默认）')}",
            "",
            "🎨 颜色主题:",
            f"  背景: {self._get_config_value('bg_color', '#F5F5F5')}",
            f"  卡片: {self._get_config_value('card_color', '#FFFFFF')}",
            f"  文字: {self._get_config_value('text_color', '#333333')}",
            f"  强调色: {self._get_config_value('accent_color', '#4A90D9')}",
            f"  边框: {self._get_config_value('border_color', '#E0E0E0')}",
            "",
            "⚙️ 其他:",
            f"  显示版本号: {'是' if self._get_config_value('show_plugin_version', True) else '否'}",
            f"  显示作者: {'是' if self._get_config_value('show_plugin_author', False) else '否'}",
            f"  显示命令数: {'是' if self._get_config_value('show_command_count', False) else '否'}",
            f"  最多展示插件数: {self._get_config_value('max_plugins', 0)}（0 不限制）",
            f"  输出格式: {self._get_config_value('output_format', 'png')}",
            f"  图片质量: {self._get_config_value('output_quality', 95)}",
            f"  底部备注: {self._get_config_value('footer_note', '') or '（空）'}",
            "",
            "💡 修改配置:",
            "  /plugin_list_config set <键> <值>",
            "  /plugin_list_config reset - 重置为默认",
            "",
            "📝 示例:",
            "  /plugin_list_config set image_width 1000",
            "  /plugin_list_config set bg_color #F0F0F0",
            "  /plugin_list_config set accent_color #E74C3C",
        ]
        return "\n".join(lines)

    def _set_config_value(self, key: str, value: str) -> str:
        """设置配置项并返回结果信息（依据 _conf_schema.json 校验类型）"""
        if key not in self._schema:
            valid_keys = "\n".join("  - {}".format(k) for k in self._schema.keys())
            return "❌ 未知配置项: {}\n有效配置项:\n{}".format(key, valid_keys)

        ftype = self._schema[key].get("type", "str")
        try:
            if ftype == "bool":
                parsed = str(value).lower() in ("true", "1", "yes", "是", "y", "on")
            elif ftype == "int":
                parsed = int(value)
            elif ftype == "float":
                parsed = float(value)
            elif ftype == "list":
                parsed = [x.strip() for x in str(value).split(",") if x.strip()]
            else:
                parsed = str(value)
        except (ValueError, TypeError) as e:
            return "❌ 值类型错误: {} 需要 {} 类型\n错误: {}".format(key, ftype, e)

        self._config[key] = parsed
        self._save_config()
        return "✅ 已设置 {} = {}".format(key, parsed)

    async def terminate(self):
        """插件卸载时清理"""
        logger.info("plugin_list 插件已卸载")