"""
DateTime 插件 - 提供时间日期相关命令
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta

from zhiyan_web.plugins import PluginBase


class DateTimePlugin(PluginBase):
    """DateTime 插件"""

    author = "ZhiYan"
    name = "datetime"

    async def initialize(self):
        """插件初始化"""
        print("DateTimePlugin initialized")
        self.register_command("time", desc="显示当前时间")
        self.register_command("date", desc="显示当前日期")
        self.register_command("week", desc="显示本周信息")
        self.register_command("calendar", desc="显示月历")
        self.register_command("countdown", desc="倒计时")

    async def terminate(self):
        """插件卸载"""
        print("DateTimePlugin terminated")

    async def on_message(self, message: dict) -> str | None:
        """处理消息"""
        content = message.get("content", "")

        if content.startswith("/time"):
            return self._time()
        elif content.startswith("/date"):
            return self._date()
        elif content.startswith("/week"):
            return self._week()
        elif content.startswith("/calendar"):
            return self._calendar(content)
        elif content.startswith("/countdown"):
            return self._countdown(content)
        return None

    def _time(self) -> str:
        """显示当前时间"""
        now = datetime.now()
        result = [
            f"日期: {now.strftime('%Y年%m月%d日')}",
            f"星期: {['一', '二', '三', '四', '五', '六', '日'][now.weekday()]}",
            f"时间: {now.strftime('%H:%M:%S')}",
            f"月份: {now.month}月",
            f"年份: {now.year}年",
            f"今天是今年第 {now.timetuple().tm_yday} 天",
        ]
        return "\n".join(result)

    def _date(self) -> str:
        """显示当前日期"""
        now = datetime.now()
        return f"当前日期: {now.strftime('%Y年%m月%d日')}"

    def _week(self) -> str:
        """显示本周信息"""
        now = datetime.now()
        result = [
            f"本周是 {now.year} 年第 {now.isocalendar()[1]} 周",
            f"本周开始: {now - timedelta(days=now.weekday())}",
            f"本周结束: {now + timedelta(days=6 - now.weekday())}",
        ]
        return "\n".join(result)

    def _calendar(self, content: str) -> str:
        """显示月历"""
        now = datetime.now()
        year = now.year
        month = now.month

        message = content.replace("/calendar", "").strip()
        if message:
            parts = message.split()
            if len(parts) == 2:
                try:
                    year, month = int(parts[0]), int(parts[1])
                except ValueError:
                    return "格式错误，请使用: /calendar 2024 12"
            elif len(parts) == 1:
                try:
                    month = int(parts[0])
                except ValueError:
                    return "格式错误，请使用: /calendar 12"

        result = [f"📅 {year}年{month}月", "日 一 二 三 四 五 六"]

        cal = calendar.monthcalendar(year, month)
        for week in cal:
            week_str = ""
            for day in week:
                if day == 0:
                    week_str += "   "
                else:
                    week_str += f"{day:2d} "
            result.append(week_str)

        return "\n".join(result)

    def _countdown(self, content: str) -> str:
        """倒计时"""
        message = content.replace("/countdown", "").strip()

        if not message:
            return "用法: /countdown <日期>\n例如: /countdown 2024-12-31"

        try:
            target = datetime.strptime(message, "%Y-%m-%d")
            now = datetime.now()
            diff = target - now

            if diff.total_seconds() < 0:
                return f"{message} 已经过去了 {abs(diff).days} 天"

            days = diff.days
            hours = diff.seconds // 3600
            minutes = (diff.seconds % 3600) // 60
            seconds = diff.seconds % 60

            return f"距离 {message} 还有:\n{days} 天 {hours} 小时 {minutes} 分 {seconds} 秒"
        except ValueError:
            return "日期格式错误，请使用 YYYY-MM-DD 格式"