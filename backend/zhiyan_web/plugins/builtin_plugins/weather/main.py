"""
Weather 插件 - 提供天气查询命令
"""

from __future__ import annotations

import requests

from zhiyan_web.plugins import PluginBase


class WeatherPlugin(PluginBase):
    """Weather 插件"""

    author = "ZhiYan"
    name = "weather"

    async def initialize(self):
        """插件初始化"""
        print("WeatherPlugin initialized")
        self.register_command("weather", desc="查询天气")
        self.register_command("forecast", desc="查询天气预报")

    async def terminate(self):
        """插件卸载"""
        print("WeatherPlugin terminated")

    async def on_message(self, message: dict) -> str | None:
        """处理消息"""
        content = message.get("content", "")

        if content.startswith("/weather"):
            city = content.replace("/weather", "").strip()
            return self._weather(city)
        elif content.startswith("/forecast"):
            city = content.replace("/forecast", "").strip()
            return self._forecast(city)
        return None

    def _weather(self, city: str) -> str:
        """查询当前天气"""
        if not city:
            return "用法: /weather <城市名>\n例如: /weather 北京"

        try:
            url = "https://api.openweathermap.org/data/2.5/weather"
            params = {
                "q": city,
                "appid": "YOUR_API_KEY",
                "units": "metric",
                "lang": "zh_cn"
            }
            response = requests.get(url, params=params, timeout=10)
            data = response.json()

            if response.status_code != 200:
                return f"获取天气失败: {data.get('message', '未知错误')}"

            temp = data["main"]["temp"]
            feels_like = data["main"]["feels_like"]
            humidity = data["main"]["humidity"]
            pressure = data["main"]["pressure"]
            weather = data["weather"][0]["description"]
            wind_speed = data["wind"]["speed"]
            city_name = data["name"]

            result = f"🌤️ {city_name} 天气\n"
            result += f"天气: {weather}\n"
            result += f"温度: {temp}°C (体感 {feels_like}°C)\n"
            result += f"湿度: {humidity}%\n"
            result += f"气压: {pressure} hPa\n"
            result += f"风速: {wind_speed} m/s"

            return result
        except requests.exceptions.Timeout:
            return "天气查询超时，请稍后重试"
        except Exception as e:
            return f"天气查询失败: {str(e)}\n请确保已配置有效的 API Key"

    def _forecast(self, city: str) -> str:
        """查询天气预报"""
        if not city:
            return "用法: /forecast <城市名>\n例如: /forecast 上海"

        try:
            url = "https://api.openweathermap.org/data/2.5/forecast"
            params = {
                "q": city,
                "appid": "YOUR_API_KEY",
                "units": "metric",
                "lang": "zh_cn"
            }
            response = requests.get(url, params=params, timeout=10)
            data = response.json()

            if response.status_code != 200:
                return f"获取预报失败: {data.get('message', '未知错误')}"

            city_name = data["city"]["name"]
            result = f"📅 {city_name} 5天预报\n"

            for i in range(0, 40, 8):
                forecast = data["list"][i]
                date = forecast["dt_txt"].split(" ")[0]
                temp_min = forecast["main"]["temp_min"]
                temp_max = forecast["main"]["temp_max"]
                weather = forecast["weather"][0]["description"]
                result += f"{date}: {weather}, {temp_min}°C ~ {temp_max}°C\n"

            return result
        except requests.exceptions.Timeout:
            return "预报查询超时，请稍后重试"
        except Exception as e:
            return f"预报查询失败: {str(e)}\n请确保已配置有效的 API Key"
