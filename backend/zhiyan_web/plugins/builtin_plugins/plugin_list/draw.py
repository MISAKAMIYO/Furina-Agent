"""
插件列表图片生成模块
使用 Pillow 绘制插件命令列表图片，支持自定义样式
"""

from __future__ import annotations

import os
import logging
from datetime import datetime
from typing import Optional

try:
    from PIL import Image, ImageDraw, ImageFont
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from zhiyan_web.config import DATA_DIR

logger = logging.getLogger("ZhiYanWeb.plugin_list")

# 输出目录
OUTPUT_DIR = None


def _get_output_dir() -> str:
    """获取图片输出目录"""
    global OUTPUT_DIR
    if OUTPUT_DIR is None:
        OUTPUT_DIR = os.path.join(DATA_DIR, "plugin_list_images")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return OUTPUT_DIR


def _hex_to_rgb(hex_color: str) -> tuple:
    """将十六进制颜色转换为 RGB 元组"""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def _hex_to_rgba(hex_color: str, alpha: int = 255) -> tuple:
    """将十六进制颜色转换为 RGBA 元组"""
    r, g, b = _hex_to_rgb(hex_color)
    return (r, g, b, alpha)


def _load_font(font_path: str, size: int, default_font_path: str = "") -> ImageFont.FreeTypeFont:
    """加载字体，支持 fallback"""
    paths_to_try = []
    if font_path and os.path.exists(font_path):
        paths_to_try.append(font_path)
    if default_font_path and os.path.exists(default_font_path):
        paths_to_try.append(default_font_path)
    # 尝试常见中文字体路径
    common_fonts = [
        "C:/Windows/Fonts/msyh.ttc",       # 微软雅黑
        "C:/Windows/Fonts/simhei.ttf",      # 黑体
        "C:/Windows/Fonts/simsun.ttc",      # 宋体
        "C:/Windows/Fonts/yahei.ttf",       # 微软雅黑（备选）
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    paths_to_try.extend(common_fonts)
    for p in paths_to_try:
        try:
            font = ImageFont.truetype(p, size, encoding="unic")
            return font
        except Exception:
            continue
    # 最后使用默认字体
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _draw_rounded_rectangle(draw: ImageDraw.Draw, xy: tuple, radius: int, fill: tuple, outline: tuple = None, width: int = 1):
    """绘制圆角矩形"""
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _draw_gradient_background(draw: ImageDraw.Draw, width: int, height: int, color_top: str, color_bottom: str):
    """绘制渐变背景"""
    top_rgb = _hex_to_rgb(color_top)
    bottom_rgb = _hex_to_rgb(color_bottom)
    for y in range(height):
        ratio = y / height
        r = int(top_rgb[0] * (1 - ratio) + bottom_rgb[0] * ratio)
        g = int(top_rgb[1] * (1 - ratio) + bottom_rgb[1] * ratio)
        b = int(top_rgb[2] * (1 - ratio) + bottom_rgb[2] * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int, draw: ImageDraw.Draw) -> list[str]:
    """将文本按宽度换行，返回多行文本列表"""
    if not text:
        return [""]
    lines = []
    # 先按自然换行分割
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        # 测量每个字符的宽度
        chars = list(paragraph)
        current_line = ""
        for char in chars:
            test_line = current_line + char
            bbox = draw.textbbox((0, 0), test_line, font=font)
            w = bbox[2] - bbox[0]
            if w <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = char
        if current_line:
            lines.append(current_line)
    return lines if lines else [""]


def _format_commands(plugin_name: str, commands: list, plugin_desc: str,
                     plugin_version: str, plugin_author: str,
                     config: dict) -> str:
    """格式化插件命令信息为可读文本"""
    parts = []
    parts.append(f"📦 {plugin_name}")
    if config.get("show_plugin_version", True) and plugin_version:
        parts[0] += f"  v{plugin_version}"
    if config.get("show_plugin_author", False) and plugin_author:
        parts[0] += f"  👤 {plugin_author}"
    if plugin_desc:
        parts.append(f"   {plugin_desc}")
    if commands:
        parts.append(f"   命令: {', '.join(commands)}")
    return "\n".join(parts)


def generate_plugin_list_image(
    plugin_data: list[dict],
    config: dict = None,
    output_path: str = None,
) -> Optional[str]:
    """
    生成插件列表图片

    参数:
        plugin_data: 插件数据列表，每个元素为 dict，包含:
            - name: 插件名称
            - desc: 插件描述
            - version: 版本号
            - author: 作者
            - commands: 命令列表
        config: 配置字典，支持 _conf_schema.json 中的所有字段
        output_path: 输出路径，留空则自动生成

    返回:
        str: 生成的图片路径，失败返回 None
    """
    if not HAS_PIL:
        logger.error("Pillow 未安装，无法生成图片")
        return None

    if not plugin_data:
        logger.warning("没有插件数据，无法生成图片")
        return None

    # 合并配置
    cfg = {
        "image_width": 800,
        "image_height_per_plugin": 120,
        "font_size_title": 28,
        "font_size_plugin_name": 18,
        "font_size_command": 14,
        "font_size_desc": 12,
        "bg_color": "#F5F5F5",
        "card_color": "#FFFFFF",
        "text_color": "#333333",
        "accent_color": "#4A90D9",
        "border_color": "#E0E0E0",
        "show_plugin_version": True,
        "show_plugin_author": False,
        "default_font": "",
        "output_format": "png",
        "output_quality": 95,
    }
    if config:
        cfg.update(config)

    try:
        width = cfg["image_width"]
        per_plugin_height = cfg["image_height_per_plugin"]
        title_font_size = cfg["font_size_title"]
        plugin_name_font_size = cfg["font_size_plugin_name"]
        cmd_font_size = cfg["font_size_command"]
        desc_font_size = cfg["font_size_desc"]

        # 加载字体
        title_font = _load_font(cfg["default_font"], title_font_size)
        plugin_name_font = _load_font(cfg["default_font"], plugin_name_font_size)
        cmd_font = _load_font(cfg["default_font"], cmd_font_size)
        desc_font = _load_font(cfg["default_font"], desc_font_size)

        # 计算图片高度
        padding_top = 60       # 顶部留白
        padding_bottom = 40    # 底部留白
        title_height = 50      # 标题区域高度
        card_margin = 10       # 卡片间距
        card_padding = 15      # 卡片内边距

        total_plugins = len(plugin_data)
        total_height = padding_top + title_height + total_plugins * (per_plugin_height + card_margin) + padding_bottom

        # 创建图片
        bg_color = _hex_to_rgb(cfg["bg_color"])
        if cfg["output_format"].lower() in ("jpeg", "jpg"):
            img = Image.new("RGB", (width, total_height), bg_color)
        else:
            img = Image.new("RGBA", (width, total_height), (*bg_color, 255))
        draw = ImageDraw.Draw(img)

        # 绘制微渐变背景
        try:
            _draw_gradient_background(draw, width, total_height, cfg["bg_color"], "#EBEBEB")
        except Exception:
            pass

        y_offset = padding_top

        # 绘制标题
        title_text = "📋 插件命令列表"
        title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
        title_w = title_bbox[2] - title_bbox[0]
        title_x = (width - title_w) // 2
        title_y = y_offset
        # 标题阴影
        shadow_color = _hex_to_rgba(cfg["text_color"], 30)
        draw.text((title_x + 2, title_y + 2), title_text, font=title_font, fill=shadow_color)
        # 标题主文字
        draw.text((title_x, title_y), title_text, font=title_font, fill=_hex_to_rgb(cfg["accent_color"]))

        # 副标题：插件数量
        subtitle_text = f"共 {total_plugins} 个插件"
        subtitle_font = _load_font(cfg["default_font"], 14)
        if subtitle_font:
            sub_bbox = draw.textbbox((0, 0), subtitle_text, font=subtitle_font)
            sub_w = sub_bbox[2] - sub_bbox[0]
            sub_x = (width - sub_w) // 2
            draw.text((sub_x, title_y + title_font_size + 5), subtitle_text, font=subtitle_font,
                      fill=_hex_to_rgb(cfg["text_color"]))

        y_offset += title_height + 10

        # 绘制每个插件卡片
        text_color = _hex_to_rgb(cfg["text_color"])
        accent_color = _hex_to_rgb(cfg["accent_color"])
        card_color = _hex_to_rgb(cfg["card_color"])
        border_color = _hex_to_rgb(cfg["border_color"])

        for idx, plugin in enumerate(plugin_data):
            card_x = 20
            card_y = y_offset
            card_w = width - 40
            card_h = per_plugin_height

            # 绘制卡片背景
            _draw_rounded_rectangle(
                draw,
                (card_x, card_y, card_x + card_w, card_y + card_h),
                radius=10,
                fill=card_color,
                outline=border_color,
                width=1,
            )

            # 左侧彩色指示条
            indicator_colors = ["#4A90D9", "#E74C3C", "#2ECC71", "#F39C12", "#9B59B6", "#1ABC9C", "#E67E22", "#3498DB"]
            indicator_color = indicator_colors[idx % len(indicator_colors)]
            _draw_rounded_rectangle(
                draw,
                (card_x + 3, card_y + 8, card_x + 6, card_y + card_h - 8),
                radius=3,
                fill=_hex_to_rgb(indicator_color),
            )

            # 插件名称
            name_text = plugin.get("name", "未知插件")
            version_text = ""
            if cfg.get("show_plugin_version", True):
                ver = plugin.get("version", "")
                if ver:
                    version_text = f"v{ver}"

            author_text = ""
            if cfg.get("show_plugin_author", False):
                author = plugin.get("author", "")
                if author:
                    author_text = f"👤 {author}"

            # 绘制插件名称 + 版本
            nx = card_x + 20
            ny = card_y + 12
            draw.text((nx, ny), name_text, font=plugin_name_font, fill=accent_color)

            # 版本号
            if version_text:
                version_font = _load_font(cfg["default_font"], 12)
                if version_font:
                    vx = nx + (plugin_name_font_size * len(name_text) // 2) + 10
                    draw.text((vx, ny + 3), version_text, font=version_font,
                              fill=_hex_to_rgb(cfg["border_color"]))

            # 作者
            if author_text:
                author_font = _load_font(cfg["default_font"], 12)
                if author_font:
                    ax = nx
                    ay = ny + plugin_name_font_size + 2
                    draw.text((ax, ay), author_text, font=author_font, fill=text_color)

            # 描述
            desc = plugin.get("desc", "")
            if desc:
                desc_y_base = ny + plugin_name_font_size + 2
                if author_text:
                    desc_y_base += 18
                desc_font_actual = _load_font(cfg["default_font"], desc_font_size)
                if desc_font_actual:
                    desc_lines = _wrap_text(desc, desc_font_actual, card_w - 60, draw)
                    for i, line in enumerate(desc_lines):
                        if len(line) > 0:
                            draw.text((nx, desc_y_base + i * (desc_font_size + 4)), line,
                                      font=desc_font_actual, fill=text_color)
                        else:
                            draw.text((nx, desc_y_base + i * (desc_font_size + 4)), "（无描述）",
                                      font=desc_font_actual, fill=_hex_to_rgb("#999999"))

            # 命令列表
            commands = plugin.get("commands", [])
            if commands:
                cmd_y_base = card_y + card_h - 30
                cmd_font_actual = _load_font(cfg["default_font"], cmd_font_size)
                if cmd_font_actual:
                    # 命令标签（show_command_count 时附带数量）
                    if cfg.get("show_command_count", False):
                        label_text = "命令 ({}):".format(len(commands))
                    else:
                        label_text = "命令:"
                    label_bbox = draw.textbbox((0, 0), label_text, font=cmd_font_actual)
                    draw.text((nx, cmd_y_base), label_text, font=cmd_font_actual, fill=accent_color)

                    # 绘制每个命令标签
                    cmd_x = nx + label_bbox[2] - label_bbox[0] + 8
                    cmd_tag_padding_x = 6
                    cmd_tag_padding_y = 2
                    cmd_tag_radius = 4

                    for cmd in commands:
                        cmd_text = f"/{cmd}" if not cmd.startswith("/") else cmd
                        cmd_bbox = draw.textbbox((0, 0), cmd_text, font=cmd_font_actual)
                        cmd_w = cmd_bbox[2] - cmd_bbox[0] + cmd_tag_padding_x * 2
                        cmd_h = cmd_bbox[3] - cmd_bbox[1] + cmd_tag_padding_y * 2 + 4

                        # 检查是否超出卡片宽度，超出则换行
                        if cmd_x + cmd_w > card_x + card_w - 15:
                            cmd_x = nx
                            cmd_y_base += cmd_h + 4

                        # 命令标签背景
                        tag_bg = _hex_to_rgba("#4A90D9", 50)
                        _draw_rounded_rectangle(
                            draw,
                            (cmd_x, cmd_y_base, cmd_x + cmd_w, cmd_y_base + cmd_h),
                            radius=cmd_tag_radius,
                            fill=tag_bg,
                        )
                        # 命令文本
                        draw.text(
                            (cmd_x + cmd_tag_padding_x, cmd_y_base + cmd_tag_padding_y),
                            cmd_text,
                            font=cmd_font_actual,
                            fill=accent_color,
                        )
                        cmd_x += cmd_w + 6

            y_offset += card_h + card_margin

        # 绘制底部信息
        footer_font = _load_font(cfg["default_font"], 11)
        footer_note = (cfg.get("footer_note") or "").strip()
        footer_x = width - 20
        # 自定义底部备注（左对齐底部）
        if footer_font and footer_note:
            fb = draw.textbbox((0, 0), footer_note, font=footer_font)
            fw = fb[2] - fb[0]
            draw.text((max(20, width - fw - 20), total_height - padding_bottom + 30), footer_note,
                      font=footer_font, fill=_hex_to_rgb(cfg["text_color"]))
        if footer_font:
            footer_text = f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            footer_bbox = draw.textbbox((0, 0), footer_text, font=footer_font)
            footer_x = width - footer_bbox[2] + footer_bbox[0] - 20
            draw.text((footer_x, total_height - padding_bottom + 15), footer_text,
                      font=footer_font, fill=_hex_to_rgb(cfg["border_color"]))

        # 保存图片
        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_format = cfg.get("output_format", "png")
            filename = f"plugin_list_{timestamp}.{output_format}"
            output_path = os.path.join(_get_output_dir(), filename)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        save_kwargs = {}
        if cfg["output_format"].lower() in ("jpeg", "jpg"):
            if img.mode == "RGBA":
                img = img.convert("RGB")
            save_kwargs["quality"] = cfg.get("output_quality", 95)
        elif cfg["output_format"].lower() == "webp":
            save_kwargs["quality"] = cfg.get("output_quality", 95)
        img.save(output_path, **save_kwargs)
        logger.info(f"插件列表图片已保存: {output_path}")
        return output_path

    except Exception as e:
        logger.error(f"生成插件列表图片失败: {e}", exc_info=True)
        return None


def render_preset_image(title: str, sub: str = "", config: dict = None,
                        output_path: str = None) -> Optional[str]:
    """绘制一张预置卡片图（用于无插件/生成失败等场景，以图代文）。

    参数:
        title: 主文案
        sub: 副文案
        config: 复用插件列表的样式配置
        output_path: 输出路径，留空自动生成

    返回:
        str: 图片路径，失败返回 None
    """
    if not HAS_PIL:
        return None

    cfg = {
        "image_width": 800,
        "bg_color": "#F5F5F5",
        "card_color": "#FFFFFF",
        "text_color": "#333333",
        "accent_color": "#4A90D9",
        "border_color": "#E0E0E0",
        "default_font": "",
        "output_format": "png",
        "output_quality": 95,
    }
    if config and isinstance(config, dict):
        cfg.update(config)

    try:
        width = max(200, int(cfg["image_width"]))
        height = 280
        bg_color = _hex_to_rgb(cfg["bg_color"])
        img = Image.new("RGBA", (width, height), (*bg_color, 255))
        draw = ImageDraw.Draw(img)

        try:
            _draw_gradient_background(draw, width, height, cfg["bg_color"], "#EBEBEB")
        except Exception:
            pass

        # 中间卡片
        card_w = width - 80
        card_h = height - 120
        card_x = (width - card_w) // 2
        card_y = (height - card_h) // 2
        _draw_rounded_rectangle(
            draw,
            (card_x, card_y, card_x + card_w, card_y + card_h),
            radius=16,
            fill=_hex_to_rgb(cfg["card_color"]),
            outline=_hex_to_rgb(cfg["border_color"]),
            width=1,
        )

        # 顶部分隔条（强调色）
        _draw_rounded_rectangle(
            draw,
            (card_x + 24, card_y + 22, card_x + 16, card_y + 26),
            radius=3,
            fill=_hex_to_rgb(cfg["accent_color"]),
        )

        # 主文案居中
        title_font = _load_font(cfg["default_font"], 26)
        if title_font:
            bbox = draw.textbbox((0, 0), title, font=title_font)
            w = bbox[2] - bbox[0]
            draw.text(((width - w) // 2, card_y + card_h // 2 - 22), title,
                      font=title_font, fill=_hex_to_rgb(cfg["text_color"]))

        # 副文案
        if sub:
            sub_font = _load_font(cfg["default_font"], 15)
            if sub_font:
                sbbox = draw.textbbox((0, 0), sub, font=sub_font)
                sw = sbbox[2] - sbbox[0]
                draw.text(((width - sw) // 2, card_y + card_h // 2 + 22), sub,
                          font=sub_font, fill=_hex_to_rgb(cfg["border_color"]))

        # 底部时间
        foot_font = _load_font(cfg["default_font"], 11)
        if foot_font:
            foot = f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            fb = draw.textbbox((0, 0), foot, font=foot_font)
            draw.text((width - (fb[2] - fb[0]) - 18, height - 34), foot,
                      font=foot_font, fill=_hex_to_rgb(cfg["border_color"]))

        if output_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = os.path.join(_get_output_dir(), f"plugin_preset_{timestamp}.png")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        img.save(output_path)
        logger.info(f"预置卡片已保存: {output_path}")
        return output_path

    except Exception as e:
        logger.error(f"生成预置卡片失败: {e}", exc_info=True)
        return None